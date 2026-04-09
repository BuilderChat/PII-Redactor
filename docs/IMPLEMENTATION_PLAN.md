# Implementation Plan Snapshot

Primary planning source: `PII-redactor-plan.v2.md`.

This repo reflects the first bootstrap session decisions:

- Python 3.12 runtime
- Middleware-only architecture (no local LLM)
- Mandatory PII entities: name, email, phone
- Scoped isolation fields: session_id, visitor_id, client_id, assistant_id
- REST-first delivery
- Output placeholder repair enabled before rehydration
