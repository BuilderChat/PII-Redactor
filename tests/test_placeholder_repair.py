from src.pii_engine import PIIEngine
from src.pii_vault import PIIVault



def test_repairs_generic_placeholders_before_rehydrate() -> None:
    engine = PIIEngine()
    vault = PIIVault()

    engine.redact("My name is Jinbad Profut. Email is jin@test.com", vault)

    repaired = engine.rehydrate("Hi <name>, contact is <email>", vault)
    assert repaired.repaired_placeholders is True
    assert "Jinbad Profut" in repaired.clean_text
    assert "jin@test.com" in repaired.clean_text
