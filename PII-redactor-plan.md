# PII Redaction Pipeline — Implementation Plan

## Architecture Overview

```
User Input
    │
    ▼
┌─────────────────────────────┐
│  LAYER 1: Regex Patterns    │  ← Emails, phones, SSNs, credit cards
│  (Presidio built-in)        │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  LAYER 2: GLiNER NER        │  ← Names, locations, orgs (zero-shot)
│  (Context-aware detection)  │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  PII VAULT (in-memory map)  │  ← Stores: <first_name_1> → "Jinbad"
│  Session-scoped key-value   │     Stores: <email_1> → "jin@example.com"
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  REDACTED TEXT sent to LLM  │  ← "My name is <first_name_1> <last_name_1>"
│  (Ollama - local model)     │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  LLM RESPONSE with tokens   │  ← "Hello <first_name_1>! I've noted your..."
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│  RE-HYDRATION LAYER         │  ← Swaps placeholders back to real PII
└─────────────┬───────────────┘
              │
              ▼
        Chat Output
   "Hello Jinbad! I've noted your..."
```

---

## Components & Where to Get Them

### 1. Microsoft Presidio (Regex + NER orchestration)

| Detail          | Info                                                        |
|-----------------|-------------------------------------------------------------|
| **GitHub**      | https://github.com/microsoft/presidio                       |
| **License**     | MIT                                                         |
| **Install**     | `pip install presidio-analyzer presidio-anonymizer`         |
| **NLP model**   | `python -m spacy download en_core_web_lg` (~780 MB)        |
| **RAM needed**  | ~500 MB–1 GB                                                |
| **CPU only?**   | Yes                                                         |
| **Docs**        | https://microsoft.github.io/presidio/                       |

Presidio handles the orchestration layer — it combines regex-based recognizers (email, phone, credit card, SSN, etc.) with pluggable NER engines. You'll swap its default spaCy NER with GLiNER for better name detection.

### 2. GLiNER (Zero-Shot NER — the name catcher)

| Detail           | Info                                                                           |
|------------------|--------------------------------------------------------------------------------|
| **GitHub**       | https://github.com/urchade/GLiNER                                              |
| **License**      | Apache 2.0                                                                     |
| **Install**      | `pip install gliner gliner-spacy`                                              |
| **Best model**   | `urchade/gliner_multi_pii-v1` (fine-tuned for PII, 6 languages)               |
| **Alt model**    | `urchade/gliner_medium-v2.1` (general-purpose, Apache 2.0)                     |
| **Model size**   | ~350 MB–800 MB depending on variant                                            |
| **RAM needed**   | ~1–2 GB                                                                        |
| **CPU latency**  | ~75 ms per inference on CPU                                                    |
| **HuggingFace**  | https://huggingface.co/urchade/gliner_multi_pii-v1                             |

**Why GLiNER over default spaCy?** GLiNER is zero-shot — you tell it what to look for ("person", "first name", "last name") and it finds them based on context, even for names it has never seen before. The `gliner_multi_pii-v1` variant is specifically fine-tuned for PII detection.

**Key:** GLiNER plugs directly into spaCy via `gliner-spacy`, which means it integrates seamlessly with Presidio. No custom wiring needed.

### 3. Ollama + Small LLM (Chat model)

| Detail           | Info                                                          |
|------------------|---------------------------------------------------------------|
| **Ollama**       | https://ollama.com — CLI tool to run LLMs locally             |
| **License**      | MIT                                                           |
| **Install**      | `curl -fsSL https://ollama.com/install.sh \| sh`              |
| **API**          | REST API on `localhost:11434` after `ollama serve`            |

**Recommended chat models (pick one):**

