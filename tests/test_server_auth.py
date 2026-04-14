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
