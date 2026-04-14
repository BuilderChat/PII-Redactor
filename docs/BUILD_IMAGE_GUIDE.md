# Build Image Guide (Air-Gapped / Offline)

This guide packages PII-Redactor so runtime can stay fully offline.

## Goal

- Download and cache all detector assets at image build time.
- Run with strict offline runtime flags.
- Fail startup if required detectors are missing.

## Required Runtime Env

Set these in your container runtime (or bake as defaults):

```bash
PII_REDACTOR_USE_PRESIDIO=true
PII_REDACTOR_PRESIDIO_MINIMAL_RECOGNIZERS=true
PII_REDACTOR_REQUIRE_PRESIDIO=true

PII_REDACTOR_USE_GLINER=true
PII_REDACTOR_GLINER_ALLOW_REMOTE_DOWNLOAD=false
PII_REDACTOR_REQUIRE_GLINER=true

HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

`PII_REDACTOR_REQUIRE_*` makes startup fail-fast if packaged assets are missing.

## Option A: Build a Dedicated Redactor Image

Use this Dockerfile in the PII-Redactor repo:

```dockerfile
FROM python:3.12-slim

WORKDIR /app

ENV PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/hf-cache \
    TRANSFORMERS_CACHE=/opt/hf-cache

COPY requirements.txt requirements.txt
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && python -m spacy download en_core_web_sm \
    && python -c "from gliner import GLiNER; GLiNER.from_pretrained('urchade/gliner_multi_pii-v1'); print('gliner_ready')"

COPY src ./src
COPY .env.example ./.env.example
COPY README.md ./README.md

ENV PII_REDACTOR_LOAD_DOTENV=false \
    PII_REDACTOR_USE_PRESIDIO=true \
    PII_REDACTOR_PRESIDIO_MINIMAL_RECOGNIZERS=true \
    PII_REDACTOR_REQUIRE_PRESIDIO=true \
    PII_REDACTOR_USE_GLINER=true \
    PII_REDACTOR_GLINER_ALLOW_REMOTE_DOWNLOAD=false \
    PII_REDACTOR_REQUIRE_GLINER=true \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

EXPOSE 8000
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

Build:

```bash
docker build -t pii-redactor:offline .
```

Run:

```bash
docker run --rm -p 8000:8000 \
  -e PII_REDACTOR_API_KEY=change-me \
  -e PII_REDACTOR_REQUIRE_API_KEY=true \
  pii-redactor:offline
```

## Option B: Bake Into an Existing App Image

If your main app image should contain this service, add these steps to your app Dockerfile:

```dockerfile
# Copy redactor code into the app build context first.
COPY PII-Redactor/requirements.txt /opt/pii-redactor/requirements.txt
RUN pip install -r /opt/pii-redactor/requirements.txt \
    && python -m spacy download en_core_web_sm \
    && python -c "from gliner import GLiNER; GLiNER.from_pretrained('urchade/gliner_multi_pii-v1')"
COPY PII-Redactor/src /opt/pii-redactor/src
```

Then run redactor either:

- as a sidecar container in the same deployment (recommended), or
- as an internal FastAPI process bound to localhost if your app process manager supports it.

## Health and Smoke Checks

After startup:

```bash
curl -s http://localhost:8000/health
```

Expected strict fields:

- `"presidio_enabled": true`
- `"gliner_enabled": true`
- `"require_presidio": true`
- `"require_gliner": true`

If either detector is not packaged correctly, startup should fail instead of silently degrading.

## Persistence for Multi-Instance Deployments

For shared rehydration across replicas, set:

```bash
PII_REDACTOR_PERSISTENCE_MODE=internal
PII_REDACTOR_INTERNAL_STORE_IMPL=supabase
PII_REDACTOR_REQUIRE_PERSISTENCE=true
```

And provide:

- `PII_REDACTOR_SUPABASE_URL` (HTTPS project URL)
- `PII_REDACTOR_SUPABASE_SERVICE_ROLE_KEY`
- `PII_REDACTOR_PERSISTENCE_MASTER_KEY`

## Operational Notes

- Build-time internet is expected for dependency/model download.
- Runtime internet is not required (and should be blocked in strict mode).
- Keep API key auth enabled in production (`PII_REDACTOR_REQUIRE_API_KEY=true`).
- Avoid logging request bodies in production to prevent raw PII exposure.
