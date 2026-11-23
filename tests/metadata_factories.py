"""Builders for hand-made metadata snapshots used by unit and integration tests."""

from datetime import UTC, datetime
from typing import Any

from text2sql.metadata.snapshot import (
    ColumnData,
    ForeignKeyInfo,
    IndexInfo,
    ServerInfo,
    SnapshotData,
    TableData,
)


def make_server(version: str = "8.0.46", lower_case_table_names: int = 0) -> ServerInfo:
    major, minor, patch = (int(p) for p in version.split("-")[0].split("."))
    return ServerInfo(
        server_version=version,
        version=(major, minor, patch),
        sql_mode="ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES",
        time_zone="SYSTEM",
        system_time_zone="UTC",
        character_set="utf8mb4",
        collation="utf8mb4_general_ci",
        lower_case_table_names=lower_case_table_names,
    )


def make_column(name: str, position: int, **overrides: Any) -> ColumnData:
    values: dict[str, Any] = {
        "name": name,
        "ordinal_position": position,
        "data_type": "int",
        "column_type": "int",
        "is_nullable": False,
        "default_value": None,
        "extra": "",
        "generation_expression": None,
        "character_set": None,
        "collation": None,
        "comment": "",
        "comment_flags": (),
        "is_primary": False,
        "is_unique": False,
    }
    values.update(overrides)
    return ColumnData(**values)


def make_table(name: str, columns: tuple[ColumnData, ...], **overrides: Any) -> TableData:
    values: dict[str, Any] = {
        "name": name,
        "table_type": "BASE TABLE",
        "engine": "InnoDB",
        "comment": "",
        "comment_flags": (),
        "row_estimate": 0,
        "columns": columns,
        "indexes": (),
        "foreign_keys": (),
        "view": None,
    }
    values.update(overrides)
    return TableData(**values)


def sample_tables() -> tuple[TableData, ...]:
    customer = make_table(
        "t_customer",
        (
            make_column("id", 1, is_primary=True, is_unique=True),
            make_column(
                "name",
                2,
                data_type="varchar",
                column_type="varchar(64)",
                character_set="utf8mb4",
                collation="utf8mb4_general_ci",
                comment="客户名称",
            ),
        ),
        indexes=(
            IndexInfo(
                name="PRIMARY",
                is_primary=True,
                is_unique=True,
                columns=("id",),
                sub_parts=(None,),
                index_type="BTREE",
            ),
        ),
    )
    order = make_table(
        "t_order",
        (
            make_column("id", 1, is_primary=True, is_unique=True),
            make_column("customer_id", 2),
            make_column("order_no", 3, data_type="varchar", column_type="varchar(32)"),
        ),
        indexes=(
            IndexInfo(
                name="PRIMARY",
                is_primary=True,
                is_unique=True,
                columns=("id",),
                sub_parts=(None,),
                index_type="BTREE",
            ),
            IndexInfo(
                name="ix_order_no_prefix",
                is_primary=False,
                is_unique=True,
                columns=("order_no",),
                sub_parts=(8,),
                index_type="BTREE",
            ),
            IndexInfo(
                name="uk_customer_order",
                is_primary=False,
                is_unique=True,
                columns=("customer_id", "order_no"),
                sub_parts=(None, None),
                index_type="BTREE",
            ),
        ),
        foreign_keys=(
            ForeignKeyInfo(
                name="fk_order_customer",
                columns=("customer_id",),
                ref_table="t_customer",
                ref_columns=("id",),
                on_update="RESTRICT",
                on_delete="RESTRICT",
            ),
        ),
    )
    return (customer, order)


def make_snapshot_data(
    tables: tuple[TableData, ...] | None = None,
    *,
    datasource_id: str = "ds_test",
    server: ServerInfo | None = None,
) -> SnapshotData:
    return SnapshotData(
        datasource_id=datasource_id,
        database_name="t2s_meta_fixture",
        server=server or make_server(),
        collected_at=datetime.now(UTC),
        tables=sample_tables() if tables is None else tables,
    )
