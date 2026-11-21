import time

import pytest
from fastapi.testclient import TestClient

from text2sql.api.app import create_app
from text2sql.api.routes import health
from text2sql.api.routes.health import ReadinessCheck, get_readiness_checks


def _client(checks: dict[str, ReadinessCheck]) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_readiness_checks] = lambda: checks
    return TestClient(app)


def _ok() -> bool:
    return True


def _down() -> bool:
    return False


def _boom() -> bool:
    raise RuntimeError("boom-secret-detail")


def test_ready_when_all_checks_pass() -> None:
    response = _client({"system_db": _ok, "business_db": _ok}).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"system_db": "ok", "business_db": "ok"},
    }


def test_not_ready_when_a_check_fails() -> None:
    response = _client({"system_db": _ok, "business_db": _down}).get("/readyz")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"system_db": "ok", "business_db": "error"}
    assert body["error"]["code"] == "NOT_READY"
    assert body["error"]["trace_id"] == response.headers["X-Trace-Id"]


def test_exception_details_are_not_returned() -> None:
    response = _client({"system_db": _boom, "business_db": _ok}).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"]["system_db"] == "error"
    assert "boom-secret-detail" not in response.text


def test_slow_check_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "READY_TIMEOUT_S", 0.1)

    def slow() -> bool:
        time.sleep(0.5)
        return True

    started = time.perf_counter()
    response = _client({"system_db": slow, "business_db": _ok}).get("/readyz")
    assert time.perf_counter() - started < 0.45
    assert response.status_code == 503
    assert response.json()["checks"]["system_db"] == "error"


def test_default_checks_use_both_databases() -> None:
    assert set(get_readiness_checks()) == {"system_db", "business_db"}
    response = TestClient(create_app()).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"system_db": "error", "business_db": "error"}
