from __future__ import annotations

import importlib
import sys
import types


_BASE_ENV_KEYS = (
    "PII_REDACTOR_LOAD_DOTENV",
    "PII_REDACTOR_REQUIRE_API_KEY",
    "PII_REDACTOR_PERSISTENCE_MODE",
    "PII_REDACTOR_REQUIRE_PERSISTENCE",
    "PII_REDACTOR_USE_PRESIDIO",
    "PII_REDACTOR_USE_GLINER",
    "PII_REDACTOR_GLINER_ALLOW_REMOTE_DOWNLOAD",
    "PII_REDACTOR_REQUIRE_GLINER",
    "PII_REDACTOR_REQUIRE_PRESIDIO",
)


def _import_server(monkeypatch, **env: str):
    for key in _BASE_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    defaults = {
        "PII_REDACTOR_LOAD_DOTENV": "false",
        "PII_REDACTOR_REQUIRE_API_KEY": "false",
        "PII_REDACTOR_PERSISTENCE_MODE": "none",
        "PII_REDACTOR_REQUIRE_PERSISTENCE": "false",
    }
    defaults.update(env)
    for key, value in defaults.items():
        monkeypatch.setenv(key, value)

    for module_name in ("src.server", "src.middleware", "src.config", "src.pii_engine"):
        sys.modules.pop(module_name, None)

    return importlib.import_module("src.server")


def test_server_startup_fails_when_gliner_required_but_unavailable(monkeypatch) -> None:
    fake_module = types.ModuleType("gliner")

    class _FakeGLiNER:
        @staticmethod
        def from_pretrained(*_args, **_kwargs):
            raise RuntimeError("local model not found")

    fake_module.GLiNER = _FakeGLiNER  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gliner", fake_module)

    try:
        try:
            _import_server(
                monkeypatch,
                PII_REDACTOR_USE_PRESIDIO="false",
                PII_REDACTOR_USE_GLINER="true",
                PII_REDACTOR_REQUIRE_GLINER="true",
                PII_REDACTOR_GLINER_ALLOW_REMOTE_DOWNLOAD="false",
            )
        except RuntimeError as exc:
            assert "GLiNER required but unavailable at startup" in str(exc)
            return
        raise AssertionError("Expected RuntimeError when GLiNER is required but missing")
    finally:
        sys.modules.pop("gliner", None)


def test_server_startup_fails_on_invalid_required_gliner_config(monkeypatch) -> None:
    try:
        _import_server(
            monkeypatch,
            PII_REDACTOR_USE_PRESIDIO="false",
            PII_REDACTOR_USE_GLINER="false",
            PII_REDACTOR_REQUIRE_GLINER="true",
        )
    except RuntimeError as exc:
        assert "PII_REDACTOR_REQUIRE_GLINER=true requires PII_REDACTOR_USE_GLINER=true" in str(exc)
        return
    raise AssertionError("Expected RuntimeError for invalid require_gliner/use_gliner configuration")
