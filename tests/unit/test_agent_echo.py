from fastapi.testclient import TestClient

from text2sql.agent.graph import build_graph, run_echo
from text2sql.api.app import create_app

QUESTION = "最近30天各行业工单数"


def test_graph_copies_question_to_answer() -> None:
    result = build_graph().invoke({"question": QUESTION, "answer": ""})
    assert result["answer"] == QUESTION
    assert run_echo(QUESTION) == QUESTION


def test_echo_endpoint() -> None:
    response = TestClient(create_app()).post("/api/v1/agent/echo", json={"question": QUESTION})
    assert response.status_code == 200
    assert response.json() == {
        "answer": QUESTION,
        "trace_id": response.headers["X-Trace-Id"],
    }


def test_unprefixed_path_is_not_found() -> None:
    response = TestClient(create_app()).post("/v1/agent/echo", json={"question": "x"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_missing_question_is_validation_error() -> None:
    response = TestClient(create_app()).post("/api/v1/agent/echo", json={})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
