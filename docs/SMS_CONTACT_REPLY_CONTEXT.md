# SMS contact-reply context

Source: 2026-10-23. Not deployed or live-accepted by this work.

The existing `/redact` API already accepts `previous_assistant_message` and
canonical `pending_name_fields`. CentralApp previously sent neither useful
question context nor pending-name hints for live inbound SMS. Its companion
change now supplies a scoped delivered SMS question and a missing prompted-name
hint; no voice transcript is needed. Test Zone has its own context adapter and
does not prove the live SMS adapter works.

## Canonical parser changes

- When an actual first/full/last-name request applies, a capitalized lone name
  after an email or phone at the beginning of a reply uses existing prompted-name
  parsing and offsets. For example, an email followed by a newline and James can
  yield em and fn tokens, just as name-before-email already could.
- The new path accepts only a single name word plus optional punctuation, not
  arbitrary trailing prose or a leftover word without name context. Runtime
  allowlists, blocked words, and existing name-request semantics still apply.
- An unspaced hyphenated name such as Jean-Luc stays one name value. Existing
  spaced aliases and explicit short-for phrases remain separate alias evidence.
- Cancel, Stop, Start, Help and Unsubscribe are excluded as inferred name words;
  the affected adjacent-contact name paths reject question starters such as
  What. This does not replace CentralApp's provider opt-out controls.

Names remain heuristic rather than guaranteed classification. This path does not
newly infer lowercase single names after a contact channel, names in arbitrary
prose, or a particular compound-first-name interpretation of an ambiguous full
name. Existing explicit introductions and full-name paths remain available.
Pending-name hints retain their existing narrow whole-word behavior; a hint by
itself does not enable combined-contact parsing. The later email-first collection
slice must align its durable expectations and test unsolicited combined replies
after an email-only question. No conversational LLM capture/recovery, model
choice, new detector, persistence mutation, schema, API field, or external call
is added here. GLiNER is not enabled.

## Offline verification

The complete Redactor suite passed **468 tests**, including 45 new synthetic
SMS/context cases. Coverage includes short/accented/joined names, both contact
orders, acknowledgment/command/question negatives, runtime allowlists, and
transcript replay parity. No database/provider calls were used. A separate
in-memory seven-case check passed CentralApp's real external-adapter payload
through this engine using mocked HTTP; it is not deployed HTTP acceptance.

From the workspace root:

```bash
cd PII-Redactor
PII_REDACTOR_LOAD_DOTENV=false PYTHON_DOTENV_DISABLED=1 \
  BUILDERCHAT_CI_OFFLINE=1 PYTHONPATH=../CentralApp/scripts/ci_guard:. \
  myenv/bin/python -m pytest tests -q
../myenv/bin/python -m ruff check src/pii_engine.py tests/test_sms_contact_reply_context.py
```

## Deployment and rollback

Both repositories need review/commit. Deploy the Redactor parser first and
CentralApp's context forwarding second, through the canonical Infra workflow.
The optional API fields already exist, so old/new service combinations remain
wire-compatible, although mixed versions do not establish the full fix.
No migration, vault clearing, historical transcript rewrite or contact repair
is required. See the ordered commands, connection budget and controlled DEV
acceptance in [SMS operations](../../CentralApp/READMEs/SMS_VOICE_OPERATIONS.md#context-aware-sms-redaction-2026-10-23-source-live-acceptance-pending).

After approved deployment, this container probe uses only synthetic data and
the heuristic engine; it needs no pytest, network, database, or provider access:

```bash
docker exec -i -w /app pii-redactor-dev python - <<'PY'
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
engine = PIIEngine(use_gliner=False, use_presidio=False)
for name in ('James', 'Jean-Luc', 'Élodie'):
    result = engine.redact('example@example.com\n' + name, PIIVault(),
                           previous_assistant_message="What's your first name?")
    assert result.redacted_text == '<em_1>\n<fn_1>'
    assert result.replacements['<fn_1>'] == name
for word in ('Thanks', 'Cancel', 'Yes', 'What time?'):
    result = engine.redact(word, PIIVault(),
                           previous_assistant_message="What's your first name?")
    assert result.redacted_text == word
print('sms_contact_reply_context=passed')
PY
```

The probe cannot establish CentralApp context selection or end-to-end collection.
Rollback each application through Infra to its reviewed prior image/source,
normally CentralApp first to remove the new read. Existing token hydration remains
compatible. Do not reset vaults or repair old contact records automatically.
