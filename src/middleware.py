from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import logging
from queue import Empty, Full, Queue
import random
from threading import Event, RLock, Thread
import time

from .allowlist_cache import LocalAllowlistCache
from .config import get_settings
from .persistence import PersistenceConfigError, PersistenceRuntimeError, VaultStore
from .pii_engine import PIIEngine, RedactionResult, RehydrationResult
from .pii_vault import PIIVault
from .types import ScopeContext


LOGGER = logging.getLogger(__name__)
_PERSISTENCE_RECOVERY_MAX_SECONDS = 300


class PersistenceUnavailableError(RuntimeError):
    """Raised when persistence health policy blocks request handling."""


@dataclass(slots=True)
class _VaultEntry:
    scope: ScopeContext
    vault: PIIVault
    last_access_epoch: float


@dataclass(slots=True)
class _PersistTask:
    op: str
    scope: ScopeContext
    snapshot: dict[str, object] | None = None
    expires_at_epoch: float | None = None
    key_version: str = "v1"
    enqueued_at_epoch: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class _PersistenceStateSnapshot:
    state: str
    healthy: bool
    worker_alive: bool
    worker_restart_count: int
    last_worker_restart_at_epoch: float | None
    last_error: str | None
    last_error_type: str | None
    last_error_category: str | None
    last_error_status_code: int | None
    last_error_operation: str | None
    last_error_at_epoch: float | None
    last_success_at_epoch: float | None
    unhealthy_since_epoch: float | None
    consecutive_failures: int
    recovery_attempts: int
    last_recovery_attempt_at_epoch: float | None
    next_recovery_at_epoch: float | None
    recovery_cooldown_seconds: int


def _epoch_to_iso8601(value: float | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _scope_hash(scope: ScopeContext) -> str:
    return hashlib.sha256(scope.key().encode("utf-8")).hexdigest()[:16]


class _RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = RLock()
        self._data: dict[str, dict[str, float]] = {}

    def record(self, name: str, elapsed_seconds: float) -> None:
        elapsed_ms = max(0.0, elapsed_seconds * 1000.0)
        with self._lock:
            item = self._data.setdefault(
                name,
                {
                    "count": 0.0,
                    "total_ms": 0.0,
                    "max_ms": 0.0,
                    "last_ms": 0.0,
                },
            )
            item["count"] += 1.0
            item["total_ms"] += elapsed_ms
            item["max_ms"] = max(item["max_ms"], elapsed_ms)
            item["last_ms"] = elapsed_ms

    def snapshot(self) -> dict[str, int | float]:
        with self._lock:
            result: dict[str, int | float] = {}
            for name, item in sorted(self._data.items()):
                count = int(item["count"])
                result[f"{name}_count"] = count
                result[f"{name}_avg_ms"] = round(item["total_ms"] / count, 2) if count else 0.0
                result[f"{name}_max_ms"] = round(item["max_ms"], 2)
                result[f"{name}_last_ms"] = round(item["last_ms"], 2)
            return result


class _RuntimeRequestState:
    def __init__(self) -> None:
        self._lock = RLock()
        self._active: dict[str, int] = {"redact": 0, "rehydrate": 0}
        self._saturated: dict[str, int] = {"redact": 0, "rehydrate": 0}
        self._persistence_blocking_requests = 0

    def enter(self, endpoint: str) -> None:
        with self._lock:
            self._active[endpoint] = self._active.get(endpoint, 0) + 1

    def exit(self, endpoint: str) -> None:
        with self._lock:
            self._active[endpoint] = max(0, self._active.get(endpoint, 0) - 1)

    def record_saturated(self, endpoint: str) -> None:
        with self._lock:
            self._saturated[endpoint] = self._saturated.get(endpoint, 0) + 1

    def record_persistence_blocking_request(self) -> None:
        with self._lock:
            self._persistence_blocking_requests += 1

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "redact_active": self._active.get("redact", 0),
                "rehydrate_active": self._active.get("rehydrate", 0),
                "redact_saturated_count": self._saturated.get("redact", 0),
                "rehydrate_saturated_count": self._saturated.get("rehydrate", 0),
                "persistence_blocking_requests": self._persistence_blocking_requests,
            }


