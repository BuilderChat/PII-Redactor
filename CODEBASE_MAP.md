# CODEBASE_MAP

## Runtime Modules

- `src/server.py`: FastAPI app, auth guard, REST endpoints (`/redact`, `/rehydrate`, `/session/end`, `/allowlist/refresh`, `/health`) with detector, request, and persistence diagnostics.
- `src/middleware.py`: Request orchestration, vault lifecycle, endpoint concurrency limits, persistence queue, persistence degraded policy, fail-open/closed behavior, and runtime health counters.
- `src/pii_engine.py`: Detection/redaction/rehydration logic. The `slm` branch defaults to tuned heuristics; GLiNER/Presidio remain optional when full dependencies are installed. Includes conservative handling for explicit/punctuation-light name intros and email-prompt-only spaced email fragments.
- `src/pii_vault.py`: Scoped token/value store and snapshot serialization.
- `src/persistence.py`: Vault persistence selector and stores (`none`, `internal`, `external`).
- `src/allowlist_cache.py`: Local per-assistant allowlist cache, selector-based extraction, atomic snapshot writes.
- `src/config.py`: Environment-backed settings loader, including persistence, logging, and endpoint concurrency controls.
- `src/logging_config.py`: Shared Redactor logging configuration for JSON/text output, Grafana/Loki fields, and Uvicorn access-log suppression.
- `src/schemas.py`: API request/response contracts, including the `/health` observability payload.
- `src/types.py`: Shared typed scope model.

## Offline Tools

- `scripts/redact_transcript_fixture.py`: Transcript fixture redaction utility with user-only, both-sides, and user-sourced-both modes.
- `scripts/clean_transcripts.py`: Simple batch transcript cleaner that writes sibling `_cleaned` files and redacts user-provided PII across user and assistant messages.
- `scripts/clean_shadow_live_transcripts.py`: Shadow export cleaner that extracts only `[LIVE TRANSCRIPT]` sections and writes sibling `_live_cleaned` files for manual review.

## Documentation

- `README.md`: Setup, API usage, persistence modes, integration flow.
- `docs/BUILD_IMAGE_GUIDE.md`: Docker build/run instructions for strict offline packaging and embedding in another app image.

## Packaging

- `requirements.txt`: SLM runtime dependencies only; excludes GLiNER, Presidio, spaCy, and model/runtime dependencies.
- `requirements-full.txt`: Optional full detector dependency set for GLiNER/Presidio experiments or rollback comparisons.

## Tests

- `tests/test_allowlist_cache.py`: Allowlist selector extraction, cache rewrite behavior, middleware merge behavior.
- `tests/test_engine_airgap.py`: Air-gap detector initialization guarantees (GLiNER local-only by default).
- `tests/test_server_auth.py`: API key guard behavior.
- `tests/test_logging_config.py`: Redactor logging field, context, and Uvicorn access-log behavior.
- `tests/test_persistence_selector.py`: Persistence mode/build validation.
- `tests/test_schemas_contract.py`: Request schema validation and behavior.
- `tests/test_middleware_*.py`: Scope isolation and runtime policies.
- `tests/test_name_false_positive_filters.py`: Name redaction tuning regression coverage, including punctuation-light self-introductions and prompt-aware spaced email fragments.
- `tests/test_transcript_cleaner.py`: Offline transcript cleaning behavior and assistant-role compatibility coverage.
- `tests/test_shadow_live_transcript_cleaner.py`: Live-section extraction and cleaning coverage for shadow export transcript files.
