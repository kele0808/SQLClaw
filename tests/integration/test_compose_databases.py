import pytest
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration


def test_system_test_database_is_reachable(system_db_url: str) -> None:
    engine = create_engine(system_db_url)
    try:
        with engine.connect() as conn:
            name = conn.execute(text("SELECT current_database()")).scalar_one()
            assert name == "t2s_test"
    finally:
        engine.dispose()


def test_probe_table_is_readable_by_t2s_ro(mysql_url: str) -> None:
    engine = create_engine(mysql_url)
    try:
        with engine.connect() as conn:
            notes = conn.execute(text("SELECT note FROM _t2s_probe")).scalars().all()
            assert notes == ["probe"]
            database = conn.execute(text("SELECT DATABASE()")).scalar_one()
            assert database == "t2s_business"
    finally:
        engine.dispose()