class _AsyncPersistenceWriter:
    def __init__(
        self,
        store: VaultStore,
        *,
        max_queue_size: int,
        persistence_mode: str,
        recovery_cooldown_seconds: int,
        metrics: _RuntimeMetrics | None = None,
    ) -> None:
        self._store = store
        self._metrics = metrics
        self._queue: Queue[_PersistTask] = Queue(maxsize=max_queue_size)
        self._stop_event = Event()
        self._state_lock = RLock()
        self._persistence_mode = persistence_mode
        self._recovery_cooldown_seconds = max(1, int(recovery_cooldown_seconds))
        self._state = "healthy"
        self._current_error: str | None = None
        self._last_error: str | None = None
        self._last_error_type: str | None = None
        self._last_error_category: str | None = None
        self._last_error_status_code: int | None = None
        self._last_error_operation: str | None = None
        self._last_error_at_epoch: float | None = None
        self._last_success_at_epoch: float | None = None
        self._unhealthy_since_epoch: float | None = None
        self._consecutive_failures = 0
        self._recovery_attempts = 0
        self._last_recovery_attempt_at_epoch: float | None = None
        self._next_recovery_at_epoch: float | None = None
        self._last_failed_task: _PersistTask | None = None
        self._worker_restart_count = 0
        self._last_worker_restart_at_epoch: float | None = None
        self._thread = self._new_thread()
        self._thread.start()

    def _new_thread(self) -> Thread:
        return Thread(target=self._run, name="pii-persist-writer", daemon=True)

    def enqueue_save(
        self,
        scope: ScopeContext,
        snapshot: dict[str, object],
        *,
        expires_at_epoch: float,
        key_version: str,
    ) -> bool:
        task = _PersistTask(
            op="save",
            scope=scope,
            snapshot=snapshot,
            expires_at_epoch=expires_at_epoch,
            key_version=key_version,
        )
        try:
            self._queue.put_nowait(task)
            return True
        except Full:
            return False

    def enqueue_delete(self, scope: ScopeContext) -> bool:
        task = _PersistTask(op="delete", scope=scope)
        try:
            self._queue.put_nowait(task)
            return True
        except Full:
            return False

    @property
    def last_error(self) -> str | None:
        with self._state_lock:
            return self._last_error

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def healthy(self) -> bool:
        with self._state_lock:
            return self._current_error is None and self._thread.is_alive()

    @property
    def worker_alive(self) -> bool:
        return self._thread.is_alive()

    def status_snapshot(self) -> _PersistenceStateSnapshot:
        with self._state_lock:
            return _PersistenceStateSnapshot(
                state=self._state,
                healthy=self._current_error is None and self._thread.is_alive(),
                worker_alive=self._thread.is_alive(),
                worker_restart_count=self._worker_restart_count,
                last_worker_restart_at_epoch=self._last_worker_restart_at_epoch,
                last_error=self._last_error,
                last_error_type=self._last_error_type,
                last_error_category=self._last_error_category,
                last_error_status_code=self._last_error_status_code,
                last_error_operation=self._last_error_operation,
                last_error_at_epoch=self._last_error_at_epoch,
                last_success_at_epoch=self._last_success_at_epoch,
                unhealthy_since_epoch=self._unhealthy_since_epoch,
                consecutive_failures=self._consecutive_failures,
                recovery_attempts=self._recovery_attempts,
                last_recovery_attempt_at_epoch=self._last_recovery_attempt_at_epoch,
                next_recovery_at_epoch=self._next_recovery_at_epoch,
                recovery_cooldown_seconds=self._recovery_cooldown_seconds,
            )

    def ensure_worker_running(self) -> bool:
        with self._state_lock:
            if self._thread.is_alive():
                return True
            if self._stop_event.is_set():
                return False

            self._thread = self._new_thread()
            self._thread.start()
            self._worker_restart_count += 1
            self._last_worker_restart_at_epoch = time.time()
            restart_count = self._worker_restart_count

        LOGGER.warning(
            "persistence_worker_restarted mode=%s restart_count=%s queue_depth=%s",
            self._persistence_mode,
            restart_count,
            self.queue_depth,
        )
        return True

    def maybe_recover(self) -> bool:
        with self._state_lock:
            if self._current_error is None:
                return True

            now = time.time()
            if self._next_recovery_at_epoch is not None and now < self._next_recovery_at_epoch:
                return False

            task = self._last_failed_task
            if task is None:
                return False
            if not self._should_retry_category(self._last_error_category):
                return False

            self._state = "recovering"
            self._recovery_attempts += 1
            attempt = self._recovery_attempts
            self._last_recovery_attempt_at_epoch = now

        LOGGER.info(
            "persistence_recovery_start mode=%s op=%s scope_hash=%s attempt=%s queue_depth=%s",
            self._persistence_mode,
            task.op,
            _scope_hash(task.scope),
            attempt,
            self.queue_depth,
        )
        try:
            self._execute_task(task)
        except Exception as exc:  # pragma: no cover - depends on external store behavior
            self._record_failure(task, exc)
            LOGGER.warning(
                "persistence_recovery_failure mode=%s op=%s scope_hash=%s attempt=%s queue_depth=%s error_type=%s error_category=%s status_code=%s error=%s",
                self._persistence_mode,
                task.op,
                _scope_hash(task.scope),
                attempt,
                self.queue_depth,
                self._error_type(exc),
                self._error_category(exc),
                self._error_status_code(exc),
                exc,
            )
            return False

        self._record_success(task)
        LOGGER.info(
            "persistence_recovery_success mode=%s op=%s scope_hash=%s attempt=%s queue_depth=%s",
            self._persistence_mode,
            task.op,
            _scope_hash(task.scope),
            attempt,
            self.queue_depth,
        )
        return True

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.2)
            except Empty:
                continue

            queue_lag_seconds = max(0.0, time.time() - task.enqueued_at_epoch)
            task_start = time.perf_counter()
            try:
                self._execute_task(task)
                self._record_success(task)
            except Exception as exc:  # pragma: no cover - depends on external store behavior
                self._record_failure(task, exc)
            finally:
                elapsed_seconds = time.perf_counter() - task_start
                if self._metrics is not None:
                    self._metrics.record("persistence_queue_lag", queue_lag_seconds)
                    self._metrics.record(f"persistence_{task.op}", elapsed_seconds)
                LOGGER.info(
                    "persistence_task_timing mode=%s op=%s scope_hash=%s duration_ms=%.2f queue_lag_ms=%.2f queue_depth=%s",
                    self._persistence_mode,
                    task.op,
                    _scope_hash(task.scope),
                    elapsed_seconds * 1000.0,
                    queue_lag_seconds * 1000.0,
                    self.queue_depth,
                )
                self._queue.task_done()

    def _execute_task(self, task: _PersistTask) -> None:
        if task.op == "save":
            assert task.snapshot is not None
            assert task.expires_at_epoch is not None
            self._store.save(
                task.scope,
                task.snapshot,
                expires_at_epoch=task.expires_at_epoch,
                key_version=task.key_version,
            )
            return
        if task.op == "delete":
            self._store.delete(task.scope)
            return
        raise RuntimeError(f"Unsupported persistence operation '{task.op}'")

    def _record_success(self, task: _PersistTask) -> None:
        recovered = False
        now = time.time()
        with self._state_lock:
            recovered = self._current_error is not None
            self._state = "healthy"
            self._current_error = None
            self._last_success_at_epoch = now
            self._unhealthy_since_epoch = None
            self._consecutive_failures = 0
            self._next_recovery_at_epoch = None
            self._last_failed_task = None
        if recovered:
            LOGGER.info(
                "persistence_recovered mode=%s op=%s scope_hash=%s queue_depth=%s",
                self._persistence_mode,
                task.op,
                _scope_hash(task.scope),
                self.queue_depth,
            )

    def _record_failure(self, task: _PersistTask, exc: Exception) -> None:
        message = str(exc)
        error_type = self._error_type(exc)
        error_category = self._error_category(exc)
        status_code = self._error_status_code(exc)
        now = time.time()
        with self._state_lock:
            if self._unhealthy_since_epoch is None:
                self._unhealthy_since_epoch = now
            self._state = self._state_for_error_category(error_category)
            self._current_error = message
            self._last_error = message
            self._last_error_type = error_type
            self._last_error_category = error_category
            self._last_error_status_code = status_code
            self._last_error_operation = task.op
            self._last_error_at_epoch = now
            self._consecutive_failures += 1
            self._last_failed_task = task
            consecutive_failures = self._consecutive_failures
            next_recovery_at = (
                now + self._recovery_delay_seconds(consecutive_failures)
                if self._should_retry_category(error_category)
                else None
            )
            self._next_recovery_at_epoch = next_recovery_at
        LOGGER.warning(
            "persistence_task_failure mode=%s op=%s scope_hash=%s queue_depth=%s consecutive_failures=%s error_type=%s error_category=%s status_code=%s next_recovery_at=%s error=%s",
            self._persistence_mode,
            task.op,
            _scope_hash(task.scope),
            self.queue_depth,
            consecutive_failures,
            error_type,
            error_category,
            status_code,
            _epoch_to_iso8601(next_recovery_at),
            message,
        )

    @staticmethod
    def _error_type(exc: Exception) -> str:
        root = exc.__cause__ or exc.__context__ or exc
        return type(root).__name__

    @staticmethod
    def _error_status_code(exc: Exception) -> int | None:
        if isinstance(exc, PersistenceRuntimeError):
            return exc.status_code
        root = exc.__cause__ or exc.__context__
        return int(root.code) if root is not None and hasattr(root, "code") else None

    @staticmethod
    def _error_category(exc: Exception) -> str:
        if isinstance(exc, PersistenceRuntimeError):
            return exc.category
        if isinstance(exc, PersistenceConfigError):
            return "misconfigured"
        if isinstance(exc, TimeoutError | ConnectionError | OSError):
            return "transient"
        status_code = _AsyncPersistenceWriter._error_status_code(exc)
        if status_code in {401, 403}:
            return "auth"
        if status_code in {400, 404, 409, 422}:
            return "schema"
        if status_code is not None and (status_code >= 500 or status_code in {408, 425, 429}):
            return "transient"
        return "unknown"

    @staticmethod
    def _state_for_error_category(error_category: str) -> str:
        if error_category == "misconfigured":
            return "misconfigured"
        if error_category in {"auth", "schema"}:
            return "unavailable"
        return "degraded"

    @staticmethod
    def _should_retry_category(error_category: str | None) -> bool:
        return error_category in {None, "transient", "backend", "unknown"}

    def _recovery_delay_seconds(self, consecutive_failures: int) -> float:
        multiplier = 2 ** min(max(consecutive_failures - 1, 0), 5)
        base_delay = min(
            _PERSISTENCE_RECOVERY_MAX_SECONDS,
            self._recovery_cooldown_seconds * multiplier,
        )
        return max(1.0, base_delay * random.uniform(0.8, 1.0))


