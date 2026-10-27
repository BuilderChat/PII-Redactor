"""A surname answer may end before a courtesy sentence and follow-up prose."""

import pytest

from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript


PROMPT = "For just-in-case follow-up, what's your last name?"


@pytest.mark.parametrize("surname", ["doyon", "Doyon", "o’neil", "pérez-garcía"])
@pytest.mark.parametrize("tail", [
    ". with thanks, This is my 6th request",
    "! Thanks, please follow up.",
    ". thank you for your help",
    "? With thanks, I have asked before.",
])
def test_prompted_surname_stops_before_courtesy_and_prose(surname, tail):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        surname + tail, PIIVault(), previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == "<ln_1>" + tail
    assert result.replacements == {"<ln_1>": surname}


@pytest.mark.parametrize("text", [
    "pending. with thanks, This is my 6th request",
    "no. thanks, I would rather not",
    "please. thank you for your help",
    "thanks. with thanks, This is my 6th request",
    "can. thanks, I have a question",
    "pricing. with thanks, I have asked before",
    "with thanks, This is my 6th request",
    "general manager. with thanks, This is my 6th request",
    "doyon with thanks, This is my 6th request",
    "doyon. with a question about closing",
    "doyon. thanksgiving is next month",
])
def test_courtesy_tail_does_not_broaden_arbitrary_reply_capture(text):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        text, PIIVault(), previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == text
    assert result.replacements == {}


@pytest.mark.parametrize("prompt", [None, "What's your role there?", "What's your company name?"])
def test_courtesy_rule_requires_last_name_context(prompt):
    text = "doyon. with thanks, This is my 6th request"
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        text, PIIVault(), previous_assistant_message=prompt,
    )
    assert result.redacted_text == text


def test_courtesy_rule_preserves_runtime_allowlist():
    text = "windsor. with thanks, This is my 6th request"
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        text, PIIVault(), previous_assistant_message=PROMPT, non_name_allowlist=["windsor"],
    )
    assert result.redacted_text == text


def test_courtesy_tail_preserves_contact_detection_and_known_first():
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()
    vault.register("fn", "Avery")
    result = engine.redact(
        "doyon. with thanks, email me at avery@example.com or +1 202-555-0143",
        vault, previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == "<ln_1>. with thanks, email me at <em_1> or <ph_1>"
    assert result.replacements["<ln_1>"] == "doyon"
    assert vault.items()["<fn_1>"] == "Avery"


def test_transcript_audit_replay_protects_surname_after_contact_and_first_name():
    result = replay_transcript([
        TranscriptTurn("user", "avery@example.com"),
        TranscriptTurn("assistant", "What's your first name?"),
        TranscriptTurn("user", "Avery"),
        TranscriptTurn("assistant", PROMPT),
        TranscriptTurn("user", "doyon. with thanks, This is my 6th request"),
    ], engine=PIIEngine(use_presidio=False, use_gliner=False))
    assert result.user_turns[-1].redacted_text == "<ln_1>. with thanks, This is my 6th request"
    assert result.user_turns[-1].replacements == {"<ln_1>": "doyon"}
    assert result.token_to_value == {
        "<em_1>": "avery@example.com", "<fn_1>": "Avery", "<ln_1>": "doyon",
    }
