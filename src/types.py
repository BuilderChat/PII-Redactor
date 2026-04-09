from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScopeContext:
    """Scope key used for strict chat-session isolation."""

    session_id: str
    visitor_id: str
    client_id: str
    assistant_id: str

    def key(self) -> str:
        return f"{self.client_id}:{self.assistant_id}:{self.visitor_id}:{self.session_id}"
