from src.pii_vault import PIIVault



def test_tokens_are_stable_per_profile_and_overwritable() -> None:
    vault = PIIVault()

    assert vault.register("fn", "Jinbad") == "<fn_1>"
    assert vault.register("ln", "Profut") == "<ln_1>"

    # Correction keeps same token.
    assert vault.register("fn", "Jinbadd") == "<fn_1>"
    assert vault.get("<fn_1>") == "Jinbadd"

    vault.advance_profile()
    assert vault.register("fn", "Alice") == "<fn_2>"
    assert vault.get("<fn_2>") == "Alice"
