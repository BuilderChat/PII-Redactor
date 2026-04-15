# Shadow Run Controls (CentralApp Integration)

Status: implemented scaffold in CentralApp (default-off, fail-open, non-blocking by default).

## Behavior Guarantees

1. V1 user-visible responses remain the served path.
2. Shadow runs in parallel and is isolated from V1 history/cache.
3. Redaction is applied only to shadow LLM submissions.
4. Shadow errors do not break V1 flow.
5. Side effects can be suppressed independently for shadow.
6. Redactor executes in-process inside CentralApp (no external redactor HTTP service required).
7. PII legend + shadow monitor directives are injected as runtime prompt guidance (not persisted as user messages).

## Runtime Controls

| Flag | Type | Default | Purpose |
|---|---|---|---|
| `CHAT_REDACTION_MODE` | string | `v1` | `v1`, `v2_shadow`, `v2_primary` control. Current implementation still keeps V1 as served output. |
| `CHAT_REDACTION_SHADOW_ENABLED` | bool | `false` | Enables shadow from `v1` mode. |
| `CHAT_REDACTION_SHADOW_SAMPLE_RATE` | float | `1.0` | Traffic sampling for shadow runs. |
| `CHAT_REDACTION_SHADOW_NONBLOCKING` | bool | `true` | When `true`, shadow runs asynchronously. |
| `CHAT_REDACTION_SHADOW_LLM_ENABLED` | bool | `true` | Executes real shadow LLM pass. |
| `CHAT_REDACTION_SHADOW_SIDE_EFFECTS_ENABLED` | bool | `false` | When `false`, suppresses write-side effects in shadow (lead submit, booking writes, transcript persist). |
| `CHAT_REDACTION_SHADOW_MAX_CONCURRENCY` | int | `24` | Max concurrent in-flight shadow workers. |
| `CHAT_REDACTION_PREWARM_ENABLED` | bool | `true` | Non-blocking startup prewarm for in-process redactor runtime (chat role only). |
| `CHAT_REDACTION_PREWARM_DELAY_SECS` | float | `0.0` | Delay before non-blocking prewarm task runs after startup. |
| `CHAT_REDACTION_REQUEST_TIMEOUT_MS` | int | `5000` | Reserved for parity with service mode; not used by in-process path. |
| `CHAT_REDACTION_FAIL_OPEN` | bool | `true` | Shadow redactor failures log and continue without impacting V1. |
| `CHAT_REDACTION_REDACTOR_BASE_URL` | string | unset | Legacy service-mode setting; ignored by current in-process integration. |
| `CHAT_REDACTION_REDACTOR_API_KEY` | string | unset | Legacy service-mode setting; ignored by current in-process integration. |
| `CHAT_REDACTION_ALLOWLIST_REFRESH_ENABLED` | bool | `true` | Refreshes redactor allowlist from DB-backed community/floor-plan data. |
| `CHAT_REDACTION_ALLOWLIST_REFRESH_TTL_SECS` | int | `600` | Refresh interval per client+assistant scope. |
| `CHAT_REDACTION_PII_NOTE_ENABLED` | bool | `true` | Prepends the PII legend note to shadow LLM user input. |
| `CHAT_REDACTION_PII_NOTE_TEXT` | string | default legend | Override note text. |
| `CHAT_REDACTION_MISSED_PII_ENABLED` | bool | `true` | Master toggle for missed-PII telemetry. |
| `CHAT_REDACTION_MISSED_PII_EMAIL_PHONE_PARSER_ENABLED` | bool | `true` | Deterministic leak check on redacted LLM-bound text. |
| `CHAT_REDACTION_MISSED_PII_LLM_NAME_FLAG_ENABLED` | bool | `true` | Parses `PII_detected=true/false` from shadow LLM output for name leakage signal. |
| `CHAT_REDACTION_SCORECARD_ENABLED` | bool | `true` | Writes per-turn + per-thread rollup scorecard rows. |
| `CHAT_REDACTION_SCORECARD_STORE_RAW_PII` | bool | `false` | Allows sampled raw token mappings in scorecard records. |
| `CHAT_REDACTION_SCORECARD_TOKEN_SAMPLE_MAX` | int | `12` | Max token mappings sampled per turn. |

## In-Process Redactor Controls (`PII_REDACTOR_*`)

These are read directly by CentralApp’s in-process redactor runtime.

