# PII-Redactor Codebase Map

Verified from local repo: 2026-09-01
Repo path: `/Users/laptop1/Documents/Business/BuilderChatDocuments/Coding/Dash/CentralApp+Redactor/PII-Redactor`
Status: Current source map for the PII-Redactor repo. Runtime deployment details are tracked separately in `../BuilderChat_Documentation/SERVER_RUNTIME_MAP.md` and `../BuilderChat_Documentation/AUDIT_RECONCILIATION_BACKLOG.md`.

Audit note as of 2026-09-01: the earlier mismatch where prod Redactor health reported healthy persistence while inspected PROD lacked `public.pii_vault_snapshots` was traced to server-side `.env.prod` pointing `PII_REDACTOR_SUPABASE_URL` and service-role credentials at the DEV Supabase project. The operator corrected `.env.prod` to target PROD and redeployed prod Redactor through the deploy script; health now reports commit `65cbe19`, `persistence_mode=internal:supabase`, `persistence_status=healthy`, and first post-correction persistence success at `2026-09-01T13:55:05.918370Z`. PROD table-row evidence from real 2026-09-01 activity confirmed encrypted `pii_vault_snapshots` rows with `key_version=v1`, future `expires_at` timestamps, and AES-GCM payload fields rather than plaintext replacements. Code confirms that internal Supabase persistence expects the configured table, uses Supabase REST/service-role credentials, stores encrypted scoped payloads, surfaces persistence health/errors, and treats TTL as logical expiry rather than physical row deletion. Remaining work is to move the table SQL from README-only setup into a formal migration/source-of-truth process.

## System Purpose

PII-Redactor is a middleware-first FastAPI service that redacts sensitive user-provided values before conversational text reaches an LLM, then rehydrates scoped placeholder tokens in model output before text is shown to end users.

Confirmed current scope from repo docs and code:

- Mandatory entity families: names, email, phone.
- Token format: `<fn_#>`, `<mn1_#>`, `<mn2_#>`, `<ln_#>`, `<em_#>`, `<ph_#>`.
- Isolation key: `thread_id + session_id + visitor_id + client_id + assistant_id`.
- API surface: REST only.
- Default detection mode: tuned deterministic heuristics.
- Optional detector branches: GLiNER and Presidio remain configurable but disabled by default in the slim runtime.
- Default failure policy: fail closed, with per-request `failure_mode` override.

## Runtime Entry Point

Confirmed from repo:

- `src/server.py` constructs `FastAPI(title="PII Redactor", version="0.1.0")`.
- Settings are loaded at import/startup through `src/config.py`.
- Logging is configured at startup through `src/logging_config.py`.
- Persistence backend is selected at startup through `build_vault_store(settings)`.
- `LocalAllowlistCache` is initialized at startup when enabled.
- `PIIMiddleware` is the central runtime orchestrator.
- Startup fails closed when required persistence or required detector configuration is invalid.

Confirmed Docker entry point:

```text
uvicorn src.server:app --host 0.0.0.0 --port 8081 --workers 1
```

Confirmed local development entry point:

```text
uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload
```

## HTTP API

Confirmed from `src/server.py`:

- `POST /redact`
  - Requires API key unless disabled by configuration.
  - Accepts scoped raw user message.
  - Calls `PIIMiddleware.process_inbound`.
  - Returns redacted text, active user index, and optionally raw replacements only when both request and server config allow it.
  - Returns `503` when saturated or unexpectedly unavailable.

- `POST /rehydrate`
  - Requires API key unless disabled by configuration.
  - Accepts scoped LLM response text.
  - Calls `PIIMiddleware.process_outbound`.
  - Returns clean text plus placeholder repair diagnostics.
  - Returns `503` when saturated or unexpectedly unavailable.

- `POST /session/end`
  - Requires API key unless disabled by configuration.
  - Ends/removes a scoped vault.
  - Returns `vault_destroyed` or `session_not_found`.

- `POST /allowlist/refresh`
  - Requires API key unless disabled by configuration.
  - Requires local allowlist cache to be enabled.
  - Accepts direct terms and/or selector-based extraction from JSON payloads.
  - Refreshes a per-client/per-assistant non-name allowlist cache.
  - Returns cache metadata including changed status, content hash, source version, and cache file.

