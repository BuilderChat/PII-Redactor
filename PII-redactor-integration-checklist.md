# PII Redactor Integration Checklist

## Decisions Locked (2026-04-14)

1. [x] Global controls are chatflow-LLM-input scoped (not per-client/per-assistant for first rollout).
2. [x] `v2_shadow` runs real chats and real LLM calls.
3. [x] `v2_shadow` must not execute live side effects (CRM writes, lead submission, emails, scheduler writes).
4. [x] Redaction applies only to external LLM submissions; non-redacted values must still be available for downstream non-LLM paths.
5. [x] Missed-PII flagging is hybrid: deterministic parser for email/phone and LLM-set flag for names.
6. [x] Interim scorecard rollup is per-thread.
7. [x] Interim raw PII storage is allowed for validation period, with access hardening required.
8. [ ] Super-admin transcript behavior migration is deferred (tracked in `deferred_list.md`).

## Build Checklist (V0.9.0 Shadow Integration)

1. [x] Redaction mode controls implemented (`v1`, `v2_shadow`, `v2_primary`) with immediate kill switch.
2. [x] V2 adapter integrated in CentralApp request path with tenant-safe scope mapping.
3. [x] V2 LLM preface injected with token legend:
   `PII Redacted. <fn_*>=first name; <mn_*>=middle name; <ln_*>=last name; <ph_*>=phone number; <em_*>=email. When relevant, use these values in your outputs and their real values will be filled in.`
4. [x] Shadow path executes non-blocking and does not alter user-visible output in `v1 + shadow`.
5. [ ] Shadow side-effect suppression verified across CRM, leads, email, and schedulers.
6. [x] Missed-PII detection + `PII_detected` bool emitted per turn.
7. [x] Scorecard schema defined and per-thread rollup writer integrated (DB tables: `pii_shadow_scorecard_turns`, `pii_shadow_scorecard_threads`).
8. [x] Shadow controls documented in `SHADOW_RUN_README.md`.
9. [x] Metrics/logging in place for redactor success/failure, shadow queue latency, and missed-PII counts.
10. [ ] Rollback runbook validated (`v2_shadow -> v1`, `v2_primary -> v1`) with no data-path regressions.
