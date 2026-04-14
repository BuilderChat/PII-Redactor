# PII-Redactor

Middleware-first PII redaction service for conversational systems.

This service redacts PII before text reaches an LLM, then rehydrates placeholders in the model response before returning text to end users.

## V1 Scope

- Mandatory entities: names, email, phone
- Token format: `<fn_#>`, `<mn1_#>`, `<mn2_#>`, `<ln_#>`, `<em_#>`, `<ph_#>`
- Isolation key: `thread_id + session_id + visitor_id + client_id + assistant_id`
- API surface: REST only (`/redact`, `/rehydrate`, `/session/end`, `/health`)
- Security: API key (raw or SHA-256 hash verification)
- Detection backend: Presidio + GLiNER (automatic fallback to regex/heuristics)
- Default failure policy: fail-closed (per-request override available)

## Token Policy

- Tokens are scoped per isolated chat context.
- Per context, active user index starts at `1`.
- Sending `new_user=true` on `/redact` advances index (`*_1 -> *_2 -> *_3`).
- If a value is corrected (e.g., spelling fix), the same token for that active index is overwritten.

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
uvicorn src.server:app --host 0.0.0.0 --port 8000 --reload
```

## Example

```bash
curl -X POST http://localhost:8000/redact \
  -H 'Content-Type: application/json' \
  -H 'x-api-key: change-me' \
  -d '{
    "thread_id": "thread_abc123",
    "session_id": "s1",
    "visitor_id": "v1",
    "client_id": "c1",
    "assistant_id": "a1",
    "message": "My name is Jinbad Profut and my email is jin@test.com",
    "previous_assistant_message": "What is your first name?",
    "non_name_allowlist": ["Windsor", "Shadow Hills", "Old Redwood Village"],
    "failure_mode": "closed"
  }'
