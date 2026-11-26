import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect, text

from text2sql.db.models import Base
from text2sql.db.system import get_engine
from text2sql.metadata.catalog.models import SNAPSHOT_TABLES

pytestmark = pytest.mark.integration

METADATA_TABLES = {
    *SNAPSHOT_TABLES,
    "metadata_datasource",
    "metadata_object_identity",
    "metadata_comment_review",
    "metadata_release",
    "metadata_release_pointer",
    "metadata_release_event",
    "metadata_stale_mark",
    "metadata_value_build",
    "metadata_value_column",
    "metadata_value",
}


def test_upgrade_matches_models_and_downgrade_removes_table(
    system_db_url: str, alembic_cfg: Config
) -> None:
    engine = get_engine()
    command.downgrade(alembic_cfg, "base")
    command.upgrade(alembic_cfg, "head")
    try:
        inspector = inspect(engine)
        assert "jobs" in inspector.get_table_names()
        assert set(inspector.get_table_names()) >= METADATA_TABLES
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")) == 1
        index_names = {ix["name"] for ix in inspector.get_indexes("jobs")}
        assert "ix_jobs_status_run_after" in index_names
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
        assert diff == []

        command.downgrade(alembic_cfg, "0001")
        tables = set(inspect(engine).get_table_names())
        assert "jobs" in tables
        assert not METADATA_TABLES & tables

        command.downgrade(alembic_cfg, "base")
        assert "jobs" not in inspect(engine).get_table_names()
    finally:
        command.upgrade(alembic_cfg, "head")
