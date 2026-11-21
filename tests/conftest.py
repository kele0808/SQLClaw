import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from text2sql.common.config import get_settings
from text2sql.db import system

UNIT_SYSTEM_DB_URL = "postgresql+psycopg://unit:unit@127.0.0.1:1/unit"
UNIT_BUSINESS_DB_URL = "mysql+pymysql://unit:unit@127.0.0.1:1/unit"


# Capture the real cached functions at import time: tests may monkeypatch
# `system.get_sessionmaker` with a plain lambda, and this fixture's teardown
# runs before monkeypatch restores the original.
_CACHED_FUNCS = (get_settings, system.get_engine, system.get_sessionmaker)


def _clear_caches() -> None:
    for func in _CACHED_FUNCS:
        func.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for key in list(os.environ):
        if key.startswith("T2S_") and not key.startswith("T2S_TEST_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("T2S_SYSTEM_DB_URL", UNIT_SYSTEM_DB_URL)
    monkeypatch.setenv("T2S_BUSINESS_DB_URL", UNIT_BUSINESS_DB_URL)
    _clear_caches()
    yield
    _clear_caches()