```

## Integration Guide

Use this order in your app:

1. Call `/redact` with the raw user message before sending content to your LLM.
2. Send `redacted` message to the LLM.
3. Call `/rehydrate` with the LLM output.
4. Render `clean` text to end users.
5. Call `/session/end` when the thread ends.

Required scope fields on every request:

- `thread_id` (must start with `thread_`)
- `session_id`
- `visitor_id`
- `client_id`
- `assistant_id`

### Request Notes

- `/redact`
  - `new_user=true` advances token profile (`*_1 -> *_2`, etc.) for the same thread scope.
  - `previous_assistant_message` improves prompted-name handling.
  - `failure_mode` supports `closed` or `open` (default inherits server setting).
  - `include_replacements=true` only returns raw replacements when `PII_REDACTOR_ALLOW_RAW_REPLACEMENTS=true`.
- `/rehydrate`
  - Use `failure_mode="closed"` for user-facing flows.
  - Use `failure_mode="open"` only for internal redacted-only tooling.

### Failure Policy

- Server default is `fail-closed` (`PII_REDACTOR_FAIL_CLOSED_DEFAULT=true`).
- In `fail-closed` mode, unavailable redaction/rehydration returns HTTP `503`.
- In `fail-open` mode, service returns passthrough text.

### Memory + Persistence Behavior

- In-memory scope cache is bounded:
  - `PII_REDACTOR_MAX_ACTIVE_SCOPES` (default `15`)
  - `PII_REDACTOR_VAULT_TTL_SECONDS` (default `3600`)
- If persistence is configured:
  - Writes are queued asynchronously (non-blocking request path)
  - Queue pressure or persistence health can force fail-closed behavior
  - Rehydrate resolves memory first, then persistence fallback

### Persistence Mode Selector

Use `PII_REDACTOR_PERSISTENCE_MODE`:

- `none`
  - In-memory only.
  - No DB integration.
- `internal`
  - Redactor process owns DB credentials/config.
  - Current internal implementation supports Supabase (`PII_REDACTOR_INTERNAL_STORE_IMPL=supabase`).
- `external`
  - Host app controls persistence implementation.
  - Provide `PII_REDACTOR_EXTERNAL_STORE_FACTORY=<module>:<callable>` or inject store in-process.

#### Internal Supabase Required Env

When `PII_REDACTOR_PERSISTENCE_MODE=internal` and `PII_REDACTOR_INTERNAL_STORE_IMPL=supabase`, set:

- `PII_REDACTOR_SUPABASE_URL`
- `PII_REDACTOR_SUPABASE_SERVICE_ROLE_KEY`
- `PII_REDACTOR_SUPABASE_TABLE` (default `pii_vault_snapshots`)
- `PII_REDACTOR_PERSISTENCE_MASTER_KEY` (required for encrypted payloads)
- `PII_REDACTOR_PERSISTENCE_KEY_VERSION` (for key rotation)

Recommended with fail-closed:

- `PII_REDACTOR_REQUIRE_PERSISTENCE=true`
- `PII_REDACTOR_PERSISTENCE_BLOCK_ON_ERROR=true`

#### External Mode Required Env

When `PII_REDACTOR_PERSISTENCE_MODE=external`, set:

- `PII_REDACTOR_EXTERNAL_STORE_FACTORY=<module>:<callable>`

Factory callable contract:

- Returns object with methods: `load(scope)`, `save(scope, snapshot, expires_at_epoch, key_version)`, `delete(scope)`
- Can accept zero args or one `settings` arg.

### Deployment Patterns

Single-instance mode (simplest):

- Run redactor side-by-side with your chat backend.
- In-memory scope cache handles active threads.

Multi-instance mode (recommended for scale):

- Use a shared persistence backend so any instance can rehydrate.
- Keep `thread_id` stable per conversation.
- Monitor `/health` fields:
  - `persistence_enabled`
  - `persistence_healthy`
  - `persistence_queue_depth`

## Name Tuning Hooks

- `previous_assistant_message` (optional): improves one-word name handling by only treating single-word replies as names when prior assistant text asked for a name.
- `non_name_allowlist` (optional): per-request city/community/domain terms that should not be treated as person names.
- Environment defaults:
  - `PII_REDACTOR_NON_NAME_TERMS` (CSV)
  - `PII_REDACTOR_NON_NAME_TERMS_JSON_PATH` (JSON tree path; keys/values are flattened into non-name terms)

## Supabase Persistence Guidance

Use encrypted persistence behind a vault-store interface:

- Encrypt values before writing to Supabase (AES-GCM with per-record nonce)
- Keep encryption keys outside DB (environment/KMS)
- Store key version metadata for rotation (`PII_REDACTOR_PERSISTENCE_KEY_VERSION`)
- Add TTL and explicit delete paths for session end
- Never log raw PII

Suggested table schema for internal Supabase mode:

```sql
create table if not exists public.pii_vault_snapshots (
  scope_key text primary key,
  thread_id text not null,
  session_id text not null,
  visitor_id text not null,
  client_id text not null,
  assistant_id text not null,
  key_version text not null,
  expires_at timestamptz not null,
  payload jsonb not null,
  updated_at timestamptz not null default now()
);

create index if not exists idx_pii_vault_snapshots_scope
  on public.pii_vault_snapshots (client_id, assistant_id, visitor_id, session_id, thread_id);

create index if not exists idx_pii_vault_snapshots_expires_at
  on public.pii_vault_snapshots (expires_at);
```

### Example `.env` (Internal Supabase)

```bash
PII_REDACTOR_PERSISTENCE_MODE=internal
PII_REDACTOR_INTERNAL_STORE_IMPL=supabase
PII_REDACTOR_REQUIRE_PERSISTENCE=true
PII_REDACTOR_PERSISTENCE_BLOCK_ON_ERROR=true
PII_REDACTOR_SUPABASE_URL=https://YOUR_PROJECT.supabase.co
PII_REDACTOR_SUPABASE_SERVICE_ROLE_KEY=YOUR_SERVICE_ROLE_KEY
PII_REDACTOR_SUPABASE_TABLE=pii_vault_snapshots
PII_REDACTOR_PERSISTENCE_MASTER_KEY=LONG_RANDOM_MASTER_KEY
PII_REDACTOR_PERSISTENCE_KEY_VERSION=v1
```

## Notes

- `PII-redactor-plan.v2.md` is preserved as the planning reference.
- Runtime health endpoint includes detector status so you can verify if Presidio/GLiNER loaded.
- If Presidio/GLiNER dependencies or models are unavailable, the engine falls back automatically to regex/heuristics.
