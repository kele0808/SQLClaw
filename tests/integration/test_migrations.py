import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from text2sql.db.models import Base
from text2sql.db.system import get_engine

pytestmark = pytest.mark.integration


def test_upgrade_matches_models_and_downgrade_removes_table(
    system_db_url: str, alembic_cfg: Config
) -> None:
    engine = get_engine()
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
    try:
        inspector = inspect(engine)
        assert "jobs" in inspector.get_table_names()
        index_names = {ix["name"] for ix in inspector.get_indexes("jobs")}
        assert "ix_jobs_status_run_after" in index_names
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == []

        command.downgrade(alembic_cfg, "base")
        assert "jobs" not in inspect(engine).get_table_names()
    finally:
        command.upgrade(alembic_cfg, "head")
