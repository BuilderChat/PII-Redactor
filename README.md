# PII-Redactor

Middleware-first PII redaction service for conversational systems.

This service redacts PII before text reaches an LLM, then rehydrates placeholders in the model response before returning text to end users.

## V1 Scope

- Mandatory entities: names, email, phone
- Token format: `<fn_#>`, `<mn1_#>`, `<mn2_#>`, `<ln_#>`, `<em_#>`, `<ph_#>`
- Isolation key: `session_id + visitor_id + client_id + assistant_id`
- API surface: REST only (`/redact`, `/rehydrate`, `/session/end`, `/health`)
- Security: API key (raw or SHA-256 hash verification)

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
    "session_id": "s1",
    "visitor_id": "v1",
    "client_id": "c1",
    "assistant_id": "a1",
    "message": "My name is Jinbad Profut and my email is jin@test.com"
  }'
```

## Supabase Persistence Guidance

V1 uses in-memory vaults for minimum moving parts and lower risk.

If cross-process/session recovery is required, add encrypted persistence behind a vault-store interface:

- Encrypt values before writing to Supabase (AES-GCM with per-record nonce)
- Keep encryption keys outside DB (environment/KMS)
- Add TTL and explicit delete paths for session end
- Never log raw PII

## Notes

- `PII-redactor-plan.v2.md` is preserved as the planning reference.
- Presidio/GLiNER integration points are scaffolded; fallback regex/heuristics are implemented for first-pass behavior.
