from __future__ import annotations

from threading import RLock

from .pii_engine import PIIEngine, RedactionResult, RehydrationResult
from .pii_vault import PIIVault
from .types import ScopeContext


class PIIMiddleware:
    """Session-aware orchestrator for inbound/outbound text processing."""

    def __init__(self, engine: PIIEngine | None = None) -> None:
        self.engine = engine or PIIEngine()
        self._lock = RLock()
        self._vaults: dict[str, PIIVault] = {}

    @property
    def active_sessions(self) -> int:
        with self._lock:
            return len(self._vaults)

    @property
    def detector_status(self) -> dict[str, object]:
        return dict(self.engine.runtime_info)

    def process_inbound(self, scope: ScopeContext, raw_user_message: str, new_user: bool = False) -> RedactionResult:
        vault = self._get_or_create_vault(scope)
        if new_user:
            vault.advance_profile()
        return self.engine.redact(raw_user_message, vault)

    def process_outbound(self, scope: ScopeContext, llm_response: str) -> RehydrationResult:
        vault = self._get_vault(scope)
        if vault is None:
            return RehydrationResult(
                clean_text=llm_response,
                repaired_text=llm_response,
                repaired_placeholders=False,
            )
        return self.engine.rehydrate(llm_response, vault)

    def end_session(self, scope: ScopeContext) -> bool:
        key = scope.key()
        with self._lock:
            vault = self._vaults.pop(key, None)
        if vault is None:
            return False
        vault.destroy()
        return True

    def _get_or_create_vault(self, scope: ScopeContext) -> PIIVault:
        key = scope.key()
        with self._lock:
            if key not in self._vaults:
                self._vaults[key] = PIIVault()
            return self._vaults[key]

    def _get_vault(self, scope: ScopeContext) -> PIIVault | None:
        with self._lock:
            return self._vaults.get(scope.key())
