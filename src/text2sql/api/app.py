from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from text2sql import __version__
from text2sql.api.middleware import TraceIdMiddleware
from text2sql.api.routes import health
from text2sql.common.errors import (
    HTTP_ERROR,
    METHOD_NOT_ALLOWED,
    NOT_FOUND,
    VALIDATION_ERROR,
    AppError,
    error_body,
)

API_V1_PREFIX = "/api/v1"

_HTTP_ERROR_CODES = {404: NOT_FOUND, 405: METHOD_NOT_ALLOWED}


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, AppError):
        raise exc
    return JSONResponse(error_body(exc.code, exc.message), status_code=exc.status_code)


async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, StarletteHTTPException):
        raise exc
    code = _HTTP_ERROR_CODES.get(exc.status_code, HTTP_ERROR)
    return JSONResponse(
        error_body(code, str(exc.detail)),
        status_code=exc.status_code,
        headers=exc.headers,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(error_body(VALIDATION_ERROR, "Request validation failed"), status_code=422)


def build_api_v1_router() -> APIRouter:
    return APIRouter(prefix=API_V1_PREFIX)


def create_app() -> FastAPI:
    app = FastAPI(title="text2sql", version=__version__)
    app.add_middleware(TraceIdMiddleware)
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.include_router(health.router)
    app.include_router(build_api_v1_router())
    return app
