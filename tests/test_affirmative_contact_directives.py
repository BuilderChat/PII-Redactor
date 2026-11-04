"""Acknowledgment plus a contact directive does not assert a visitor name."""

import pytest

from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript


PROMPT = "Would you like our consultant to reach out? I can pass along your info by text or email."
VERBS = ["have", "get", "ask", "tell", "make"]
RECIPIENTS = [
    "her", "him", "them", "those guys", "the team", "a salesperson",
    "someone", "somebody", "the sales team", "your team", "a sales representative",
    "one of the salespeople",
    "anyone", "anybody", "these people", "those folks", "your salesperson",
    "the consultant", "a sales rep", "one of your agents",
]


@pytest.mark.parametrize("verb", VERBS)
@pytest.mark.parametrize("recipient", RECIPIENTS)
@pytest.mark.parametrize("channel,token", [("202-555-0143", "<ph_1>"), ("avery@example.com", "<em_1>")])
def test_directive_recipient_pairs_do_not_establish_names(verb, recipient, channel, token):
    prefix = f"sure {verb} {recipient} text me about new homes there. "
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        prefix + channel, PIIVault(), previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == prefix + token
    assert result.replacements == {token: channel}


@pytest.mark.parametrize("prefix", ["Sure, ", "YES! ", "okay—", "yep ", "sure please "])
def test_acknowledgment_punctuation_and_casing(prefix):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        prefix + "Have Her text me at 202-555-0143", PIIVault(), previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == prefix + "Have Her text me at <ph_1>"
    assert result.replacements == {"<ph_1>": "202-555-0143"}


@pytest.mark.parametrize("name", ["Avery Doyon", "avery doyon", "Élodie O’Neil"])
def test_volunteered_full_name_still_protected_without_name_question(name):
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        f"sure {name}, text me at 202-555-0143", PIIVault(), previous_assistant_message=PROMPT,
    )
    first, last = name.split()
    assert result.redacted_text == "sure <fn_1> <ln_1>, text me at <ph_1>"
    assert result.replacements == {"<fn_1>": first, "<ln_1>": last, "<ph_1>": "202-555-0143"}


def test_explicit_personal_intro_elsewhere_in_directive_still_protected():
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        "sure have her text me. My name is Avery Doyon. 202-555-0143", PIIVault(),
        previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == "sure have her text me. My name is <fn_1> <ln_1>. <ph_1>"
    assert result.replacements == {"<fn_1>": "Avery", "<ln_1>": "Doyon", "<ph_1>": "202-555-0143"}


def test_known_scoped_names_still_redact_when_repeated_in_directive():
    vault = PIIVault()
    vault.register("fn", "Avery")
    vault.register("ln", "Doyon")
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        "sure ask someone to text Avery Doyon at 202-555-0143", vault,
        previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == "sure ask someone to text <fn_1> <ln_1> at <ph_1>"


def test_replay_has_phone_but_no_invented_name_bindings():
    result = replay_transcript([
        TranscriptTurn("assistant", PROMPT),
        TranscriptTurn("user", "sure have her text me about new homes there. 202-555-0143"),
    ], engine=PIIEngine(use_presidio=False, use_gliner=False))
    assert result.user_turns[0].redacted_text == "sure have her text me about new homes there. <ph_1>"
    assert result.token_to_value == {"<ph_1>": "202-555-0143"}


@pytest.mark.parametrize("verb", ["get", "ask", "tell", "make"])
def test_directive_words_inside_real_name_do_not_globally_block_names(verb):
    # Preserve these previously accepted surnames; add no global word blacklist.
    result = PIIEngine(use_presidio=False, use_gliner=False).redact(
        f"My name is Avery {verb.title()}. Email avery@example.com", PIIVault(),
        previous_assistant_message=PROMPT,
    )
    assert result.redacted_text == "My name is <fn_1> <ln_1>. Email <em_1>"
