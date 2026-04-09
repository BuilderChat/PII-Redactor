from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, status

from .config import get_settings
from .middleware import PIIMiddleware
from .schemas import (
    HealthResponse,
    RedactRequest,
    RedactResponse,
    RehydrateRequest,
    RehydrateResponse,
    SessionEndRequest,
    SessionEndResponse,
)

settings = get_settings()
middleware = PIIMiddleware()
app = FastAPI(title="PII Redactor", version="0.1.0")



def _validate_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if not settings.require_api_key:
        return

    if not settings.api_key and not settings.api_key_sha256:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server is missing API key configuration",
        )

    provided = x_api_key or ""

    if settings.api_key and secrets.compare_digest(provided, settings.api_key):
        return

    if settings.api_key_sha256:
        digest = hashlib.sha256(provided.encode("utf-8")).hexdigest()
        if secrets.compare_digest(digest, settings.api_key_sha256):
            return

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")


@app.post("/redact", response_model=RedactResponse, dependencies=[Depends(_validate_api_key)])
def redact(request: RedactRequest) -> RedactResponse:
    result = middleware.process_inbound(
        scope=request.to_scope(),
        raw_user_message=request.message,
        new_user=request.new_user,
    )
    return RedactResponse(
        redacted=result.redacted_text,
        active_user_index=result.active_profile,
        replacements=result.replacements,
    )


@app.post("/rehydrate", response_model=RehydrateResponse, dependencies=[Depends(_validate_api_key)])
def rehydrate(request: RehydrateRequest) -> RehydrateResponse:
    result = middleware.process_outbound(scope=request.to_scope(), llm_response=request.message)
    return RehydrateResponse(
        clean=result.clean_text,
        repaired_text=result.repaired_text,
        repaired_placeholders=result.repaired_placeholders,
    )


@app.post("/session/end", response_model=SessionEndResponse, dependencies=[Depends(_validate_api_key)])
def end_session(request: SessionEndRequest) -> SessionEndResponse:
    ended = middleware.end_session(scope=request.to_scope())
    status_text = "vault_destroyed" if ended else "session_not_found"
    return SessionEndResponse(status=status_text)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", active_sessions=middleware.active_sessions)
