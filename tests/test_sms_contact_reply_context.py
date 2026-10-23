"""Synthetic SMS replies use the same canonical engine as chat and audit."""

import pytest

from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript


@pytest.mark.parametrize("name", ["James", "Li", "Élodie", "Jean-Luc", "D’Arcy"])
@pytest.mark.parametrize("layout", ["name", "name_email", "email_name"])
def test_prompted_single_name_and_email_preserve_the_complete_name(name, layout):
    body = {"name": name, "name_email": name + "\nexample@example.com",
            "email_name": "example@example.com\n" + name}[layout]
    engine = PIIEngine(use_gliner=False, use_presidio=False)
    result = engine.redact(body, PIIVault(), previous_assistant_message="Can I get your first name?",
                           pending_name_fields=["first_name"])
    assert result.replacements["<fn_1>"] == name
    assert name not in result.redacted_text
    assert not any(key.startswith("<fn_") and key != "<fn_1>" for key in result.replacements)
    if layout != "name":
        assert result.replacements["<em_1>"] == "example@example.com"


@pytest.mark.parametrize("word", ["Thanks", "Yes", "No", "Cancel", "Stop", "Help", "Unsubscribe", "What time?"])
@pytest.mark.parametrize("layout", ["alone", "before_email", "after_email"])
def test_acknowledgments_commands_and_questions_do_not_become_names(word, layout):
    body = {"alone": word, "before_email": word + "\nexample@example.com",
            "after_email": "example@example.com\n" + word}[layout]
    result = PIIEngine(use_gliner=False, use_presidio=False).redact(
        body, PIIVault(), previous_assistant_message="Can I get your first name?",
        pending_name_fields=["first_name"],
    )
    assert not any(key.startswith(("<fn_", "<ln_")) for key in result.replacements)
    assert word in result.redacted_text
    if layout != "alone":
        assert "<em_1>" in result.redacted_text


def test_contact_before_single_name_requires_name_context_and_respects_allowlist():
    engine = PIIEngine(use_gliner=False, use_presidio=False)
    body = "example@example.com\nAvery"
    assert engine.redact(body, PIIVault()).redacted_text == "<em_1>\nAvery"
    result = engine.redact(body, PIIVault(), previous_assistant_message="What's your first name?",
                           non_name_allowlist=["Avery"])
    assert result.redacted_text == "<em_1>\nAvery"


def test_sms_context_replay_and_live_engine_agree_without_voice_transcript():
    engine = PIIEngine(use_gliner=False, use_presidio=False)
    prompt = "Can I get your first name and email for your meeting?"
    body = "example@example.com\nJames"
    result = replay_transcript([
        TranscriptTurn("assistant", prompt),
        TranscriptTurn("user", body, pending_name_fields=("first_name",)),
    ], engine=engine)
    assert result.user_turns[0].redacted_text == "<em_1>\n<fn_1>"


@pytest.mark.parametrize("body,expected", [
    ("Avery Doyon\nexample@example.com", "<fn_1> <ln_1>\n<em_1>"),
    ("example@example.com\nAvery Doyon", "<em_1>\n<fn_1> <ln_1>"),
])
def test_full_name_collection_protects_both_names_in_either_contact_order(body, expected):
    result = PIIEngine(use_gliner=False, use_presidio=False).redact(
        body, PIIVault(), previous_assistant_message="What's your first and last name?",
    )
    assert result.redacted_text == expected


def test_last_name_request_does_not_assign_first_name_to_single_contact_tail():
    result = PIIEngine(use_gliner=False, use_presidio=False).redact(
        "example@example.com\nDoyon", PIIVault(), previous_assistant_message="What's your last name?",
    )
    assert result.redacted_text == "<em_1>\n<ln_1>"


def test_name_detection_does_not_consume_an_arbitrary_contact_tail_sentence():
    result = PIIEngine(use_gliner=False, use_presidio=False).redact(
        "example@example.com\nJames will call tomorrow", PIIVault(),
        previous_assistant_message="What's your first name?",
    )
    assert result.redacted_text == "<em_1>\nJames will call tomorrow"
