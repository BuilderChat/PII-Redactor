from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib
import inspect
import json
import os
from threading import RLock
from typing import Protocol
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from .config import Settings
from .types import ScopeContext


class PersistenceConfigError(RuntimeError):
    """Raised when persistence mode/configuration is invalid."""


class VaultStore(Protocol):
    """Persistence contract for vault snapshots."""

    def load(self, scope: ScopeContext) -> dict[str, object] | None:
        ...

    def save(
        self,
        scope: ScopeContext,
        snapshot: dict[str, object],
        *,
        expires_at_epoch: float,
        key_version: str,
    ) -> None:
        ...

    def delete(self, scope: ScopeContext) -> None:
        ...


@dataclass(slots=True)
class StoredSnapshot:
    snapshot: dict[str, object]
    expires_at_epoch: float
    key_version: str


class MemoryVaultStore:
    """Thread-safe in-memory store used for local development and tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._data: dict[str, StoredSnapshot] = {}

    def load(self, scope: ScopeContext) -> dict[str, object] | None:
        with self._lock:
            item = self._data.get(scope.key())
            if item is None:
                return None
            return dict(item.snapshot)

    def save(
        self,
        scope: ScopeContext,
        snapshot: dict[str, object],
        *,
        expires_at_epoch: float,
        key_version: str,
    ) -> None:
        with self._lock:
            self._data[scope.key()] = StoredSnapshot(
                snapshot=dict(snapshot),
                expires_at_epoch=expires_at_epoch,
                key_version=key_version,
            )

    def delete(self, scope: ScopeContext) -> None:
        with self._lock:
            self._data.pop(scope.key(), None)


class _ScopeCipher:
    """Envelope-style symmetric cipher with per-scope key derivation."""

    def __init__(self, master_key: str) -> None:
        if not master_key:
            raise PersistenceConfigError("PII_REDACTOR_PERSISTENCE_MASTER_KEY is required for encrypted persistence")
        self._master_key = master_key

    def _derive_key_bytes(self, scope: ScopeContext, key_version: str) -> bytes:
        material = f"{key_version}:{scope.key()}:{self._master_key}".encode("utf-8")
        return hashlib.sha256(material).digest()

    def encrypt(self, scope: ScopeContext, payload: dict[str, object], *, key_version: str) -> dict[str, str]:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except Exception as exc:  # pragma: no cover - depends on optional runtime dependency
            raise PersistenceConfigError(
                "cryptography package is required for Supabase persistence; install 'cryptography'"
            ) from exc

        plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        key = self._derive_key_bytes(scope, key_version)
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
        return {
            "alg": "AESGCM",
            "nonce_hex": nonce.hex(),
            "ciphertext_hex": ciphertext.hex(),
        }

    def decrypt(self, scope: ScopeContext, encrypted: dict[str, object], *, key_version: str) -> dict[str, object]:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except Exception as exc:  # pragma: no cover - depends on optional runtime dependency
            raise PersistenceConfigError(
                "cryptography package is required for Supabase persistence; install 'cryptography'"
            ) from exc

        nonce_hex = str(encrypted.get("nonce_hex") or "")
        ciphertext_hex = str(encrypted.get("ciphertext_hex") or "")
        if not nonce_hex or not ciphertext_hex:
            raise PersistenceConfigError("Encrypted payload is missing nonce/ciphertext fields")

        nonce = bytes.fromhex(nonce_hex)
        ciphertext = bytes.fromhex(ciphertext_hex)
        key = self._derive_key_bytes(scope, key_version)
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        payload = json.loads(plaintext.decode("utf-8"))
        if not isinstance(payload, dict):
            raise PersistenceConfigError("Decrypted payload is not a JSON object")
        return payload


class SupabaseVaultStore:
    """Supabase-backed encrypted vault snapshot store."""

    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        table: str,
        master_key: str,
    ) -> None:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        except Exception as exc:  # pragma: no cover - depends on optional runtime dependency
            raise PersistenceConfigError(
                "cryptography package is required for Supabase persistence; install requirements.txt"
            ) from exc

        if not supabase_url:
            raise PersistenceConfigError("PII_REDACTOR_SUPABASE_URL is required for internal supabase mode")
        if not service_role_key:
            raise PersistenceConfigError(
                "PII_REDACTOR_SUPABASE_SERVICE_ROLE_KEY is required for internal supabase mode"
            )
        if not table:
            raise PersistenceConfigError("PII_REDACTOR_SUPABASE_TABLE must not be empty")
        self._base_url = supabase_url.rstrip("/")
        self._service_role_key = service_role_key
        self._table = table
        self._cipher = _ScopeCipher(master_key)

    def load(self, scope: ScopeContext) -> dict[str, object] | None:
        params = {
            "select": "payload,key_version,expires_at",
            "scope_key": f"eq.{scope.key()}",
            "limit": "1",
        }
        endpoint = f"{self._rest_url()}?{urlparse.urlencode(params)}"
        response = self._request("GET", endpoint)
        if not isinstance(response, list) or not response:
            return None
        row = response[0]
        if not isinstance(row, dict):
            return None

        expires_at_raw = row.get("expires_at")
        if isinstance(expires_at_raw, str):
            try:
                expires_at_dt = datetime.fromisoformat(expires_at_raw.replace("Z", "+00:00"))
            except ValueError:
                expires_at_dt = None
            if expires_at_dt is not None and expires_at_dt <= datetime.now(tz=timezone.utc):
                return None

        encrypted_payload = row.get("payload")
        if not isinstance(encrypted_payload, dict):
            return None
        key_version = str(row.get("key_version") or "v1")
        return self._cipher.decrypt(scope, encrypted_payload, key_version=key_version)

    def save(
        self,
        scope: ScopeContext,
        snapshot: dict[str, object],
        *,
        expires_at_epoch: float,
        key_version: str,
    ) -> None:
        encrypted_payload = self._cipher.encrypt(scope, snapshot, key_version=key_version)
        expires_at_iso = datetime.fromtimestamp(expires_at_epoch, tz=timezone.utc).isoformat()
        body = [
            {
                "scope_key": scope.key(),
                "thread_id": scope.thread_id,
                "session_id": scope.session_id,
                "visitor_id": scope.visitor_id,
                "client_id": scope.client_id,
                "assistant_id": scope.assistant_id,
                "key_version": key_version,
                "expires_at": expires_at_iso,
                "payload": encrypted_payload,
            }
        ]
        params = {"on_conflict": "scope_key"}
        endpoint = f"{self._rest_url()}?{urlparse.urlencode(params)}"
        self._request(
            "POST",
            endpoint,
            body=body,
            prefer_headers=("resolution=merge-duplicates", "return=minimal"),
        )

    def delete(self, scope: ScopeContext) -> None:
        params = {
            "scope_key": f"eq.{scope.key()}",
        }
        endpoint = f"{self._rest_url()}?{urlparse.urlencode(params)}"
        self._request("DELETE", endpoint, prefer_headers=("return=minimal",))

    def _rest_url(self) -> str:
        encoded_table = urlparse.quote(self._table, safe="")
        return f"{self._base_url}/rest/v1/{encoded_table}"

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: object | None = None,
        prefer_headers: tuple[str, ...] = (),
    ) -> object:
        headers = {
            "apikey": self._service_role_key,
            "Authorization": f"Bearer {self._service_role_key}",
            "Content-Type": "application/json",
        }
        if prefer_headers:
            headers["Prefer"] = ",".join(prefer_headers)

        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

        req = urlrequest.Request(url=url, method=method, headers=headers, data=payload)
        try:
            with urlrequest.urlopen(req, timeout=5) as resp:
                raw = resp.read()
                if not raw:
                    return None
                return json.loads(raw.decode("utf-8"))
        except urlerror.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise PersistenceConfigError(f"Supabase request failed ({exc.code}): {detail}") from exc
        except urlerror.URLError as exc:
            raise PersistenceConfigError(f"Supabase request failed: {exc}") from exc


def build_vault_store(
    settings: Settings,
    *,
    external_store: VaultStore | None = None,
) -> tuple[VaultStore | None, str]:
    mode = (settings.persistence_mode or "none").strip().lower()
    if mode not in {"none", "internal", "external"}:
        raise PersistenceConfigError(
            "PII_REDACTOR_PERSISTENCE_MODE must be one of: none, internal, external"
        )

    if mode == "none":
        if settings.require_persistence:
            raise PersistenceConfigError(
                "PII_REDACTOR_REQUIRE_PERSISTENCE=true is incompatible with PII_REDACTOR_PERSISTENCE_MODE=none"
            )
        return None, "none"

    if mode == "internal":
        impl = (settings.internal_store_impl or "").strip().lower()
        if impl == "memory":
            return MemoryVaultStore(), "internal:memory"
        if impl == "supabase":
            store = SupabaseVaultStore(
                supabase_url=settings.supabase_url,
                service_role_key=settings.supabase_service_role_key,
                table=settings.supabase_table,
                master_key=settings.persistence_master_key,
            )
            return store, "internal:supabase"
        raise PersistenceConfigError(
            "PII_REDACTOR_INTERNAL_STORE_IMPL must be one of: supabase, memory"
        )

    # external mode
    if external_store is not None:
        return external_store, "external:injected"

    factory_path = settings.external_store_factory.strip()
    if not factory_path:
        raise PersistenceConfigError(
            "External persistence mode requires either injected external_store or "
            "PII_REDACTOR_EXTERNAL_STORE_FACTORY=<module>:<callable>"
        )
    if ":" not in factory_path:
        raise PersistenceConfigError(
            "PII_REDACTOR_EXTERNAL_STORE_FACTORY must be in format <module>:<callable>"
        )

    module_name, callable_name = factory_path.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise PersistenceConfigError(
            f"Unable to import external store factory module '{module_name}': {exc}"
        ) from exc

    factory = getattr(module, callable_name, None)
    if not callable(factory):
        raise PersistenceConfigError(
            f"External store factory '{factory_path}' is not callable"
        )

    try:
        signature = inspect.signature(factory)
        if len(signature.parameters) == 0:
            candidate = factory()
        else:
            candidate = factory(settings)
    except Exception as exc:
        raise PersistenceConfigError(
            f"External store factory '{factory_path}' failed: {exc}"
        ) from exc

    if candidate is None:
        raise PersistenceConfigError(
            f"External store factory '{factory_path}' returned None"
        )
    if not all(hasattr(candidate, method) for method in ("load", "save", "delete")):
        raise PersistenceConfigError(
            f"External store factory '{factory_path}' returned unsupported object"
        )
    return candidate, "external:factory"
