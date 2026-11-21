import re
import uuid
from contextvars import ContextVar, Token

TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,64}$")

_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)


def get_trace_id() -> str | None:
    return _trace_id.get()


def set_trace_id(trace_id: str | None) -> Token[str | None]:
    return _trace_id.set(trace_id)


def reset_trace_id(token: Token[str | None]) -> None:
    _trace_id.reset(token)


def new_trace_id() -> str:
    return uuid.uuid4().hex


def is_valid_trace_id(value: str) -> bool:
    return TRACE_ID_PATTERN.fullmatch(value) is not None
