from __future__ import annotations

import re
from threading import RLock
from typing import Dict

from .config import ENTITY_KEYS

TOKEN_RE = re.compile(r"^<(?P<entity>[a-z0-9]+)_(?P<idx>\d+)>$")


class PIIVault:
    """Session-scoped placeholder vault.

    Policy:
    - Active profile starts at 1.
    - Token numbers increment per entity across the scope (e.g., <fn_1>, <fn_2>, ...).
    - Distinct values do not overwrite prior tokens.
    - Re-registering the same normalized value returns the existing token.
    - `advance_profile()` moves token namespace to *_2, *_3, etc.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._current_profile = 1
        self._token_to_value: Dict[str, str] = {}
        self._entity_to_tokens: Dict[str, list[str]] = {}
        self._entity_counters: Dict[str, int] = {}
        self._value_index: Dict[tuple[str, str], str] = {}

    @property
    def current_profile(self) -> int:
        return self._current_profile

    def advance_profile(self) -> int:
        with self._lock:
            self._current_profile += 1
            return self._current_profile

    def register(self, entity_key: str, value: str, *, prefer_latest: bool = False) -> str:
        if entity_key not in ENTITY_KEYS:
            raise ValueError(f"Unsupported entity key: {entity_key}")
        if not value:
            raise ValueError("Cannot register empty PII value")

        with self._lock:
            normalized = self._normalize(entity_key, value)
            existing = self._value_index.get((entity_key, normalized))
            if existing:
                return existing

            if prefer_latest:
                latest = self.latest_token_for_entity(entity_key)
                if latest:
                    old_value = self._token_to_value.get(latest)
                    if old_value:
                        old_norm = self._normalize(entity_key, old_value)
                        self._value_index.pop((entity_key, old_norm), None)
                    self._token_to_value[latest] = value
                    self._value_index[(entity_key, normalized)] = latest
                    return latest

            next_idx = self._entity_counters.get(entity_key, 0) + 1
            token = f"<{entity_key}_{next_idx}>"
            while token in self._token_to_value:
                next_idx += 1
                token = f"<{entity_key}_{next_idx}>"

            self._entity_counters[entity_key] = next_idx
            self._entity_to_tokens.setdefault(entity_key, []).append(token)
            self._token_to_value[token] = value
            self._value_index[(entity_key, normalized)] = token
            return token

    def has_token(self, token: str) -> bool:
        return token in self._token_to_value

    def token_for(self, entity_key: str, profile: int | None = None) -> str | None:
        if entity_key not in ENTITY_KEYS:
            return None
        if profile is None:
            return self.latest_token_for_entity(entity_key)
        token = f"<{entity_key}_{profile}>"
        return token if token in self._token_to_value else None

    def latest_token_for_entity(self, entity_key: str) -> str | None:
        tokens = self._entity_to_tokens.get(entity_key, [])
        return tokens[-1] if tokens else None

    def tokens_for_profile(self, profile: int | None = None) -> Dict[str, str]:
        use_index = self._current_profile if profile is None else profile
        out: Dict[str, str] = {}
        for entity_key in ENTITY_KEYS:
            token = f"<{entity_key}_{use_index}>"
            if token in self._token_to_value:
                out[entity_key] = token
        return out

    def get(self, token: str) -> str | None:
        return self._token_to_value.get(token)

    def items(self) -> Dict[str, str]:
        return dict(self._token_to_value)

    def destroy(self) -> None:
        with self._lock:
            self._current_profile = 1
            self._token_to_value.clear()
            self._entity_to_tokens.clear()
            self._entity_counters.clear()
            self._value_index.clear()

    def snapshot(self) -> Dict[str, object]:
        legacy_profile_map: Dict[str, Dict[str, str]] = {}
        max_counter = max(self._entity_counters.values(), default=0)
        for idx in range(1, max_counter + 1):
            profile_tokens: Dict[str, str] = {}
            for entity_key in ENTITY_KEYS:
                token = f"<{entity_key}_{idx}>"
                if token in self._token_to_value:
                    profile_tokens[entity_key] = token
            if profile_tokens:
                legacy_profile_map[str(idx)] = profile_tokens

        return {
            "current_profile": self._current_profile,
            "token_to_value": dict(self._token_to_value),
            "entity_to_tokens": {entity: list(tokens) for entity, tokens in self._entity_to_tokens.items()},
            "entity_counters": {entity: int(counter) for entity, counter in self._entity_counters.items()},
            "profile_entity_to_token": legacy_profile_map,
        }

    @classmethod
    def from_snapshot(cls, data: Dict[str, object]) -> "PIIVault":
        vault = cls()
        vault._current_profile = int(data.get("current_profile", 1))

        token_to_value = data.get("token_to_value", {})
        if isinstance(token_to_value, dict):
            vault._token_to_value = {str(k): str(v) for k, v in token_to_value.items()}

        entity_to_tokens = data.get("entity_to_tokens", {})
        if isinstance(entity_to_tokens, dict):
            rebuilt_tokens: Dict[str, list[str]] = {}
            for entity, tokens in entity_to_tokens.items():
                if entity not in ENTITY_KEYS or not isinstance(tokens, list):
                    continue
                token_list = [str(token) for token in tokens if isinstance(token, str)]
                if token_list:
                    rebuilt_tokens[entity] = token_list
            vault._entity_to_tokens = rebuilt_tokens

        entity_counters = data.get("entity_counters", {})
        if isinstance(entity_counters, dict):
            rebuilt_counters: Dict[str, int] = {}
            for entity, counter in entity_counters.items():
                if entity not in ENTITY_KEYS:
                    continue
                try:
                    rebuilt_counters[entity] = int(counter)
                except (TypeError, ValueError):
                    continue
            vault._entity_counters = rebuilt_counters

        if not vault._entity_to_tokens:
            # Backward compatibility: recover entity token order from legacy profile map.
            legacy_profile = data.get("profile_entity_to_token", {})
            if isinstance(legacy_profile, dict):
                recovered: Dict[str, list[str]] = {entity: [] for entity in ENTITY_KEYS}
                for _profile, mapping in legacy_profile.items():
                    if not isinstance(mapping, dict):
                        continue
                    for entity, token in mapping.items():
                        if entity in ENTITY_KEYS and isinstance(token, str):
                            recovered[entity].append(token)
                vault._entity_to_tokens = {
                    entity: sorted(
                        set(tokens),
                        key=lambda token: int(TOKEN_RE.match(token).group("idx")) if TOKEN_RE.match(token) else 0,
                    )
                    for entity, tokens in recovered.items()
                    if tokens
                }

        if not vault._entity_to_tokens:
            # Fallback recovery from token dictionary.
            recovered: Dict[str, list[str]] = {entity: [] for entity in ENTITY_KEYS}
            for token in vault._token_to_value:
                match = TOKEN_RE.match(token)
                if not match:
                    continue
                entity = match.group("entity")
                if entity in ENTITY_KEYS:
                    recovered[entity].append(token)
            vault._entity_to_tokens = {
                entity: sorted(
                    set(tokens),
                    key=lambda token: int(TOKEN_RE.match(token).group("idx")) if TOKEN_RE.match(token) else 0,
                )
                for entity, tokens in recovered.items()
                if tokens
            }

        if not vault._entity_counters:
            counters: Dict[str, int] = {}
            for entity, tokens in vault._entity_to_tokens.items():
                max_idx = 0
                for token in tokens:
                    match = TOKEN_RE.match(token)
                    if not match:
                        continue
                    max_idx = max(max_idx, int(match.group("idx")))
                if max_idx > 0:
                    counters[entity] = max_idx
            vault._entity_counters = counters

        for token, value in vault._token_to_value.items():
            match = TOKEN_RE.match(token)
            if not match:
                continue
            entity = match.group("entity")
            if entity in ENTITY_KEYS and value:
                vault._value_index[(entity, vault._normalize(entity, value))] = token

        return vault

    @staticmethod
    def _normalize(entity_key: str, value: str) -> str:
        base = re.sub(r"\s+", " ", value.strip()).lower()
        if entity_key in {"fn", "mn1", "mn2", "ln"}:
            return re.sub(r"[^a-z0-9]+", "", base)
        if entity_key == "ph":
            return re.sub(r"\D+", "", base)
        return base
