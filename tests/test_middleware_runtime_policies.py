from __future__ import annotations

import time
from threading import Event, Thread

from src.middleware import PIIMiddleware, PersistenceUnavailableError, RedactorSaturatedError
from src.persistence import PersistenceRuntimeError
from src.pii_engine import RedactionResult, RehydrationResult
from src.types import ScopeContext


def _scope(label: str) -> ScopeContext:
    return ScopeContext(
        thread_id=f"thread_{label}",
        session_id=f"s_{label}",
        visitor_id=f"v_{label}",
        client_id="c1",
        assistant_id="a1",
    )


class _FlakySaveStore:
    def __init__(self) -> None:
        self._snapshots: dict[str, dict[str, object]] = {}
        self.save_calls = 0

    def load(self, scope: ScopeContext) -> dict[str, object] | None:
        return self._snapshots.get(scope.key())

    def save(
        self,
        scope: ScopeContext,
        snapshot: dict[str, object],
        *,
        expires_at_epoch: float,
        key_version: str,
    ) -> None:
        del expires_at_epoch, key_version
        self.save_calls += 1
        if self.save_calls == 1:
            raise TimeoutError("temporary supabase timeout")
        self._snapshots[scope.key()] = dict(snapshot)

    def delete(self, scope: ScopeContext) -> None:
        self._snapshots.pop(scope.key(), None)


class _AlwaysFailingSaveStore:
    def __init__(self) -> None:
        self.save_calls = 0

    def load(self, scope: ScopeContext) -> dict[str, object] | None:
        del scope
        return None

    def save(
        self,
        scope: ScopeContext,
        snapshot: dict[str, object],
        *,
        expires_at_epoch: float,
        key_version: str,
    ) -> None:
        del scope, snapshot, expires_at_epoch, key_version
        self.save_calls += 1
        raise TimeoutError("temporary supabase timeout")

    def delete(self, scope: ScopeContext) -> None:
        del scope


class _AlwaysFailingLoadStore:
    def load(self, scope: ScopeContext) -> dict[str, object] | None:
        del scope
        raise TimeoutError("temporary load timeout")

    def save(
        self,
        scope: ScopeContext,
        snapshot: dict[str, object],
        *,
        expires_at_epoch: float,
        key_version: str,
    ) -> None:
        del scope, snapshot, expires_at_epoch, key_version

    def delete(self, scope: ScopeContext) -> None:
        del scope


class _AuthFailureStore:
    def load(self, scope: ScopeContext) -> dict[str, object] | None:
        del scope
        return None

    def save(
        self,
        scope: ScopeContext,
        snapshot: dict[str, object],
        *,
        expires_at_epoch: float,
        key_version: str,
    ) -> None:
        del scope, snapshot, expires_at_epoch, key_version
        raise PersistenceRuntimeError(
            "Supabase save failed for table 'pii_vault_snapshots' (403)",
            operation="save",
            status_code=403,
            category="auth",
        )

    def delete(self, scope: ScopeContext) -> None:
        del scope


class _FakeEngine:
    runtime_info = {
        "presidio_enabled": False,
        "gliner_enabled": False,
        "name_detection_mode": "test",
        "gliner_model": "",
    }

    def redact(self, raw_user_message, vault, **_kwargs) -> RedactionResult:
        del vault
        return RedactionResult(
            redacted_text=f"redacted:{raw_user_message}",
            replacements={},
            active_profile=1,
        )

    def rehydrate(self, llm_response, vault) -> RehydrationResult:
        del vault
        return RehydrationResult(
            clean_text=f"clean:{llm_response}",
            repaired_text=llm_response,
            repaired_placeholders=False,
        )


class _BlockingEngine(_FakeEngine):
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def redact(self, raw_user_message, vault, **_kwargs) -> RedactionResult:
        self.started.set()
        if not self.release.wait(timeout=2.0):
            raise TimeoutError("blocking test engine timed out")
        return super().redact(raw_user_message, vault, **_kwargs)


class _BlockingRehydrateEngine(_FakeEngine):
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def rehydrate(self, llm_response, vault) -> RehydrationResult:
        self.started.set()
        if not self.release.wait(timeout=2.0):
            raise TimeoutError("blocking rehydrate test engine timed out")
        return super().rehydrate(llm_response, vault)