| Flag | Type | Default | Purpose |
|---|---|---|---|
| `PII_REDACTOR_PERSISTENCE_MODE` | string | `none` | Persistence backend mode (`none`, `internal`, `external`). |
| `PII_REDACTOR_INTERNAL_STORE_IMPL` | string | `supabase` | Internal persistence backend (`supabase` or `memory`). |
| `PII_REDACTOR_REQUIRE_PERSISTENCE` | bool | `false` | Fail startup if persistence is not configured/available. |
| `PII_REDACTOR_PERSISTENCE_BLOCK_ON_ERROR` | bool | `true` | Block request path on persistence write failures. |
| `PII_REDACTOR_PERSISTENCE_MASTER_KEY` | string | unset | Master encryption key for persisted vault snapshots. |
| `PII_REDACTOR_PERSISTENCE_KEY_VERSION` | string | `v1` | Key version tag stored with encrypted snapshots. |
| `PII_REDACTOR_SUPABASE_URL` | string | unset | Supabase project URL for internal persistence mode. |
| `PII_REDACTOR_SUPABASE_SERVICE_ROLE_KEY` | string | unset | Supabase service role key for internal persistence mode. |
| `PII_REDACTOR_SUPABASE_TABLE` | string | `pii_vault_snapshots` | Target table for encrypted vault snapshots. |
| `PII_REDACTOR_ALLOWLIST_CACHE_ENABLED` | bool | `true` | Enables local allowlist cache used by shadow DB refreshes. |
| `PII_REDACTOR_ALLOWLIST_CACHE_DIR` | string | `.cache/non_name_allowlists` | Local cache dir for compiled allowlist term sets. |
| `PII_REDACTOR_ALLOWLIST_CACHE_MAX_TERMS` | int | `50000` | Maximum allowlist terms retained per scope. |
| `PII_REDACTOR_REQUIRE_GLINER` | bool | `false` | Fail startup unless GLiNER detector is available. |
| `PII_REDACTOR_REQUIRE_PRESIDIO` | bool | `false` | Fail startup unless Presidio detector is available. |
| `PII_REDACTOR_ALLOW_RAW_REPLACEMENTS` | bool | `false` | Controls whether raw replacements can be exposed by redactor outputs. |

## Recommended Safe Shadow Profile

```bash
CHAT_REDACTION_MODE=v1
CHAT_REDACTION_SHADOW_ENABLED=true
CHAT_REDACTION_SHADOW_NONBLOCKING=true
CHAT_REDACTION_SHADOW_LLM_ENABLED=true
CHAT_REDACTION_SHADOW_SIDE_EFFECTS_ENABLED=false
CHAT_REDACTION_PREWARM_ENABLED=true
CHAT_REDACTION_PREWARM_DELAY_SECS=0
CHAT_REDACTION_FAIL_OPEN=true
CHAT_REDACTION_MISSED_PII_ENABLED=true
CHAT_REDACTION_SCORECARD_ENABLED=true
CHAT_REDACTION_SCORECARD_STORE_RAW_PII=false
PII_REDACTOR_PERSISTENCE_MODE=internal
PII_REDACTOR_INTERNAL_STORE_IMPL=supabase
PII_REDACTOR_REQUIRE_PERSISTENCE=true
PII_REDACTOR_ALLOWLIST_CACHE_ENABLED=true
```

## PII Legend Default

`PII Redacted. <fn_*>=first name; <mn_*>=middle name; <ln_*>=last name; <ph_*>=phone number; <em_*>=email. When relevant, use these values in your outputs and their real values will be filled in.`

## Scorecard Storage (Current)

CentralApp now writes:

1. `pii_shadow_scorecard_turns` (per-turn telemetry)
2. `pii_shadow_scorecard_threads` (per-thread rollups)

Rollups include:

1. success/failure counts
2. `PII_detected` aggregates
3. parser + LLM-name-flag counts
4. latency p50/p95 for redactor, shadow LLM, shadow total, backend total, widget total (nullable until widget ack is available)

## Runtime Dependency Notes

1. CentralApp image/runtime must include `PII-Redactor/` directory.
2. In-process redactor uses `PII_REDACTOR_*` env vars directly from CentralApp runtime.
3. If strict detector requirements are enabled (`PII_REDACTOR_REQUIRE_GLINER=true`, `PII_REDACTOR_REQUIRE_PRESIDIO=true`), required packages/models must be available in the same runtime image.

## Rollback

Immediate rollback is env-only:

```bash
CHAT_REDACTION_SHADOW_ENABLED=false
CHAT_REDACTION_MODE=v1
```
