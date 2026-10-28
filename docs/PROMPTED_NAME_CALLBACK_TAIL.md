# Full-name answers followed by a callback request

Source updated 2026-10-28. Server deployment has not been verified.

After a first/full-name request, `Avery Doyon please have someone reach out to me`
now becomes `<fn_1> <ln_1> please have someone reach out to me`. Previously, the
parser rejected the unpunctuated continuation and also the comma form because
separator trimming removed the comma before tail acceptance. A period already
worked. The existing full-name branch now accepts an anchored callback tail:

- `please have/ask someone/the team/a team member`;
- optional `to`;
- `reach out to me`, `call me`, or `contact me`;
- optional final sentence punctuation and whitespace.

Whitespace or separating punctuation precedes the request. The entire tail must
match; this does not accept arbitrary prose or contact requests directed at a
third party. Existing name capitalization rules are unchanged. The branch now
reads the tail from prefix-adjusted working text, preserving name offsets after
acknowledgments and contracted introductions. Only the two name words become
tokens; the callback request is retained verbatim.

Runtime non-name phrases, geography checks and existing per-word filters remain
active. The new callback acceptance also rejects exact known housing phrases and
recognized complete role titles, reusing the existing business-reply helper.
It does not add a global community list or a general person/place classifier.

## Client communities and floor plans

CentralApp's `_ensure_shadow_allowlist_refreshed` in `main.py` fetches the
client/assistant-scoped community tree and floor-plan inventory and posts terms
to Redactor `/allowlist/refresh`. Despite the helper's historical name, primary
redaction calls it too. Refresh is enabled by default through
`CHAT_REDACTION_ALLOWLIST_REFRESH_ENABLED`; refreshes are TTL-cached and failures
are logged. The collector currently walks dictionary keys and string values;
it is broader than only community names.

Redactor's `LocalAllowlistCache` is keyed by client and assistant. Live middleware
and transcript audit both merge this cached list with any request allowlist.
`PII_REDACTOR_ALLOWLIST_CACHE_ENABLED` defaults to true. A known complete community
phrase such as Lewis Ranch is therefore protected when supplied by that scope's
data. Actual live cache contents/refresh success were not inspected. Missing,
disabled or stale cache data is not proof that an arbitrary phrase is protected;
generic geography filters are additional guards, not a replacement for scoped
community data.

No API/config/database/CentralApp edits are required. Live detection and replay
use the canonical engine. The parser adds no I/O or PII logs; existing request,
audit and allowlist refresh start/result/error logs remain in place. Stored leads
and vault bindings are unchanged.

## Validation and DEV acceptance

Local results on 2026-10-28: all 534 Redactor tests passed, including 33 new
callback cases. Changed-file Ruff, source security lint with existing exclusions,
and source/test compilation passed. Tests cover punctuation, first/full prompts,
prefix offsets, request-scoped community phrases, real middleware cache scope
isolation, callback exclusions, and phone-to-name transcript replay.
CentralApp full CI was not run because CentralApp source did not change.
No live database/provider test or deployment was performed.

From the workspace root:

```bash
cd PII-Redactor
myenv/bin/python -m pytest tests -q
../myenv/bin/python -m ruff check src/pii_engine.py tests/test_prompted_name_callback_tail.py
myenv/bin/python -m compileall -q src tests
```

After an operator deploys the reviewed Redactor image through Infra, this offline
image probe checks engine behavior without pytest, database access or sends:

```bash
docker exec -i -w /app pii-redactor-dev python - <<'PY'
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
engine = PIIEngine(use_presidio=False, use_gliner=False)
prompt = "What's your first name so I can get this scheduled for your clients?"
tail = ' please have someone reach out to me'
result = engine.redact('Avery Doyon' + tail, PIIVault(), previous_assistant_message=prompt)
assert result.redacted_text == '<fn_1> <ln_1>' + tail
assert result.replacements == {'<fn_1>': 'Avery', '<ln_1>': 'Doyon'}
result = engine.redact('Lewis Ranch' + tail, PIIVault(), previous_assistant_message=prompt,
                       non_name_allowlist=['Lewis Ranch'])
assert result.redacted_text == 'Lewis Ranch' + tail
print('prompted_name_callback_tail=passed')
PY
```

In a fresh DEV widget thread with provider-send gates intact, supply a synthetic
phone, wait for the first-name request, then supply the synthetic full-name plus
callback reply. Check fn/ln tokens before the LLM call, correct rehydration and
the lead's names. For that same client/assistant, check successful allowlist
refresh metadata and a known community reply without creating name bindings.
Do not log raw replacements or transcripts. The source probe alone does not
establish the HTTP/widget scope, live cache data, or provider delivery.

Rollback by reverting this parser change and redeploying the prior Redactor image
through Infra. No migration or schema rollback is required.
