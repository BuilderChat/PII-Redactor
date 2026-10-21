# First-name replies with surname assertions

Source change: 2026-10-21. Server deployment has not been verified.

After a first/full-name request, `Victor and last name is Doyon` now becomes
`<fn_1> and last name is <ln_1>`. Previously the leading first name stayed raw,
including during transcript audit replay. The prompted parser now accepts a
leading name candidate followed immediately by `and`, a comma, or a semicolon,
optional `my`, and an independently accepted `last name is`, `surname is`, or
`family name is` assertion. Existing non-name filters and runtime allowlists
still apply. This does not generally accept arbitrary sentence starters, or
infer a bare first name from this shape without a first/full-name request.

Separately, keyed name values stop before the next name label rather than
consuming `and last` as part of the preceding value. This keeps
`First name is Victor and last name is Doyon` as fn/ln rather than two fn tokens,
including when volunteered outside name collection.

Live redaction and audit replay use the same engine. No CentralApp source,
API/configuration, database migration, or logging changes are needed. Existing
request and audit start/success/failure logs remain; the parser does no I/O or
logging. This change does not repair existing vault bindings or stored leads.
Audit evidence counts are entry counts and do not establish that both fields
were captured.

Separate existing edge case observed during local checks: after a last-name-only
question, this composite reply can classify the leading first name as another
surname. The new prompted rule is limited to first/full-name requests; handling
that ambiguous last-name-only context requires a separate regression and policy.

## Local verification

From the workspace root:

```bash
cd PII-Redactor
myenv/bin/python -m pytest tests -q
../myenv/bin/python -m ruff check src/pii_engine.py tests/test_prompted_name_surname_assertion.py
myenv/bin/python -m compileall -q src tests
```

The regression suite uses synthetic data and covers both shapes, punctuation,
lowercase and Unicode names, acknowledgment prefixes, runtime allowlists,
business replies, blocked words, ordinary prose, and email-to-name audit replay.
No live database or provider access is needed.

Local result on 2026-10-21: all 402 Redactor tests passed, including 38 new
regression cases; Ruff on changed Python files and source/test compilation passed.
CentralApp full CI was not run because no CentralApp files changed. No deployment
or live widget/provider check was performed.

## DEV acceptance and rollback

After an operator deploys the reviewed Redactor image through Infra, run this
offline image probe; it requires neither pytest nor database/provider access:

```bash
docker exec -i -w /app pii-redactor-dev python - <<'PY'
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript
engine = PIIEngine(use_presidio=False, use_gliner=False)
prompt = "Got it! What's your first name?"
for prefix in ('', 'First name is '):
    text = prefix + 'Victor and last name is Doyon'
    expected = prefix + '<fn_1> and last name is <ln_1>'
    result = engine.redact(text, PIIVault(), previous_assistant_message=prompt)
    assert result.redacted_text == expected
    assert result.replacements == {'<fn_1>': 'Victor', '<ln_1>': 'Doyon'}
    replay = replay_transcript([
        TranscriptTurn('user', 'victor@example.com'),
        TranscriptTurn('assistant', prompt), TranscriptTurn('user', text),
    ], engine=engine)
    assert replay.user_turns[-1].redacted_text == expected
result = engine.redact('General Manager', PIIVault(),
                       previous_assistant_message="What's your role there?")
assert result.redacted_text == 'General Manager'
print('prompted_name_surname_assertions=passed')
PY
```

Then use a fresh DEV widget thread with provider-send gates intact: supply a
synthetic email, answer the first-name question with the composite reply, and
verify fn/ln tokens, both captured lead fields, and finalization. Repeat with
both fields explicitly labelled. This widget check establishes request-context
propagation that the image probe alone cannot verify. Do not log replacement
maps or raw messages. Historical lead correction requires separate review.

Rollback by reverting the parser change and redeploying the previous Redactor
image through Infra; there is no schema rollback.
