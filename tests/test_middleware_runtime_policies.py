from __future__ import annotations

import time

from src.middleware import PIIMiddleware, PersistenceUnavailableError
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
    assert middleware.detector_status["persistence_last_error_operation"] == "save"
    assert store.save_calls >= 2
