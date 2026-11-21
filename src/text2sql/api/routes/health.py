import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from text2sql.common.errors import NOT_READY, error_body
from text2sql.db import business, system

logger = logging.getLogger(__name__)

router = APIRouter()

ReadinessCheck = Callable[[], bool]

READY_TIMEOUT_S = 2.0

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="readyz")


def get_readiness_checks() -> dict[str, ReadinessCheck]:
    return {"system_db": system.ping, "business_db": business.ping}


def _run_check(name: str, check: ReadinessCheck) -> str:
    future = _executor.submit(check)
    try:
        passed = future.result(timeout=READY_TIMEOUT_S)
    except Exception:
        logger.warning("readiness_check_failed", extra={"check": name}, exc_info=True)
        return "error"
    return "ok" if passed else "error"


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(
    checks: Annotated[dict[str, ReadinessCheck], Depends(get_readiness_checks)],
) -> JSONResponse:
    results = {name: _run_check(name, check) for name, check in checks.items()}
    if all(result == "ok" for result in results.values()):
        return JSONResponse({"status": "ready", "checks": results})
    body = {
        "status": "not_ready",
        "checks": results,
        **error_body(NOT_READY, "Service not ready"),
    }
    return JSONResponse(body, status_code=503)
