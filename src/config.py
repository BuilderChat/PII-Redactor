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



def get_settings() -> Settings:
    return Settings(
        api_key=os.getenv("PII_REDACTOR_API_KEY", ""),
        api_key_sha256=os.getenv("PII_REDACTOR_API_KEY_SHA256", "").lower(),
        require_api_key=os.getenv("PII_REDACTOR_REQUIRE_API_KEY", "true").lower() == "true",
        log_level=os.getenv("PII_REDACTOR_LOG_LEVEL", "INFO"),
    )
