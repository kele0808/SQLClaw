import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text

from text2sql.common.config import get_settings
from text2sql.db.system import get_engine

REPO_ROOT = Path(__file__).resolve().parents[2]

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


@pytest.fixture
def alembic_cfg() -> Config:
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.attributes["configure_logging"] = False
    return cfg


def _truncate_jobs(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE jobs RESTART IDENTITY"))


@pytest.fixture
def migrated_db(system_db_url: str, alembic_cfg: Config) -> Iterator[Engine]:
    command.upgrade(alembic_cfg, "head")
    engine = get_engine()
    _truncate_jobs(engine)
    yield engine
    _truncate_jobs(engine)