class PIIMiddleware:
    """Session-aware orchestrator for inbound/outbound text processing."""

    def __init__(
        self,
        engine: PIIEngine | None = None,
        *,
        vault_store: VaultStore | None = None,
        persistence_mode: str | None = None,
        vault_ttl_seconds: int | None = None,
        max_active_scopes: int | None = None,
        persistence_queue_max: int | None = None,
        persistence_block_on_error: bool | None = None,
        persistence_recovery_cooldown_seconds: int | None = None,
        persistence_key_version: str | None = None,
        allowlist_cache: LocalAllowlistCache | None = None,
    ) -> None:
        settings = get_settings()
        self.engine = engine or PIIEngine()
        self._metrics = _RuntimeMetrics()
        self._request_state = _RuntimeRequestState()
        self._lock = RLock()
        self._vaults: OrderedDict[str, _VaultEntry] = OrderedDict()
        self._store = vault_store
        self._persistence_mode = (persistence_mode or settings.persistence_mode or "none").strip().lower()
        if settings.require_persistence and self._store is None:
            raise ValueError("Persistence is required but no vault store implementation was provided")
        self._vault_ttl_seconds = (
            settings.vault_ttl_seconds if vault_ttl_seconds is None else max(60, int(vault_ttl_seconds))
        )
        self._max_active_scopes = (
            settings.max_active_scopes if max_active_scopes is None else max(1, int(max_active_scopes))
        )
        self._persistence_block_on_error = (
            settings.persistence_block_on_error
            if persistence_block_on_error is None
            else bool(persistence_block_on_error)
        )
        self._persistence_recovery_cooldown_seconds = (
            settings.persistence_recovery_cooldown_seconds
            if persistence_recovery_cooldown_seconds is None
            else max(1, int(persistence_recovery_cooldown_seconds))
        )
        self._persistence_key_version = (
            settings.persistence_key_version if persistence_key_version is None else persistence_key_version
        )
        queue_max = settings.persistence_queue_max if persistence_queue_max is None else max(1, int(persistence_queue_max))
        self._persistence_queue_max = queue_max if vault_store else 0
        self._writer = (
            _AsyncPersistenceWriter(
                vault_store,
                max_queue_size=queue_max,
                persistence_mode=self._persistence_mode,
                recovery_cooldown_seconds=self._persistence_recovery_cooldown_seconds,
                metrics=self._metrics,
            )
            if vault_store
            else None
        )
        self._allowlist_cache = allowlist_cache

    @property
    def active_sessions(self) -> int:
        with self._lock:
            expired = self._prune_expired_locked(time.time())
            self._cleanup_scope_entries(expired)
            return len(self._vaults)

    @property
    def detector_status(self) -> dict[str, object]:
        if self._writer is not None:
            self._writer.ensure_worker_running()
        writer_status = self._writer.status_snapshot() if self._writer is not None else None
        if self._store is None:
            persistence_status = "disabled"
            persistence_state = "disabled"
        elif writer_status is None or writer_status.healthy:
            persistence_status = "healthy"
            persistence_state = "healthy"
        elif self._persistence_block_on_error:
            persistence_status = "blocking"
            persistence_state = writer_status.state
        else:
            persistence_status = writer_status.state
            persistence_state = writer_status.state
        request_status = self._request_state.snapshot()
        status = dict(self.engine.runtime_info)
        status.update(
            {
                "redact_active": request_status["redact_active"],
                "rehydrate_active": request_status["rehydrate_active"],
                "redact_max_concurrency": 0,
                "rehydrate_max_concurrency": 0,
                "redact_saturated_count": request_status["redact_saturated_count"],
                "rehydrate_saturated_count": request_status["rehydrate_saturated_count"],
                "persistence_enabled": self._store is not None,
                "persistence_mode": self._persistence_mode,
                "persistence_status": persistence_status,
                "persistence_state": persistence_state,
                "persistence_block_on_error": self._persistence_block_on_error,
                "persistence_healthy": writer_status.healthy if writer_status is not None else True,
                "persistence_worker_alive": writer_status.worker_alive if writer_status is not None else True,
                "persistence_worker_restart_count": (
                    writer_status.worker_restart_count if writer_status is not None else 0
                ),
                "persistence_last_worker_restart_at": (
                    _epoch_to_iso8601(writer_status.last_worker_restart_at_epoch)
                    if writer_status is not None
                    else None
                ),
                "persistence_last_error": writer_status.last_error if writer_status is not None else "",
                "persistence_last_error_type": writer_status.last_error_type if writer_status is not None else "",
                "persistence_last_error_category": (
                    writer_status.last_error_category if writer_status is not None else ""
                ),
                "persistence_last_error_status_code": (
                    writer_status.last_error_status_code if writer_status is not None else None
                ),
                "persistence_last_error_operation": (
                    writer_status.last_error_operation if writer_status is not None else ""
                ),
                "persistence_last_error_at": (
                    _epoch_to_iso8601(writer_status.last_error_at_epoch) if writer_status is not None else None
                ),
                "persistence_last_success_at": (
                    _epoch_to_iso8601(writer_status.last_success_at_epoch) if writer_status is not None else None
                ),
                "persistence_unhealthy_since": (
                    _epoch_to_iso8601(writer_status.unhealthy_since_epoch) if writer_status is not None else None
                ),
                "persistence_consecutive_failures": (
                    writer_status.consecutive_failures if writer_status is not None else 0
                ),
                "persistence_recovery_attempts": writer_status.recovery_attempts if writer_status is not None else 0,
                "persistence_last_recovery_attempt_at": (
                    _epoch_to_iso8601(writer_status.last_recovery_attempt_at_epoch) if writer_status is not None else None
                ),
                "persistence_next_recovery_at": (
                    _epoch_to_iso8601(writer_status.next_recovery_at_epoch) if writer_status is not None else None
                ),
                "persistence_recovery_cooldown_seconds": (
                    writer_status.recovery_cooldown_seconds
                    if writer_status is not None
                    else self._persistence_recovery_cooldown_seconds
                ),
                "persistence_queue_depth": self._writer.queue_depth if self._writer is not None else 0,
                "persistence_queue_max": self._persistence_queue_max,
                "persistence_blocking_requests": request_status["persistence_blocking_requests"],
                "performance_metrics": self._metrics.snapshot(),
                "scope_ttl_seconds": self._vault_ttl_seconds,
                "max_active_scopes": self._max_active_scopes,
                "allowlist_cache_enabled": self._allowlist_cache is not None,
            }
        )
        return status

    def process_inbound(
        self,
        scope: ScopeContext,
        raw_user_message: str,
        new_user: bool = False,
        previous_assistant_message: str | None = None,
        non_name_allowlist: list[str] | None = None,
        fail_closed: bool = True,
    ) -> RedactionResult:
        LOGGER.info("redact_start scope=%s", scope.key())
        self._request_state.enter("redact")
        started = time.perf_counter()
        gate_seconds = 0.0
        detector_seconds = 0.0
        enqueue_seconds = 0.0
        try:
            gate_started = time.perf_counter()
            self._ensure_persistence_healthy()
            gate_seconds = time.perf_counter() - gate_started
            self._metrics.record("redact_persistence_gate", gate_seconds)
            vault = self._get_or_create_vault(scope, fail_closed=fail_closed)
            if new_user:
                vault.advance_profile()
            combined_allowlist = list(non_name_allowlist or ())
            if self._allowlist_cache is not None:
                cached_terms = self._allowlist_cache.get(scope.client_id, scope.assistant_id)
                if cached_terms:
                    merged = set(combined_allowlist)
                    merged.update(cached_terms)
                    combined_allowlist = sorted(merged)
            detector_started = time.perf_counter()
            result = self.engine.redact(
                raw_user_message,
                vault,
                previous_assistant_message=previous_assistant_message,
                non_name_allowlist=combined_allowlist,
            )
            detector_seconds = time.perf_counter() - detector_started
            self._metrics.record("redact_detector", detector_seconds)
            enqueue_started = time.perf_counter()
            self._persist_snapshot(scope, vault, fail_closed=fail_closed)
            enqueue_seconds = time.perf_counter() - enqueue_started
            self._metrics.record("redact_persist_enqueue", enqueue_seconds)
            total_seconds = time.perf_counter() - started
            self._metrics.record("redact_total", total_seconds)
            LOGGER.info(
                "redact_success scope=%s profile=%s replacements=%s",
                scope.key(),
                result.active_profile,
                len(result.replacements),
            )
            LOGGER.info(
                "redact_timing scope_hash=%s total_ms=%.2f persistence_gate_ms=%.2f detector_ms=%.2f persist_enqueue_ms=%.2f queue_depth=%s",
                _scope_hash(scope),
                total_seconds * 1000.0,
                gate_seconds * 1000.0,
                detector_seconds * 1000.0,
                enqueue_seconds * 1000.0,
                self._writer.queue_depth if self._writer is not None else 0,
            )
            return result
        except Exception as exc:
            total_seconds = time.perf_counter() - started
            self._metrics.record("redact_failure_total", total_seconds)
            LOGGER.info(
                "redact_timing scope_hash=%s total_ms=%.2f persistence_gate_ms=%.2f detector_ms=%.2f persist_enqueue_ms=%.2f status=failure",
                _scope_hash(scope),
                total_seconds * 1000.0,
                gate_seconds * 1000.0,
                detector_seconds * 1000.0,
                enqueue_seconds * 1000.0,
            )
            LOGGER.warning("redact_failure scope=%s fail_closed=%s error=%s", scope.key(), fail_closed, exc)
            if fail_closed:
                raise
            return RedactionResult(redacted_text=raw_user_message, replacements={}, active_profile=1)
        finally:
            self._request_state.exit("redact")

    def process_outbound(
        self,
        scope: ScopeContext,
        llm_response: str,
        *,
        fail_closed: bool = True,
    ) -> RehydrationResult:
        LOGGER.info("rehydrate_start scope=%s", scope.key())
        self._request_state.enter("rehydrate")
        started = time.perf_counter()
        gate_seconds = 0.0
        rehydrate_seconds = 0.0
        try:
            gate_started = time.perf_counter()
            self._ensure_persistence_healthy()
            gate_seconds = time.perf_counter() - gate_started
            self._metrics.record("rehydrate_persistence_gate", gate_seconds)
            vault = self._get_vault(scope, fail_closed=fail_closed, allow_store_load=True)
            if vault is None:
                if fail_closed:
                    raise PersistenceUnavailableError("Vault not found for scoped rehydration")
                total_seconds = time.perf_counter() - started
                self._metrics.record("rehydrate_total", total_seconds)
                LOGGER.info(
                    "rehydrate_timing scope_hash=%s total_ms=%.2f persistence_gate_ms=%.2f rehydrate_ms=0.00 status=missing_vault_fail_open",
                    _scope_hash(scope),
                    total_seconds * 1000.0,
                    gate_seconds * 1000.0,
                )
                return RehydrationResult(
                    clean_text=llm_response,
                    repaired_text=llm_response,
                    repaired_placeholders=False,
                )
            rehydrate_started = time.perf_counter()
            result = self.engine.rehydrate(llm_response, vault)
            rehydrate_seconds = time.perf_counter() - rehydrate_started
            self._metrics.record("rehydrate_engine", rehydrate_seconds)
            total_seconds = time.perf_counter() - started
            self._metrics.record("rehydrate_total", total_seconds)
            LOGGER.info(
                "rehydrate_success scope=%s repaired_placeholders=%s",
                scope.key(),
                result.repaired_placeholders,
            )
            LOGGER.info(
                "rehydrate_timing scope_hash=%s total_ms=%.2f persistence_gate_ms=%.2f rehydrate_ms=%.2f queue_depth=%s",
                _scope_hash(scope),
                total_seconds * 1000.0,
                gate_seconds * 1000.0,
                rehydrate_seconds * 1000.0,
                self._writer.queue_depth if self._writer is not None else 0,
            )
            return result
        except Exception as exc:
            total_seconds = time.perf_counter() - started
            self._metrics.record("rehydrate_failure_total", total_seconds)
            LOGGER.info(
                "rehydrate_timing scope_hash=%s total_ms=%.2f persistence_gate_ms=%.2f rehydrate_ms=%.2f status=failure",
                _scope_hash(scope),
                total_seconds * 1000.0,
                gate_seconds * 1000.0,
                rehydrate_seconds * 1000.0,
            )
            LOGGER.warning("rehydrate_failure scope=%s fail_closed=%s error=%s", scope.key(), fail_closed, exc)
            if fail_closed:
                raise
            return RehydrationResult(
                clean_text=llm_response,
                repaired_text=llm_response,
                repaired_placeholders=False,
            )
        finally:
            self._request_state.exit("rehydrate")

    def end_session(self, scope: ScopeContext, *, fail_closed: bool = True) -> bool:
        LOGGER.info("session_end_start scope=%s", scope.key())
        key = scope.key()
        with self._lock:
            entry = self._vaults.pop(key, None)
        vault = entry.vault if entry is not None else None
        if vault is None:
            LOGGER.info("session_end_success scope=%s status=session_not_found", scope.key())
            return False
        if self._writer is not None:
            queued = self._writer.enqueue_delete(scope)
            if not queued:
                LOGGER.warning(
                    "persistence_queue_full op=delete scope_hash=%s fail_closed=%s queue_depth=%s queue_max=%s",
                    _scope_hash(scope),
                    fail_closed,
                    self._writer.queue_depth,
                    self._persistence_queue_max,
                )
            if not queued and fail_closed:
                raise PersistenceUnavailableError("Persistence queue full while ending session")
        vault.destroy()
        LOGGER.info("session_end_success scope=%s status=vault_destroyed", scope.key())
        return True

    def _get_or_create_vault(self, scope: ScopeContext, *, fail_closed: bool) -> PIIVault:
        key = scope.key()
        now = time.time()

        with self._lock:
            expired = self._prune_expired_locked(now)
            self._cleanup_scope_entries(expired)
            entry = self._vaults.get(key)
            if entry is not None:
                entry.last_access_epoch = now
                self._vaults.move_to_end(key)
                return entry.vault

        loaded_vault: PIIVault | None = None
        if self._store is not None:
            try:
                snapshot = self._load_snapshot(scope, operation="redact_load")
                if snapshot:
                    loaded_vault = PIIVault.from_snapshot(snapshot)
            except Exception:
                if fail_closed:
                    raise

        with self._lock:
            entry = self._vaults.get(key)
            if entry is not None:
                entry.last_access_epoch = now
                self._vaults.move_to_end(key)
                return entry.vault

            vault = loaded_vault or PIIVault()
            self._vaults[key] = _VaultEntry(scope=scope, vault=vault, last_access_epoch=now)
            self._vaults.move_to_end(key)
            evicted = self._evict_over_capacity_locked()

        self._cleanup_scope_entries(evicted)
        return vault

    def _get_vault(
        self,
        scope: ScopeContext,
        *,
        fail_closed: bool,
        allow_store_load: bool,
    ) -> PIIVault | None:
        key = scope.key()
        now = time.time()

        with self._lock:
            expired = self._prune_expired_locked(now)
            self._cleanup_scope_entries(expired)
            entry = self._vaults.get(key)
            if entry is not None:
                entry.last_access_epoch = now
                self._vaults.move_to_end(key)
                return entry.vault

        if not allow_store_load or self._store is None:
            return None

        try:
            snapshot = self._load_snapshot(scope, operation="rehydrate_load")
        except Exception:
            if fail_closed:
                raise
            return None

        if not snapshot:
            return None

        vault = PIIVault.from_snapshot(snapshot)
        with self._lock:
            self._vaults[key] = _VaultEntry(scope=scope, vault=vault, last_access_epoch=now)
            self._vaults.move_to_end(key)
            evicted = self._evict_over_capacity_locked()
        self._cleanup_scope_entries(evicted)
        return vault

    def _load_snapshot(self, scope: ScopeContext, *, operation: str) -> dict[str, object] | None:
        if self._store is None:
            return None
        started = time.perf_counter()
        try:
            return self._store.load(scope)
        finally:
            elapsed_seconds = time.perf_counter() - started
            self._metrics.record("persistence_load", elapsed_seconds)
            self._metrics.record(f"persistence_{operation}", elapsed_seconds)
            LOGGER.info(
                "persistence_load_timing op=%s scope_hash=%s duration_ms=%.2f queue_depth=%s",
                operation,
                _scope_hash(scope),
                elapsed_seconds * 1000.0,
                self._writer.queue_depth if self._writer is not None else 0,
            )

    def _persist_snapshot(self, scope: ScopeContext, vault: PIIVault, *, fail_closed: bool) -> None:
        if self._writer is None:
            return

        expires_at_epoch = time.time() + self._vault_ttl_seconds
        snapshot = vault.snapshot()
        queued = self._writer.enqueue_save(
            scope,
            snapshot,
            expires_at_epoch=expires_at_epoch,
            key_version=self._persistence_key_version,
        )
        if not queued:
            self._metrics.record("persistence_enqueue_full", 0.0)
            LOGGER.warning(
                "persistence_queue_full op=save scope_hash=%s fail_closed=%s queue_depth=%s queue_max=%s",
                _scope_hash(scope),
                fail_closed,
                self._writer.queue_depth,
                self._persistence_queue_max,
            )
        if not queued and fail_closed:
            raise PersistenceUnavailableError("Persistence queue full while saving vault snapshot")

    def _ensure_persistence_healthy(self) -> None:
        if self._writer is None:
            return
        self._writer.ensure_worker_running()
        if self._persistence_block_on_error and not self._writer.healthy:
            self._writer.maybe_recover()
        if self._persistence_block_on_error and not self._writer.healthy:
            writer_status = self._writer.status_snapshot()
            self._request_state.record_persistence_blocking_request()
            LOGGER.warning(
                "persistence_blocking_request state=%s error_category=%s queue_depth=%s queue_max=%s",
                writer_status.state,
                writer_status.last_error_category,
                self._writer.queue_depth,
                self._persistence_queue_max,
            )
            raise PersistenceUnavailableError("Persistence layer unhealthy")

    def _prune_expired_locked(self, now_epoch: float) -> list[tuple[str, _VaultEntry]]:
        expired_keys = [
            key
            for key, entry in self._vaults.items()
            if now_epoch - entry.last_access_epoch > self._vault_ttl_seconds
        ]
        expired_entries: list[tuple[str, _VaultEntry]] = []
        for key in expired_keys:
            entry = self._vaults.pop(key, None)
            if entry is not None:
                expired_entries.append((key, entry))
        return expired_entries

    def _evict_over_capacity_locked(self) -> list[tuple[str, _VaultEntry]]:
        evicted: list[tuple[str, _VaultEntry]] = []
        while len(self._vaults) > self._max_active_scopes:
            key, entry = self._vaults.popitem(last=False)
            evicted.append((key, entry))
        return evicted

    def _cleanup_scope_entries(self, entries: list[tuple[str, _VaultEntry]]) -> None:
        if not entries:
            return

        for _key, entry in entries:
            if self._writer is not None:
                self._writer.enqueue_delete(entry.scope)
            entry.vault.destroy()