- `GET /health`
  - Does not require API key in current code.
  - Returns detector, concurrency, persistence, queue, cache, scope, and performance health metadata.
  - Includes `REDACTOR_COMMIT` from environment when present.

## Runtime Modules

- `src/server.py`
  - FastAPI app, API key guard, endpoint definitions, startup validation, health response assembly, and endpoint-level exception handling.

- `src/config.py`
  - Environment-backed settings loader.
  - Loads `.env` from repo root by default outside pytest unless `PII_REDACTOR_LOAD_DOTENV=false`.
  - Owns API key, failure policy, detector toggles, concurrency limits, persistence settings, Supabase settings, logging settings, vault limits, and allowlist cache settings.

- `src/middleware.py`
  - Central request orchestration.
  - Owns inbound redaction, outbound rehydration, vault lifecycle, active scope cache, endpoint concurrency limits, persistence queueing, persistence degraded policy, fail-open/fail-closed behavior, recovery cooldowns, and runtime health counters.

- `src/pii_engine.py`
  - Detection, redaction, token assignment support, rehydration support, placeholder repair, deterministic name/email/phone heuristics, and optional GLiNER/Presidio integration paths.
  - Current slim defaults disable GLiNER and Presidio.

- `src/pii_vault.py`
  - Scoped token/value storage and snapshot serialization.

- `src/persistence.py`
  - Persistence selector and store implementations.
  - Supported modes: `none`, `internal`, `external`.
  - Internal implementation supports Supabase-backed encrypted vault snapshots.
  - External implementation loads an injected/factory-backed store.

- `src/allowlist_cache.py`
  - Local per-assistant non-name allowlist cache.
  - Supports selector-based term extraction from JSON payloads.
  - Uses atomic snapshot writes and content hashes to avoid unnecessary rewrites.

- `src/logging_config.py`
  - Shared logging setup for JSON/text formats, environment/app role fields, request/client/assistant context, and Uvicorn access-log suppression.

- `src/schemas.py`
  - Pydantic request/response contracts for all endpoints, including health payload and request failure-mode resolution.

- `src/types.py`
  - Shared scope model used to isolate vaults and request state.

## Runtime Configuration

Confirmed from `src/config.py` and README:

- API key:
  - `PII_REDACTOR_API_KEY`
  - `PII_REDACTOR_API_KEY_SHA256`
  - `PII_REDACTOR_REQUIRE_API_KEY` defaults true.

- Failure policy:
  - `PII_REDACTOR_FAIL_CLOSED_DEFAULT` defaults true.
  - Request-level `failure_mode` can choose `closed` or `open`.
  - `PII_REDACTOR_ALLOW_RAW_REPLACEMENTS` defaults false.

- Detector controls:
  - `PII_REDACTOR_USE_PRESIDIO` defaults false.
  - `PII_REDACTOR_REQUIRE_PRESIDIO` defaults false.
  - `PII_REDACTOR_USE_GLINER` defaults false.
  - `PII_REDACTOR_REQUIRE_GLINER` defaults false.
  - `PII_REDACTOR_GLINER_ALLOW_REMOTE_DOWNLOAD` defaults false.
  - `PII_REDACTOR_GLINER_MODEL` defaults `urchade/gliner_multi_pii-v1`.

- Scope/vault limits:
  - `PII_REDACTOR_VAULT_TTL_SECONDS` defaults `3600`.
  - `PII_REDACTOR_MAX_ACTIVE_SCOPES` defaults `15`.

- Endpoint concurrency:
  - `PII_REDACTOR_REDACT_MAX_CONCURRENCY` defaults `24`.
  - `PII_REDACTOR_REHYDRATE_MAX_CONCURRENCY` defaults `24`.
  - `PII_REDACTOR_CONCURRENCY_ACQUIRE_TIMEOUT_SECONDS` defaults `0.5`.

