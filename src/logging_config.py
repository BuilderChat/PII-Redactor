from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
import time


_MISSING_CONTEXT_VALUE = "-"


def _resolve_environment_name() -> str:
    for env_name in ("ENV", "ENVIRONMENT", "APP_ENV"):
        value = str(os.getenv(env_name) or "").strip().lower()
        if value:
            return value
    return "local"


def _resolve_log_level(raw_level: str) -> int:
    return getattr(logging, str(raw_level or "INFO").strip().upper(), logging.INFO)


def _resolve_log_format(raw_format: str) -> str:
    normalized = str(raw_format or "").strip().lower()
    if normalized in {"json", "text"}:
        return normalized
    if str(os.getenv("PII_REDACTOR_LOG_JSON") or os.getenv("LOG_JSON") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return "json"
    return "text"


class _ContextFilter(logging.Filter):
    def __init__(self, *, environment: str, app_role: str = "redactor"):
        super().__init__()
        self.environment = environment
        self.app_role = app_role

    def filter(self, record: logging.LogRecord) -> bool:
        request_id = getattr(record, "request_id", _MISSING_CONTEXT_VALUE) or _MISSING_CONTEXT_VALUE
        client_id = getattr(record, "client_id", _MISSING_CONTEXT_VALUE) or _MISSING_CONTEXT_VALUE
        assistant_id = getattr(record, "assistant_id", _MISSING_CONTEXT_VALUE) or _MISSING_CONTEXT_VALUE
        record.environment = self.environment
        record.app_role = self.app_role
        record.request_id = request_id
        record.client_id = client_id
        record.assistant_id = assistant_id
        return True


class _UtcTextFormatter(logging.Formatter):
    converter = time.gmtime


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "environment": getattr(record, "environment", _MISSING_CONTEXT_VALUE),
            "app_role": getattr(record, "app_role", "redactor"),
            "request_id": _json_context_value(getattr(record, "request_id", _MISSING_CONTEXT_VALUE)),
            "client_id": _json_context_value(getattr(record, "client_id", _MISSING_CONTEXT_VALUE)),
            "assistant_id": _json_context_value(getattr(record, "assistant_id", _MISSING_CONTEXT_VALUE)),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _json_context_value(value):
    return None if value in {None, "", _MISSING_CONTEXT_VALUE} else value


def _build_formatter(log_format: str) -> logging.Formatter:
    if log_format == "json":
        return _JsonFormatter()
    return _UtcTextFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s | env=%(environment)s app_role=%(app_role)s"
    )


def configure_logging(*, log_level: str, log_format: str, access_logs: bool) -> int:
    resolved_level = _resolve_log_level(log_level)
    handler = logging.StreamHandler()
    handler.addFilter(_ContextFilter(environment=_resolve_environment_name()))
    handler.setFormatter(_build_formatter(_resolve_log_format(log_format)))
    logging.basicConfig(level=resolved_level, handlers=[handler], force=True)

    for logger_name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    logging.getLogger("uvicorn.access").disabled = not bool(access_logs)
    if not access_logs:
        logging.getLogger("uvicorn.access").handlers.clear()
        logging.getLogger("uvicorn.access").propagate = False

    return resolved_level
