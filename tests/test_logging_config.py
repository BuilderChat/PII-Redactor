from __future__ import annotations

import json
import logging

from src import logging_config


def _build_record(message: str, *, level: int = logging.INFO) -> logging.LogRecord:
    return logging.LogRecord(
        name="redactor.logging.test",
        level=level,
        pathname=__file__,
        lineno=12,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_json_formatter_emits_grafana_fields_with_missing_context_as_null(monkeypatch) -> None:
    monkeypatch.setenv("ENV", "dev")
    record = _build_record("redactor_startup_success persistence_mode=none")
    context_filter = logging_config._ContextFilter(environment=logging_config._resolve_environment_name())
    assert context_filter.filter(record) is True

    payload = json.loads(logging_config._JsonFormatter().format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "redactor.logging.test"
    assert payload["message"] == "redactor_startup_success persistence_mode=none"
    assert payload["environment"] == "dev"
    assert payload["app_role"] == "redactor"
    assert payload["request_id"] is None
    assert payload["client_id"] is None
    assert payload["assistant_id"] is None


def test_context_filter_preserves_explicit_redactor_context() -> None:
    record = _build_record("allowlist_refresh_success")
    record.request_id = "req-123"
    record.client_id = "1008"
    record.assistant_id = "1008-chat-001"

    assert logging_config._ContextFilter(environment="dev").filter(record) is True
    payload = json.loads(logging_config._JsonFormatter().format(record))

    assert payload["request_id"] == "req-123"
    assert payload["client_id"] == "1008"
    assert payload["assistant_id"] == "1008-chat-001"


def test_configure_logging_disables_uvicorn_access_logs_by_default(monkeypatch) -> None:
    monkeypatch.setenv("LOG_JSON", "true")
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.disabled = False
    access_logger.propagate = True
    access_logger.addHandler(logging.NullHandler())

    level = logging_config.configure_logging(log_level="DEBUG", log_format="text", access_logs=False)

    assert level == logging.DEBUG
    assert access_logger.disabled is True
    assert access_logger.propagate is False
    assert access_logger.handlers == []


def test_configure_logging_can_keep_uvicorn_access_logs(monkeypatch) -> None:
    monkeypatch.delenv("LOG_JSON", raising=False)
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.disabled = True
    access_logger.propagate = False

    level = logging_config.configure_logging(log_level="WARNING", log_format="json", access_logs=True)

    assert level == logging.WARNING
    assert access_logger.disabled is False


def test_configure_logging_routes_uvicorn_lifecycle_logs_through_root_formatter() -> None:
    uvicorn_error_logger = logging.getLogger("uvicorn.error")
    uvicorn_error_logger.addHandler(logging.NullHandler())
    uvicorn_error_logger.propagate = False

    logging_config.configure_logging(log_level="INFO", log_format="json", access_logs=False)

    assert uvicorn_error_logger.handlers == []
    assert uvicorn_error_logger.propagate is True
