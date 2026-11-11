# Email confirmation followed by a surname label

Source updated 2026-11-11. Deployment has not been verified.

`correct email last name doyon` now becomes `correct email last name <ln_1>`.
Previously, the whitespace surname-label parser required the label at the start
of the reply, while inline surname parsing required `is`.

`CONFIRMED_EMAIL_SURNAME_REPLY_RE` accepts a complete combined answer:

- `correct email`, or optional `the` followed by `email [is] correct/right`;
- whitespace or a comma/period/semicolon/exclamation separator;
- optional `my`, then `last name`, `lastname`, `surname` or `family name`;
- one Unicode/apostrophe/hyphen name word, with optional final punctuation.

Only the surname is tokenized. The pattern is case-insensitive and anchored to
the whole reply. It does not relax all inline labels, accept arbitrary preceding
text, infer compound surnames, or consume trailing prose/third-party references.
The existing surname-label validation and scoped runtime allowlist checks are
reused; the new path also rejects existing blocked tokens and explicit missing
values such as unknown/none/null/unavailable/optional. Other detection paths are
unchanged. Like an explicit labelled answer, this complete shape does not require
a preceding name question; no global word or community exclusions are added.

Live redaction and transcript audit replay share the canonical engine. Already
stored leads and vault bindings are not repaired. The parser adds no I/O or logs;
existing request/audit start/success/failure logging remains without raw PII.
No CentralApp/API/config/schema change is required.

## Validation

On 2026-11-11 all 848 Redactor tests passed, including 48 new regressions. Ruff,
source security lint with existing project exclusions, and source/test
compilation passed. Cases cover confirmation/label variants, lowercase and
Unicode/joined surnames, refusals/missing-value and prose controls, runtime
allowlists, and email/first-name/combined-answer transcript replay. CentralApp
full CI was not run because CentralApp source did not change. No live
database/provider check or deployment was performed.

From the workspace root:

```bash
cd PII-Redactor
myenv/bin/python -m pytest tests -q
../myenv/bin/python -m ruff check src/pii_engine.py tests/test_confirmed_email_surname.py
myenv/bin/python -m compileall -q src tests
```

## DEV acceptance and rollback

After an operator deploys the reviewed Redactor image through Infra, this offline
image probe needs no pytest, database access or provider sends:

```bash
docker exec -i -w /app pii-redactor-dev python - <<'PY'
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript
engine = PIIEngine(use_presidio=False, use_gliner=False)
prompt = "Is that email correct? And what's your last name, in case they need it?"
answer = 'correct email last name doyon'
result = engine.redact(answer, PIIVault(), previous_assistant_message=prompt)
assert result.redacted_text == 'correct email last name <ln_1>'
assert result.replacements == {'<ln_1>': 'doyon'}
replay = replay_transcript([
    TranscriptTurn('assistant', prompt), TranscriptTurn('user', answer),
], engine=engine)
assert replay.user_turns[0].redacted_text == 'correct email last name <ln_1>'
text = 'correct email last name unknown'
assert engine.redact(text, PIIVault(), previous_assistant_message=prompt).redacted_text == text
print('confirmed_email_surname=passed')
PY
```

Then use a fresh DEV widget thread with provider-send gates intact: supply a
synthetic email and first name, wait for the combined confirmation/last-name
question and submit the synthetic answer above. Verify the ln token before the
LLM call, correct visible rehydration and the lead surname. Check an allowlisted
community value creates no surname binding. Do not log raw replacement maps or
transcripts. The source probe does not establish HTTP/widget context propagation.

Rollback by reverting this parser change and redeploying the previous Redactor
image through Infra. There is no schema rollback.
