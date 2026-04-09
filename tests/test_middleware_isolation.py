from src.middleware import PIIMiddleware
from src.types import ScopeContext



def test_scope_isolation_across_sessions() -> None:
    middleware = PIIMiddleware()

    scope_a = ScopeContext("s1", "v1", "c1", "a1")
    scope_b = ScopeContext("s1", "v2", "c1", "a1")

    inbound_a = middleware.process_inbound(scope_a, "My name is Alice Jones")
    inbound_b = middleware.process_inbound(scope_b, "My name is Bob Stone")

    assert inbound_a.redacted_text == "My name is <fn_1> <ln_1>"
    assert inbound_b.redacted_text == "My name is <fn_1> <ln_1>"

    outbound_a = middleware.process_outbound(scope_a, "Hello <fn_1> <ln_1>")
    outbound_b = middleware.process_outbound(scope_b, "Hello <fn_1> <ln_1>")

    assert outbound_a.clean_text == "Hello Alice Jones"
    assert outbound_b.clean_text == "Hello Bob Stone"
