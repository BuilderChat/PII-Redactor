from __future__ import annotations

import os
from dataclasses import dataclass


ENTITY_KEYS = ("fn", "mn1", "mn2", "ln", "em", "ph")
NAME_ENTITY_KEYS = ("fn", "mn1", "mn2", "ln")


@dataclass(frozen=True)
class Settings:
    api_key: str
    api_key_sha256: str
    require_api_key: bool
    log_level: str
    use_presidio: bool
    use_gliner: bool
    gliner_model: str
    gliner_threshold: float
    gliner_labels: tuple[str, ...]


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.getenv(name)
    if not value:
        return default
    parsed = tuple(part.strip() for part in value.split(",") if part.strip())
    return parsed or default


def get_settings() -> Settings:
    return Settings(
        api_key=os.getenv("PII_REDACTOR_API_KEY", ""),
        api_key_sha256=os.getenv("PII_REDACTOR_API_KEY_SHA256", "").lower(),
        require_api_key=_env_bool("PII_REDACTOR_REQUIRE_API_KEY", True),
        log_level=os.getenv("PII_REDACTOR_LOG_LEVEL", "INFO"),
        use_presidio=_env_bool("PII_REDACTOR_USE_PRESIDIO", True),
        use_gliner=_env_bool("PII_REDACTOR_USE_GLINER", True),
        gliner_model=os.getenv("PII_REDACTOR_GLINER_MODEL", "urchade/gliner_multi_pii-v1"),
        gliner_threshold=_env_float("PII_REDACTOR_GLINER_THRESHOLD", 0.6),
        gliner_labels=_env_csv(
            "PII_REDACTOR_GLINER_LABELS",
            ("person", "name", "first name", "last name", "full name"),
        ),
    )
