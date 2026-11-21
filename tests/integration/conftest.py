import os

import pytest

from text2sql.common.config import get_settings

MYSQL_TARGETS = {
    "mysql57": "T2S_TEST_MYSQL57_URL",
    "mysql80": "T2S_TEST_MYSQL80_URL",
}


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set")
    return value


@pytest.fixture
def system_db_url(monkeypatch: pytest.MonkeyPatch) -> str:
    url = require_env("T2S_TEST_SYSTEM_DB_URL")
    monkeypatch.setenv("T2S_SYSTEM_DB_URL", url)
    get_settings.cache_clear()
    return url


@pytest.fixture(params=list(MYSQL_TARGETS))
def mysql_target(request: pytest.FixtureRequest) -> str:
    target: str = request.param  # type: ignore[attr-defined, unused-ignore]
    return target


@pytest.fixture
def mysql_url(mysql_target: str, monkeypatch: pytest.MonkeyPatch) -> str:
    url = require_env(MYSQL_TARGETS[mysql_target])
    monkeypatch.setenv("T2S_BUSINESS_DB_URL", url)
    get_settings.cache_clear()
    return url
