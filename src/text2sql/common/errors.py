from text2sql.common.tracing import get_trace_id

INTERNAL_ERROR = "INTERNAL_ERROR"
VALIDATION_ERROR = "VALIDATION_ERROR"
NOT_FOUND = "NOT_FOUND"
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
HTTP_ERROR = "HTTP_ERROR"
NOT_READY = "NOT_READY"


class AppError(Exception):
    """Base class for expected, client-visible errors."""

    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    trace_id = get_trace_id() or ""
    return {"error": {"code": code, "message": message, "trace_id": trace_id}}
