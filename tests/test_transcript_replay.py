from __future__ import annotations

import pytest

from src.pii_engine import PIIEngine
from src.transcript_replay import TranscriptTurn, replay_transcript


def _replay(
    turns: list[tuple[str, str]],
    *,
    non_name_allowlist: list[str] | None = None,
):
    return replay_transcript(
        [TranscriptTurn(role=role, content=content) for role, content in turns],
        engine=PIIEngine(use_presidio=False, use_gliner=False),
        non_name_allowlist=non_name_allowlist,
    )


def _values(result, prefix: str) -> set[str]:
    return {
        value
        for token, value in result.token_to_value.items()
        if token.startswith(f"<{prefix}_")
    }


def test_replay_uses_the_actual_preceding_assistant_turn_and_one_vault() -> None:
    result = _replay(
        [
            ("assistant", "What's your first name?"),
            ("user", "Avery"),
            ("assistant", "Thanks, Avery. What's your last name?"),
            ("user", "D'Angelo"),
            ("assistant", "Thanks, Avery D'Angelo."),
            ("user", "Avery D'Angelo"),
        ]
    )

    assert [turn.redacted_text for turn in result.user_turns] == [
        "<fn_1>",
        "<ln_1>",
        "<fn_1> <ln_1>",
    ]
    assert result.token_to_value == {"<fn_1>": "Avery", "<ln_1>": "D'Angelo"}


def test_replay_scope_is_isolated_from_other_replays() -> None:
    first = _replay([("assistant", "What's your first name?"), ("user", "Avery")])
    second = _replay([("assistant", "What would you like to know?"), ("user", "Avery")])

    assert first.user_turns[-1].redacted_text == "<fn_1>"
    assert second.user_turns[-1].redacted_text == "Avery"
    assert second.token_to_value == {}


def test_replay_preserves_runtime_non_name_allowlist() -> None:
    result = _replay(
        [("assistant", "What's your first and last name?"), ("user", "Windsor California")],
        non_name_allowlist=["Windsor"],
    )

    assert result.user_turns[-1].redacted_text == "Windsor California"
    assert result.token_to_value == {}


@pytest.mark.parametrize(
    ("turns", "unsupported_values"),
    [
        (
            [
                ("user", "Hello. Do you build along the coast only?"),
                ("assistant", "What area interests you most?"),
                ("user", "I purchased land in Harnett County"),
                ("assistant", "What's your preferred contact method: email or phone?"),
                ("user", "This is ironic the property is in Cameron NC"),
            ],
            {"Ironic"},
        ),
        (
            [
                ("user", "Can you send the HOA information?"),
                ("assistant", "What's your preferred contact method, email or phone?"),
                ("user", "suzanne@example.com"),
                ("assistant", "What's your first name?"),
                ("user", "Suzanne"),
                ("assistant", "Just for our records, what's your last name?"),
                ("user", "Can I get pricing and floorplans?"),
            ],
            {"Can"},
        ),
        (
            [
                ("user", "I want pricing for the Sutton plan"),
                ("assistant", "What's your email address?"),
                ("user", "sharin@example.com"),
                ("assistant", "What's your first name?"),
                ("user", "Sharin"),
                ("assistant", "Would you like to add your last name just in case?"),
                ("user", "Are there lots available for building the Sutton?"),
            ],
            {"Are"},
        ),
    ],
)
def test_reported_prose_does_not_establish_unsupported_names(
    turns: list[tuple[str, str]],
    unsupported_values: set[str],
) -> None:
    result = _replay(turns)

    assert not (unsupported_values & (_values(result, "fn") | _values(result, "ln")))


def test_same_name_referential_reply_does_not_establish_name_evidence() -> None:
    result = _replay(
        [
            ("assistant", "Would you prefer email or phone, and what's your first name?"),
            ("user", "Phone"),
            ("assistant", "What's your phone number?"),
            ("user", "Dhana"),
            ("assistant", "Is that your first name? I also need your phone number."),
            ("user", "555-123-4567"),
            ("assistant", "Is Regina your first name, or should I use a different name?"),
            ("user", "Same name"),
        ]
    )

    assert result.user_turns[-1].redacted_text == "Same name"
    assert _values(result, "fn") == set()
    assert _values(result, "ln") == set()


def test_model_or_tool_pending_value_has_no_user_turn_evidence() -> None:
    result = _replay(
        [
            ("user", "What size lot?"),
            ("assistant", "Are you looking in Sanger?"),
            ("user", "Sanger"),
            ("assistant", "Would you like the lot-size details via email or text?"),
            ("user", "Yes text"),
            ("assistant", "Perfect! What's your phone number?"),
            ("user", "555-123-4567"),
            ("assistant", "Got it. What's your first name?"),
        ]
    )

    assert "Pending" not in result.token_to_value.values()
    assert _values(result, "fn") == set()
    assert result.user_turns[-1].redacted_text == "<ph_1>"