- Persistence:
  - `PII_REDACTOR_PERSISTENCE_MODE` defaults `none`.
  - `PII_REDACTOR_REQUIRE_PERSISTENCE` defaults false.
  - `PII_REDACTOR_PERSISTENCE_BLOCK_ON_ERROR` defaults true.
  - `PII_REDACTOR_PERSISTENCE_QUEUE_MAX` defaults `1024`.
  - `PII_REDACTOR_PERSISTENCE_RECOVERY_COOLDOWN_SECONDS` defaults `30`.
  - `PII_REDACTOR_INTERNAL_STORE_IMPL` defaults `supabase`.
  - `PII_REDACTOR_SUPABASE_TABLE` defaults `pii_vault_snapshots`.
  - `PII_REDACTOR_SUPABASE_REQUEST_TIMEOUT_SECONDS` defaults `15`.

- Allowlist cache:
  - `PII_REDACTOR_ALLOWLIST_CACHE_ENABLED` defaults true.
  - `PII_REDACTOR_ALLOWLIST_CACHE_DIR` defaults `.cache/non_name_allowlists`.
  - `PII_REDACTOR_ALLOWLIST_CACHE_MAX_TERMS` defaults `50000`.

- Logging:
  - `PII_REDACTOR_LOG_LEVEL` falls back to `LOG_LEVEL`, then `INFO`.
  - `PII_REDACTOR_LOG_FORMAT` falls back to `LOG_FORMAT`, then `text`.
  - `PII_REDACTOR_ACCESS_LOGS` defaults false.

Do not document secret values from `.env`.

## Data and State Boundaries

Confirmed from repo:

- Request scope is derived from `thread_id`, `session_id`, `visitor_id`, `client_id`, and `assistant_id`.
- Persisted vault `scope_key` is `client_id:assistant_id:visitor_id:session_id:thread_id`.
- Supabase persistence saves encrypted payload, key version, expiry, and the scope columns through the Supabase REST API.
- Expired persisted snapshots are ignored on load but are not deleted by expiry alone.
- `/session/end` deletes persisted state only when the scope is present in the process-local in-memory vault map; unknown/not-loaded scopes return `session_not_found` without deleting a persisted row.
- In-memory TTL/capacity eviction enqueues persisted deletes for evicted active scopes.
- In-memory vault state is bounded by TTL and max active scopes.
- Optional persistence can store encrypted vault snapshots.
- Local allowlist cache stores non-name terms by `client_id + assistant_id`.
- Raw replacements are not returned unless `include_replacements=true` and `PII_REDACTOR_ALLOW_RAW_REPLACEMENTS=true`.

Requires runtime verification:

- Production/development persistence mode.
- Production/development fail-open/fail-closed defaults.
- Actual allowlist cache directory mounts.
- Whether persistence snapshots are stored in Supabase or only in process memory.
- Current Redactor API key separation between dev and prod.

## Request Flow

Confirmed from repo:

1. Caller sends a scoped request with API key when API-key enforcement is enabled.
2. `src/server.py` validates API key using raw key or SHA-256 comparison.
3. Pydantic schema validates request shape and converts it to a `ScopeContext`.
4. `PIIMiddleware` enforces endpoint concurrency limits.
5. Middleware resolves or creates the scoped vault.
6. `/redact` detects PII and replaces values with scoped tokens.
7. `/rehydrate` replaces known scoped tokens with original values and reports placeholder repair diagnostics.
8. Middleware queues persistence writes/deletes when persistence is enabled.
9. Endpoint returns redacted or clean text, or a `503` on saturation/unavailability.

## Packaging

Confirmed from repo:

- `Dockerfile`
  - Base image: `python:3.11-slim-bookworm`.
  - Installs `curl`.
  - Installs `requirements.txt`.
  - Copies only `src` into `/app/src`.
  - Exposes `8081`.

- `requirements.txt`
  - Slim runtime dependency set.
  - Excludes GLiNER, Presidio, spaCy, and model/runtime dependencies.

- `requirements-full.txt`
  - Optional full detector dependency set for GLiNER/Presidio experiments or rollback comparisons.

- `requirements-dev.txt`
  - Local development/test dependency set.

- `Makefile`
  - `venv`, `install`, `run`, `test`, and `lint` helper targets.
  - `lint` currently runs `python -m compileall src tests`.

## Deployment

Confirmed from repo:

