# CODEBASE_MAP

## Runtime Modules

- `src/server.py`: FastAPI app, auth guard, REST endpoints (`/redact`, `/rehydrate`, `/session/end`, `/allowlist/refresh`, `/health`).
- `src/middleware.py`: Request orchestration, vault lifecycle, persistence queue, fail-open/closed behavior.
- `src/pii_engine.py`: Detection/redaction/rehydration logic (Presidio + GLiNER + heuristics).
- `src/pii_vault.py`: Scoped token/value store and snapshot serialization.
- `src/persistence.py`: Vault persistence selector and stores (`none`, `internal`, `external`).
- `src/allowlist_cache.py`: Local per-assistant allowlist cache, selector-based extraction, atomic snapshot writes.
- `src/config.py`: Environment-backed settings loader.
- `src/schemas.py`: API request/response contracts.
- `src/types.py`: Shared typed scope model.

## Tests

- `tests/test_allowlist_cache.py`: Allowlist selector extraction, cache rewrite behavior, middleware merge behavior.
- `tests/test_server_auth.py`: API key guard behavior.
- `tests/test_persistence_selector.py`: Persistence mode/build validation.
- `tests/test_schemas_contract.py`: Request schema validation and behavior.
- `tests/test_middleware_*.py`: Scope isolation and runtime policies.
- `tests/test_name_false_positive_filters.py`: Name redaction tuning regression coverage.
