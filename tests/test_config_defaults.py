from __future__ import annotations

import importlib
import sys


def test_slm_defaults_to_heuristic_only(monkeypatch) -> None:
    for key in (
        "PII_REDACTOR_LOAD_DOTENV",
        "PII_REDACTOR_USE_GLINER",
        "PII_REDACTOR_USE_PRESIDIO",
        "PII_REDACTOR_REQUIRE_GLINER",
        "PII_REDACTOR_REQUIRE_PRESIDIO",
        "PII_REDACTOR_REDACT_MAX_CONCURRENCY",
        "PII_REDACTOR_REHYDRATE_MAX_CONCURRENCY",
        "PII_REDACTOR_CONCURRENCY_ACQUIRE_TIMEOUT_SECONDS",
        "PII_REDACTOR_LOG_LEVEL",
        "PII_REDACTOR_LOG_FORMAT",
        "PII_REDACTOR_ACCESS_LOGS",
        "LOG_LEVEL",
        "LOG_FORMAT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PII_REDACTOR_LOAD_DOTENV", "false")

    sys.modules.pop("src.config", None)
    config = importlib.import_module("src.config")
    settings = config.get_settings()

    assert settings.use_gliner is False
    assert settings.use_presidio is False
    assert settings.require_gliner is False
    assert settings.require_presidio is False
    assert settings.redact_max_concurrency == 24
    assert settings.rehydrate_max_concurrency == 24
    assert settings.concurrency_acquire_timeout_seconds == 0.5
    assert settings.log_level == "INFO"
    assert settings.log_format == "text"
    assert settings.access_logs is False


def test_logging_settings_accept_shared_and_redactor_specific_env(monkeypatch) -> None:
    for key in (
        "PII_REDACTOR_LOAD_DOTENV",
        "PII_REDACTOR_LOG_LEVEL",
        "PII_REDACTOR_LOG_FORMAT",
        "PII_REDACTOR_ACCESS_LOGS",
        "LOG_LEVEL",
        "LOG_FORMAT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PII_REDACTOR_LOAD_DOTENV", "false")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setenv("PII_REDACTOR_ACCESS_LOGS", "true")

    sys.modules.pop("src.config", None)
    config = importlib.import_module("src.config")
    settings = config.get_settings()

    assert settings.log_level == "DEBUG"
    assert settings.log_format == "json"
    assert settings.access_logs is True

    monkeypatch.setenv("PII_REDACTOR_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("PII_REDACTOR_LOG_FORMAT", "text")
    sys.modules.pop("src.config", None)
    config = importlib.import_module("src.config")
    settings = config.get_settings()

    assert settings.log_level == "WARNING"
    assert settings.log_format == "text"
