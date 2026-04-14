from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ENTITY_KEYS = ("fn", "mn1", "mn2", "ln", "em", "ph")
NAME_ENTITY_KEYS = ("fn", "mn1", "mn2", "ln")
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DOTENV_LOADED = False


@dataclass(frozen=True)
class Settings:
    api_key: str
    api_key_sha256: str
    require_api_key: bool
    fail_closed_default: bool
    allow_raw_replacements: bool
    log_level: str
    use_presidio: bool
    presidio_minimal_recognizers: bool
    require_presidio: bool
    use_gliner: bool
    gliner_allow_remote_download: bool
    require_gliner: bool
    gliner_model: str
    gliner_threshold: float
    gliner_labels: tuple[str, ...]
    non_name_terms: tuple[str, ...]
    non_name_terms_json_path: str
    vault_ttl_seconds: int
    max_active_scopes: int
    persistence_queue_max: int
    persistence_block_on_error: bool
    persistence_key_version: str
    require_persistence: bool
    persistence_mode: str
    internal_store_impl: str
    external_store_factory: str
    supabase_url: str
    supabase_service_role_key: str
    supabase_table: str
    persistence_master_key: str
    allowlist_cache_enabled: bool
    allowlist_cache_dir: str
    allowlist_cache_max_terms: int


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


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


def _load_dotenv_once() -> None:
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    running_under_pytest = "pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST") is not None
    default_load_dotenv = not running_under_pytest
    if _env_bool("PII_REDACTOR_LOAD_DOTENV", default_load_dotenv):
        load_dotenv(_REPO_ROOT / ".env", override=False)
    _DOTENV_LOADED = True


def get_settings() -> Settings:
    _load_dotenv_once()
    return Settings(
        api_key=os.getenv("PII_REDACTOR_API_KEY", ""),
        api_key_sha256=os.getenv("PII_REDACTOR_API_KEY_SHA256", "").lower(),
        require_api_key=_env_bool("PII_REDACTOR_REQUIRE_API_KEY", True),
        fail_closed_default=_env_bool("PII_REDACTOR_FAIL_CLOSED_DEFAULT", True),
        allow_raw_replacements=_env_bool("PII_REDACTOR_ALLOW_RAW_REPLACEMENTS", False),
        log_level=os.getenv("PII_REDACTOR_LOG_LEVEL", "INFO"),
        use_presidio=_env_bool("PII_REDACTOR_USE_PRESIDIO", True),
        presidio_minimal_recognizers=_env_bool("PII_REDACTOR_PRESIDIO_MINIMAL_RECOGNIZERS", True),
        require_presidio=_env_bool("PII_REDACTOR_REQUIRE_PRESIDIO", False),
        use_gliner=_env_bool("PII_REDACTOR_USE_GLINER", True),
        gliner_allow_remote_download=_env_bool("PII_REDACTOR_GLINER_ALLOW_REMOTE_DOWNLOAD", False),
        require_gliner=_env_bool("PII_REDACTOR_REQUIRE_GLINER", False),
        gliner_model=os.getenv("PII_REDACTOR_GLINER_MODEL", "urchade/gliner_multi_pii-v1"),
        gliner_threshold=_env_float("PII_REDACTOR_GLINER_THRESHOLD", 0.75),
        gliner_labels=_env_csv(
            "PII_REDACTOR_GLINER_LABELS",
            ("name", "first name", "last name", "full name"),
        ),
        non_name_terms=_env_csv("PII_REDACTOR_NON_NAME_TERMS", ()),
        non_name_terms_json_path=os.getenv("PII_REDACTOR_NON_NAME_TERMS_JSON_PATH", "").strip(),
        vault_ttl_seconds=max(60, _env_int("PII_REDACTOR_VAULT_TTL_SECONDS", 3600)),
        max_active_scopes=max(1, _env_int("PII_REDACTOR_MAX_ACTIVE_SCOPES", 15)),
        persistence_queue_max=max(1, _env_int("PII_REDACTOR_PERSISTENCE_QUEUE_MAX", 1024)),
        persistence_block_on_error=_env_bool("PII_REDACTOR_PERSISTENCE_BLOCK_ON_ERROR", True),
        persistence_key_version=os.getenv("PII_REDACTOR_PERSISTENCE_KEY_VERSION", "v1").strip() or "v1",
        require_persistence=_env_bool("PII_REDACTOR_REQUIRE_PERSISTENCE", False),
        persistence_mode=(os.getenv("PII_REDACTOR_PERSISTENCE_MODE", "none").strip().lower() or "none"),
        internal_store_impl=(os.getenv("PII_REDACTOR_INTERNAL_STORE_IMPL", "supabase").strip().lower() or "supabase"),
        external_store_factory=os.getenv("PII_REDACTOR_EXTERNAL_STORE_FACTORY", "").strip(),
        supabase_url=os.getenv("PII_REDACTOR_SUPABASE_URL", "").strip(),
        supabase_service_role_key=os.getenv("PII_REDACTOR_SUPABASE_SERVICE_ROLE_KEY", "").strip(),
        supabase_table=os.getenv("PII_REDACTOR_SUPABASE_TABLE", "pii_vault_snapshots").strip() or "pii_vault_snapshots",
        persistence_master_key=os.getenv("PII_REDACTOR_PERSISTENCE_MASTER_KEY", "").strip(),
        allowlist_cache_enabled=_env_bool("PII_REDACTOR_ALLOWLIST_CACHE_ENABLED", True),
        allowlist_cache_dir=os.getenv("PII_REDACTOR_ALLOWLIST_CACHE_DIR", ".cache/non_name_allowlists").strip()
        or ".cache/non_name_allowlists",
        allowlist_cache_max_terms=max(100, _env_int("PII_REDACTOR_ALLOWLIST_CACHE_MAX_TERMS", 50000)),
    )
