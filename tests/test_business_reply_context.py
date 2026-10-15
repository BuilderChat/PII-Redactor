"""Synthetic business-collection regressions and personal-name controls."""

import pytest

from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault
from src.transcript_replay import TranscriptTurn, replay_transcript


def redact(text, prompt=None, vault=None, pending=None):
    return PIIEngine(use_presidio=False, use_gliner=False).redact(
        text,
        vault if vault is not None else PIIVault(),
        previous_assistant_message=prompt,
        pending_name_fields=pending,
    )


@pytest.mark.parametrize("intro", ["name's", "name’s", "my name's", "my name’s"])
@pytest.mark.parametrize(
    "prompt",
    [
        None,
        "What's your first name?",
        "What's your full name?",
        "What's your last name?",
        "What's your role there?",
    ],
)
def test_contracted_intro_redacts_value_instead_of_label(intro, prompt):
    result = redact(f"{intro} avery. email is avery@example.com", prompt)
    assert result.redacted_text == f"{intro} <fn_1>. email is <em_1>"
    assert result.replacements == {"<fn_1>": "avery", "<em_1>": "avery@example.com"}


@pytest.mark.parametrize("intro", ["name's", "name’s", "my name's", "my name’s"])
def test_contracted_full_name_without_prompt_remains_protected(intro):
    result = redact(f"{intro} Avery Doyon")
    assert result.redacted_text == f"{intro} <fn_1> <ln_1>"


@pytest.mark.parametrize(
    "prompt,answer",
    [
        ("What's your role there?", "General Manager"),
        ("What's your role at the company?", "Director of Operations"),
        ("What is your job title?", "Chief Executive Officer"),
        (
            "I've got your first name on file. What's your role there?",
            "General Manager",
        ),
        ("I've got your first name on file—what's your role there?", "General Manager"),
        ("What's your company name?", "Fine Custom Homes LLC"),
        ("What builder or company are you with?", "River Oak Builders"),
        ("What's your company name?", "Acme Software"),
        ("What's your company website?", "https://FineCustomHomes.com"),
        ("Could you share your builder website?", "www.FineCustomHomes.com"),
        ("What would you like to discuss during the meeting?", "Online Sales"),
        ("Where would you like us to focus the assessment?", "Customer Care"),
        ("What is the subject of your reach-out request?", "Lead Follow-Up"),
    ],
)
def test_business_only_reply_does_not_establish_personal_names(prompt, answer):
    result = redact(answer, prompt, pending=["first_name"])
    assert result.redacted_text == answer
    assert result.replacements == {}


@pytest.mark.parametrize(
    "prompt,answer,expected",
    [
        (
            "What's your role there?",
            "General Manager. Email me at avery@example.com",
            "General Manager. Email me at <em_1>",
        ),
        (
            "What's your role there?",
            "General Manager, 555-123-4567",
            "General Manager, <ph_1>",
        ),
        (
            "What's your company name?",
            "Acme Software, avery@example.com",
            "Acme Software, <em_1>",
        ),
        (
            "What's your role there?",
            "My name is Avery Doyon, General Manager",
            "My name is <fn_1> <ln_1>, General Manager",
        ),
        (
            "What's your company name?",
            "My name is Avery Doyon from Acme Software",
            "My name is <fn_1> <ln_1> from Acme Software",
        ),
        (
            "What's your company website?",
            "name's avery. email is avery@example.com",
            "name's <fn_1>. email is <em_1>",
        ),
        (
            "Where would you like us to focus the assessment?",
            "Online Sales. My name is Avery Doyon",
            "Online Sales. My name is <fn_1> <ln_1>",
        ),
    ],
)
def test_business_context_preserves_personal_pii_in_mixed_replies(
    prompt, answer, expected
):
    assert redact(answer, prompt).redacted_text == expected


@pytest.mark.parametrize(
    "prompt",
    [
        "What's your company name?",
        "What's your role there?",
        "What's your company website?",
        "Where should we focus the assessment?",
    ],
)
def test_unsolicited_full_name_still_redacts_in_business_context(prompt):
    assert redact("Avery Doyon", prompt).redacted_text == "<fn_1> <ln_1>"


def test_business_question_does_not_disable_known_name_replacement_or_correction():
    vault = PIIVault()
    redact("Avery Doyon", "What's your full name?", vault)
    assert redact("Avery", "What's your company name?", vault).redacted_text == "<fn_1>"
    correction = redact("Avery Doyen", "What's your role there?", vault)
    assert correction.redacted_text == "<fn_1> <ln_1>"
    assert vault.get("<ln_1>") == "Doyen"


