from __future__ import annotations

from typing import Literal
from typing import Any

from pydantic import BaseModel, Field
from pydantic import model_validator

from .config import AUDIT_MAX_CHARACTERS_HARD_LIMIT, AUDIT_MAX_TURNS_HARD_LIMIT, ENTITY_KEYS
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
    pending_name_fields: list[Literal["first_name", "last_name"]] = Field(
        default_factory=list,
        max_length=2,
    )
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


class AuditTranscriptTurn(BaseModel):
    role: Literal["assistant", "agent", "user"]
    content: str = Field(min_length=1, max_length=AUDIT_MAX_CHARACTERS_HARD_LIMIT)
    pending_name_fields: list[Literal["first_name", "last_name"]] = Field(
        default_factory=list,
        max_length=2,
    )


class AuditTranscriptRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    assistant_id: str | None = Field(default=None, max_length=128)
    turns: list[AuditTranscriptTurn] = Field(min_length=1, max_length=AUDIT_MAX_TURNS_HARD_LIMIT)
    non_name_allowlist: list[str] | None = Field(default=None, max_length=5_000)

    @model_validator(mode="after")
    def default_assistant(self) -> "AuditTranscriptRequest":
        self.assistant_id = _resolve_assistant_id(self.client_id, self.assistant_id)
        return self


class AuditReplacementEvidence(BaseModel):
    token: str
    entity: Literal["fn", "mn1", "mn2", "ln", "em", "ph"]
    value: str


class AuditUserTurnResponse(BaseModel):
    turn_index: int
    redacted: str
    evidence: list[AuditReplacementEvidence]


class AuditTranscriptResponse(BaseModel):
    user_turns: list[AuditUserTurnResponse]


def replacement_entity(token: str) -> str:
    entity = token.removeprefix("<").split("_", 1)[0]
    if entity not in ENTITY_KEYS:
        raise ValueError(f"Unsupported replacement token: {token}")
    return entity


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
    redact_active: int = 0
    rehydrate_active: int = 0
    redact_max_concurrency: int = 0
    rehydrate_max_concurrency: int = 0
    redact_saturated_count: int = 0
    rehydrate_saturated_count: int = 0
    audit_active: int = 0
    audit_max_concurrency: int = 0
    audit_saturated_count: int = 0
    audit_timeout_count: int = 0
    presidio_enabled: bool
    gliner_enabled: bool
    require_gliner: bool
    require_presidio: bool
    name_detection_mode: str
    gliner_model: str
    persistence_enabled: bool
    persistence_mode: str
    persistence_status: str
    persistence_state: str
    persistence_block_on_error: bool
    persistence_healthy: bool
    persistence_worker_alive: bool = True
    persistence_worker_restart_count: int = 0
    persistence_last_worker_restart_at: str | None = None
    persistence_last_error_type: str | None = None
    persistence_last_error_category: str | None = None
    persistence_last_error_status_code: int | None = None
    persistence_last_error_operation: str | None = None
    persistence_last_error_at: str | None = None
    persistence_last_success_at: str | None = None
    persistence_unhealthy_since: str | None = None
    persistence_consecutive_failures: int = 0
    persistence_recovery_attempts: int = 0
    persistence_last_recovery_attempt_at: str | None = None
    persistence_next_recovery_at: str | None = None
    persistence_recovery_cooldown_seconds: int = 0
    persistence_queue_depth: int
    persistence_queue_max: int = 0
    persistence_blocking_requests: int = 0
    performance_metrics: dict[str, int | float] = Field(default_factory=dict)
    scope_ttl_seconds: int
    max_active_scopes: int
    allowlist_cache_enabled: bool
    commit: str = "unknown"
