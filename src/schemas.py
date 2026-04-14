from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .types import ScopeContext


class ScopeRequest(BaseModel):
    thread_id: str = Field(min_length=8, max_length=256, pattern=r"^thread_.+")
    session_id: str = Field(min_length=1, max_length=128)
    visitor_id: str = Field(min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=128)
    assistant_id: str = Field(min_length=1, max_length=128)

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