- `scripts/deploy_redactor_with_commit.sh` builds and recreates a configured Redactor service through the shared compose directory.
- Defaults:
  - `REPO_DIR=$HOME/apps/PII-Redactor`
  - `COMPOSE_DIR=$HOME/apps/builderchat`
  - `REDACTOR_SERVICE=pii-redactor-prod`
  - `HEALTH_CHECK_CONTAINER=centralapp-chat`
  - `HEALTH_URL=http://pii-redactor-prod:8081/health`
- The script exports `REDACTOR_COMMIT` from the repo's current short git SHA.
- After recreate, it checks `/health` from the CentralApp chat container and requires the returned commit to match.

Requires runtime verification:

- Whether this script is the current production deploy path or a reference/helper.
- Whether dev deploy uses this script with overrides or a separate server script.
- Whether deployed compose files exactly match local repo compose files.

## Offline Tools

Confirmed from repo:

- `scripts/redact_transcript_fixture.py`
  - Redacts transcript fixtures for review/testing.

- `scripts/clean_transcripts.py`
  - Batch transcript cleaner that writes sibling `_cleaned` files.
  - Redacts user-provided PII across user messages and assistant messages that repeat user-provided PII.

- `scripts/clean_shadow_live_transcripts.py`
  - Extracts `[LIVE TRANSCRIPT]` sections from shadow exports and writes sibling `_live_cleaned` files.

## Documentation

Confirmed from repo:

- `README.md`
  - Main setup, API usage, token policy, failure policy, logging, memory/persistence behavior, allowlist cache behavior, deployment patterns, and integration guidance.

- `docs/BUILD_IMAGE_GUIDE.md`
  - Docker build/run instructions for strict offline packaging and embedding in another app image.

- `docs/IMPLEMENTATION_PLAN.md`
  - Historical/current implementation planning reference; current status should be verified before treating it as source of truth.

- `PII-redactor-integration-checklist.md`
  - Integration checklist; current status should be verified during CentralApp flow audit.

- `PII-redactor-plan.md` and `PII-redactor-plan.v2.md`
  - Planning documents; likely historical unless verified otherwise.

- `SHADOW_RUN_README.md`
  - Shadow-run workflow/reference; current operational status requires verification.

- `deferred_list.md`, `notes.txt`, `PII_scorecard.json`, `system_prompt.txt`, `name_gazetteer.json`
  - Supporting notes/data/artifacts. Current active use requires verification before cleanup or reliance.

## Tests

Confirmed from repo:

- `tests/test_allowlist_cache.py`
- `tests/test_config_defaults.py`
- `tests/test_engine_airgap.py`
- `tests/test_engine_roundtrip.py`
- `tests/test_engine_runtime_info.py`
- `tests/test_logging_config.py`
- `tests/test_middleware_isolation.py`
- `tests/test_middleware_runtime_policies.py`
- `tests/test_name_false_positive_filters.py`
- `tests/test_persistence_selector.py`
- `tests/test_pii_vault.py`
- `tests/test_placeholder_repair.py`
- `tests/test_schemas_contract.py`
- `tests/test_server_auth.py`
- `tests/test_server_detector_requirements.py`
- `tests/test_shadow_live_transcript_cleaner.py`
- `tests/test_transcript_cleaner.py`

Coverage themes:

- API auth and schema contracts.
- Config defaults.
- Detector startup requirements and air-gap behavior.
- Engine roundtrip and placeholder repair.
- Middleware isolation and runtime policies.
- Persistence selector behavior.
- Vault behavior.
- Logging format/context behavior.
- Allowlist cache behavior.
- Name false-positive regression coverage.
- Offline transcript cleaning behavior.

## Known Audit Targets

- Verify CentralApp's live Redactor call path and whether it always sends redacted text to LLM providers.
- Verify CentralApp behavior when Redactor fails open or fails closed.
- Verify whether contact-capture confirmation text uses tokens safely before rehydration.
- Verify production/development Redactor persistence mode.
- Verify Redactor health checks in Docker/Caddy/deploy scripts.
- Verify whether `/health` should remain unauthenticated and whether exposed network paths are internal-only.
- Verify whether root planning files are historical, active, or cleanup candidates.
- Verify whether private transcript fixtures are intentionally tracked and protected from accidental publication.
