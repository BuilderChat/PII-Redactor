from __future__ import annotations

import sys
import types

from src.pii_engine import PIIEngine


def test_gliner_default_is_local_cache_only(monkeypatch) -> None:
    monkeypatch.setenv("PII_REDACTOR_LOAD_DOTENV", "false")
    monkeypatch.setenv("PII_REDACTOR_GLINER_ALLOW_REMOTE_DOWNLOAD", "false")

    calls: list[dict[str, object]] = []

    class _FakeGLiNER:
        @staticmethod
        def from_pretrained(model_name: str, **kwargs):
            calls.append({"model_name": model_name, **kwargs})
            raise RuntimeError("model not available in local cache")

    fake_module = types.ModuleType("gliner")
    fake_module.GLiNER = _FakeGLiNER  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gliner", fake_module)

    engine = PIIEngine(use_presidio=False, use_gliner=True)
    info = engine.runtime_info
    assert info["gliner_enabled"] is False
    assert len(calls) == 1
    assert calls[0].get("local_files_only") is True


def test_gliner_remote_download_can_be_enabled_explicitly(monkeypatch) -> None:
    monkeypatch.setenv("PII_REDACTOR_LOAD_DOTENV", "false")
    monkeypatch.setenv("PII_REDACTOR_GLINER_ALLOW_REMOTE_DOWNLOAD", "true")

    calls: list[dict[str, object]] = []

    class _FakeGLiNER:
        @staticmethod
        def from_pretrained(model_name: str, **kwargs):
            calls.append({"model_name": model_name, **kwargs})
            raise RuntimeError("forced test failure")

    fake_module = types.ModuleType("gliner")
    fake_module.GLiNER = _FakeGLiNER  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gliner", fake_module)

    engine = PIIEngine(use_presidio=False, use_gliner=True)
    info = engine.runtime_info
    assert info["gliner_enabled"] is False
    assert len(calls) == 1
    assert calls[0].get("local_files_only") is False
