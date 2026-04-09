from __future__ import annotations

from pydantic import BaseModel, Field

from .types import ScopeContext


class ScopeRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    visitor_id: str = Field(min_length=1, max_length=128)
    client_id: str = Field(min_length=1, max_length=128)
    assistant_id: str = Field(min_length=1, max_length=128)

    def to_scope(self) -> ScopeContext:
        return ScopeContext(
            session_id=self.session_id,
            visitor_id=self.visitor_id,
            client_id=self.client_id,
            assistant_id=self.assistant_id,
        )


class RedactRequest(ScopeRequest):
    message: str = Field(min_length=1)
    new_user: bool = False


class RehydrateRequest(ScopeRequest):
    message: str = Field(min_length=1)


class SessionEndRequest(ScopeRequest):
    pass


class RedactResponse(BaseModel):
    redacted: str
    active_user_index: int
    replacements: dict[str, str]


class RehydrateResponse(BaseModel):
    clean: str
    repaired_text: str
    repaired_placeholders: bool


class SessionEndResponse(BaseModel):
    status: str


class HealthResponse(BaseModel):
    status: str
    active_sessions: int
