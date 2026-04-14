from __future__ import annotations

from dataclasses import replace
import sys
import types

from src.config import Settings
from src.persistence import MemoryVaultStore, PersistenceConfigError, build_vault_store


def _settings(**overrides: object) -> Settings:
    base = Settings(
        api_key="",
        api_key_sha256="",
        require_api_key=False,
        fail_closed_default=True,
        allow_raw_replacements=False,
        log_level="INFO",
        use_presidio=False,
        use_gliner=False,
        gliner_model="urchade/gliner_multi_pii-v1",
        gliner_threshold=0.75,
        gliner_labels=("name", "first name", "last name", "full name"),
        non_name_terms=(),
        non_name_terms_json_path="",
        vault_ttl_seconds=3600,
        max_active_scopes=15,
        persistence_queue_max=1024,
        persistence_block_on_error=True,
        persistence_key_version="v1",
        require_persistence=False,
        persistence_mode="none",
        internal_store_impl="supabase",
        external_store_factory="",
        supabase_url="",
        supabase_service_role_key="",
        supabase_table="pii_vault_snapshots",
        persistence_master_key="",
    )
    return replace(base, **overrides)


def test_none_mode_returns_no_store() -> None:
    store, mode = build_vault_store(_settings(persistence_mode="none"))
    assert store is None
    assert mode == "none"


def test_none_mode_with_required_persistence_raises() -> None:
    try:
        build_vault_store(_settings(persistence_mode="none", require_persistence=True))
    except PersistenceConfigError:
        return
    raise AssertionError("Expected PersistenceConfigError")


def test_internal_memory_mode_builds_memory_store() -> None:
    store, mode = build_vault_store(
        _settings(persistence_mode="internal", internal_store_impl="memory", require_persistence=True)
    )
    assert isinstance(store, MemoryVaultStore)
    assert mode == "internal:memory"


def test_internal_supabase_mode_requires_credentials() -> None:
    try:
        build_vault_store(
            _settings(
                persistence_mode="internal",
                internal_store_impl="supabase",
                require_persistence=True,
                supabase_url="",
                supabase_service_role_key="",
                persistence_master_key="",
            )
        )
    except PersistenceConfigError:
        return
    raise AssertionError("Expected PersistenceConfigError")


def test_external_mode_uses_injected_store() -> None:
    injected = MemoryVaultStore()
    store, mode = build_vault_store(_settings(persistence_mode="external"), external_store=injected)
    assert store is injected
    assert mode == "external:injected"


def test_external_mode_factory_path_is_resolved() -> None:
    module_name = "tests._tmp_external_store_factory"
    temp_module = types.ModuleType(module_name)

    def make_store(_: Settings | None = None) -> MemoryVaultStore:
        return MemoryVaultStore()

    temp_module.make_store = make_store  # type: ignore[attr-defined]
    sys.modules[module_name] = temp_module
    try:
        store, mode = build_vault_store(
            _settings(
                persistence_mode="external",
                external_store_factory=f"{module_name}:make_store",
            )
        )
    finally:
        sys.modules.pop(module_name, None)

    assert isinstance(store, MemoryVaultStore)
    assert mode == "external:factory"
