import pytest

from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript


PROMPT = "Is that email correct? And what's your last name, in case they need it?"


@pytest.mark.parametrize("prefix", ["correct email ", "email is correct, ", "the email is right. "])
@pytest.mark.parametrize("label", ["last name ", "my last name ", "surname ", "family name "])
@pytest.mark.parametrize("name", ["doyon", "O’Neil", "pérez-garcía"])
def test_confirmation_then_labelled_surname(prefix, label, name):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        prefix + label + name, PIIVault(), previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == prefix + label + "<ln_1>"
    assert result.replacements == {"<ln_1>": name}


@pytest.mark.parametrize("text", [
    "correct email last name please", "correct email last name pending",
    "correct email last name unknown", "correct email last name not provided",
    "correct email last name optional", "correct email last name doyon is my agent",
    "please correct email last name field", "correct email last name?",
    "correct email", "the last name field is missing",
])
def test_confirmation_rule_rejects_prose_and_missing_values(text):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(text, PIIVault(), previous_assistant_message=PROMPT)
    assert result.redacted_text == text
    assert result.replacements == {}


def test_confirmation_surname_allowlist():
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        "correct email last name windsor", PIIVault(), previous_assistant_message=PROMPT,
        non_name_allowlist=["windsor"],
    )
    assert result.redacted_text == "correct email last name windsor"


def test_confirmation_replay_keeps_first_and_last_entities_separate():
    result = replay_transcript([
        TranscriptTurn("user", "avery@example.com"),
        TranscriptTurn("assistant", "What's your first name?"),
        TranscriptTurn("user", "Avery"),
        TranscriptTurn("assistant", PROMPT),
        TranscriptTurn("user", "correct email last name doyon"),
    ], engine=PIIEngine(use_presidio=False, use_gliner=False))
    assert result.user_turns[-1].redacted_text == "correct email last name <ln_1>"
    assert result.token_to_value == {"<em_1>": "avery@example.com", "<fn_1>": "Avery", "<ln_1>": "doyon"}
