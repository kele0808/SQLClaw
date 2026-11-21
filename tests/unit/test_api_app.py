import io
import json
import logging
import re
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from text2sql.api.app import create_app
from text2sql.common.errors import AppError
from text2sql.common.logging import JsonFormatter


class _Payload(BaseModel):
    n: int


def _build_app() -> FastAPI:
    app = create_app()

    @app.get("/api/v1/_test/app-error")
    def app_error() -> None:
        raise AppError("DEMO_CONFLICT", "Demo conflict", 409)

    @app.get("/api/v1/_test/crash")
    def crash() -> None:
        raise RuntimeError("postgresql+psycopg://u:leakedpw@db/x")

    @app.post("/api/v1/_test/validate")
    def validate(payload: _Payload) -> dict[str, int]:
        return {"n": payload.n}

    @app.get("/api/v1/_test/log")
    def log_inside() -> dict[str, str]:
        logging.getLogger("text2sql.test.route").info("inside_handler")
        return {"status": "ok"}

    return app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(_build_app(), raise_server_exceptions=False) as test_client:
        yield test_client


def test_healthz(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Trace-Id"])


def test_valid_incoming_trace_id_is_echoed(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Trace-Id": "client-trace-01"})
    assert response.headers["X-Trace-Id"] == "client-trace-01"


def test_invalid_incoming_trace_id_is_replaced(client: TestClient) -> None:
    response = client.get("/healthz", headers={"X-Trace-Id": "bad id!"})
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Trace-Id"])


def test_app_error_uses_unified_body(client: TestClient) -> None:
    response = client.get("/api/v1/_test/app-error")
    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "DEMO_CONFLICT",
            "message": "Demo conflict",
            "trace_id": response.headers["X-Trace-Id"],
        }
    }


def test_validation_error(client: TestClient) -> None:
    response = client.post("/api/v1/_test/validate", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_route_uses_unified_body(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["trace_id"] == response.headers["X-Trace-Id"]


def test_uncaught_exception_hides_details(client: TestClient) -> None:
    response = client.get("/api/v1/_test/crash")
    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "Internal server error",
            "trace_id": response.headers["X-Trace-Id"],
        }
    }
    assert "leakedpw" not in response.text
    assert "Traceback" not in response.text


def test_all_log_lines_of_a_request_share_trace_id(client: TestClient) -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("text2sql")
    saved_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        client.get("/api/v1/_test/log", headers={"X-Trace-Id": "trace-log-0001"})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(saved_level)
    entries = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert {"inside_handler", "http_request"} <= {e["msg"] for e in entries}
    assert {e["trace_id"] for e in entries} == {"trace-log-0001"}
    request_entry = next(e for e in entries if e["msg"] == "http_request")
    assert request_entry["method"] == "GET"
    assert request_entry["path"] == "/api/v1/_test/log"
    assert request_entry["status_code"] == 200
    assert isinstance(request_entry["duration_ms"], float)
