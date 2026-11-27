from uuid import UUID

import pytest
from sqlalchemy import Engine, select, text

from text2sql.db.system import session_scope
from text2sql.metadata.catalog.models import MetadataObjectIdentity
from text2sql.metadata.identity import assign_identities, column_key

pytestmark = pytest.mark.integration

INSERT_SNAPSHOT = """
INSERT INTO metadata_snapshot (
  datasource_id, database_name, server_version, version_major, version_minor, version_patch,
  sql_mode, time_zone, system_time_zone, character_set, collation_name,
  lower_case_table_names, content_hash, table_count, column_count, collected_at)
VALUES ('ds_id', 'shop', '8.0.46', 8, 0, 46, '', 'SYSTEM', 'UTC', 'utf8mb4',
        'utf8mb4_general_ci', 0, md5(random()::text) || md5(random()::text), 0, 0, now())
RETURNING id
"""


def _new_snapshot(engine: Engine) -> int:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO metadata_datasource (datasource_id, dialect, database_name) "
                "VALUES ('ds_id', 'mysql', 'shop') ON CONFLICT DO NOTHING"
            )
        )
        return int(conn.execute(text(INSERT_SNAPSHOT)).scalar_one())


def _assign(snapshot_id: int, *keys: tuple[str, str]) -> dict[tuple[str, str], UUID]:
    with session_scope() as session:
        return dict(
            assign_identities(session, datasource_id="ds_id", snapshot_id=snapshot_id, keys=keys)
        )


def test_column_key() -> None:
    assert column_key("t_order", "status") == "t_order.status"


def test_keys_keep_uuid_while_present(metadata_db: Engine) -> None:
    keys = (("TABLE", "t"), ("COLUMN", "t.a"))
    first = _assign(_new_snapshot(metadata_db), *keys)
    second_snapshot = _new_snapshot(metadata_db)
    second = _assign(second_snapshot, *keys, ("COLUMN", "t.b"))
    assert second[("TABLE", "t")] == first[("TABLE", "t")]
    assert second[("COLUMN", "t.a")] == first[("COLUMN", "t.a")]
    assert ("COLUMN", "t.b") not in first
    with session_scope() as session:
        row = session.get(MetadataObjectIdentity, first[("TABLE", "t")])
        assert row is not None
        assert row.last_seen_snapshot_id == second_snapshot


def test_missing_key_is_retired_and_recreated_with_new_uuid(metadata_db: Engine) -> None:
    first = _assign(_new_snapshot(metadata_db), ("COLUMN", "t.a"))
    gone_snapshot = _new_snapshot(metadata_db)
    _assign(gone_snapshot)
    back = _assign(_new_snapshot(metadata_db), ("COLUMN", "t.a"))
    assert back[("COLUMN", "t.a")] != first[("COLUMN", "t.a")]
    with session_scope() as session:
        rows = session.scalars(
            select(MetadataObjectIdentity).order_by(MetadataObjectIdentity.first_snapshot_id)
        ).all()
    assert [r.retired_at_snapshot_id for r in rows] == [gone_snapshot, None]
