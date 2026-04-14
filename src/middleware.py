from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import logging
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
import time

from .config import get_settings
from .persistence import VaultStore
from .pii_engine import PIIEngine, RedactionResult, RehydrationResult
from .pii_vault import PIIVault
from .types import ScopeContext


LOGGER = logging.getLogger(__name__)


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


class _AsyncPersistenceWriter:
    def __init__(self, store: VaultStore, max_queue_size: int) -> None:
        self._store = store
        self._queue: Queue[_PersistTask] = Queue(maxsize=max_queue_size)
        self._stop_event = Event()
        self._thread = Thread(target=self._run, name="pii-persist-writer", daemon=True)
        self._last_error: str | None = None
        self._thread.start()

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
        return self._last_error

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def healthy(self) -> bool:
        return self._last_error is None

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.2)
            except Empty:
                continue

            try:
                if task.op == "save":
                    assert task.snapshot is not None
                    assert task.expires_at_epoch is not None
                    self._store.save(
                        task.scope,
                        task.snapshot,
                        expires_at_epoch=task.expires_at_epoch,
                        key_version=task.key_version,
                    )
                elif task.op == "delete":
                    self._store.delete(task.scope)
                self._last_error = None
            except Exception as exc:  # pragma: no cover - depends on external store behavior
                self._last_error = str(exc)
            finally:
                self._queue.task_done()


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
        persistence_key_version: str | None = None,
    ) -> None:
        settings = get_settings()
        self.engine = engine or PIIEngine()
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
        self._persistence_key_version = (
            settings.persistence_key_version if persistence_key_version is None else persistence_key_version
        )
        queue_max = settings.persistence_queue_max if persistence_queue_max is None else max(1, int(persistence_queue_max))
        self._writer = _AsyncPersistenceWriter(vault_store, max_queue_size=queue_max) if vault_store else None

    @property
    def active_sessions(self) -> int:
        with self._lock:
            expired = self._prune_expired_locked(time.time())
            self._cleanup_scope_entries(expired)
            return len(self._vaults)

    @property
    def detector_status(self) -> dict[str, object]:
        status = dict(self.engine.runtime_info)
        status.update(
            {
                "persistence_enabled": self._store is not None,
                "persistence_mode": self._persistence_mode,
                "persistence_healthy": self._writer.healthy if self._writer is not None else True,
                "persistence_last_error": self._writer.last_error if self._writer is not None else "",
                "persistence_queue_depth": self._writer.queue_depth if self._writer is not None else 0,
                "scope_ttl_seconds": self._vault_ttl_seconds,
                "max_active_scopes": self._max_active_scopes,
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
        try:
            self._ensure_persistence_healthy()
            vault = self._get_or_create_vault(scope, fail_closed=fail_closed)
            if new_user:
                vault.advance_profile()
            result = self.engine.redact(
                raw_user_message,
                vault,
                previous_assistant_message=previous_assistant_message,
                non_name_allowlist=non_name_allowlist,
            )
            self._persist_snapshot(scope, vault, fail_closed=fail_closed)
            LOGGER.info(
                "redact_success scope=%s profile=%s replacements=%s",
                scope.key(),
                result.active_profile,
                len(result.replacements),
            )
            return result
        except Exception as exc:
            LOGGER.warning("redact_failure scope=%s fail_closed=%s error=%s", scope.key(), fail_closed, exc)
            if fail_closed:
                raise
            return RedactionResult(redacted_text=raw_user_message, replacements={}, active_profile=1)

    def process_outbound(
        self,
        scope: ScopeContext,
        llm_response: str,
        *,
        fail_closed: bool = True,
    ) -> RehydrationResult:
        LOGGER.info("rehydrate_start scope=%s", scope.key())
        try:
            self._ensure_persistence_healthy()
            vault = self._get_vault(scope, fail_closed=fail_closed, allow_store_load=True)
            if vault is None:
                if fail_closed:
                    raise PersistenceUnavailableError("Vault not found for scoped rehydration")
                return RehydrationResult(
                    clean_text=llm_response,
                    repaired_text=llm_response,
                    repaired_placeholders=False,
                )
            result = self.engine.rehydrate(llm_response, vault)
            LOGGER.info(
                "rehydrate_success scope=%s repaired_placeholders=%s",
                scope.key(),
                result.repaired_placeholders,
            )
            return result
        except Exception as exc:
            LOGGER.warning("rehydrate_failure scope=%s fail_closed=%s error=%s", scope.key(), fail_closed, exc)
            if fail_closed:
                raise
            return RehydrationResult(
                clean_text=llm_response,
                repaired_text=llm_response,
                repaired_placeholders=False,
            )

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
            if not queued and fail_closed:
                LOGGER.warning("session_end_failure scope=%s fail_closed=%s reason=queue_full", scope.key(), fail_closed)
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
                snapshot = self._store.load(scope)
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
            snapshot = self._store.load(scope)
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
        if not queued and fail_closed:
            raise PersistenceUnavailableError("Persistence queue full while saving vault snapshot")

    def _ensure_persistence_healthy(self) -> None:
        if self._writer is None:
            return
        if self._persistence_block_on_error and not self._writer.healthy:
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
