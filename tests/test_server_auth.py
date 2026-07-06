from __future__ import annotations

import importlib
import sys

from fastapi import HTTPException


_AUTH_ENV_KEYS = (
    "PII_REDACTOR_LOAD_DOTENV",
    "PII_REDACTOR_API_KEY",
    "PII_REDACTOR_API_KEY_SHA256",
    "PII_REDACTOR_REQUIRE_API_KEY",
    "PII_REDACTOR_USE_PRESIDIO",
    "PII_REDACTOR_USE_GLINER",
    "PII_REDACTOR_PERSISTENCE_MODE",
    "PII_REDACTOR_REQUIRE_PERSISTENCE",
    "PII_REDACTOR_PERSISTENCE_RECOVERY_COOLDOWN_SECONDS",
)


def _load_server(monkeypatch, **env: str):
    for key in _AUTH_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)

    defaults = {
        "PII_REDACTOR_LOAD_DOTENV": "false",
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


def test_api_key_guard_returns_503_when_required_but_unconfigured(monkeypatch) -> None:
    server = _load_server(
        monkeypatch,
        PII_REDACTOR_REQUIRE_API_KEY="true",
    )
    try:
        server._validate_api_key("any-key")
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail == "Server is missing API key configuration"
        return
    raise AssertionError("Expected HTTPException for missing API key configuration")


def test_api_key_guard_rejects_invalid_and_accepts_valid_key(monkeypatch) -> None:
    server = _load_server(
        monkeypatch,
        PII_REDACTOR_REQUIRE_API_KEY="true",
        PII_REDACTOR_API_KEY="unit-test-key",
    )

    try:
        server._validate_api_key("wrong")
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("Expected invalid key to be rejected")

    server._validate_api_key("unit-test-key")


def test_health_reports_degraded_persistence_details(monkeypatch) -> None:
    server = _load_server(monkeypatch, PII_REDACTOR_REQUIRE_API_KEY="false")

    class _FakeMiddleware:
        active_sessions = 2
        detector_status = {
            "presidio_enabled": True,
            "gliner_enabled": True,
            "name_detection_mode": "gliner",
            "gliner_model": "urchade/gliner_multi_pii-v1",
            "persistence_enabled": True,
            "persistence_mode": "internal:supabase",
            "persistence_status": "blocking",
            "persistence_state": "degraded",
            "persistence_block_on_error": True,
            "persistence_healthy": False,
            "persistence_worker_alive": True,
            "persistence_worker_restart_count": 2,
            "persistence_last_worker_restart_at": "2026-06-02T12:00:15Z",
            "persistence_last_error_type": "TimeoutError",
            "persistence_last_error_category": "transient",
            "persistence_last_error_status_code": None,
            "persistence_last_error_operation": "save",
            "persistence_last_error_at": "2026-06-02T12:00:00Z",
            "persistence_last_success_at": "2026-06-02T11:59:00Z",
            "persistence_unhealthy_since": "2026-06-02T12:00:00Z",
            "persistence_consecutive_failures": 3,
            "persistence_recovery_attempts": 1,
            "persistence_last_recovery_attempt_at": "2026-06-02T12:00:30Z",
            "persistence_next_recovery_at": "2026-06-02T12:01:00Z",
            "persistence_recovery_cooldown_seconds": 30,
            "redact_active": 1,
            "rehydrate_active": 2,
            "redact_max_concurrency": 0,
            "rehydrate_max_concurrency": 0,
            "redact_saturated_count": 3,
            "rehydrate_saturated_count": 4,
            "persistence_queue_depth": 4,
            "persistence_queue_max": 12,
            "persistence_blocking_requests": 5,
            "performance_metrics": {
                "redact_total_count": 2,
                "redact_total_avg_ms": 12.5,
                "redact_total_max_ms": 20.0,
                "redact_total_last_ms": 5.0,
            },
            "scope_ttl_seconds": 3600,
            "max_active_scopes": 15,
            "allowlist_cache_enabled": True,
        }

    server.middleware = _FakeMiddleware()

    response = server.health()

    assert response.status == "degraded"
    assert response.persistence_status == "blocking"
    assert response.persistence_state == "degraded"
    assert response.persistence_block_on_error is True
    assert response.persistence_worker_alive is True
    assert response.persistence_worker_restart_count == 2
    assert response.persistence_last_error_type == "TimeoutError"
    assert response.persistence_last_error_category == "transient"
    assert response.persistence_last_error_operation == "save"
    assert response.persistence_recovery_attempts == 1
    assert response.redact_active == 1
    assert response.rehydrate_active == 2
    assert response.redact_saturated_count == 3
    assert response.rehydrate_saturated_count == 4
    assert response.persistence_queue_depth == 4
    assert response.persistence_queue_max == 12
    assert response.persistence_blocking_requests == 5
    assert response.performance_metrics["redact_total_count"] == 2
