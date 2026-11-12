import pytest

from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript


PROMPT = "Thanks, <fn_1>! The team will send the Pierre's Bay width and depth to <em_1>. What's your last name, just in case?"


@pytest.mark.parametrize("surname", ["West", "North", "East", "South", "west", "WEST", "Western", "Northeast"])
@pytest.mark.parametrize("suffix", ["", ".", "!", "? "])
@pytest.mark.parametrize("allowlist", [[], ["West", "North", "East", "South", "Western", "Northeast"]])
def test_explicit_last_name_answer_uses_surname_entity(surname, suffix, allowlist):
    vault = PIIVault()
    vault.register("fn", "Avery")
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        surname + suffix, vault, previous_assistant_message=PROMPT, non_name_allowlist=allowlist,
    )
    assert result.redacted_text == "<ln_1>" + suffix
    assert result.replacements == {"<ln_1>": surname}
    assert vault.items() == {"<fn_1>": "Avery", "<ln_1>": surname}


@pytest.mark.parametrize("prompt,text", [
    ("Where would you like to build?", "west"),
    (None, "west"),
    ("What's your first name?", "west"),
    ("What's your last name?", "west of town"),
    ("What's your last name?", "in the west"),
    ("What's your last name?", "West Texas"),
])
def test_geographic_and_non_surname_contexts_stay_unchanged(prompt, text):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        text, PIIVault(), previous_assistant_message=prompt,
    )
    assert result.redacted_text == text
    assert result.replacements == {}


def test_unrelated_community_allowlist_still_applies():
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        "West Ridge", PIIVault(), previous_assistant_message=PROMPT, non_name_allowlist=["West Ridge"],
    )
    assert result.redacted_text == "West Ridge"


def test_directional_surname_replay_keeps_existing_first_name():
    result = replay_transcript([
        TranscriptTurn("assistant", "What's your first name?"),
        TranscriptTurn("user", "Avery"),
        TranscriptTurn("assistant", PROMPT),
        TranscriptTurn("user", "West"),
    ], engine=PIIEngine(use_presidio=False, use_gliner=False))
    assert result.user_turns[-1].redacted_text == "<ln_1>"
    assert result.token_to_value == {"<fn_1>": "Avery", "<ln_1>": "West"}
