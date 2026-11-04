# Contact directives after acknowledgments

Source updated 2026-10-29. Deployment has not been verified.

The acknowledgment-plus-contact detector previously interpreted
`sure have her text me about new homes there. 202-555-0143` as first name `have`,
surname `her`, and a phone number. It stripped `sure`, treated the next two words
as a full name, and accepted a phone/email anywhere later as sufficient evidence.
This path intentionally works without a name question to catch volunteered names.

`CONTACT_DIRECTIVE_PREFIX_RE` now rejects that detector's name inference when
the acknowledgment is followed by an optional `please`, one of `have`, `get`,
`ask`, `tell`, `make`, and a bounded recipient phrase:

- her, him, them, someone, somebody, anyone, anybody;
- those/these guys, people or folks;
- the/your/our/a/an team, salesperson, salespeople, representative, rep,
  consultant, agent, person or manager, optionally preceded by sales;
- one of the/your/our salespeople, representatives, reps, consultants, agents
  or team.

Matching is case-insensitive with word boundaries. Existing acknowledgment
punctuation handling remains active. The guard only rejects inferred names in
`_detect_affirmative_name_contact_spans`. It does not suppress the message,
blacklist those words globally, alter pending-name state, or require a name
question for genuine volunteered names. Known scoped values, explicit personal
introductions elsewhere in the reply, email and phone detection continue normally.
Live redaction and transcript audit replay use the same engine.

This is a bounded grammar, not a claim to understand every English directive.
Other inference paths and already incorrect vault bindings are unchanged. It does
not repair stored leads; historical cleanup needs separately reviewed evidence.
No API, configuration, CentralApp, database or provider behavior changes are
required. The parser adds no logging/I/O; existing request and audit
start/success/failure logs remain without raw PII logging.

## Local validation

On 2026-10-29 all 749 Redactor tests passed, including 215 new cases. Changed-file
Ruff, source security lint with existing exclusions, and source/test compilation
passed. New tests cover the verb/recipient matrix with phone and email, casing,
punctuation, volunteered uppercase/lowercase/Unicode names, explicit introductions
and known names inside directive replies, existing accepted surname words, and
transcript replay without invented name bindings.

From the workspace root:

```bash
cd PII-Redactor
myenv/bin/python -m pytest tests -q
../myenv/bin/python -m ruff check src/pii_engine.py tests/test_affirmative_contact_directives.py
myenv/bin/python -m compileall -q src tests
```

CentralApp full CI was not run because no CentralApp source changed. No live
database/provider test or deployment was performed.

## DEV acceptance and rollback

After deploying the reviewed image through canonical Infra tooling, run this
offline image probe without pytest, database access or provider sends:

```bash
docker exec -i -w /app pii-redactor-dev python - <<'PY'
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript
engine = PIIEngine(use_presidio=False, use_gliner=False)
prompt = 'Would you like our consultant to reach out? Text or email is fine.'
answer = 'sure have her text me about new homes there. 202-555-0143'
expected = 'sure have her text me about new homes there. <ph_1>'
result = engine.redact(answer, PIIVault(), previous_assistant_message=prompt)
assert result.redacted_text == expected
assert result.replacements == {'<ph_1>': '202-555-0143'}
replay = replay_transcript([
    TranscriptTurn('assistant', prompt), TranscriptTurn('user', answer),
], engine=engine)
assert replay.token_to_value == {'<ph_1>': '202-555-0143'}
result = engine.redact('sure Avery Doyon, text me at 202-555-0143', PIIVault(),
                       previous_assistant_message=prompt)
assert result.redacted_text == 'sure <fn_1> <ln_1>, text me at <ph_1>'
print('affirmative_contact_directives=passed')
PY
```

In a fresh DEV widget thread with provider-send gates intact, request a community
consultant, receive an offer of text/email follow-up without a name question, and
send the synthetic directive reply. Verify the phone is protected, no fn/ln
bindings are created, and no bogus name enters the lead or assistant reply.
Repeat with a volunteered synthetic full name to verify name capture still works.
Do not log raw replacements or message bodies. The engine probe does not verify
full widget routing or deployed prompt context.

Rollback by reverting this source change and redeploying the previous Redactor
image through Infra. No schema rollback is required.
