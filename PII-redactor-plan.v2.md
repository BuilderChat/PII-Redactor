# PII Redaction Pipeline — Implementation Plan (Revised)

## Key Constraint

Server has **7.5 GB total RAM** with ~1.1 GB already used by existing containers.
The PII redactor runs as **middleware** wrapping the existing chat LLM — no second model needed.

---

## Architecture Overview

```
User Input
    │
    ▼
┌─────────────────────────────────────┐
│  PII REDACTOR MIDDLEWARE            │
│                                     │
│  ┌───────────────────────────────┐  │
│  │ LAYER 1: Regex Patterns       │  │  ← Emails, phones, SSNs, credit cards
│  │ (Presidio built-in)           │  │
│  └─────────────┬─────────────────┘  │
│                │                    │
│                ▼                    │
│  ┌───────────────────────────────┐  │
│  │ LAYER 2: GLiNER NER           │  │  ← Names, locations, orgs (zero-shot)
│  │ (Context-aware detection)     │  │
│  └─────────────┬─────────────────┘  │
│                │                    │
│                ▼                    │
│  ┌───────────────────────────────┐  │
│  │ PII VAULT (in-memory map)     │  │  ← <first_name_1> → "Jinbad"
│  │ Session-scoped key-value      │  │     <email_1> → "jin@example.com"
│  └─────────────┬─────────────────┘  │
│                │                    │
│     Redacted text out               │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│  YOUR EXISTING CHAT LLM             │
│  (centralapp-chat or cloud API)     │
│  Sees only: "My name is             │
│   <first_name_1> <last_name_1>"     │
└────────────────┬────────────────────┘
                 │
                 ▼
┌─────────────────────────────────────┐
│  PII REDACTOR MIDDLEWARE (output)   │
│                                     │
│  1. Validate placeholders present   │
│  2. Swap placeholders → real PII    │
│     "Hello <first_name_1>!"         │
│      → "Hello Jinbad!"             │
└────────────────┬────────────────────┘
                 │
                 ▼
           Chat Output
      "Hello Jinbad! I've noted
       your details."
```

**What changed from v1:** No Ollama. No second LLM. The PII redactor is pure
middleware that intercepts messages going to and from your existing chat backend.
This saves ~4–10 GB of RAM.

---

## RAM Budget

| Component                        | RAM        | Notes                                    |
|----------------------------------|------------|------------------------------------------|
| Existing containers              | ~1.1 GB    | Already running                          |
| GLiNER (`gliner_multi_pii-v1`)   | ~1–1.5 GB  | Zero-shot NER for names                  |
| Presidio + spaCy (`en_core_web_sm`) | ~200–300 MB | Small model; GLiNER does heavy NER    |
| Python app (FastAPI + pipeline)  | ~100–200 MB | The middleware orchestrator              |
| OS + system overhead             | ~1.5–2 GB  | Linux, buffers, etc.                     |
| **Total**                        | **~4–5 GB** | Leaves ~2.5 GB headroom                 |

---

## Components & Where to Get Them

### 1. Microsoft Presidio (Regex + NER orchestration)

| Detail          | Info                                                        |
|-----------------|-------------------------------------------------------------|
| **GitHub**      | https://github.com/microsoft/presidio                       |
| **License**     | MIT                                                         |
| **Install**     | `pip install presidio-analyzer presidio-anonymizer`         |
| **NLP model**   | `python -m spacy download en_core_web_sm` (~12 MB)         |
| **RAM needed**  | ~200–300 MB                                                 |
| **Docs**        | https://microsoft.github.io/presidio/                       |

**Note:** We use `en_core_web_sm` (not `en_core_web_lg`) because GLiNER replaces
spaCy's NER entirely. The small spaCy model is only needed for tokenization and
lemmatization that Presidio uses internally for context-aware detection.

### 2. GLiNER (Zero-Shot NER — the name catcher)