def test_business_collection_replay_retains_original_person_identity():
    turns = [
        ("assistant", "What's your first name?"),
        ("user", "name's avery. email is avery@example.com"),
        ("assistant", "What's your company name?"),
        ("user", "Acme Software"),
        ("assistant", "What's your company website?"),
        ("user", "https://acme.example.com"),
        ("assistant", "What's your role there?"),
        ("user", "General Manager"),
        ("assistant", "Where should we focus the assessment?"),
        ("user", "Online Sales"),
        ("assistant", "Anything else?"),
        ("user", "My last name is Doyon"),
    ]
    result = replay_transcript(
        [TranscriptTurn(role=role, content=content) for role, content in turns],
        engine=PIIEngine(use_presidio=False, use_gliner=False),
    )
    assert result.user_turns[0].redacted_text == "name's <fn_1>. email is <em_1>"
    assert [turn.redacted_text for turn in result.user_turns[1:5]] == [
        "Acme Software",
        "https://acme.example.com",
        "General Manager",
        "Online Sales",
    ]
    assert result.token_to_value == {
        "<fn_1>": "avery",
        "<em_1>": "avery@example.com",
        "<ln_1>": "Doyon",
    }


@pytest.mark.parametrize(
    "prompt",
    [
        "What's your company name?",
        "What's your company's website?",
        "What's your role there?",
        "Where would you like us to focus the assessment?",
    ],
)
@pytest.mark.parametrize(
    "answer,expected",
    [
        ("Actually, my name is Avery Doyon", "Actually, my name is <fn_1> <ln_1>"),
        ("Actually, my name's Avery Doyon", "Actually, my name's <fn_1> <ln_1>"),
        ("My last name is Doyon", "My last name is <ln_1>"),
        ("avery@example.com, 555-123-4567", "<em_1>, <ph_1>"),
    ],
)
def test_volunteered_identity_and_contact_values_remain_protected(
    prompt, answer, expected
):
    assert redact(answer, prompt).redacted_text == expected


def test_explicit_pending_personal_name_evidence_survives_business_question():
    result = redact("Dhana", "What's your company name?", pending=["first_name"])
    assert result.redacted_text == "<fn_1>"


def test_latest_personal_question_wins_over_earlier_business_question():
    result = redact(
        "General Manager", "What's your role there? And what's your full name?"
    )
    assert result.redacted_text == "<fn_1> <ln_1>"


def test_company_question_does_not_globally_ban_a_business_word_used_in_personal_name():
    assert (
        redact("My name is Avery Software", "What's your company name?").redacted_text
        == "My name is <fn_1> <ln_1>"
    )


@pytest.mark.parametrize(
    "answer,expected",
    [
        (
            "General Manager. My name's Avery Doyon",
            "General Manager. My name's <fn_1> <ln_1>",
        ),
        (
            "Acme Software. My name is Avery Doyon",
            "Acme Software. My name is <fn_1> <ln_1>",
        ),
    ],
)
def test_only_business_span_is_filtered_in_two_clause_reply(answer, expected):
    prompt = (
        "What's your role there?"
        if answer.startswith("General")
        else "What's your company name?"
    )
    assert redact(answer, prompt).redacted_text == expected


def test_website_context_does_not_hide_name_before_domain_shaped_punctuation():
    assert (
        redact("My name is Avery.Software", "What's your website?").redacted_text
        == "My name is <fn_1>.Software"
    )


def test_website_label_and_separate_self_introduction():
    text = "Our company website is https://acme.example.com. My name's Avery Doyon"
    assert (
        redact(text, "What's your website?").redacted_text
        == "Our company website is https://acme.example.com. My name's <fn_1> <ln_1>"
    )


@pytest.mark.parametrize("name", ["Anne-Marie O'Neil", "Élodie D’Angelo"])
def test_contracted_intro_preserves_joined_and_accented_name_detection(name):
    result = redact(f"My name’s {name}", "What's your company name?")
    assert result.redacted_text == "My name’s <fn_1> <ln_1>"
    assert " ".join(result.replacements.values()) == name


@pytest.mark.parametrize("name", ["Avery From", "Avery From Smith", "Avery Quinn From"])
def test_company_bridge_does_not_globally_exclude_from_as_a_name(name):
    result = redact(f"My name is {name}", "What's your company name?")
    assert all(part in result.replacements.values() for part in name.split())


def test_assessment_examples_do_not_turn_the_question_into_a_website_request():
    prompt = (
        "One last thing: where would you like us to focus the assessment? "
        "For example, are you looking to improve your website, online sales, "
        "lead follow-up, self-guided tours, build communication, or customer care?"
    )
    assert redact("Customer Care", prompt).redacted_text == "Customer Care"
    assert (
        redact("My name is Avery Doyon", prompt).redacted_text
        == "My name is <fn_1> <ln_1>"
    )
