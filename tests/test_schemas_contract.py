from __future__ import annotations

from pydantic import ValidationError

from src.schemas import RedactRequest, RehydrateRequest


def test_thread_id_is_required_and_must_start_with_thread_prefix() -> None:
    try:
        RedactRequest(
            session_id="s1",
            visitor_id="v1",
            client_id="c1",
            assistant_id="a1",
            message="hello",
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("Expected missing thread_id validation error")

    try:
        RedactRequest(
            thread_id="abc",
            session_id="s1",
            visitor_id="v1",
            client_id="c1",
            assistant_id="a1",
            message="hello",
        )
    except ValidationError:
        return
    raise AssertionError("Expected thread_id prefix validation error")


def test_failure_mode_resolution_defaults_to_config_value() -> None:
    redact = RedactRequest(
        thread_id="thread_1",
        session_id="s1",
        visitor_id="v1",
        client_id="c1",
        assistant_id="a1",
        message="hello",
    )
    assert redact.fail_closed(default_closed=True) is True
    assert redact.fail_closed(default_closed=False) is False

    redact_open = redact.model_copy(update={"failure_mode": "open"})
    redact_closed = redact.model_copy(update={"failure_mode": "closed"})
    assert redact_open.fail_closed(default_closed=True) is False
    assert redact_closed.fail_closed(default_closed=False) is True


def test_rehydrate_failure_mode_resolution() -> None:
    request = RehydrateRequest(
        thread_id="thread_2",
        session_id="s2",
        visitor_id="v2",
        client_id="c2",
        assistant_id="a2",
        message="hello <fn_1>",
        failure_mode="open",
    )
    assert request.fail_closed(default_closed=True) is False

