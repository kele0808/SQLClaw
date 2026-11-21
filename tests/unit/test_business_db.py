from typing import Any

import pytest

from text2sql.db import business


def test_connect_args_use_timeout_and_read_only_session() -> None:
    assert business.business_connect_args(7) == {
        "connect_timeout": 7,
        "read_timeout": 7,
        "init_command": "SET SESSION TRANSACTION READ ONLY",
    }


def test_engine_gets_business_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T2S_BUSINESS_DB_TIMEOUT_S", "9")
    captured: dict[str, Any] = {}

    def fake_create_engine(url: str, **kwargs: Any) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(business, "create_engine", fake_create_engine)
    business.get_business_engine()
    assert captured["url"] == "mysql+pymysql://unit:unit@127.0.0.1:1/unit"
    assert captured["pool_pre_ping"] is True
    assert captured["connect_args"] == {
        "connect_timeout": 9,
        "read_timeout": 9,
        "init_command": "SET SESSION TRANSACTION READ ONLY",
    }


def test_ping_returns_false_when_unreachable() -> None:
    assert business.ping() is False
