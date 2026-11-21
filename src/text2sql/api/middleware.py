import logging
import time

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from text2sql.common.errors import INTERNAL_ERROR, error_body
from text2sql.common.tracing import (
    is_valid_trace_id,
    new_trace_id,
    reset_trace_id,
    set_trace_id,
)

logger = logging.getLogger("text2sql.api.request")

TRACE_HEADER = "X-Trace-Id"


class TraceIdMiddleware:
    """Trace id per request, request log line, and last-resort 500 response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get(TRACE_HEADER)
        trace_id = incoming if incoming and is_valid_trace_id(incoming) else new_trace_id()
        token = set_trace_id(trace_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_trace(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                MutableHeaders(scope=message).append(TRACE_HEADER, trace_id)
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        except Exception:
            logger.exception("unhandled_exception")
            if response_started:
                raise
            response = JSONResponse(
                error_body(INTERNAL_ERROR, "Internal server error"), status_code=500
            )
            await response(scope, receive, send_with_trace)
        finally:
            logger.info(
                "http_request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                },
            )
            reset_trace_id(token)
