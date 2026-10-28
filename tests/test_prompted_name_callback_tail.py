"""A full-name reply can immediately precede a request for personal follow-up."""

import pytest

from src.allowlist_cache import LocalAllowlistCache
from src.middleware import PIIMiddleware
from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript
from src.types import ScopeContext


PROMPT = "I have your phone number on file. What's your first name so I can get this scheduled for your clients?"


@pytest.mark.parametrize("prompt", [PROMPT, "What's your full name?"])
@pytest.mark.parametrize("separator", [" ", ", ", ". "])
@pytest.mark.parametrize("tail", [
    "please have someone reach out to me",
    "please have someone call me",
    "please ask the team to contact me",
])
def test_full_name_before_callback_request(prompt, separator, tail):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        "Avery Doyon" + separator + tail, PIIVault(), previous_assistant_message=prompt,
    )
    assert result.redacted_text == "<fn_1> <ln_1>" + separator + tail
    assert result.replacements == {"<fn_1>": "Avery", "<ln_1>": "Doyon"}


@pytest.mark.parametrize("prefix", ["Sure, ", "name's "])
def test_callback_reply_keeps_offsets_after_existing_prefix_handling(prefix):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        prefix + "Avery Doyon please have someone reach out to me", PIIVault(),
        previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == prefix + "<fn_1> <ln_1> please have someone reach out to me"


@pytest.mark.parametrize("name", ["Lewis Ranch", "Willow Bend"])
def test_complete_community_phrase_is_protected_by_runtime_allowlist(name):
    text = name + " please have someone call me"
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        text, PIIVault(), previous_assistant_message=PROMPT, non_name_allowlist=[name],
    )
    assert result.redacted_text == text
    assert result.replacements == {}


@pytest.mark.parametrize("text", [
    "Can Someone please have someone reach out to me",
    "General Manager please have someone call me",
    "Floor Plans please have someone call me",
    "North Texas please have someone call me",
    "My clients Avery Doyon please have someone reach out to me",
    "Avery Doyon please have someone reach out to my clients",
    "Avery Doyon please send the floor plans",
    "Avery Doyon please have someone contact my agent",
])
def test_callback_rule_does_not_accept_arbitrary_prose_or_third_party_request(text):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        text, PIIVault(), previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == text
    assert result.replacements == {}


def test_callback_capture_requires_name_request():
    text = "Avery Doyon please have someone reach out to me"
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(text, PIIVault())
    assert result.redacted_text == text


def test_phone_then_full_name_callback_replay():
    result = replay_transcript([
        TranscriptTurn("user", "The one in Wylie. +1 202-555-0143"),
        TranscriptTurn("assistant", PROMPT),
        TranscriptTurn("user", "Avery Doyon please have someone reach out to me"),
    ], engine=PIIEngine(use_presidio=False, use_gliner=False), non_name_allowlist=["Lewis Ranch"])
    assert result.user_turns[-1].redacted_text == "<fn_1> <ln_1> please have someone reach out to me"
    assert result.token_to_value == {"<ph_1>": "+1 202-555-0143", "<fn_1>": "Avery", "<ln_1>": "Doyon"}


def test_client_assistant_cached_communities_reach_real_engine(tmp_path):
    cache = LocalAllowlistCache(tmp_path)
    # A synthetic community deliberately has the same shape as a person's name,
    # so protection here must come from the scoped cache, not geography heuristics.
    cache.refresh(client_id="1005", assistant_id="1005-chat-001", terms=["Avery Doyon"])
    middleware = PIIMiddleware(engine=PIIEngine(use_presidio=False, use_gliner=False), allowlist_cache=cache)
    scope = ScopeContext(thread_id="test-thread", session_id="test-session", visitor_id="test-visitor",
                         client_id="1005", assistant_id="1005-chat-001")
    text = "Avery Doyon please have someone call me"
    result = middleware.process_inbound(scope, text, previous_assistant_message=PROMPT)
    assert result.redacted_text == text
    other_scope = ScopeContext(thread_id="other-thread", session_id="other-session", visitor_id="other-visitor",
                               client_id="1005", assistant_id="1005-chat-002")
    other = middleware.process_inbound(other_scope, text, previous_assistant_message=PROMPT)
    assert other.redacted_text == "<fn_1> <ln_1> please have someone call me"
