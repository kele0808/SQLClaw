import re

from text2sql.common.tracing import (
    get_trace_id,
    is_valid_trace_id,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)


def test_new_trace_id_is_32_hex() -> None:
    value = new_trace_id()
    assert re.fullmatch(r"[0-9a-f]{32}", value)
    assert new_trace_id() != value


def test_set_get_reset() -> None:
    assert get_trace_id() is None
    token = set_trace_id("trace-abc-123")
    assert get_trace_id() == "trace-abc-123"
    reset_trace_id(token)
    assert get_trace_id() is None


def test_validation_pattern() -> None:
    assert is_valid_trace_id("abcd-1234")
    assert is_valid_trace_id("a" * 64)
    assert not is_valid_trace_id("short")
    assert not is_valid_trace_id("a" * 65)
    assert not is_valid_trace_id("has space-123")
    assert not is_valid_trace_id("under_score_1")
