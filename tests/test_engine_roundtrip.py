from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault



def test_redact_and_rehydrate_roundtrip() -> None:
    engine = PIIEngine()
    vault = PIIVault()

    text = "My name is Jinbad Profut and my email is jin@test.com. Call me at 555-123-4567."
    redacted = engine.redact(text, vault)

    assert "<fn_1> <ln_1>" in redacted.redacted_text
    assert "<em_1>" in redacted.redacted_text
    assert "<ph_1>" in redacted.redacted_text

    response = "Hello <fn_1>, I saved <em_1> and <ph_1>."
    rehydrated = engine.rehydrate(response, vault)

    assert "Jinbad" in rehydrated.clean_text
    assert "jin@test.com" in rehydrated.clean_text
    assert "555-123-4567" in rehydrated.clean_text


def test_opening_greeting_context_is_not_redacted_as_user_name() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()

    text = (
        "<opening_greeting_context>\n"
        "A scripted greeting was already shown to the visitor before this first user message.\n"
        "Treat it as an assistant message already sent in this chat.\n"
        "Hi there! My name is Mia. Let's explore floor plans, locations, and more.\n"
        "</opening_greeting_context>"
    )

    redacted = engine.redact(text, vault)

    assert redacted.redacted_text == text
    assert redacted.replacements == {}


def test_user_name_outside_opening_greeting_context_is_still_redacted() -> None:
    engine = PIIEngine(use_presidio=False, use_gliner=False)
    vault = PIIVault()

    text = (
        "<opening_greeting_context>\n"
        "Hi there! My name is Mia.\n"
        "</opening_greeting_context>\n"
        "My name is Dana Lopez."
    )

    redacted = engine.redact(text, vault)

    assert "My name is <fn_1> <ln_1>." in redacted.redacted_text
    assert "Hi there! My name is Mia." in redacted.redacted_text
    assert sorted(redacted.replacements.values()) == ["Dana", "Lopez"]
