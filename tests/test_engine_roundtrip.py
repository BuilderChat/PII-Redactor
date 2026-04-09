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

    response = f"Hello <fn_1>, I saved <em_1> and <ph_1>."
    rehydrated = engine.rehydrate(response, vault)

    assert "Jinbad" in rehydrated.clean_text
    assert "jin@test.com" in rehydrated.clean_text
    assert "555-123-4567" in rehydrated.clean_text
