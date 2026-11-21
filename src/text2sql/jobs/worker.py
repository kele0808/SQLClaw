"""Job worker: `uv run python -m text2sql.jobs.worker`."""

import logging
import signal
import threading
import time
from collections.abc import Callable
from types import FrameType
from typing import Any

from text2sql.common.config import get_settings
from text2sql.common.logging import configure_logging
from text2sql.common.tracing import new_trace_id, reset_trace_id, set_trace_id
from text2sql.db.system import session_scope
from text2sql.jobs.queue import NonRetryableJobError, claim, complete, fail

logger = logging.getLogger(__name__)

Handler = Callable[[dict[str, Any]], None]

_HANDLERS: dict[str, Handler] = {}


def register(kind: str) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        if kind in _HANDLERS:
            raise ValueError(f"handler already registered for kind {kind!r}")
        _HANDLERS[kind] = handler
        return handler

    return decorator


def registered_kinds() -> list[str]:
    return sorted(_HANDLERS)


@register("noop")
def noop(payload: dict[str, Any]) -> None:
    logger.info("noop_job", extra={"payload_keys": sorted(payload)})


def _format_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _record_failure(job_id: int, error: str, *, retryable: bool) -> None:
    with session_scope() as session:
        fail(session, job_id, error, retryable=retryable)


def run_once(*, lease_s: int) -> bool:
    with session_scope() as session:
        job = claim(session, registered_kinds(), lease_s)
        if job is None:
            return False
        job_id, kind, trace_id = job.id, job.kind, job.trace_id
        payload = dict(job.payload)

    token = set_trace_id(trace_id or new_trace_id())
    started = time.perf_counter()
    log_extra: dict[str, Any] = {"job_id": job_id, "kind": kind}
    try:
        handler = _HANDLERS.get(kind)
        if handler is None:
            logger.error("job_handler_missing", extra=log_extra)
            _record_failure(job_id, f"no handler registered for kind {kind!r}", retryable=False)
            return True
        try:
            handler(payload)
        except NonRetryableJobError as exc:
            logger.warning("job_failed_non_retryable", extra=log_extra, exc_info=True)
            _record_failure(job_id, _format_error(exc), retryable=False)
        except Exception as exc:
            logger.exception("job_failed", extra=log_extra)
            _record_failure(job_id, _format_error(exc), retryable=True)
        else:
            with session_scope() as session:
                complete(session, job_id)
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            logger.info("job_succeeded", extra={**log_extra, "duration_ms": duration_ms})
    finally:
        reset_trace_id(token)
    return True


def run_worker(stop_event: threading.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        try:
            processed = run_once(lease_s=settings.job_lease_s)
        except Exception:
            logger.exception("worker_iteration_failed")
            processed = False
        if not processed:
            stop_event.wait(settings.job_poll_interval_s)


def install_signal_handlers(stop_event: threading.Event) -> None:
    def request_stop(signum: int, frame: FrameType | None) -> None:
        logger.info("worker_stop_requested", extra={"signal": signum})
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop_event = threading.Event()
    install_signal_handlers(stop_event)
    logger.info("worker_started", extra={"kinds": registered_kinds()})
    run_worker(stop_event)
    logger.info("worker_stopped")


if __name__ == "__main__":
    # Re-import under the package name so handlers registered through
    # `text2sql.jobs.worker.register` and this entry point share one registry.
    from text2sql.jobs.worker import main as package_main

    package_main()
