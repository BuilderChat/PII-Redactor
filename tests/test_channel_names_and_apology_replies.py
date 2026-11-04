import pytest

from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript


@pytest.mark.parametrize("label,contact,token", [
    ("phone", "202-555-0143", "<ph_1>"), ("email", "avery@example.com", "<em_1>"),
])
@pytest.mark.parametrize("separator", ["- ", " - ", ": ", "—"])
@pytest.mark.parametrize("name", ["Avery Doyon", "Élodie O’Neil", "Anne-Marie Doyon"])
def test_channel_label_full_name_matching_contact(label, contact, token, separator, name):
    first, last = name.split()
    text = f"{label}{separator}{name}- {contact}"
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(text, PIIVault())
    assert result.redacted_text == f"{label}{separator}<fn_1> <ln_1>- {token}"
    assert result.replacements == {"<fn_1>": first, "<ln_1>": last, token: contact}


@pytest.mark.parametrize("text", [
    "phone- Avery Doyon- avery@example.com",
    "email- Avery Doyon- 202-555-0143",
    "phone- Have Her- 202-555-0143",
    "phone- Floor Plans- 202-555-0143",
    "phone- General Manager- 202-555-0143",
    "phone- Avery Doyon please ask someone to call 202-555-0143",
    "phone- Avery Doyon", "phone- 202-555-0143",
])
def test_channel_wrapper_does_not_infer_names_from_invalid_shape(text):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(text, PIIVault())
    assert not any(k.startswith(("<fn_", "<ln_")) for k in result.replacements)


def test_channel_name_respects_complete_community_allowlist():
    text = "phone- Avery Doyon- 202-555-0143"
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        text, PIIVault(), non_name_allowlist=["Avery Doyon"],
    )
    assert result.redacted_text == "phone- Avery Doyon- <ph_1>"


@pytest.mark.parametrize("prefix", ["Sorry. ", "sorry, ", "Sorry! "])
@pytest.mark.parametrize("field,entity", [("first", "fn"), ("last", "ln")])
def test_apology_before_prompted_name(prefix, field, entity):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        prefix + "Avery", PIIVault(), previous_assistant_message=f"What's your {field} name?",
    )
    assert result.redacted_text == prefix + f"<{entity}_1>"
    assert result.replacements == {f"<{entity}_1>": "Avery"}


@pytest.mark.parametrize("text", ["Sorry. I forgot", "Sorry. No thanks", "Sorry. Please call me", "Sorry. Pending"])
def test_apology_prose_and_refusals_not_names(text):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        text, PIIVault(), previous_assistant_message="What's your first name?",
    )
    assert result.redacted_text == text
    assert result.replacements == {}


@pytest.mark.parametrize("punctuation", [".", "!", "?", ". "])
def test_surname_punctuation_keeps_last_name_entity(punctuation):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        "Doyon" + punctuation, PIIVault(), previous_assistant_message="What's your last name?",
    )
    assert result.redacted_text == "<ln_1>" + punctuation
    assert result.replacements == {"<ln_1>": "Doyon"}


def test_apology_name_then_surname_replay():
    result = replay_transcript([
        TranscriptTurn("assistant", "What's your first name so the team knows who to send the lot details to?"),
        TranscriptTurn("user", "Sorry. Avery"),
        TranscriptTurn("assistant", "No worries, Avery. What's your last name, just in case?"),
        TranscriptTurn("user", "Doyon."),
    ], engine=PIIEngine(use_presidio=False, use_gliner=False))
    assert result.user_turns[0].redacted_text == "Sorry. <fn_1>"
    assert result.user_turns[1].redacted_text == "<ln_1>."
    assert result.token_to_value == {"<fn_1>": "Avery", "<ln_1>": "Doyon"}


def test_channel_name_replay_then_repeated_names():
    result = replay_transcript([
        TranscriptTurn("assistant", "What's your preferred way to connect—email or phone?"),
        TranscriptTurn("user", "phone- Avery Doyon- 202-555-0143"),
        TranscriptTurn("assistant", "What's your first name?"),
        TranscriptTurn("user", "Avery"),
        TranscriptTurn("assistant", "What's your last name?"),
        TranscriptTurn("user", "Doyon."),
    ], engine=PIIEngine(use_presidio=False, use_gliner=False))
    assert result.user_turns[0].redacted_text == "phone- <fn_1> <ln_1>- <ph_1>"
    assert result.user_turns[1].redacted_text == "<fn_1>"
    assert result.user_turns[2].redacted_text == "<ln_1>."


def test_apology_name_requires_name_prompt_and_respects_allowlist():
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    assert engine.redact("Sorry. Avery", PIIVault(), previous_assistant_message="What's your role?").redacted_text == "Sorry. Avery"
    result = engine.redact("Sorry. Windsor", PIIVault(), previous_assistant_message="What's your first name?",
                           non_name_allowlist=["Windsor"])
    assert result.redacted_text == "Sorry. Windsor"


def test_apology_full_name_has_correct_offsets():
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        "Sorry. Avery Doyon", PIIVault(), previous_assistant_message="What's your full name?",
    )
    assert result.redacted_text == "Sorry. <fn_1> <ln_1>"
