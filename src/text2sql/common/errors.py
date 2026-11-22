from text2sql.common.tracing import get_trace_id

INTERNAL_ERROR = "INTERNAL_ERROR"
VALIDATION_ERROR = "VALIDATION_ERROR"
NOT_FOUND = "NOT_FOUND"
METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
HTTP_ERROR = "HTTP_ERROR"
NOT_READY = "NOT_READY"

# Spec 02 metadata
METADATA_NO_DATABASE = "METADATA_NO_DATABASE"
METADATA_UNSUPPORTED_VERSION = "METADATA_UNSUPPORTED_VERSION"
METADATA_TOO_LARGE = "METADATA_TOO_LARGE"
METADATA_SNAPSHOT_NOT_FOUND = "METADATA_SNAPSHOT_NOT_FOUND"
METADATA_OBJECT_NOT_FOUND = "METADATA_OBJECT_NOT_FOUND"
RELEASE_NOT_FOUND = "RELEASE_NOT_FOUND"
RELEASE_INVALID = "RELEASE_INVALID"
RELEASE_GATE_REQUIRED = "RELEASE_GATE_REQUIRED"
STALE_MARK_NOT_FOUND = "STALE_MARK_NOT_FOUND"
VALUE_INDEX_NOT_FOUND = "VALUE_INDEX_NOT_FOUND"


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
