FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PII_REDACTOR_USE_PRESIDIO=false \
    PII_REDACTOR_REQUIRE_PRESIDIO=false \
    PII_REDACTOR_USE_GLINER=false \
    PII_REDACTOR_REQUIRE_GLINER=false

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r /app/requirements.txt

COPY src /app/src

EXPOSE 8081

CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8081", "--workers", "1"]