def _wait_for(predicate, *, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_detector_status_reports_active_redact_request() -> None:
    engine = _BlockingEngine()
    middleware = PIIMiddleware(engine=engine)
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            middleware.process_inbound(_scope("active_redact"), "My name is Alice Jones")
        except BaseException as exc:  # pragma: no cover - failure surfaced after join
            errors.append(exc)

    thread = Thread(target=_run)
    thread.start()
    assert engine.started.wait(timeout=1.0)

    status = middleware.detector_status
    assert status["redact_active"] == 1
    assert status["rehydrate_active"] == 0
    assert status["redact_saturated_count"] == 0
    assert status["rehydrate_saturated_count"] == 0
    assert status["persistence_blocking_requests"] == 0

    engine.release.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert errors == []
    assert middleware.detector_status["redact_active"] == 0


def test_redact_concurrency_saturation_fails_fast_and_releases_active_counter() -> None:
    engine = _BlockingEngine()
    middleware = PIIMiddleware(
        engine=engine,
        redact_max_concurrency=1,
        concurrency_acquire_timeout_seconds=0.01,
    )
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            middleware.process_inbound(_scope("redact_limit_active"), "My name is Alice Jones")
        except BaseException as exc:  # pragma: no cover - failure surfaced after join
            errors.append(exc)

    thread = Thread(target=_run)
    thread.start()
    assert engine.started.wait(timeout=1.0)
    assert middleware.detector_status["redact_active"] == 1

    try:
        middleware.process_inbound(_scope("redact_limit_rejected"), "My name is Bob Stone")
    except RedactorSaturatedError:
        pass
    else:
        raise AssertionError("Expected second redact request to saturate")

    saturated_status = middleware.detector_status
    assert saturated_status["redact_active"] == 1
    assert saturated_status["redact_saturated_count"] == 1
    assert saturated_status["redact_max_concurrency"] == 1

    engine.release.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert errors == []
    assert middleware.detector_status["redact_active"] == 0


def test_rehydrate_concurrency_saturation_fails_fast_and_releases_active_counter() -> None:
    engine = _BlockingRehydrateEngine()
    middleware = PIIMiddleware(
        engine=engine,
        rehydrate_max_concurrency=1,
        concurrency_acquire_timeout_seconds=0.01,
    )
    scope = _scope("rehydrate_limit")
    middleware.process_inbound(scope, "My name is Alice Jones")
    errors: list[BaseException] = []

    def _run() -> None:
        try:
            middleware.process_outbound(scope, "Hello <fn_1>")
        except BaseException as exc:  # pragma: no cover - failure surfaced after join
            errors.append(exc)

    thread = Thread(target=_run)
    thread.start()
    assert engine.started.wait(timeout=1.0)
    assert middleware.detector_status["rehydrate_active"] == 1

    try:
        middleware.process_outbound(scope, "Hello <fn_1>")
    except RedactorSaturatedError:
        pass
    else:
        raise AssertionError("Expected second rehydrate request to saturate")

    saturated_status = middleware.detector_status
    assert saturated_status["rehydrate_active"] == 1
    assert saturated_status["rehydrate_saturated_count"] == 1
    assert saturated_status["rehydrate_max_concurrency"] == 1

    engine.release.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert errors == []
    assert middleware.detector_status["rehydrate_active"] == 0


def test_rehydrate_missing_scope_fail_open_returns_passthrough() -> None:
    middleware = PIIMiddleware()
    result = middleware.process_outbound(_scope("missing"), "Hello <fn_1>", fail_closed=False)
    assert result.clean_text == "Hello <fn_1>"
    assert result.repaired_text == "Hello <fn_1>"
    assert result.repaired_placeholders is False


def test_rehydrate_missing_scope_fail_closed_raises() -> None:
    middleware = PIIMiddleware()
    try:
        middleware.process_outbound(_scope("missing"), "Hello <fn_1>", fail_closed=True)
    except PersistenceUnavailableError:
        return
    raise AssertionError("Expected PersistenceUnavailableError when fail_closed=True")


def test_max_active_scope_limit_evicts_oldest_entry() -> None:
    middleware = PIIMiddleware(max_active_scopes=2, vault_ttl_seconds=3600)
    scope_a = _scope("a")
    scope_b = _scope("b")
    scope_c = _scope("c")

    middleware.process_inbound(scope_a, "My name is Alice Jones")
    middleware.process_inbound(scope_b, "My name is Bob Stone")
    middleware.process_inbound(scope_c, "My name is Carol North")

    assert middleware.active_sessions == 2
    a_result = middleware.process_outbound(scope_a, "Hello <fn_1>", fail_closed=False)
    assert a_result.clean_text == "Hello <fn_1>"
    b_result = middleware.process_outbound(scope_b, "Hello <fn_1>", fail_closed=False)
    c_result = middleware.process_outbound(scope_c, "Hello <fn_1>", fail_closed=False)
    assert b_result.clean_text == "Hello Bob"
    assert c_result.clean_text == "Hello Carol"


def test_ttl_prunes_in_memory_scope() -> None:
    middleware = PIIMiddleware(max_active_scopes=15, vault_ttl_seconds=3600)
    scope = _scope("ttl")
    middleware.process_inbound(scope, "My name is Alice Jones")
    key = scope.key()
    with middleware._lock:  # type: ignore[attr-defined]
        middleware._vaults[key].last_access_epoch = time.time() - 7200  # type: ignore[attr-defined]
    assert middleware.active_sessions == 0


def test_persistence_writer_recovers_without_restart() -> None:
    scope = _scope("recover")
    store = _FlakySaveStore()
    middleware = PIIMiddleware(
        engine=_FakeEngine(),
        vault_store=store,
        persistence_mode="internal:supabase",
        persistence_block_on_error=True,
        persistence_recovery_cooldown_seconds=1,
    )

    first = middleware.process_inbound(scope, "My name is Alice Jones", fail_closed=True)
    assert first.redacted_text != "My name is Alice Jones"
    assert _wait_for(lambda: middleware.detector_status["persistence_healthy"] is False)

    time.sleep(1.05)
    second = middleware.process_inbound(scope, "My email is alice@example.com", fail_closed=True)

    assert second.redacted_text != "My email is alice@example.com"
    assert middleware.detector_status["persistence_healthy"] is True
    assert middleware.detector_status["persistence_recovery_attempts"] == 1
    assert middleware.detector_status["persistence_last_error_type"] == "TimeoutError"
    assert middleware.detector_status["persistence_last_error_category"] == "transient"
    assert middleware.detector_status["persistence_last_error_operation"] == "save"
    metrics = middleware.detector_status["performance_metrics"]
    assert metrics["redact_total_count"] >= 2
    assert metrics["redact_detector_count"] >= 2
    assert metrics["persistence_load_count"] >= 1
    assert metrics["persistence_save_count"] >= 1
    assert metrics["persistence_queue_lag_count"] >= 1
    assert store.save_calls >= 2


def test_transient_save_degraded_allows_existing_in_memory_redact_scope() -> None:
    scope = _scope("save_degraded_existing_redact")
    store = _AlwaysFailingSaveStore()
    middleware = PIIMiddleware(
        engine=_FakeEngine(),
        vault_store=store,
        persistence_mode="internal:supabase",
        persistence_block_on_error=True,
    )

    first = middleware.process_inbound(scope, "My name is Alice Jones", fail_closed=True)
    assert first.redacted_text == "redacted:My name is Alice Jones"
    assert _wait_for(lambda: middleware.detector_status["persistence_healthy"] is False)

    degraded = middleware.detector_status
    assert degraded["persistence_status"] == "degraded_nonblocking"
    assert degraded["persistence_state"] == "degraded_nonblocking"
    assert degraded["persistence_last_error_category"] == "transient"
    assert degraded["persistence_last_error_operation"] == "save"

    second = middleware.process_inbound(scope, "My email is alice@example.com", fail_closed=True)
    assert second.redacted_text == "redacted:My email is alice@example.com"
    assert middleware.detector_status["persistence_blocking_requests"] == 0
    assert _wait_for(lambda: store.save_calls >= 2)


def test_transient_save_degraded_blocks_new_scope_that_would_need_load() -> None:
    store = _AlwaysFailingSaveStore()
    middleware = PIIMiddleware(
        engine=_FakeEngine(),
        vault_store=store,
        persistence_mode="internal:supabase",
        persistence_block_on_error=True,
    )

    middleware.process_inbound(_scope("save_degraded_existing"), "My name is Alice Jones", fail_closed=True)
    assert _wait_for(lambda: middleware.detector_status["persistence_healthy"] is False)

    try:
        middleware.process_inbound(_scope("save_degraded_new"), "My name is Bob Stone", fail_closed=True)
    except PersistenceUnavailableError:
        pass
    else:
        raise AssertionError("Expected degraded persistence to block a new fail-closed scope")

    assert middleware.detector_status["persistence_blocking_requests"] == 1


def test_transient_save_degraded_allows_existing_in_memory_rehydrate_scope() -> None:
    scope = _scope("save_degraded_rehydrate")
    store = _AlwaysFailingSaveStore()
    middleware = PIIMiddleware(
        engine=_FakeEngine(),
        vault_store=store,
        persistence_mode="internal:supabase",
        persistence_block_on_error=True,
    )

    middleware.process_inbound(scope, "My name is Alice Jones", fail_closed=True)
    assert _wait_for(lambda: middleware.detector_status["persistence_healthy"] is False)

    result = middleware.process_outbound(scope, "Hello <fn_1>", fail_closed=True)

    assert result.clean_text == "clean:Hello <fn_1>"
    assert middleware.detector_status["persistence_blocking_requests"] == 0


def test_persistence_save_queue_full_fails_closed() -> None:
    middleware = PIIMiddleware(
        engine=_FakeEngine(),
        vault_store=_AlwaysFailingSaveStore(),
        persistence_mode="internal:supabase",
        persistence_block_on_error=True,
    )
    assert middleware._writer is not None

    def _reject_save(*_args, **_kwargs) -> bool:
        return False

    middleware._writer.enqueue_save = _reject_save

    try:
        middleware.process_inbound(_scope("queue_full_redact"), "My name is Alice Jones", fail_closed=True)
    except PersistenceUnavailableError:
        pass
    else:
        raise AssertionError("Expected queue-full save to fail closed")

    metrics = middleware.detector_status["performance_metrics"]
    assert metrics["persistence_enqueue_full_count"] == 1


def test_load_failure_missing_vault_still_fails_closed() -> None:
    middleware = PIIMiddleware(
        engine=_FakeEngine(),
        vault_store=_AlwaysFailingLoadStore(),
        persistence_mode="internal:supabase",
        persistence_block_on_error=True,
    )

    try:
        middleware.process_outbound(_scope("missing_load_failure"), "Hello <fn_1>", fail_closed=True)
    except TimeoutError:
        pass
    else:
        raise AssertionError("Expected missing-vault load failure to fail closed")


def test_persistence_auth_failure_reports_unavailable_state() -> None:
    scope = _scope("auth_failure")
    middleware = PIIMiddleware(
        engine=_FakeEngine(),
        vault_store=_AuthFailureStore(),
        persistence_mode="internal:supabase",
        persistence_block_on_error=True,
        persistence_recovery_cooldown_seconds=1,
    )

    middleware.process_inbound(scope, "My name is Alice Jones", fail_closed=True)
    assert _wait_for(lambda: middleware.detector_status["persistence_healthy"] is False)

    status = middleware.detector_status
    assert status["persistence_status"] == "blocking"
    assert status["persistence_state"] == "unavailable"
    assert status["persistence_last_error_category"] == "auth"
    assert status["persistence_last_error_status_code"] == 403
    assert status["persistence_unhealthy_since"] is not None
    assert status["persistence_next_recovery_at"] is None

    try:
        middleware.process_inbound(_scope("auth_failure_blocked"), "My phone is 555-010-1212", fail_closed=True)
    except PersistenceUnavailableError:
        pass
    else:
        raise AssertionError("Expected unhealthy persistence to block the next fail-closed request")

    blocked_status = middleware.detector_status
    assert blocked_status["persistence_blocking_requests"] == 1
    assert blocked_status["persistence_queue_max"] >= 1


def test_persistence_worker_restarts_when_thread_is_dead() -> None:
    store = _FlakySaveStore()
    middleware = PIIMiddleware(
        engine=_FakeEngine(),
        vault_store=store,
        persistence_mode="internal:supabase",
        persistence_block_on_error=True,
    )

    writer = middleware._writer  # type: ignore[attr-defined]
    assert writer is not None
    original_thread = writer._thread  # type: ignore[attr-defined]
    writer._thread = type(  # type: ignore[attr-defined]
        "DeadThread",
        (),
        {"is_alive": lambda self: False},
    )()

    status = middleware.detector_status

    assert status["persistence_worker_alive"] is True
    assert status["persistence_worker_restart_count"] == 1
    assert status["persistence_last_worker_restart_at"] is not None
    assert writer._thread is not original_thread  # type: ignore[attr-defined]
    assert writer._thread.is_alive() is True  # type: ignore[attr-defined]
