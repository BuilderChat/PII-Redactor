from __future__ import annotations

from dataclasses import replace
import sys
import types
from urllib import error as urlerror

from src.config import Settings
from src.persistence import MemoryVaultStore, PersistenceConfigError, PersistenceRuntimeError, build_vault_store
import src.persistence as persistence


def _settings(**overrides: object) -> Settings:
    base = Settings(
        api_key="",
        api_key_sha256="",
        require_api_key=False,
        fail_closed_default=True,
        allow_raw_replacements=False,
        log_level="INFO",
        log_format="text",
        access_logs=False,
        use_presidio=False,
        presidio_minimal_recognizers=True,
        require_presidio=False,
        use_gliner=False,
        gliner_allow_remote_download=False,
        require_gliner=False,
        gliner_model="urchade/gliner_multi_pii-v1",
        gliner_threshold=0.75,
        gliner_labels=("name", "first name", "last name", "full name"),
        non_name_terms=(),
        non_name_terms_json_path="",
        vault_ttl_seconds=3600,
        max_active_scopes=15,
        redact_max_concurrency=24,
        rehydrate_max_concurrency=24,
        concurrency_acquire_timeout_seconds=0.5,
        persistence_queue_max=1024,
        persistence_block_on_error=True,
        persistence_recovery_cooldown_seconds=30,
        persistence_key_version="v1",
        require_persistence=False,
        persistence_mode="none",
        internal_store_impl="supabase",
        external_store_factory="",
        supabase_url="",
        supabase_service_role_key="",
        supabase_table="pii_vault_snapshots",
        persistence_master_key="",
        supabase_request_timeout_seconds=15,
        allowlist_cache_enabled=True,
        allowlist_cache_dir=".cache/non_name_allowlists",
        allowlist_cache_max_terms=50000,
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


def test_internal_supabase_mode_rejects_postgres_connection_url() -> None:
    try:
        build_vault_store(
            _settings(
                persistence_mode="internal",
                internal_store_impl="supabase",
                require_persistence=True,
                supabase_url="postgresql://user:pass@host:5432/db",
                supabase_service_role_key="service_role",
                persistence_master_key="master_key",
            )
        )
    except PersistenceConfigError:
        return
    raise AssertionError("Expected PersistenceConfigError for invalid Supabase URL scheme")


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


def test_supabase_runtime_error_classifies_http_status(monkeypatch) -> None:
    store, _mode = build_vault_store(
        _settings(
            persistence_mode="internal",
            internal_store_impl="supabase",
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service_role",
            persistence_master_key="master_key",
        )
    )

    def fail_urlopen(*_args, **_kwargs):
        raise urlerror.HTTPError(
            url="https://example.supabase.co/rest/v1/pii_vault_snapshots",
            code=503,
            msg="Service Unavailable",
            hdrs=None,
            fp=None,
        )

    monkeypatch.setattr(persistence.urlrequest, "urlopen", fail_urlopen)

    try:
        store.delete(types.SimpleNamespace(key=lambda: "scope_key"))
    except PersistenceRuntimeError as exc:
        assert exc.operation == "delete"
        assert exc.status_code == 503
        assert exc.category == "transient"
        assert "Service Unavailable" not in str(exc)
        return
    raise AssertionError("Expected PersistenceRuntimeError")


def test_supabase_request_uses_configured_timeout(monkeypatch) -> None:
    store, _mode = build_vault_store(
        _settings(
            persistence_mode="internal",
            internal_store_impl="supabase",
            supabase_url="https://example.supabase.co",
            supabase_service_role_key="service_role",
            persistence_master_key="master_key",
            supabase_request_timeout_seconds=17,
        )
    )
    captured: dict[str, object] = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return b"[]"

    def fake_urlopen(_req, *, timeout):
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(persistence.urlrequest, "urlopen", fake_urlopen)

    result = store.load(types.SimpleNamespace(key=lambda: "scope_key"))

    assert result is None
    assert captured["timeout"] == 17
