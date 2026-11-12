# Directional surnames in explicit last-name answers

Source updated 2026-11-12. Deployment has not been verified.

The reported `West` answer after a last-name question reproduced `<fn_2>` in a
fresh local vault that already contained one first name. Prompted parsing rejected
West as a direction; the generic name detector accepted it because a name had
been requested, and a generic single-word name defaults to fn.

Prompted last-name parsing now accepts a complete one-word reply whose lowercase
value belongs to the existing `GEO_DIRECTION_WORDS` set, with optional surrounding
whitespace and final `.`, `!`, `?` punctuation. It emits ln before ordinary
blocked-word/allowlist checks, so the explicit surname answer wins over both a
direction filter and an exact directional-word allowlist collision. The explicit
span wins over the overlapping generic name span through existing span selection.

The set includes cardinal directions, their northern/southern/eastern/western
forms, and northeast/northwest/southeast/southwest. No new direction vocabulary
or global name/geography exclusions are introduced. Pending-field inference
already rejects direction words, so pending metadata alone cannot activate this
exception. First/full-name parsing and location requests are unchanged. Replies
such as `west of town`, `in the west`, `West Texas` and allowlisted `West Ridge`
remain subject to existing non-name filters.

This rule intentionally disambiguates a standalone answer from the actual last-name
question. It does not generally relabel all generic name detections as surnames,
change arbitrary non-direction allowlist collisions, or repair already-issued
incorrect vault bindings. Live redaction and transcript replay share the canonical
engine. No CentralApp/API/config/database changes, new I/O, or PII logs are added;
existing request/audit start/success/failure logs remain sufficient.

## Validation

On 2026-11-12 all 920 Redactor tests passed, including 72 new directional-surname
cases. Changed-file Ruff, source security lint with existing project exclusions,
and source/test compilation passed. Cases cover directional surname variants,
case/punctuation, exact-word allowlist collisions, location/prose controls,
multiword community allowlists and first-name-to-surname replay without a new fn.
CentralApp full CI was not run because no CentralApp source changed. No live
database/provider check or deployment was performed.

From the workspace root:

```bash
cd PII-Redactor
myenv/bin/python -m pytest tests -q
../myenv/bin/python -m ruff check src/pii_engine.py tests/test_directional_surnames.py
myenv/bin/python -m compileall -q src tests
```

## DEV acceptance and rollback

After an operator deploys the reviewed Redactor image through Infra, this offline
image probe requires neither pytest nor database/provider access:

```bash
docker exec -i -w /app pii-redactor-dev python - <<'PY'
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript
engine = PIIEngine(use_presidio=False, use_gliner=False)
prompt = "Thanks, <fn_1>! What's your last name, just in case?"
vault = PIIVault()
vault.register('fn', 'Avery')
result = engine.redact('West', vault, previous_assistant_message=prompt,
                       non_name_allowlist=['West'])
assert result.redacted_text == '<ln_1>'
assert vault.items() == {'<fn_1>': 'Avery', '<ln_1>': 'West'}
replay = replay_transcript([
    TranscriptTurn('assistant', "What's your first name?"),
    TranscriptTurn('user', 'Avery'), TranscriptTurn('assistant', prompt),
    TranscriptTurn('user', 'West'),
], engine=engine)
assert replay.user_turns[-1].redacted_text == '<ln_1>'
assert replay.token_to_value == {'<fn_1>': 'Avery', '<ln_1>': 'West'}
assert engine.redact('west of town', PIIVault(), previous_assistant_message=prompt).redacted_text == 'west of town'
print('directional_surnames=passed')
PY
```

Use a fresh DEV widget thread with provider-send gates intact. Supply a synthetic
first name, wait for a last-name question, then answer West. Verify ln is issued,
no second first-name binding appears, visible rehydration is correct and the lead
retains both name fields. Test a location question with a geographic reply too.
Do not log raw replacements or transcripts. The image probe does not establish
HTTP/widget context propagation, stored-lead correction or provider delivery.

Rollback by reverting this parser exception and redeploying the prior Redactor
image through Infra. No schema rollback is required.
