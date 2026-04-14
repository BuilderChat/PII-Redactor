from src.pii_vault import PIIVault



def test_tokens_increment_per_entity_without_overwrite() -> None:
    vault = PIIVault()

    assert vault.register("fn", "Jinbad") == "<fn_1>"
    assert vault.register("ln", "Profut") == "<ln_1>"

    # Distinct value gets a new token instead of overwriting.
    assert vault.register("fn", "Jinbadd") == "<fn_2>"
    assert vault.get("<fn_1>") == "Jinbad"
    assert vault.get("<fn_2>") == "Jinbadd"
    assert vault.latest_token_for_entity("fn") == "<fn_2>"

    # Re-registering the same normalized value reuses existing token.
    assert vault.register("fn", "  jinbad  ") == "<fn_1>"

    vault.advance_profile()
    assert vault.register("fn", "Alice") == "<fn_3>"
    assert vault.get("<fn_3>") == "Alice"


def test_multi_value_entities_increment_without_collision() -> None:
    vault = PIIVault()

    assert vault.register("fn", "John") == "<fn_1>"
    assert vault.register("fn", "Kelly") == "<fn_2>"
    assert vault.register("fn", "Chris") == "<fn_3>"
    assert vault.register("fn", "Linda") == "<fn_4>"

    assert vault.register("em", "john@example.com") == "<em_1>"
    assert vault.register("em", "kelly@example.com") == "<em_2>"

    assert vault.register("ph", "+1 (555) 111-2222") == "<ph_1>"
    assert vault.register("ph", "555.333.4444") == "<ph_2>"
