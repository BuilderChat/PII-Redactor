from __future__ import annotations

import re
from threading import RLock
from typing import Dict

from .config import ENTITY_KEYS


class PIIVault:
    """Session-scoped placeholder vault.

    Policy:
    - Active profile starts at 1.
    - Entity tokens are fixed for the active profile (e.g., <fn_1>, <em_1>).
    - New values for an existing entity token overwrite the previous value.
    - `advance_profile()` moves token namespace to *_2, *_3, etc.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._current_profile = 1
        self._token_to_value: Dict[str, str] = {}
        self._profile_entity_to_token: Dict[int, Dict[str, str]] = {}
        self._value_index: Dict[tuple[str, str], str] = {}

    @property
    def current_profile(self) -> int:
        return self._current_profile

    def advance_profile(self) -> int:
        with self._lock:
            self._current_profile += 1
            return self._current_profile

    def register(self, entity_key: str, value: str) -> str:
        if entity_key not in ENTITY_KEYS:
            raise ValueError(f"Unsupported entity key: {entity_key}")
        if not value:
            raise ValueError("Cannot register empty PII value")

        with self._lock:
            profile_map = self._profile_entity_to_token.setdefault(self._current_profile, {})
            token = profile_map.get(entity_key)
            if token is None:
                token = f"<{entity_key}_{self._current_profile}>"
                profile_map[entity_key] = token

            old_value = self._token_to_value.get(token)
            if old_value:
                self._value_index.pop((entity_key, self._normalize(old_value)), None)

            self._token_to_value[token] = value
            self._value_index[(entity_key, self._normalize(value))] = token
            return token

    def has_token(self, token: str) -> bool:
        return token in self._token_to_value

    def token_for(self, entity_key: str, profile: int | None = None) -> str | None:
        use_profile = self._current_profile if profile is None else profile
        return self._profile_entity_to_token.get(use_profile, {}).get(entity_key)

    def latest_token_for_entity(self, entity_key: str) -> str | None:
        for profile in range(self._current_profile, 0, -1):
            token = self._profile_entity_to_token.get(profile, {}).get(entity_key)
            if token:
                return token
        return None

    def tokens_for_profile(self, profile: int | None = None) -> Dict[str, str]:
        use_profile = self._current_profile if profile is None else profile
        return dict(self._profile_entity_to_token.get(use_profile, {}))

    def get(self, token: str) -> str | None:
        return self._token_to_value.get(token)

    def items(self) -> Dict[str, str]:
        return dict(self._token_to_value)

    def destroy(self) -> None:
        with self._lock:
            self._current_profile = 1
            self._token_to_value.clear()
            self._profile_entity_to_token.clear()
            self._value_index.clear()

    def snapshot(self) -> Dict[str, object]:
        return {
            "current_profile": self._current_profile,
            "token_to_value": dict(self._token_to_value),
            "profile_entity_to_token": {
                str(profile): dict(entity_map)
                for profile, entity_map in self._profile_entity_to_token.items()
            },
        }

    @classmethod
    def from_snapshot(cls, data: Dict[str, object]) -> "PIIVault":
        vault = cls()
        vault._current_profile = int(data.get("current_profile", 1))

        token_to_value = data.get("token_to_value", {})
        if isinstance(token_to_value, dict):
            vault._token_to_value = {str(k): str(v) for k, v in token_to_value.items()}

        profile_entity = data.get("profile_entity_to_token", {})
        if isinstance(profile_entity, dict):
            rebuilt: Dict[int, Dict[str, str]] = {}
            for profile, mapping in profile_entity.items():
                if isinstance(mapping, dict):
                    rebuilt[int(profile)] = {str(k): str(v) for k, v in mapping.items()}
            vault._profile_entity_to_token = rebuilt

        for profile_map in vault._profile_entity_to_token.values():
            for entity_key, token in profile_map.items():
                value = vault._token_to_value.get(token)
                if value:
                    vault._value_index[(entity_key, vault._normalize(value))] = token

        return vault

    @staticmethod
    def _normalize(value: str) -> str:
        return re.sub(r"\s+", " ", value.strip()).lower()