def test_courtesy_prose_does_not_establish_last_name_evidence() -> None:
    result = _replay(
        [
            ("user", "I purchased land in Harnett County"),
            ("assistant", "What's your preferred contact method: email or phone?"),
            ("user", "This is ironic the property is in Cameron NC"),
            ("assistant", "Should I have the specialist reach out via email or phone?"),
            ("user", "Email please at visitor@example.com"),
            ("assistant", "Just for our records, what's your last name?"),
            ("user", "Thank you so very much"),
        ]
    )

    assert result.user_turns[-1].redacted_text == "Thank you so very much"
    assert _values(result, "ln") == set()


def test_parenthetical_channel_qualifier_does_not_establish_name_evidence() -> None:
    result = _replay(
        [
            ("user", "What time are tours available until?"),
            ("assistant", "Would you like me to connect you with the consultant?"),
            ("user", "Yes please"),
            ("assistant", "What's your preferred way to hear from us, email or phone?"),
            ("user", "Phone (messaging) 555-123-4567"),
        ]
    )

    assert result.user_turns[-1].redacted_text == "Phone (messaging) <ph_1>"
    assert _values(result, "fn") == set()


@pytest.mark.parametrize(
    ("turns", "expected_text", "expected_names"),
    [
        (
            [("assistant", "What's your last name?"), ("user", "So")],
            "<ln_1>",
            {"So"},
        ),
        (
            [("assistant", "How can I help?"), ("user", "My last name is So")],
            "My last name is <ln_1>",
            {"So"},
        ),
        (
            [("user", "Hi this is John Smith with a pricing question")],
            "Hi this is <fn_1> <ln_1> with a pricing question",
            {"John", "Smith"},
        ),
        (
            [("user", "John Smith john@example.com")],
            "<fn_1> <ln_1> <em_1>",
            {"John", "Smith"},
        ),
        (
            [("user", "Sure 555-123-4567 (Nick)")],
            "Sure <ph_1> (<fn_1>)",
            {"Nick"},
        ),
    ],
)
def test_replay_positive_name_controls(
    turns: list[tuple[str, str]],
    expected_text: str,
    expected_names: set[str],
) -> None:
    result = _replay(turns)

    assert result.user_turns[-1].redacted_text == expected_text
    assert expected_names <= (_values(result, "fn") | _values(result, "ln"))


def test_replay_preserves_apostrophe_hyphen_and_correction_flows() -> None:
    result = _replay(
        [
            ("assistant", "What's your first and last name?"),
            ("user", "Anne-Marie O'Neil"),
            ("assistant", "What's your preferred contact method?"),
            ("user", "Anne-Marie O'Neal"),
        ]
    )

    assert result.user_turns[0].redacted_text == "<fn_1> <ln_1>"
    assert result.user_turns[1].redacted_text == "<fn_1> <ln_1>"
    assert result.token_to_value["<ln_1>"] == "O'Neal"


def test_replay_preserves_two_person_suffix_flow() -> None:
    result = _replay(
        [
            ("assistant", "What's your first name?"),
            ("user", "Sebero Marin Sr. & Erika Canela"),
        ]
    )

    assert result.user_turns[-1].redacted_text == "<fn_1> <ln_1> Sr. & <fn_2> <ln_2>"
    assert result.token_to_value == {
        "<fn_1>": "Sebero",
        "<ln_1>": "Marin",
        "<fn_2>": "Erika",
        "<ln_2>": "Canela",
    }


@pytest.mark.parametrize(
    ("turns", "kwargs", "message"),
    [
        ([TranscriptTurn(role="user", content="Hello")], {"max_turns": 0}, "max_turns"),
        (
            [
                TranscriptTurn(role="assistant", content="Hello"),
                TranscriptTurn(role="user", content="Hello"),
            ],
            {"max_turns": 1},
            "turn",
        ),
        ([TranscriptTurn(role="user", content="Hello")], {"max_characters": 4}, "character"),
        (
            [TranscriptTurn(role="system", content="Hello")],  # type: ignore[arg-type]
            {},
            "role",
        ),
    ],
)
def test_replay_rejects_invalid_or_unbounded_input(turns, kwargs, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        replay_transcript(
            turns,
            engine=PIIEngine(use_presidio=False, use_gliner=False),
            **kwargs,
        )