| Model                | Size  | RAM Needed | Pull Command                     | Best For                     |
|----------------------|-------|------------|----------------------------------|------------------------------|
| **Phi-4 Mini**       | ~2 GB | 4–6 GB     | `ollama pull phi4-mini`          | Instruction following, fast  |
| **Llama 3.2 3B**     | ~2 GB | 4–6 GB     | `ollama pull llama3.2:3b`        | General quality              |
| **Gemma 3 4B**       | ~3 GB | 6–8 GB     | `ollama pull gemma3`             | Multimodal option            |
| **Qwen 2.5 7B**      | ~4 GB | 8–10 GB    | `ollama pull qwen2.5:7b`        | Best quality (needs more RAM)|
| **Mistral 7B**       | ~4 GB | 8–10 GB    | `ollama pull mistral:7b`         | Strong all-rounder           |

**Recommendation:** Start with **Phi-4 Mini** or **Llama 3.2 3B** — they're small, fast, and follow instructions well enough to understand and preserve placeholder tokens. If you have 16+ GB RAM to spare on the Hetzner box, **Qwen 2.5 7B** or **Mistral 7B** will give noticeably better conversational quality.

---

## Server Requirements (Hetzner)

**Minimum for the full pipeline (CPU-only):**
- **CPU:** 4+ cores (more helps with parallel requests)
- **RAM:** 8 GB minimum (for 3B model + GLiNER + Presidio)
- **RAM:** 16 GB recommended (for 7B model + everything else comfortably)
- **Storage:** 10–15 GB for all models and dependencies
- **GPU:** Not required — everything runs on CPU, GPU just makes it faster
- **OS:** Ubuntu 22.04 or 24.04 LTS

---

## Implementation Plan (for Claude Code / Codex)

### Phase 1: Server Setup & Dependencies

```bash
# 1. Install system dependencies
sudo apt update && sudo apt install -y python3 python3-pip python3-venv curl

# 2. Create project
mkdir -p ~/pii-pipeline && cd ~/pii-pipeline
python3 -m venv venv
source venv/bin/activate

# 3. Install Presidio + GLiNER
pip install presidio-analyzer presidio-anonymizer
pip install gliner gliner-spacy
python -m spacy download en_core_web_sm   # small model (GLiNER replaces the NER)

# 4. Install Ollama
curl -fsSL https://ollama.com/install.sh | sh
ollama pull phi4-mini    # or your chosen model
```

### Phase 2: Build the PII Detection Engine

Create a module that combines Presidio + GLiNER:

```
pii_engine.py
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
- Use **numbered, typed placeholders** (`<first_name_1>`, not `[REDACTED]`) so the LLM knows *what kind* of info was there and can use them naturally in responses.
- The PII Vault is **session-scoped** — each chat session gets its own vault, destroyed when the session ends.
- Placeholders use angle brackets + underscores because they're unlikely to appear in normal text and easy for the LLM to reproduce.

### Phase 3: Configure the LLM System Prompt

The local LLM needs a system prompt that teaches it to work with placeholders:

```
SYSTEM PROMPT (store as Ollama Modelfile or pass via API):
─────────────────────────────────────────────────────────
You are a helpful customer service assistant.

IMPORTANT: User messages contain PII placeholders like <first_name_1>,
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

### Phase 4: Build the Pipeline Orchestrator

```
pipeline.py
├── chat_session class:
│     ├── vault: PIIVault instance (per-session)
│     ├── conversation_history: list of messages
│     │
│     ├── process_user_message(raw_input):
│     │     1. pii_engine.redact(raw_input) → redacted_text + vault updates
│     │     2. append {"role": "user", "content": redacted_text} to history
│     │     3. send history to Ollama API (localhost:11434/api/chat)
│     │     4. receive LLM response (contains placeholders)
│     │     5. pii_engine.rehydrate(response) → final response with real PII
│     │     6. append {"role": "assistant", "content": redacted_response} to history
│     │          (keep history redacted so LLM never accumulates real PII)
│     │     7. return final_response to user
│     │
│     └── on_session_end():
│           destroy vault (PII is gone)
```