| Detail           | Info                                                              |
|------------------|-------------------------------------------------------------------|
| **GitHub**       | https://github.com/urchade/GLiNER                                 |
| **License**      | Apache 2.0                                                        |
| **Install**      | `pip install gliner gliner-spacy`                                 |
| **Best model**   | `urchade/gliner_multi_pii-v1` (fine-tuned for PII, 6 languages)  |
| **Alt model**    | `urchade/gliner_medium-v2.1` (general-purpose, Apache 2.0)       |
| **Model size**   | ~350–800 MB depending on variant                                  |
| **RAM needed**   | ~1–1.5 GB                                                         |
| **CPU latency**  | ~75 ms per inference on CPU                                       |
| **HuggingFace**  | https://huggingface.co/urchade/gliner_multi_pii-v1                |

### 3. Your Existing Chat LLM (no change needed)

The middleware connects to whatever your chat backend already is. Two likely setups:

**If `centralapp-chat` IS the LLM (self-hosted):**
- The middleware intercepts HTTP requests going to its API
- No config changes on the LLM side — it just sees cleaner input

**If `centralapp-chat` calls a cloud API (Claude, GPT, etc.):**
- The middleware sits between your app and the API call
- PII is stripped BEFORE it leaves your server
- Real PII NEVER hits the cloud — this is the privacy guarantee

Either way, the LLM needs a system prompt addition (see Phase 3 below).

---

## Integration Patterns

### Option A: Reverse Proxy (recommended for Docker setups)

The PII middleware runs as its own container and proxies requests to your
existing chat container:

```
User → centralapp-chat → PII-Redactor middleware → LLM backend
                              (new container)
```

Your existing `centralapp-chat` calls the middleware instead of calling the
LLM directly. The middleware redacts, forwards to the LLM, rehydrates, and
returns the response.

### Option B: Library Import

If you control the `centralapp-chat` codebase, import the PII engine
directly as a Python module — no extra container needed:

```python
from pii_redactor import PIIEngine

engine = PIIEngine()

# Before sending to LLM
redacted_text, session_vault = engine.redact(user_message)

# Send redacted_text to your LLM...
llm_response = your_existing_llm_call(redacted_text)

# After receiving LLM response
clean_response = engine.rehydrate(llm_response, session_vault)
```

### Option C: Sidecar Container

Run the PII middleware as a sidecar in your Docker Compose. Your chat app
sends messages to `http://pii-redactor:8000/redact` and
`http://pii-redactor:8000/rehydrate` as part of its existing message flow.

---

## Implementation Plan (for Claude Code / Codex)

### Phase 1: Server Setup & Dependencies

```bash
# 1. Install system dependencies
sudo apt update && sudo apt install -y python3 python3-pip python3-venv

# 2. Create project
mkdir -p ~/PII-Redactor && cd ~/PII-Redactor
python3 -m venv venv
source venv/bin/activate

# 3. Install Presidio + GLiNER
pip install presidio-analyzer presidio-anonymizer
pip install gliner gliner-spacy
python -m spacy download en_core_web_sm

# 4. Install middleware dependencies
pip install fastapi uvicorn requests python-dotenv
```

### Phase 2: Build the PII Detection Engine

```
src/pii_engine.py
├── configure Presidio with GLiNER as the NER backend
├── define entity labels: ["person", "first name", "last name",
│                          "email", "phone number", "address",
│                          "date of birth", "social security number",
│                          "credit card number"]
├── build PII Vault class (dict mapping placeholders → real values)
│     e.g., {"<first_name_1>": "Jinbad", "<last_name_1>": "Profut"}
├── redact() function:
│     input:  "My name is Jinbad Profut and my email is jin@test.com"
│     output: "My name is <first_name_1> <last_name_1> and my email is <email_1>"
│     vault:  {"<first_name_1>": "Jinbad", "<last_name_1>": "Profut",
│              "<email_1>": "jin@test.com"}
└── rehydrate() function:
      input:  "Hello <first_name_1>! I'll contact you at <email_1>."
      output: "Hello Jinbad! I'll contact you at jin@test.com."
```

