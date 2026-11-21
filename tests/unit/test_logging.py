import io
import json
import logging

from text2sql.common.logging import JsonFormatter, configure_logging
from text2sql.common.tracing import reset_trace_id, set_trace_id


def _capture(name: str) -> tuple[logging.Logger, io.StringIO, logging.Handler]:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger(name)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger, stream, handler


def test_json_fields_and_extra() -> None:
    logger, stream, handler = _capture("text2sql.test.json")
    token = set_trace_id("trace-log-0001")
    try:
        logger.info("hello %s", "世界", extra={"job_id": 7})
    finally:
        reset_trace_id(token)
        logger.removeHandler(handler)
    entry = json.loads(stream.getvalue())
    assert entry["level"] == "INFO"
    assert entry["logger"] == "text2sql.test.json"
    assert entry["msg"] == "hello 世界"
    assert entry["trace_id"] == "trace-log-0001"
    assert entry["job_id"] == 7
    assert entry["ts"].endswith("+00:00")
    assert "世界" in stream.getvalue()


def test_trace_id_null_outside_context() -> None:
    logger, stream, handler = _capture("text2sql.test.null")
    try:
        logger.info("no trace")
    finally:
        logger.removeHandler(handler)
    assert json.loads(stream.getvalue())["trace_id"] is None


def test_exception_is_formatted() -> None:
    logger, stream, handler = _capture("text2sql.test.exc")
    try:
        try:
            raise ValueError("bad value")
        except ValueError:
            logger.exception("failed")
    finally:
        logger.removeHandler(handler)
    entry = json.loads(stream.getvalue())
    assert entry["msg"] == "failed"
    assert "ValueError: bad value" in entry["exc"]


def test_configure_logging_installs_single_json_handler() -> None:
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    try:
        configure_logging("debug")
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JsonFormatter)
        assert root.level == logging.DEBUG
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
