from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tests.metadata_factories import make_column, make_server, make_snapshot_data, make_table
from text2sql.common.errors import AppError
from text2sql.metadata.snapshot import (
    ColumnInfo,
    SchemaSnapshot,
    TableInfo,
    check_supported_version,
    parse_version,
    snapshot_content_hash,
    table_content_hash,
)


def _loaded(lower_case_table_names: int = 0, version: str = "8.0.46") -> SchemaSnapshot:
    data = make_snapshot_data(server=make_server(version, lower_case_table_names))
    tables = tuple(
        TableInfo(
            **{k: v for k, v in t.model_dump().items() if k != "columns"},
            columns=tuple(ColumnInfo(**c.model_dump(), object_uuid=uuid4()) for c in t.columns),
            object_uuid=uuid4(),
        )
        for t in data.tables
    )
    return SchemaSnapshot(
        snapshot_id=1,
        datasource_id=data.datasource_id,
        database_name=data.database_name,
        server=data.server,
        content_hash=snapshot_content_hash(data),
        collected_at=data.collected_at,
        tables=tables,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("5.7.44", (5, 7, 44)),
        ("5.7.44-log", (5, 7, 44)),
        ("8.0.46", (8, 0, 46)),
        ("8.0.36-28.1", (8, 0, 36)),
    ],
)
def test_parse_version(raw: str, expected: tuple[int, int, int]) -> None:
    assert parse_version(raw) == expected


def test_parse_version_rejects_garbage() -> None:
    with pytest.raises(AppError) as excinfo:
        parse_version("unknown")
    assert excinfo.value.code == "METADATA_UNSUPPORTED_VERSION"


@pytest.mark.parametrize("version", [(5, 6, 51), (5, 7, 7), (5, 5, 62)])
def test_versions_below_5_7_8_are_rejected(version: tuple[int, int, int]) -> None:
    with pytest.raises(AppError) as excinfo:
        check_supported_version(version)
    assert excinfo.value.code == "METADATA_UNSUPPORTED_VERSION"


@pytest.mark.parametrize("version", [(5, 7, 8), (5, 7, 44), (8, 0, 46)])
def test_supported_versions_pass(version: tuple[int, int, int]) -> None:
    check_supported_version(version)


def test_models_are_frozen() -> None:
    column = make_column("id", 1)
    with pytest.raises(ValueError, match="frozen"):
        column.name = "other"


def test_hash_ignores_collected_at_and_row_estimate() -> None:
    first = make_snapshot_data()
    tables = tuple(t.model_copy(update={"row_estimate": 12345}) for t in first.tables)
    second = make_snapshot_data(tables).model_copy(
        update={"collected_at": datetime(2020, 1, 1, tzinfo=UTC)}
    )
    assert snapshot_content_hash(first) == snapshot_content_hash(second)
    assert table_content_hash(first.tables[0]) == table_content_hash(tables[0])


def test_hash_ignores_table_order_but_not_structure() -> None:
    data = make_snapshot_data()
    reordered = make_snapshot_data(tuple(reversed(data.tables)))
    assert snapshot_content_hash(data) == snapshot_content_hash(reordered)
    extra = make_table("t_extra", (make_column("id", 1),))
    changed = make_snapshot_data((*data.tables, extra))
    assert snapshot_content_hash(changed) != snapshot_content_hash(data)
    assert len(snapshot_content_hash(data)) == 64


def test_hash_changes_with_comment() -> None:
    data = make_snapshot_data()
    customer = data.tables[0]
    renamed = customer.model_copy(update={"comment": "客户"})
    assert table_content_hash(renamed) != table_content_hash(customer)


def test_table_lookup_respects_lower_case_table_names() -> None:
    assert _loaded(0).table("T_CUSTOMER") is None
    assert _loaded(0).table("t_customer") is not None
    found = _loaded(1).table("T_CUSTOMER")
    assert found is not None
    assert found.name == "t_customer"


def test_column_lookup_is_case_insensitive() -> None:
    snapshot = _loaded()
    column = snapshot.column("t_order", "CUSTOMER_ID")
    assert column is not None
    assert column.name == "customer_id"
    assert snapshot.column("t_order", "missing") is None
    assert snapshot.column("missing", "id") is None


def test_unique_column_sets_skip_prefix_indexes() -> None:
    sets = _loaded().unique_column_sets("t_order")
    assert sets == (frozenset({"id"}), frozenset({"customer_id", "order_no"}))
    assert _loaded().unique_column_sets("missing") == ()


@pytest.mark.parametrize(("version", "key"), [("5.7.44", "mysql57"), ("8.0.46", "mysql80")])
def test_target_key(version: str, key: str) -> None:
    assert _loaded(version=version).target_key() == key


def test_target_key_rejects_other_versions() -> None:
    with pytest.raises(AppError):
        _loaded(version="8.4.3").target_key()
