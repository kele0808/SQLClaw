import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration

INSERT_DATASOURCE = """
INSERT INTO metadata_datasource (datasource_id, dialect, database_name)
VALUES ('ds_trig', 'mysql', 'shop')
"""

INSERT_SNAPSHOT = """
INSERT INTO metadata_snapshot (
  datasource_id, database_name, server_version, version_major, version_minor, version_patch,
  sql_mode, time_zone, system_time_zone, character_set, collation_name,
  lower_case_table_names, content_hash, table_count, column_count, collected_at)
VALUES ('ds_trig', 'shop', '8.0.46', 8, 0, 46, '', 'SYSTEM', 'UTC', 'utf8mb4',
        'utf8mb4_general_ci', 0, repeat('a', 64), 1, 1, now())
RETURNING id
"""

INSERT_TABLE = """
INSERT INTO metadata_table (snapshot_id, object_uuid, table_name, table_type, engine,
                            table_comment, content_hash)
VALUES (:sid, gen_random_uuid(), 't_a', 'BASE TABLE', 'InnoDB', '', repeat('b', 64))
"""

INSERT_RELEASE = """
INSERT INTO metadata_release (datasource_id, snapshot_id, db_dialect, db_version, created_by)
VALUES ('ds_trig', :sid, 'mysql', '8.0.46', 'tester')
RETURNING id
"""


def _seed(engine: Engine) -> tuple[int, int]:
    with engine.begin() as conn:
        conn.execute(text(INSERT_DATASOURCE))
        snapshot_id = int(conn.execute(text(INSERT_SNAPSHOT)).scalar_one())
        conn.execute(text(INSERT_TABLE), {"sid": snapshot_id})
        release_id = int(conn.execute(text(INSERT_RELEASE), {"sid": snapshot_id}).scalar_one())
    return snapshot_id, release_id


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE metadata_snapshot SET table_count = 2",
        "DELETE FROM metadata_snapshot",
        "UPDATE metadata_table SET table_comment = 'x'",
        "DELETE FROM metadata_table",
    ],
)
def test_snapshot_rows_are_immutable(metadata_db: Engine, statement: str) -> None:
    _seed(metadata_db)
    with (
        pytest.raises(DBAPIError, match="snapshot rows are immutable"),
        metadata_db.begin() as conn,
    ):
        conn.execute(text(statement))


def test_release_components_are_immutable(metadata_db: Engine) -> None:
    snapshot_id, release_id = _seed(metadata_db)
    with (
        pytest.raises(DBAPIError, match="release components are immutable"),
        metadata_db.begin() as conn,
    ):
        conn.execute(
            text("UPDATE metadata_release SET snapshot_id = :sid WHERE id = :rid"),
            {"sid": snapshot_id + 1, "rid": release_id},
        )


def test_release_status_fields_can_change(metadata_db: Engine) -> None:
    _, release_id = _seed(metadata_db)
    with metadata_db.begin() as conn:
        conn.execute(
            text(
                "UPDATE metadata_release SET status = 'PUBLISHED', evaluation_status = 'PASSED', "
                "eval_run_id = 7, note = 'ok' WHERE id = :rid"
            ),
            {"rid": release_id},
        )
        status = conn.execute(
            text("SELECT status FROM metadata_release WHERE id = :rid"), {"rid": release_id}
        ).scalar_one()
    assert status == "PUBLISHED"
