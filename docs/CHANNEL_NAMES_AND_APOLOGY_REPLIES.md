# Channel-labelled names and prompted apology replies

Source updated 2026-11-04. Deployment has not been verified.

## Channel wrapper

`phone- Avery Doyon- 202-555-0143` now becomes
`phone- <fn_1> <ln_1>- <ph_1>`. The new wrapper requires an anchored phone/email
label, a dash/colon separator, exactly two name words, another dash/comma/colon
separator, and a complete contact value matching the labelled channel (optional
final sentence punctuation). Unicode en/em dashes are accepted. The original
label/separators remain verbatim. Wrong-channel values and arbitrary prose are
not accepted by this wrapper.

The wrapper delegates name capture to the existing adjacent name/contact parser,
preserving offsets, word filters, geography checks and scoped runtime community
allowlists. Additional directive-prefix, known housing phrase and complete role
title exclusions reuse existing rules. It does not infer names from a bare
channel label, a phone alone, or any phrase embedded somewhere in a sentence.
It supports the existing Unicode/apostrophe/hyphen name-word shape, not arbitrary
multi-part names or unsolicited single names.

## Apology prefix and surname entity

After a first/last/full-name request, `Sorry. Avery` now protects Avery using the
requested field. The prefix rule accepts `sorry` followed by `.`, `,`, or `!`,
whitespace, and a complete one/two-word answer with optional sentence punctuation.
It delegates only that answer back to prompted parsing, preserving original
offsets and existing refusal/non-name/allowlist checks. It does not globally
strip apologies or suppress messages, and ordinary apology sentences remain
subject to the existing guards.

A bare surname such as `Doyon.` after a last-name question previously fell through
because the prompted parser did not accept a punctuation-only tail. Generic name
inference could then assign fn to that one word. The prompted parser now accepts
punctuation-only `.`, `!`, `?` tails, retaining the ln classification. Local fresh
vault probes reproduced this independently of stale state; the supplied
production excerpt alone does not establish every runtime input or binding.

Live redaction and transcript replay share the canonical engine. Already-issued
vault bindings and stored leads are not rewritten. There are no CentralApp,
API/config, migration, network or persistence changes. No parser logging is
added; existing request/audit start/success/failure logs remain without raw PII.

## Validation

On 2026-11-04, all 800 Redactor tests passed, including 51 new regressions. Ruff,
source security lint with existing exclusions, and source/test compilation passed.
Cases include channel/Unicode/separator variations, wrong-channel inputs,
community allowlists, business titles, directives, apologies/refusals, name prompt
scope, correct entity assignment, full-name offsets and sequential replay.
No live database/provider check or deployment was performed. CentralApp full CI
was not run because no CentralApp source changed.

From the workspace root:

```bash
cd PII-Redactor
myenv/bin/python -m pytest tests -q
../myenv/bin/python -m ruff check src/pii_engine.py tests/test_channel_names_and_apology_replies.py
myenv/bin/python -m compileall -q src tests
```

## DEV acceptance and rollback

After an operator deploys the reviewed image through canonical Infra tooling,
run this offline image probe (no pytest, database or provider sends):

```bash
docker exec -i -w /app pii-redactor-dev python - <<'PY'
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript
engine = PIIEngine(use_presidio=False, use_gliner=False)
result = engine.redact('phone- Avery Doyon- 202-555-0143', PIIVault())
assert result.redacted_text == 'phone- <fn_1> <ln_1>- <ph_1>'
result = replay_transcript([
    TranscriptTurn('assistant', "What's your first name?"),
    TranscriptTurn('user', 'Sorry. Avery'),
    TranscriptTurn('assistant', "What's your last name, just in case?"),
    TranscriptTurn('user', 'Doyon.'),
], engine=engine)
assert result.user_turns[0].redacted_text == 'Sorry. <fn_1>'
assert result.user_turns[1].redacted_text == '<ln_1>.'
assert result.token_to_value == {'<fn_1>': 'Avery', '<ln_1>': 'Doyon'}
print('channel_names_and_apology_replies=passed')
PY
```

In fresh DEV widget sessions with provider-send gates intact, answer a contact
method question with the synthetic phone/name format; separately answer a
first-name question with `Sorry. Avery`, then a last-name question with `Doyon.`.
Verify fn/ln tokens before the LLM call, rehydrated responses, and correct lead
fields. Check an allowlisted community in the channel-wrapper position creates
no new name bindings. Do not log raw transcripts or replacement maps. The image
probe does not establish HTTP/widget context propagation or provider delivery.

Rollback by reverting these parser changes and redeploying the previous Redactor
image through Infra. No schema rollback is required.
