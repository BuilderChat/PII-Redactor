from __future__ import annotations

import time

from src.middleware import PIIMiddleware, PersistenceUnavailableError
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


def _wait_for(predicate, *, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


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
