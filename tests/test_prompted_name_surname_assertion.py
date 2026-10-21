"""Prompted first names followed by an explicit surname assertion."""

import pytest

from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript


@pytest.mark.parametrize("prompt", ["Got it! What's your first name?", "What's your full name?"])
@pytest.mark.parametrize(
    "answer,expected,first,last",
    [
        ("Victor and last name is Doyon", "<fn_1> and last name is <ln_1>", "Victor", "Doyon"),
        ("Victor, last name is Doyon", "<fn_1>, last name is <ln_1>", "Victor", "Doyon"),
        ("Victor and my last name is Doyon", "<fn_1> and my last name is <ln_1>", "Victor", "Doyon"),
        ("victor; my surname is doyon.", "<fn_1>; my surname is <ln_1>.", "victor", "doyon"),
        ("Élodie and family name is O’Neil", "<fn_1> and family name is <ln_1>", "Élodie", "O’Neil"),
        ("Sure, Victor and last name is Doyon", "Sure, <fn_1> and last name is <ln_1>", "Victor", "Doyon"),
    ],
)
def test_prompted_surname_continuation(prompt, answer, expected, first, last):
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    result = engine.redact(answer, PIIVault(), previous_assistant_message=prompt)
    assert result.redacted_text == expected
    assert result.replacements == {"<fn_1>": first, "<ln_1>": last}


@pytest.mark.parametrize("prompt", [None, "What's your first name?", "What's your role there?"])
@pytest.mark.parametrize("connector", [" and ", " and my ", ", ", "; ", " "])
def test_explicit_labels_keep_separate_entities(prompt, connector):
    answer = f"First name is Victor{connector}last name is Doyon"
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        answer, PIIVault(), previous_assistant_message=prompt
    )
    assert result.redacted_text == f"First name is <fn_1>{connector}last name is <ln_1>"
    assert result.replacements == {"<fn_1>": "Victor", "<ln_1>": "Doyon"}


@pytest.mark.parametrize(
    "prompt,answer,expected",
    [
        ("What's your first name?", "Thanks and last name is Doyon", "Thanks and last name is <ln_1>"),
        ("What's your first name?", "General Manager and last name is Doyon", "General Manager and last name is <ln_1>"),
        ("What's your first name?", "Victor and I want incentive details", "Victor and I want incentive details"),
        ("What's your first name?", "Victor and last name is pending", "Victor and last name is pending"),
        ("What's your role there?", "General Manager", "General Manager"),
        ("What's your company name?", "Acme Software", "Acme Software"),
        ("What would you like to discuss?", "Online Sales", "Online Sales"),
        (None, "Victor and last name is Doyon", "Victor and last name is <ln_1>"),
    ],
)
def test_continuation_does_not_infer_first_name_from_prose(prompt, answer, expected):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        answer, PIIVault(), previous_assistant_message=prompt
    )
    assert result.redacted_text == expected
    assert not any(token.startswith("<fn_") for token in result.replacements)


def test_surname_continuation_respects_non_name_allowlist():
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        "Acme and last name is Doyon", PIIVault(),
        previous_assistant_message="What's your first name?", non_name_allowlist=["Acme"],
    )
    assert result.redacted_text == "Acme and last name is <ln_1>"


@pytest.mark.parametrize("prefix", ["", "First name is "])
def test_audit_replay_captures_both_name_fields_after_email(prefix):
    result = replay_transcript(
        [
            TranscriptTurn("user", "victor@example.com"),
            TranscriptTurn("assistant", "Got it! What's your first name?"),
            TranscriptTurn("user", f"{prefix}Victor and last name is Doyon"),
        ],
        engine=PIIEngine(use_presidio=False, use_gliner=False),
    )
    assert result.user_turns[-1].redacted_text == f"{prefix}<fn_1> and last name is <ln_1>"
    assert result.token_to_value == {
        "<em_1>": "victor@example.com", "<fn_1>": "Victor", "<ln_1>": "Doyon",
    }
