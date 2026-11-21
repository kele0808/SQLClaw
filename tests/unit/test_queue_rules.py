import pytest
from sqlalchemy.orm import Session

from text2sql.jobs.queue import (
    MAX_ERROR_LENGTH,
    NonRetryableJobError,
    backoff_seconds,
    enqueue,
    truncate_error,
)


def test_backoff_is_two_to_the_attempts() -> None:
    assert [backoff_seconds(n) for n in (1, 2, 3, 4)] == [2, 4, 8, 16]


def test_truncate_error() -> None:
    assert MAX_ERROR_LENGTH == 2000
    assert truncate_error("x" * 2500) == "x" * 2000
    assert truncate_error("short") == "short"


def test_non_retryable_is_an_exception() -> None:
    assert issubclass(NonRetryableJobError, Exception)


@pytest.mark.parametrize(
    ("kind", "max_attempts"),
    [("", 3), ("k" * 65, 3), ("noop", 0)],
)
def test_enqueue_rejects_invalid_arguments(kind: str, max_attempts: int) -> None:
    with pytest.raises(ValueError):
        enqueue(Session(), kind, {}, max_attempts=max_attempts)