**Critical design decisions:**
- Use **numbered, typed placeholders** (`<first_name_1>`, not `[REDACTED]`) so
  the LLM knows what kind of info was redacted and can use placeholders naturally.
- The PII Vault is **session-scoped** — each chat session gets its own vault,
  destroyed when the session ends.
- Placeholders use angle brackets + underscores because they're unlikely to
  appear in normal text and easy for the LLM to reproduce.

### Phase 3: Add System Prompt to Your Existing LLM

Wherever your existing chat LLM gets its system prompt, append this:

```
IMPORTANT: User messages may contain PII placeholders like <first_name_1>,
<last_name_1>, <email_1>, <phone_1>, etc. These replace real personal
information for privacy.

Rules:
1. Treat placeholders as if they are the real values. Refer to users
   by their name placeholder naturally (e.g., "Hello <first_name_1>!")
2. NEVER ask users to provide information that is already captured
   in a placeholder.
3. When you need to reference PII in your response, use the EXACT
   placeholder token. Do not modify, abbreviate, or invent new ones.
4. If you need PII the user hasn't provided, ask for it normally —
   the system will redact it before you see it.
5. Do not mention that you are seeing placeholders. Respond as if
   everything is normal.
```

### Phase 4: Build the Middleware

```
src/middleware.py
├── class PIIMiddleware:
│     ├── pii_engine: PIIEngine instance (shared, thread-safe)
│     ├── sessions: dict[session_id → PIIVault]
│     │
│     ├── process_inbound(session_id, raw_user_message):
│     │     1. Get or create vault for session
│     │     2. pii_engine.redact(raw_user_message, vault) → redacted text
│     │     3. Return redacted text (to be sent to LLM)
│     │
│     ├── process_outbound(session_id, llm_response):
│     │     1. Get vault for session
│     │     2. pii_engine.rehydrate(llm_response, vault) → clean response
│     │     3. Return clean response (to be sent to user)
│     │
│     └── end_session(session_id):
│           Destroy vault — PII is gone from memory
```

### Phase 5: Expose as API or Integrate Directly

**Option A — Standalone API (sidecar container):**

```
src/server.py (FastAPI)
├── POST /redact
│     Body: { "session_id": "abc123", "message": "I'm Jinbad Profut" }
│     Returns: { "redacted": "I'm <first_name_1> <last_name_1>" }
│
├── POST /rehydrate
│     Body: { "session_id": "abc123", "message": "Hello <first_name_1>!" }
│     Returns: { "clean": "Hello Jinbad!" }
│
├── POST /session/end
│     Body: { "session_id": "abc123" }
│     Returns: { "status": "vault_destroyed" }
│
└── GET /health
      Returns: { "status": "ok", "gliner_loaded": true }
```

**Option B — Direct integration into centralapp-chat:**

```python
# In your existing chat handler, wrap the LLM call:

from pii_redactor import PIIMiddleware

pii = PIIMiddleware()

async def handle_chat_message(session_id, user_message):
    # Redact before LLM sees it
    redacted = pii.process_inbound(session_id, user_message)

    # Your existing LLM call (unchanged)
    llm_response = await your_llm_call(redacted)

    # Rehydrate before user sees it
    clean = pii.process_outbound(session_id, llm_response)

    return clean
```

### Phase 6: Dockerize (if using Option A)

