from __future__ import annotations

import importlib
import logging
import sys
from threading import Event, Thread
import time

from fastapi import HTTPException
import pytest

from src.allowlist_cache import LocalAllowlistCache
from src.pii_engine import PIIEngine, RedactionResult
from src.transcript_audit import (
    TranscriptAuditSaturatedError,
    TranscriptAuditService,
    TranscriptAuditTimeoutError,
)
from src.transcript_replay import TranscriptTurn


_SERVER_ENV_KEYS = (
    "PII_REDACTOR_LOAD_DOTENV",
    "PII_REDACTOR_API_KEY",
    "PII_REDACTOR_API_KEY_SHA256",
    "PII_REDACTOR_REQUIRE_API_KEY",
    "PII_REDACTOR_USE_PRESIDIO",
    "PII_REDACTOR_USE_GLINER",
    "PII_REDACTOR_PERSISTENCE_MODE",
    "PII_REDACTOR_REQUIRE_PERSISTENCE",
    "PII_REDACTOR_AUDIT_MAX_TURNS",
    "PII_REDACTOR_AUDIT_MAX_CHARACTERS",
    "PII_REDACTOR_AUDIT_TIMEOUT_SECONDS",
    "PII_REDACTOR_AUDIT_MAX_CONCURRENCY",
    "PII_REDACTOR_AUDIT_ACQUIRE_TIMEOUT_SECONDS",
)


def _load_server(monkeypatch, **env: str):
    for key in _SERVER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    defaults = {
        "PII_REDACTOR_LOAD_DOTENV": "false",
        "PII_REDACTOR_REQUIRE_API_KEY": "true",
        "PII_REDACTOR_API_KEY": "audit-test-key",
        "PII_REDACTOR_USE_PRESIDIO": "false",
        "PII_REDACTOR_USE_GLINER": "false",
        "PII_REDACTOR_PERSISTENCE_MODE": "none",
        "PII_REDACTOR_REQUIRE_PERSISTENCE": "false",
    }
    defaults.update(env)
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)

    for module_name in ("src.server", "src.middleware", "src.config"):
        sys.modules.pop(module_name, None)
    return importlib.import_module("src.server")


def test_audit_service_reuses_scoped_allowlist_without_live_vault_state(tmp_path) -> None:
    cache = LocalAllowlistCache(str(tmp_path / "allowlists"))
    cache.refresh(client_id="client-a", assistant_id="assistant-a", terms=["Windsor"])
    service = TranscriptAuditService(
        engine=PIIEngine(use_presidio=False, use_gliner=False),
        allowlist_cache=cache,
    )
    try:
        first = service.audit(
            client_id="client-a",
            assistant_id="assistant-a",
            turns=(
                TranscriptTurn(role="assistant", content="What's your first and last name?"),
                TranscriptTurn(role="user", content="Windsor California"),
            ),
        )
        second = service.audit(
            client_id="client-b",
            assistant_id="assistant-b",
            turns=(
                TranscriptTurn(role="assistant", content="What's your first name?"),
                TranscriptTurn(role="user", content="Avery"),
            ),
        )
    finally:
        service.close()

    assert first.user_turns[-1].redacted_text == "Windsor California"
    assert first.token_to_value == {}
    assert second.user_turns[-1].redacted_text == "<fn_1>"
    assert second.token_to_value == {"<fn_1>": "Avery"}


def test_audit_service_enforces_configured_bounds() -> None:
    service = TranscriptAuditService(
        engine=PIIEngine(use_presidio=False, use_gliner=False),
        max_turns=1,
        max_characters=5,
    )
    try:
        with pytest.raises(ValueError, match="turn"):
            service.audit(
                client_id="c1",
                assistant_id="a1",
                turns=(
                    TranscriptTurn(role="assistant", content="Name?"),
                    TranscriptTurn(role="user", content="Avery"),
                ),
            )
        with pytest.raises(ValueError, match="character"):
            service.audit(
                client_id="c1",
                assistant_id="a1",
                turns=(TranscriptTurn(role="user", content="Avery J"),),
            )
    finally:
        service.close()


class _SlowEngine:
    def __init__(self, *, release: Event | None = None, delay: float = 0.0) -> None:
        self.started = Event()
        self.finished = Event()
        self.release = release
        self.delay = delay

    def redact(self, text, vault, previous_assistant_message=None, non_name_allowlist=None):
        del vault, previous_assistant_message, non_name_allowlist
        self.started.set()
        if self.release is not None:
            self.release.wait(timeout=1.0)
        if self.delay:
            time.sleep(self.delay)
        self.finished.set()
        return RedactionResult(redacted_text=text, replacements={}, active_profile=1)


