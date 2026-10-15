# Business collection replies and contracted name introductions

Source behavior updated 2026-10-15. DEV/PROD acceptance is not established by
the local results below.

## Detection boundary

`src/business_reply_context.py` identifies the latest requested company, website,
role, or assessment/meeting-subject field from the existing preceding-assistant
message. It recognizes question variations rather than a specific assistant ID;
client 1003 / assistant 1003-chat-001 supplied the motivating examples.

The helper returns bounded character ranges for recognizable business values:

- common job titles in a role reply;
- leading company phrases with a recognized business suffix;
- a leading website URL, optionally introduced by a website label;
- leading assessment topics such as Online Sales, Customer Care, and Lead Follow-Up.

Only newly inferred name spans entirely inside those ranges are discarded.
Email/phone detection, prior scoped name replacements, explicit introductions,
corrections, and names elsewhere in a mixed reply remain active. Explicit pending
name metadata is preserved. A prior first-name acknowledgment does not by itself
make a later business question a personal-name request.

This is not a universal company/person classifier. Unfamiliar business phrases
and ambiguous two-word answers retain existing detection behavior. Known person
values may still be tokenized when repeated inside business text or URLs; the
helper does not erase scoped evidence to improve readability. It does not repair
already-issued incorrect vault bindings or existing CRM records.

## Contracted introductions

`name's Avery`, `name’s Avery`, and their `my name…` variants capture Avery rather
than the label word name. Prompted first/full-name parsing skips the introduction
prefix with its original offsets preserved. A contracted introduction after a
last-name question goes through explicit-introduction detection rather than
assigning a surname token to the label.

Explicit-introduction matching reuses the existing Unicode/joiner-aware name-word
shape, including hyphens and apostrophes. Name-plus-company prose can stop after
an already supplied first/last pair before a following `from …` clause. From is
not globally excluded as a surname or middle name.

Both live redaction and transcript audit use the same canonical engine. No new
API field, tenant-specific branch, migration, environment variable, or external
call is introduced. The helper performs no logging or I/O; existing middleware
and endpoint start/success/failure logging remains in place without raw PII.

## Validation

Local results on 2026-10-15: the complete Redactor suite passed 364 tests,
including 79 new regression cases. Ruff passed on changed Python files, and
source security lint passed with the workspace's existing rule exclusions.
CentralApp full CI was not run because this change does not modify CentralApp.
No server deployment or live provider test was performed.

From the workspace root:

```bash
cd PII-Redactor
myenv/bin/python -m pytest tests/test_business_reply_context.py \
  tests/test_name_false_positive_filters.py tests/test_transcript_replay.py -q
myenv/bin/python -m pytest tests -q
../myenv/bin/python -m ruff check src/business_reply_context.py src/pii_engine.py \
  tests/test_business_reply_context.py
```

The new regressions use synthetic names/contact channels. They cover the two
reported shapes, observed question variations, unsolicited/corrected names,
email/phone protection, mixed replies, website punctuation, accented/joined names,
existing scoped replacements, and a sequential business-collection replay.

## DEV acceptance and rollback

Build/deploy the reviewed PII-Redactor version through the canonical Infra workflow.
CentralApp source/config deployment is not required for this change. On DEV, with
existing provider-send gates intact, use a fresh thread/session and replay:

1. First-name ask → `name's Avery. email is avery@example.com`.
2. Company ask → `Acme Software`.
3. Website ask → `https://acme.example.com`.
4. Role ask → `General Manager`.
5. Assessment-focus ask → `Customer Care`.
6. Volunteered correction → `My last name is Doyon`.

Verify the first-name/email tokens refer to the actual supplied values, steps
2–5 create no new personal-name bindings, and step 6 still issues a surname token.
Also test a mixed role reply with an email or phone and an explicit introduction
during a company question. Inspect tokenized output and permitted metadata;
do not add raw replacement maps or message bodies to logs.

After deploying, this container probe checks the image's engine without pytest,
database access, or provider sends:

```bash
docker exec -i -w /app pii-redactor-dev python - <<'PY'
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
engine = PIIEngine(use_presidio=False, use_gliner=False)
vault = PIIVault()
r = engine.redact("name's Avery. email is avery@example.com", vault,
                  previous_assistant_message="What's your first name?")
assert r.redacted_text == "name's <fn_1>. email is <em_1>"
r = engine.redact('General Manager', vault,
                  previous_assistant_message="What's your role there?")
assert r.redacted_text == 'General Manager'
r = engine.redact('My last name is Doyon', vault,
                  previous_assistant_message="What's your company website?")
assert r.redacted_text == 'My last name is <ln_1>'
print('business_reply_context=passed')
PY
```

This source probe does not establish full HTTP routing or widget acceptance.
Rollback by reverting this source change and redeploying the prior Redactor image
through Infra. No schema/data rollback is needed.
