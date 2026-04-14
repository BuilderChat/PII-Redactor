from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import RLock
import time
from typing import Any


@dataclass(frozen=True, slots=True)
class AllowlistSelector:
    selector: str
    include: str = "values"  # values | keys | both


@dataclass(frozen=True, slots=True)
class AllowlistRefreshResult:
    changed: bool
    term_count: int
    content_hash: str
    cache_file: str
    source_version: str | None
    updated_at_epoch: float


def _normalize_text_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9'\-\s]", " ", value).strip().lower())


def _tokenize_selector(selector: str) -> list[str | int]:
    text = selector.strip()
    if not text:
        raise ValueError("Selector must not be empty")
    if text.startswith("$"):
        text = text[1:]
    text = text.lstrip(".")
    if not text:
        return []

    tokens: list[str | int] = []
    buf: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == ".":
            if buf:
                token = "".join(buf).strip()
                if token:
                    tokens.append(token)
                buf.clear()
            i += 1
            continue
        if ch == "[":
            if buf:
                token = "".join(buf).strip()
                if token:
                    tokens.append(token)
                buf.clear()
            end = text.find("]", i + 1)
            if end < 0:
                raise ValueError(f"Invalid selector '{selector}': missing closing ']'")
            content = text[i + 1 : end].strip()
            if content in {"", "*"}:
                tokens.append("*")
            elif content.isdigit():
                tokens.append(int(content))
            else:
                tokens.append(content.strip("'\""))
            i = end + 1
            continue
        buf.append(ch)
        i += 1

    if buf:
        token = "".join(buf).strip()
        if token:
            tokens.append(token)
    return tokens


def _descendants_including_self(node: Any) -> list[Any]:
    out: list[Any] = [node]
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for value in current.values():
                out.append(value)
                stack.append(value)
        elif isinstance(current, list):
            for value in current:
                out.append(value)
                stack.append(value)
    return out


def _select_nodes(payload: Any, selector: str) -> list[Any]:
    nodes: list[Any] = [payload]
    for token in _tokenize_selector(selector):
        expanded: list[Any] = []
        if token == "**":
            for node in nodes:
                expanded.extend(_descendants_including_self(node))
            nodes = expanded
            continue
        if token == "*":
            for node in nodes:
                if isinstance(node, dict):
                    expanded.extend(node.values())
                elif isinstance(node, list):
                    expanded.extend(node)
            nodes = expanded
            continue
        if isinstance(token, int):
            for node in nodes:
                if isinstance(node, list) and 0 <= token < len(node):
                    expanded.append(node[token])
            nodes = expanded
            continue

        for node in nodes:
            if isinstance(node, dict) and token in node:
                expanded.append(node[token])
        nodes = expanded
    return nodes


def _collect_string_values(node: Any) -> list[str]:
    out: list[str] = []
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, str):
            out.append(current)
            continue
        if isinstance(current, dict):
            stack.extend(current.values())
            continue
        if isinstance(current, list):
            stack.extend(current)
    return out


def _collect_string_keys(node: Any) -> list[str]:
    out: list[str] = []
    stack: list[Any] = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if isinstance(key, str):
                    out.append(key)
                stack.append(value)
            continue
        if isinstance(current, list):
            stack.extend(current)
    return out


def extract_allowlist_terms(payload: Any, selectors: list[AllowlistSelector]) -> list[str]:
    terms: list[str] = []
    for selector in selectors:
        include = selector.include.lower()
        if include not in {"values", "keys", "both"}:
            raise ValueError(f"Invalid selector include='{selector.include}'. Use values|keys|both")
        nodes = _select_nodes(payload, selector.selector)
        for node in nodes:
            if include in {"values", "both"}:
                terms.extend(_collect_string_values(node))
            if include in {"keys", "both"}:
                terms.extend(_collect_string_keys(node))
    return terms


class LocalAllowlistCache:
    def __init__(self, cache_dir: str, *, max_terms: int = 50000) -> None:
        self._root = Path(cache_dir).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_terms = max(1, int(max_terms))
        self._lock = RLock()
        self._memory: dict[str, list[str]] = {}

    @staticmethod
    def _scope_key(client_id: str, assistant_id: str) -> str:
        return f"{client_id}:{assistant_id}"

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
        return slug[:64] or "unknown"

    def _cache_path(self, client_id: str, assistant_id: str) -> Path:
        scope_key = self._scope_key(client_id, assistant_id)
        digest = hashlib.sha256(scope_key.encode("utf-8")).hexdigest()[:16]
        filename = f"{self._slug(client_id)}__{self._slug(assistant_id)}__{digest}.json"
        return self._root / filename

    def _normalize_terms(self, terms: list[str] | None) -> list[str]:
        deduped: set[str] = set()
        for term in terms or ():
            normalized = _normalize_text_phrase(str(term))
            if normalized:
                deduped.add(normalized)
        normalized_terms = sorted(deduped)
        if len(normalized_terms) > self._max_terms:
            raise ValueError(
                f"Extracted term count {len(normalized_terms)} exceeds allowlist max_terms={self._max_terms}"
            )
        return normalized_terms

    @staticmethod
    def _content_hash(terms: list[str]) -> str:
        material = "\n".join(terms).encode("utf-8")
        return hashlib.sha256(material).hexdigest()

    def get(self, client_id: str, assistant_id: str) -> list[str]:
        key = self._scope_key(client_id, assistant_id)
        with self._lock:
            cached = self._memory.get(key)
            if cached is not None:
                return list(cached)

        path = self._cache_path(client_id, assistant_id)
        if not path.exists():
            return []

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        terms = payload.get("terms")
        if not isinstance(terms, list):
            return []
        normalized = self._normalize_terms([str(item) for item in terms if isinstance(item, str)])
        with self._lock:
            self._memory[key] = normalized
        return list(normalized)

    def refresh(
        self,
        *,
        client_id: str,
        assistant_id: str,
        terms: list[str],
        source_version: str | None = None,
    ) -> AllowlistRefreshResult:
        normalized = self._normalize_terms(terms)
        content_hash = self._content_hash(normalized)
        key = self._scope_key(client_id, assistant_id)
        path = self._cache_path(client_id, assistant_id)

        existing_hash = ""
        if path.exists():
            try:
                existing_payload = json.loads(path.read_text(encoding="utf-8"))
                existing_hash = str(existing_payload.get("content_hash") or "")
            except Exception:
                existing_hash = ""

        changed = existing_hash != content_hash
        now = time.time()

        if changed:
            serialized = {
                "client_id": client_id,
                "assistant_id": assistant_id,
                "source_version": source_version or "",
                "term_count": len(normalized),
                "content_hash": content_hash,
                "updated_at_epoch": now,
                "terms": normalized,
            }
            with NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=str(self._root),
                prefix=".allowlist_",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                json.dump(serialized, tmp, ensure_ascii=False, separators=(",", ":"))
                tmp_path = Path(tmp.name)
            tmp_path.replace(path)

        with self._lock:
            self._memory[key] = normalized

        return AllowlistRefreshResult(
            changed=changed,
            term_count=len(normalized),
            content_hash=content_hash,
            cache_file=str(path),
            source_version=source_version,
            updated_at_epoch=now,
        )
