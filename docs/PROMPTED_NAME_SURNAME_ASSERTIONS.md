# First-name replies with surname assertions

Source updated 2026-11-18. Server deployment has not been verified.

After a first/full/last-name request, `Victor and last name is Doyon` now becomes
`<fn_1> and last name is <ln_1>`. Previously the leading first name stayed raw,
including during transcript audit replay. The prompted parser now accepts a
leading name candidate followed immediately by whitespace, `and`, a comma, or a semicolon,
optional `my`, and an independently accepted `last name is`, `surname is`, or
`family name is` assertion. Existing non-name filters and runtime allowlists
still apply. This does not generally accept arbitrary sentence starters, or
infer a bare first name from this shape without a name request.

The whitespace-only connector was added on 2026-11-18 for replies such as
`Avery last name is shareef`. The older name-before-label detector now skips its
match when the label continues with `is`, allowing inline parsing to capture
the explicit surname value instead. This prevents an overlapping incorrect ln
binding for Avery. Bare reversed shorthand such as `Doyon last name` remains
supported. Without a name question, the explicit surname is still protected but
the leading unlabelled word is not inferred as a first name by this helper.

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

The composite assertion is handled before last-name courtesy and fallback
parsing. Previously those paths could assign ln to the leading first name as
well as the labelled surname. The shared helper now uses the explicit surname
label to classify the distinct leading candidate as fn. If both values are the
same (case-insensitively), the leading value stays ln: `Doyon, last name is Doyon`
does not establish a first name. Existing vault bindings are not rewritten.

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
Last-name-context cases also cover existing first-name bindings, repeated
surnames, normal bare/courtesy surname replies, and mixed email input.
No live database or provider access is needed.

Local result on 2026-10-21: all 423 Redactor tests passed, including 59 regression
cases in this feature's test module (21 added for last-name context).
Ruff on changed Python files and source/test compilation passed.
CentralApp full CI was not run because no CentralApp files changed. No deployment
or live widget/provider check was performed.

On 2026-11-18 all 1,045 Redactor tests passed, including 18 new cases for
whitespace-only assertions, optional my, Unicode names, reversed-label shorthand,
blocked/prose/community prefixes, non-name context and repeated-name replay.
Changed-file Ruff, source security lint with existing exclusions, and source/test
compilation passed. No CentralApp source or runtime contract changed; CentralApp
full CI and live database/provider checks were not run. Nothing was deployed.

## DEV acceptance and rollback

After an operator deploys the reviewed Redactor image through Infra, run this
offline image probe; it requires neither pytest nor database/provider access:

```bash
docker exec -i -w /app pii-redactor-dev python - <<'PY'
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript
engine = PIIEngine(use_presidio=False, use_gliner=False)
for prompt in ("Got it! What's your first name?", "What's your last name?"):
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
result = engine.redact('Doyon, last name is Doyon', PIIVault(),
                       previous_assistant_message="What's your last name?")
assert result.redacted_text == '<ln_1>, last name is <ln_1>'
result = engine.redact('General Manager', PIIVault(),
                       previous_assistant_message="What's your role there?")
assert result.redacted_text == 'General Manager'
prompt = 'What first name should the team use when they reach out with pricing?'
replay = replay_transcript([
    TranscriptTurn('assistant', prompt),
    TranscriptTurn('user', 'Avery last name is shareef'),
    TranscriptTurn('assistant', 'What should I call you?'),
    TranscriptTurn('user', 'Avery'),
], engine=engine)
assert replay.user_turns[0].redacted_text == '<fn_1> last name is <ln_1>'
assert replay.user_turns[1].redacted_text == '<fn_1>'
assert replay.token_to_value == {'<fn_1>': 'Avery', '<ln_1>': 'shareef'}
assert engine.redact('Doyon last name', PIIVault()).redacted_text == '<ln_1> last name'
print('prompted_name_surname_assertions=passed')
PY
```

Then use a fresh DEV widget thread with provider-send gates intact: supply a
synthetic email, answer the first-name question with the composite reply, and
verify fn/ln tokens, both captured lead fields, and finalization. Repeat with
both fields explicitly labelled. In a fresh
thread, reproduce `Avery last name is shareef` after the reported first-name
question; a repeated Avery must reuse fn, not ln. Then try the explicit
`First name is Avery` clarification and verify no additional name bindings.
Also capture the first name, wait for a last-name-only question, and supply
the whitespace-only composite reply again; verify no extra
surname binding is created for the known first name. This widget check establishes request-context
propagation that the image probe alone cannot verify. Do not log replacement
maps or raw messages. Historical lead correction requires separate review.

Rollback by reverting the parser change and redeploying the previous Redactor
image through Infra; there is no schema rollback.