def test_audit_timeout_is_explicit_and_slot_releases_after_worker_finishes() -> None:
    engine = _SlowEngine(delay=0.05)
    service = TranscriptAuditService(
        engine=engine,  # type: ignore[arg-type]
        timeout_seconds=0.01,
        max_concurrency=1,
    )
    try:
        with pytest.raises(TranscriptAuditTimeoutError):
            service.audit(
                client_id="c1",
                assistant_id="a1",
                turns=(TranscriptTurn(role="user", content="hello"),),
            )
        assert service.status["timeout_count"] == 1
        assert engine.finished.wait(timeout=1.0)
        assert service.status["active"] == 0
    finally:
        service.close()


def test_audit_concurrency_fails_fast_when_worker_is_occupied() -> None:
    release = Event()
    engine = _SlowEngine(release=release)
    service = TranscriptAuditService(
        engine=engine,  # type: ignore[arg-type]
        timeout_seconds=1.0,
        max_concurrency=1,
        acquire_timeout_seconds=0.01,
    )
    errors: list[BaseException] = []

    def _run_first() -> None:
        try:
            service.audit(
                client_id="c1",
                assistant_id="a1",
                turns=(TranscriptTurn(role="user", content="first"),),
            )
        except BaseException as exc:  # pragma: no cover - asserted through errors
            errors.append(exc)

    worker = Thread(target=_run_first)
    worker.start()
    assert engine.started.wait(timeout=1.0)
    try:
        with pytest.raises(TranscriptAuditSaturatedError):
            service.audit(
                client_id="c1",
                assistant_id="a1",
                turns=(TranscriptTurn(role="user", content="second"),),
            )
        assert service.status["saturated_count"] == 1
    finally:
        release.set()
        worker.join(timeout=1.0)
        service.close()

    assert not errors


def test_authenticated_audit_endpoint_returns_turn_evidence_without_live_session(
    monkeypatch,
    caplog,
) -> None:
    server = _load_server(monkeypatch)
    request = server.AuditTranscriptRequest(
        client_id="client-a",
        assistant_id="assistant-a",
        turns=[
            {"role": "assistant", "content": "What's your first name?"},
            {"role": "user", "content": "Avery"},
        ],
    )
    server.middleware.process_inbound = lambda **_kwargs: pytest.fail("audit used live inbound middleware")
    server.middleware._persist_snapshot = lambda *_args, **_kwargs: pytest.fail("audit persisted a vault")
    try:
        with pytest.raises(HTTPException) as unauthorized:
            server._validate_api_key(None)
        assert unauthorized.value.status_code == 401
        server._validate_api_key("audit-test-key")
        with caplog.at_level(logging.DEBUG, logger="src.server"):
            response = server.audit_transcript(request)
    finally:
        server.audit_service.close()

    assert response.model_dump() == {
        "user_turns": [
            {
                "turn_index": 1,
                "redacted": "<fn_1>",
                "evidence": [{"token": "<fn_1>", "entity": "fn", "value": "Avery"}],
            }
        ]
    }
    assert server.middleware.active_sessions == 0
    assert "Avery" not in caplog.text


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (
            TranscriptAuditSaturatedError("busy"),
            503,
            "Transcript audit service saturated",
        ),
        (
            TranscriptAuditTimeoutError("slow"),
            504,
            "Transcript audit timed out",
        ),
        (ValueError("bad transcript"), 422, "bad transcript"),
    ],
)
def test_audit_endpoint_maps_expected_failures(
    monkeypatch,
    error: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    server = _load_server(monkeypatch, PII_REDACTOR_REQUIRE_API_KEY="false")

    class _FailingAuditService:
        def audit(self, **_kwargs):
            raise error

    original_service = server.audit_service
    server.audit_service = _FailingAuditService()
    request = server.AuditTranscriptRequest(
        client_id="c1",
        assistant_id="a1",
        turns=[{"role": "user", "content": "hello"}],
    )
    try:
        with pytest.raises(HTTPException) as raised:
            server.audit_transcript(request)
    finally:
        original_service.close()

    assert raised.value.status_code == expected_status
    assert raised.value.detail == expected_detail