**Ollama API call example:**
```python
import requests

response = requests.post("http://localhost:11434/api/chat", json={
    "model": "phi4-mini",
    "messages": conversation_history,
    "stream": False
})
reply = response.json()["message"]["content"]
```

### Phase 5: API / Integration Layer

Expose the pipeline as a REST API or WebSocket for your chat frontend:

```
server.py (FastAPI or Flask)
├── POST /chat/start       → create session, return session_id
├── POST /chat/message      → { session_id, message } → returns response
├── POST /chat/end          → destroy session + vault
└── WebSocket /chat/ws      → real-time chat (optional)
```

### Phase 6: Testing & Hardening

```
tests/
├── test_name_detection.py
│     - Common names: "John Smith" ✓
│     - Unusual names: "Jinbad Profut" ✓
│     - Names after "My name is": contextual detection ✓
│     - Names with titles: "Dr. Xylophone McFadden" ✓
│     - Names at start of sentence ✓
│     - Multiple names in one message ✓
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
│
├── test_llm_placeholder_compliance.py
│     - LLM uses exact placeholder tokens in response ✓
│     - LLM doesn't invent new placeholder formats ✓
│     - LLM doesn't ask for info already in placeholders ✓
│
└── test_vault_lifecycle.py
      - Vault created per session ✓
      - Vault destroyed on session end ✓
      - No PII persists in memory after cleanup ✓
```

---

## Air-Gap / Offline Deployment

Once everything is working on the Hetzner server with internet:

1. **Download all models while online:**
   - GLiNER model: `python -c "from gliner import GLiNER; GLiNER.from_pretrained('urchade/gliner_multi_pii-v1')"`
   - spaCy model: `python -m spacy download en_core_web_sm`
   - Ollama model: `ollama pull phi4-mini`

2. **Package everything:**
   ```bash
   # Python packages
   pip freeze > requirements.txt
   pip download -r requirements.txt -d ./offline-packages/

   # Ollama binary + models
   cp -r ~/.ollama ./ollama-backup/

   # GLiNER model cache
   cp -r ~/.cache/huggingface ./hf-cache-backup/
   ```

3. **On air-gapped server:**
   ```bash
   pip install --no-index --find-links=./offline-packages/ -r requirements.txt
   cp -r ./ollama-backup/ ~/.ollama/
   cp -r ./hf-cache-backup/ ~/.cache/huggingface/
   ```

4. **Block outbound traffic:**
   ```bash
   # Allow only local network
   sudo ufw default deny outgoing
   sudo ufw default deny incoming
   sudo ufw allow from 192.168.0.0/16  # adjust to your network
   sudo ufw enable
   ```

---

## File Structure

```
pii-pipeline/
├── server.py              # FastAPI app — chat endpoints
├── pipeline.py            # Orchestrator — ties everything together
├── pii_engine.py          # Presidio + GLiNER detection & redaction
├── pii_vault.py           # In-memory PII storage (session-scoped)
├── llm_client.py          # Ollama API wrapper
├── config.py              # Model names, thresholds, placeholder format
├── system_prompt.txt      # LLM system prompt (placeholder instructions)
├── tests/
│   ├── test_name_detection.py
│   ├── test_regex_patterns.py
│   ├── test_rehydration.py
│   └── test_vault_lifecycle.py
├── requirements.txt
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
| Ollama                   | https://ollama.com                                     |
| Ollama model library     | https://ollama.com/library                             |
| Phi-4 Mini (Ollama)      | https://ollama.com/library/phi4-mini                   |
| Llama 3.2 3B (Ollama)    | https://ollama.com/library/llama3.2                    |
| Qwen 2.5 7B (Ollama)     | https://ollama.com/library/qwen2.5                     |
| Mistral 7B (Ollama)      | https://ollama.com/library/mistral                     |
| Air-gapped Ollama guide  | https://github.com/khmowais/offline_ollama_guide       |