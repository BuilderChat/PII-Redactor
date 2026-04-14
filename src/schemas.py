from __future__ import annotations

from typing import Literal
from typing import Any

from pydantic import BaseModel, Field
from pydantic import model_validator

from .types import ScopeContext


def _resolve_assistant_id(client_id: str, assistant_id: str | None) -> str:
    candidate = (assistant_id or "").strip()
    if not candidate:
        candidate = f"{client_id}_chat_001"
    if len(candidate) > 128:
        raise ValueError("assistant_id must be <= 128 characters after defaulting")
    return candidate


class ScopeRequest(BaseModel):
    thread_id: str = Field(min_length=8, max_length=256, pattern=r"^thread_.+")
    session_id: str = Field(min_length=1, max_length=128)
    visitor_id: str = Field(min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=128)
    assistant_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def default_assistant(self) -> "ScopeRequest":
        self.assistant_id = _resolve_assistant_id(self.client_id, self.assistant_id)
        return self

    def to_scope(self) -> ScopeContext:
        return ScopeContext(
            thread_id=self.thread_id,
            session_id=self.session_id,
            visitor_id=self.visitor_id,
            client_id=self.client_id,
            assistant_id=self.assistant_id,
        )


class RedactRequest(ScopeRequest):
    message: str = Field(min_length=1)
    new_user: bool = False
    previous_assistant_message: str | None = None
    non_name_allowlist: list[str] | None = None
    failure_mode: Literal["closed", "open"] | None = None
    include_replacements: bool = False

    def fail_closed(self, default_closed: bool) -> bool:
        if self.failure_mode == "open":
            return False
        if self.failure_mode == "closed":
            return True
        return default_closed


class RehydrateRequest(ScopeRequest):
    message: str = Field(min_length=1)
    failure_mode: Literal["closed", "open"] | None = None

    def fail_closed(self, default_closed: bool) -> bool:
        if self.failure_mode == "open":
            return False
        if self.failure_mode == "closed":
            return True
        return default_closed


class SessionEndRequest(ScopeRequest):
    pass


class AllowlistSelectorRequest(BaseModel):
    selector: str = Field(min_length=1, max_length=256)
    include: Literal["values", "keys", "both"] = "values"


class AllowlistRefreshRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    assistant_id: str | None = Field(default=None, max_length=128)
    payload: Any | None = None
    selectors: list[AllowlistSelectorRequest] | None = None
    terms: list[str] | None = None
    source_version: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_sources(self) -> "AllowlistRefreshRequest":
        self.assistant_id = _resolve_assistant_id(self.client_id, self.assistant_id)
        if self.terms:
            return self
        if self.payload is not None and self.selectors:
            return self
        raise ValueError("Provide either terms or payload+selectors for allowlist refresh")


class AllowlistRefreshResponse(BaseModel):
    status: Literal["updated", "unchanged"]
    client_id: str
    assistant_id: str
    term_count: int
    changed: bool
    content_hash: str
    source_version: str | None = None
    cache_file: str


class RedactResponse(BaseModel):
    redacted: str
    active_user_index: int
    replacements: dict[str, str] | None = None


class RehydrateResponse(BaseModel):
    clean: str
    repaired_text: str
    repaired_placeholders: bool


class SessionEndResponse(BaseModel):
    status: str


class HealthResponse(BaseModel):
    status: str
    active_sessions: int
    presidio_enabled: bool
    gliner_enabled: bool
    name_detection_mode: str
    gliner_model: str
    persistence_enabled: bool
    persistence_mode: str
    persistence_healthy: bool
    persistence_queue_depth: int
    scope_ttl_seconds: int
    max_active_scopes: int
    allowlist_cache_enabled: bool
