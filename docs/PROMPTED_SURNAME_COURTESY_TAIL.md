# Prompted surname followed by a courtesy sentence

Source updated 2026-10-27. Deployment has not been verified.

A reply shaped like `doyon. with thanks, This is my 6th request` after an explicit
last-name question now becomes `<ln_1>. with thanks, This is my 6th request`.
The motivating production report was successfully recovered in the lead flow;
this change protects the name earlier, before LLM processing.

Previously, multiword-name guards rejected the following `with`/`thanks`, and
existing sentence/prose tail rules required a capitalized surname and different
continuation cues. `PROMPTED_LAST_NAME_COURTESY_TAIL_RE` now accepts sentence-ending
punctuation (`.`, `!`, `?`), whitespace and a courtesy phrase (`with thanks`,
`thanks`, `thank you`). It runs after leading-candidate blocked-word and runtime
allowlist checks, before multiword parsing. The rule applies only when the
canonical prompted parser resolves a last-name request. It accepts lowercase,
accented, apostrophe-joined and hyphenated name words using the existing pattern.

This does not add arbitrary prose continuation capture, infer compound surnames,
or loosen other name-request types. Without the sentence boundary and courtesy
phrase this new rule does not apply. Existing detection rules remain active;
email/phone detection still protects contact channels in the follow-up text.
Live redaction and transcript audit replay share the canonical engine.

The parser performs no I/O or logging. Existing request/audit start/success/failure
logs remain sufficient; no raw transcript or replacement logging is added.
No CentralApp/config/API/schema changes are required. Existing stored records
and vault bindings are not rewritten.

## Local verification

From the workspace root:

```bash
cd PII-Redactor
myenv/bin/python -m pytest tests -q
../myenv/bin/python -m ruff check src/pii_engine.py tests/test_prompted_surname_courtesy_tail.py
myenv/bin/python -m compileall -q src tests
```

On 2026-10-27, all 501 Redactor tests passed, including 33 new cases. Changed-file
Ruff, source security lint with existing project exclusions, and source/test
compilation passed. The synthetic regressions cover the reported reply shape,
capitalization, Unicode/joined surnames, courtesy variations, refusals/blocked
words, runtime allowlists, non-name contexts, follow-up contact channels, and
email/first-name/surname transcript replay. No live database/provider test or
deployment was performed. CentralApp full CI was not run; no CentralApp source
changed.

## DEV acceptance and rollback

After deploying the reviewed Redactor image through the canonical Infra workflow,
this offline image probe requires no database/provider access or pytest:

```bash
docker exec -i -w /app pii-redactor-dev python - <<'PY'
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript
engine = PIIEngine(use_presidio=False, use_gliner=False)
prompt = "For just-in-case follow-up, what's your last name?"
answer = 'doyon. with thanks, This is my 6th request'
expected = '<ln_1>. with thanks, This is my 6th request'
result = engine.redact(answer, PIIVault(), previous_assistant_message=prompt)
assert result.redacted_text == expected
assert result.replacements == {'<ln_1>': 'doyon'}
replay = replay_transcript([
    TranscriptTurn('assistant', prompt), TranscriptTurn('user', answer),
], engine=engine)
assert replay.user_turns[-1].redacted_text == expected
blocked = 'pending. with thanks, This is my 6th request'
assert engine.redact(blocked, PIIVault(), previous_assistant_message=prompt).redacted_text == blocked
print('prompted_surname_courtesy_tail=passed')
PY
```

Then use a fresh DEV widget thread with provider-send gates intact: supply a
synthetic email and first name, wait for the last-name question, and supply the
synthetic reply above. Confirm the user turn contains the ln token before the
LLM call, rehydration remains correct, and the submitted lead retains the surname.
The image probe alone does not verify HTTP/widget context propagation.

Rollback by reverting this parser change and redeploying the previous Redactor
image through Infra. No schema rollback is required.
