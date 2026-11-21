import pytest
from fastapi.testclient import TestClient

from text2sql.api.app import create_app

pytestmark = pytest.mark.integration


def test_ready_with_live_databases(system_db_url: str, mysql_url: str) -> None:
    response = TestClient(create_app()).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "checks": {"system_db": "ok", "business_db": "ok"},
    }


def test_not_ready_when_mysql_is_down(system_db_url: str) -> None:
    # The autouse fixture points T2S_BUSINESS_DB_URL at 127.0.0.1:1 (nothing listens).
    response = TestClient(create_app()).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"system_db": "ok", "business_db": "error"}