```dockerfile
# Dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN python -m spacy download en_core_web_sm

COPY src/ ./src/
COPY system_prompt.txt .

# Pre-download GLiNER model at build time so it's baked into the image
RUN python -c "from gliner import GLiNER; GLiNER.from_pretrained('urchade/gliner_multi_pii-v1')"

EXPOSE 8000
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

```yaml
# Add to your existing docker-compose.yml
services:
  pii-redactor:
    build: ./PII-Redactor
    container_name: pii-redactor
    restart: unless-stopped
    ports:
      - "8000:8000"
    mem_limit: 2g
    environment:
      - GLINER_MODEL=urchade/gliner_multi_pii-v1
      - GLINER_THRESHOLD=0.6
    networks:
      - your_existing_network  # same network as centralapp-chat
```

### Phase 7: Testing & Hardening

```
tests/
├── test_name_detection.py
│     - Common names: "John Smith" ✓
│     - Unusual names: "Jinbad Profut" ✓
│     - Names after "My name is": contextual detection ✓
│     - Names with titles: "Dr. Xylophone McFadden" ✓
│     - Names at start of sentence ✓
│     - Multiple names in one message ✓
│     - Non-English names ✓
│
├── test_regex_patterns.py
│     - Email variants ✓
│     - Phone formats (international, with/without country code) ✓
│     - SSN formats ✓
│     - Credit card numbers ✓
│
├── test_rehydration.py
│     - All placeholders correctly swapped back ✓
│     - Unrecognized placeholders left as-is (no crashes) ✓
│     - Partial matches don't trigger false swaps ✓
│     - Nested or adjacent placeholders ✓
│
├── test_middleware_integration.py
│     - Full round-trip: redact → (mock) LLM → rehydrate ✓
│     - Multiple messages in same session accumulate vault ✓
│     - Session isolation (session A can't see session B PII) ✓
│
└── test_vault_lifecycle.py
      - Vault created per session ✓
      - Vault destroyed on session end ✓
      - No PII persists in memory after cleanup ✓
      - Concurrent session safety ✓
```

**Name detection fallback heuristic (Phase 7+):**

If GLiNER misses an unusual name, add a rule-based fallback:

```python
# If the previous assistant message asked for a name
# (detected via keyword match: "your name", "name please", etc.)
# then treat the next 1-4 capitalized words in the user's reply as a name.
#
# "What's your name?"  →  "Jinbad Profut"  →  redact both words as name
```

This catches the exact scenario you described with near-zero compute cost.

---

## File Structure

```
PII-Redactor/
├── src/
│   ├── __init__.py
│   ├── pii_engine.py       # Presidio + GLiNER detection & redaction
│   ├── pii_vault.py        # In-memory PII storage (session-scoped)
│   ├── middleware.py        # Inbound/outbound processing logic
│   ├── server.py            # FastAPI endpoints (if running as sidecar)
│   └── config.py            # Model names, thresholds, placeholder format
├── tests/
│   ├── __init__.py
│   ├── test_name_detection.py
│   ├── test_regex_patterns.py
│   ├── test_rehydration.py
│   ├── test_middleware_integration.py
│   └── test_vault_lifecycle.py
├── docs/
│   └── IMPLEMENTATION_PLAN.md
├── Dockerfile
├── docker-compose.override.yml
├── requirements.txt
├── system_prompt.txt
├── .env.example
├── .gitignore
├── Makefile
└── README.md
```

---

## Key Links Summary

| Component               | URL                                                    |
|--------------------------|--------------------------------------------------------|
| Presidio (GitHub)        | https://github.com/microsoft/presidio                  |
| Presidio (Docs)          | https://microsoft.github.io/presidio/                  |
| GLiNER (GitHub)          | https://github.com/urchade/GLiNER                      |
| GLiNER PII model (HF)   | https://huggingface.co/urchade/gliner_multi_pii-v1     |
| GLiNER spaCy plugin      | `pip install gliner-spacy`                             |

---

## Migration Path

If you later move to a bigger server or want a dedicated local LLM:

1. Add Ollama as another container
2. Change `llm_client.py` to point at `http://ollama:11434`
3. The middleware stays exactly the same — it doesn't care what the LLM is

The PII redaction layer is fully LLM-agnostic by design.
