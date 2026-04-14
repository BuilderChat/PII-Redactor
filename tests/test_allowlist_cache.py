from __future__ import annotations

import time
from pathlib import Path

from src.allowlist_cache import AllowlistSelector, LocalAllowlistCache, extract_allowlist_terms
from src.middleware import PIIMiddleware
from src.pii_engine import RedactionResult, RehydrationResult
from src.types import ScopeContext


class _FakeEngine:
    def __init__(self) -> None:
        self.runtime_info = {}
        self.last_non_name_allowlist: list[str] | None = None

    def redact(
        self,
        text: str,
        vault,
        previous_assistant_message: str | None = None,
        non_name_allowlist: list[str] | None = None,
    ) -> RedactionResult:
        self.last_non_name_allowlist = list(non_name_allowlist or ())
        return RedactionResult(redacted_text=text, replacements={}, active_profile=vault.current_profile)

    def rehydrate(self, text: str, vault) -> RehydrationResult:
        return RehydrationResult(clean_text=text, repaired_text=text, repaired_placeholders=False)


def test_extract_floor_plan_names_from_arbitrary_payload() -> None:
    payload = {
        "rows": [
            {"id": 1, "name": "Cypress II", "meta": {"display_name": "ignore"}},
            {"id": 2, "name": "Hampton II"},
        ]
    }
    selectors = [AllowlistSelector(selector="**.name", include="values")]
    terms = extract_allowlist_terms(payload, selectors)
    assert "Cypress II" in terms
    assert "Hampton II" in terms
    assert "ignore" not in terms


def test_extract_community_tree_keys() -> None:
    payload = {
        "Windsor": {
            "Old Redwood Village": [],
        },
        "Shadow Hills": {},
    }
    selectors = [AllowlistSelector(selector="**", include="keys")]
    terms = extract_allowlist_terms(payload, selectors)
    assert "Windsor" in terms
    assert "Old Redwood Village" in terms
    assert "Shadow Hills" in terms


def test_cache_writes_only_when_content_changes(tmp_path) -> None:
    cache = LocalAllowlistCache(str(tmp_path))
    first = cache.refresh(client_id="c1", assistant_id="a1", terms=["Windsor", "Cypress II"])
    assert first.changed is True
    cache_file = Path(first.cache_file)
    first_mtime_ns = int(cache_file.stat().st_mtime_ns)

    second = cache.refresh(client_id="c1", assistant_id="a1", terms=["cypress ii", "windsor"])
    assert second.changed is False
    second_mtime_ns = int(cache_file.stat().st_mtime_ns)
    assert second_mtime_ns == first_mtime_ns

    time.sleep(0.01)
    third = cache.refresh(client_id="c1", assistant_id="a1", terms=["Windsor", "Cypress II", "Denton"])
    assert third.changed is True
    third_mtime_ns = int(cache_file.stat().st_mtime_ns)
    assert third_mtime_ns > second_mtime_ns


def test_middleware_merges_cached_allowlist_with_request_allowlist(tmp_path) -> None:
    cache = LocalAllowlistCache(str(tmp_path))
    cache.refresh(client_id="c1", assistant_id="a1", terms=["Windsor"])

    engine = _FakeEngine()
    middleware = PIIMiddleware(engine=engine, allowlist_cache=cache)
    scope = ScopeContext(
        thread_id="thread_1",
        session_id="s1",
        visitor_id="v1",
        client_id="c1",
        assistant_id="a1",
    )
    middleware.process_inbound(scope, "hello", non_name_allowlist=["Shadow Hills"])
    assert engine.last_non_name_allowlist is not None
    assert "Windsor" in engine.last_non_name_allowlist or "windsor" in engine.last_non_name_allowlist
    assert "Shadow Hills" in engine.last_non_name_allowlist
