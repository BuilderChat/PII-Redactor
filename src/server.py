from __future__ import annotations

import hashlib
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, status

from .allowlist_cache import AllowlistSelector, LocalAllowlistCache, extract_allowlist_terms
from .config import get_settings
from .middleware import PIIMiddleware
from .persistence import PersistenceConfigError, build_vault_store
from .schemas import (
    AllowlistRefreshRequest,
    AllowlistRefreshResponse,
    HealthResponse,
    RedactRequest,
    RedactResponse,
    RehydrateRequest,
    RehydrateResponse,
    SessionEndRequest,
    SessionEndResponse,
)

settings = get_settings()
try:
    vault_store, persistence_mode = build_vault_store(settings)
except PersistenceConfigError as exc:
    raise RuntimeError(f"Invalid persistence configuration: {exc}") from exc

allowlist_cache = (
    LocalAllowlistCache(
        settings.allowlist_cache_dir,
        max_terms=settings.allowlist_cache_max_terms,
    )
    if settings.allowlist_cache_enabled
    else None
)
middleware = PIIMiddleware(
    vault_store=vault_store,
    persistence_mode=persistence_mode,
    allowlist_cache=allowlist_cache,
)

detector_status = middleware.detector_status
if settings.require_gliner:
    if not settings.use_gliner:
        raise RuntimeError(
            "Invalid detector configuration: PII_REDACTOR_REQUIRE_GLINER=true "
            "requires PII_REDACTOR_USE_GLINER=true"
        )
    if not bool(detector_status.get("gliner_enabled")):
        detail = str(detector_status.get("gliner_load_error") or "unknown error")
        raise RuntimeError(f"GLiNER required but unavailable at startup: {detail}")
if settings.require_presidio:
    if not settings.use_presidio:
        raise RuntimeError(
            "Invalid detector configuration: PII_REDACTOR_REQUIRE_PRESIDIO=true "
            "requires PII_REDACTOR_USE_PRESIDIO=true"
        )
    if not bool(detector_status.get("presidio_enabled")):
        detail = str(detector_status.get("presidio_load_error") or "unknown error")
        raise RuntimeError(f"Presidio required but unavailable at startup: {detail}")

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


@app.post(
    "/redact",
    response_model=RedactResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(_validate_api_key)],
)
def redact(request: RedactRequest) -> RedactResponse:
    fail_closed = request.fail_closed(settings.fail_closed_default)
    try:
        result = middleware.process_inbound(
            scope=request.to_scope(),
            raw_user_message=request.message,
            new_user=request.new_user,
            previous_assistant_message=request.previous_assistant_message,
            non_name_allowlist=request.non_name_allowlist,
            fail_closed=fail_closed,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redaction service unavailable",
        ) from exc

    replacements = (
        dict(result.replacements)
        if request.include_replacements and settings.allow_raw_replacements
        else None
    )
    return RedactResponse(
        redacted=result.redacted_text,
        active_user_index=result.active_profile,
        replacements=replacements,
    )


@app.post("/rehydrate", response_model=RehydrateResponse, dependencies=[Depends(_validate_api_key)])
def rehydrate(request: RehydrateRequest) -> RehydrateResponse:
    fail_closed = request.fail_closed(settings.fail_closed_default)
    try:
        result = middleware.process_outbound(
            scope=request.to_scope(),
            llm_response=request.message,
            fail_closed=fail_closed,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rehydrate service unavailable",
        ) from exc
    return RehydrateResponse(
        clean=result.clean_text,
        repaired_text=result.repaired_text,
        repaired_placeholders=result.repaired_placeholders,
    )


@app.post("/session/end", response_model=SessionEndResponse, dependencies=[Depends(_validate_api_key)])
def end_session(request: SessionEndRequest) -> SessionEndResponse:
    try:
        ended = middleware.end_session(scope=request.to_scope(), fail_closed=settings.fail_closed_default)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session cleanup unavailable",
        ) from exc
    status_text = "vault_destroyed" if ended else "session_not_found"
    return SessionEndResponse(status=status_text)


@app.post(
    "/allowlist/refresh",
    response_model=AllowlistRefreshResponse,
    dependencies=[Depends(_validate_api_key)],
)
def refresh_allowlist(request: AllowlistRefreshRequest) -> AllowlistRefreshResponse:
    if allowlist_cache is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Allowlist cache is not enabled on this server",
        )

    terms: list[str] = list(request.terms or ())
    if request.payload is not None and request.selectors:
        selectors = [
            AllowlistSelector(selector=item.selector, include=item.include)
            for item in request.selectors
        ]
        try:
            terms.extend(extract_allowlist_terms(request.payload, selectors))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    try:
        result = allowlist_cache.refresh(
            client_id=request.client_id,
            assistant_id=request.assistant_id,
            terms=terms,
            source_version=request.source_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    return AllowlistRefreshResponse(
        status="updated" if result.changed else "unchanged",
        client_id=request.client_id,
        assistant_id=request.assistant_id,
        term_count=result.term_count,
        changed=result.changed,
        content_hash=result.content_hash,
        source_version=result.source_version,
        cache_file=result.cache_file,
    )


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    detector_status = middleware.detector_status
    return HealthResponse(
        status="ok",
        active_sessions=middleware.active_sessions,
        presidio_enabled=bool(detector_status.get("presidio_enabled")),
        gliner_enabled=bool(detector_status.get("gliner_enabled")),
        require_gliner=bool(settings.require_gliner),
        require_presidio=bool(settings.require_presidio),
        name_detection_mode=str(detector_status.get("name_detection_mode", "heuristic")),
        gliner_model=str(detector_status.get("gliner_model", "")),
        persistence_enabled=bool(detector_status.get("persistence_enabled")),
        persistence_mode=str(detector_status.get("persistence_mode", "none")),
        persistence_healthy=bool(detector_status.get("persistence_healthy")),
        persistence_queue_depth=int(detector_status.get("persistence_queue_depth", 0)),
        scope_ttl_seconds=int(detector_status.get("scope_ttl_seconds", 0)),
        max_active_scopes=int(detector_status.get("max_active_scopes", 0)),
        allowlist_cache_enabled=bool(detector_status.get("allowlist_cache_enabled")),
    )
