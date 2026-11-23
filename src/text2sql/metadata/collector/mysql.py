"""MySQL 5.7 / 8.0 metadata collector (Spec 02 M2).

Reads only `information_schema` and session variables on one read-only
connection. Every selected column has an explicit lower-case alias because
8.0 returns information_schema column names in upper case and 5.7 does not.
"""

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Connection, Engine, text

from text2sql.common.errors import METADATA_NO_DATABASE, METADATA_TOO_LARGE, AppError
from text2sql.metadata.snapshot import (
    ColumnData,
    ForeignKeyInfo,
    IndexInfo,
    ServerInfo,
    SnapshotData,
    TableData,
    ViewInfo,
    check_supported_version,
    parse_version,
)
from text2sql.security.prompt_guard import scan_text

logger = logging.getLogger(__name__)

Row = Mapping[str, Any]

MAX_TABLES = 2000
MAX_COLUMNS = 50000

SERVER_SQL = """
SELECT VERSION() AS server_version, DATABASE() AS database_name,
       @@sql_mode AS sql_mode, @@time_zone AS time_zone,
       @@system_time_zone AS system_time_zone,
       @@character_set_database AS character_set,
       @@collation_database AS collation_name,
       @@lower_case_table_names AS lower_case_table_names
"""

TABLES_SQL = """
SELECT TABLE_NAME AS table_name, TABLE_TYPE AS table_type, ENGINE AS engine,
       TABLE_COMMENT AS table_comment, TABLE_ROWS AS table_rows
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME
"""

COLUMNS_SQL = """
SELECT TABLE_NAME AS table_name, COLUMN_NAME AS column_name,
       ORDINAL_POSITION AS ordinal_position, DATA_TYPE AS data_type,
       COLUMN_TYPE AS column_type, IS_NULLABLE AS is_nullable,
       COLUMN_DEFAULT AS column_default, COLUMN_KEY AS column_key, EXTRA AS extra,
       COLUMN_COMMENT AS column_comment, CHARACTER_SET_NAME AS character_set,
       COLLATION_NAME AS collation_name, GENERATION_EXPRESSION AS generation_expression
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME, ORDINAL_POSITION
"""

INDEXES_SQL = """
SELECT TABLE_NAME AS table_name, INDEX_NAME AS index_name, NON_UNIQUE AS non_unique,
       SEQ_IN_INDEX AS seq_in_index, COLUMN_NAME AS column_name, SUB_PART AS sub_part,
       INDEX_TYPE AS index_type
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
"""

# LEFT JOIN: if the account cannot see REFERENTIAL_CONSTRAINTS, the key is still
# collected and the ON UPDATE / ON DELETE rules are empty strings.
FOREIGN_KEYS_SQL = """
SELECT k.TABLE_NAME AS table_name, k.CONSTRAINT_NAME AS constraint_name,
       k.COLUMN_NAME AS column_name, k.ORDINAL_POSITION AS ordinal_position,
       k.REFERENCED_TABLE_SCHEMA AS ref_schema, k.REFERENCED_TABLE_NAME AS ref_table,
       k.REFERENCED_COLUMN_NAME AS ref_column,
       r.UPDATE_RULE AS on_update, r.DELETE_RULE AS on_delete
FROM information_schema.KEY_COLUMN_USAGE k
LEFT JOIN information_schema.REFERENTIAL_CONSTRAINTS r
  ON r.CONSTRAINT_SCHEMA = k.CONSTRAINT_SCHEMA
 AND r.CONSTRAINT_NAME = k.CONSTRAINT_NAME
 AND r.TABLE_NAME = k.TABLE_NAME
WHERE k.TABLE_SCHEMA = DATABASE() AND k.REFERENCED_TABLE_NAME IS NOT NULL
ORDER BY k.TABLE_NAME, k.CONSTRAINT_NAME, k.ORDINAL_POSITION
"""

VIEWS_SQL = """
SELECT TABLE_NAME AS table_name, VIEW_DEFINITION AS view_definition,
       CHECK_OPTION AS check_option, SECURITY_TYPE AS security_type
FROM information_schema.VIEWS
WHERE TABLE_SCHEMA = DATABASE()
ORDER BY TABLE_NAME
"""


@dataclass(frozen=True)
class RawMetadata:
    """Rows exactly as returned by the queries above."""

    server: Row
    tables: Sequence[Row]
    columns: Sequence[Row]
    indexes: Sequence[Row]
    foreign_keys: Sequence[Row]
    views: Sequence[Row]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _optional_text(value: Any) -> str | None:
    return None if value is None else _text(value)


def parse_server(row: Row) -> tuple[str, ServerInfo]:
    """Return `(database_name, server)`; fail on no default database or old versions."""
    database_name = _optional_text(row["database_name"])
    if not database_name:
        raise AppError(METADATA_NO_DATABASE, "business_db_url has no default database", 422)
    server_version = _text(row["server_version"])
    version = parse_version(server_version)
    check_supported_version(version)
    server = ServerInfo(
        server_version=server_version,
        version=version,
        sql_mode=_text(row["sql_mode"]),
        time_zone=_text(row["time_zone"]),
        system_time_zone=_text(row["system_time_zone"]),
        character_set=_text(row["character_set"]),
        collation=_text(row["collation_name"]),
        lower_case_table_names=int(row["lower_case_table_names"]),
    )
    return database_name, server


def _build_indexes(rows: Sequence[Row]) -> tuple[IndexInfo, ...]:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[_text(row["index_name"])].append(row)
    indexes = []
    for name in sorted(grouped):
        parts = sorted(grouped[name], key=lambda r: int(r["seq_in_index"]))
        indexes.append(
            IndexInfo(
                name=name,
                is_primary=name == "PRIMARY",
                is_unique=int(parts[0]["non_unique"]) == 0,
                columns=tuple(_text(p["column_name"]) for p in parts),
                sub_parts=tuple(
                    None if p["sub_part"] is None else int(p["sub_part"]) for p in parts
                ),
                index_type=_text(parts[0]["index_type"]),
            )
        )
    return tuple(indexes)


def _build_foreign_keys(
    rows: Sequence[Row], database_name: str, table_name: str
) -> tuple[ForeignKeyInfo, ...]:
    grouped: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        grouped[_text(row["constraint_name"])].append(row)
    keys = []
    for name in sorted(grouped):
        parts = sorted(grouped[name], key=lambda r: int(r["ordinal_position"]))
        ref_schema = _text(parts[0]["ref_schema"])
        if ref_schema != database_name:
            logger.warning(
                "metadata_cross_database_fk_ignored",
                extra={"table": table_name, "constraint": name, "ref_schema": ref_schema},
            )
            continue
        keys.append(
            ForeignKeyInfo(
                name=name,
                columns=tuple(_text(p["column_name"]) for p in parts),
                ref_table=_text(parts[0]["ref_table"]),
                ref_columns=tuple(_text(p["ref_column"]) for p in parts),
                on_update=_text(parts[0]["on_update"]),
                on_delete=_text(parts[0]["on_delete"]),
            )
        )
    return tuple(keys)


def _build_columns(rows: Sequence[Row], indexes: tuple[IndexInfo, ...]) -> tuple[ColumnData, ...]:
    primary = {c for i in indexes if i.is_primary for c in i.columns}
    single_unique = {
        i.columns[0]
        for i in indexes
        if i.is_unique and len(i.columns) == 1 and i.sub_parts == (None,)
    }
    columns = []
    for row in sorted(rows, key=lambda r: int(r["ordinal_position"])):
        name = _text(row["column_name"])
        comment = _text(row["column_comment"])
        columns.append(
            ColumnData(
                name=name,
                ordinal_position=int(row["ordinal_position"]),
                data_type=_text(row["data_type"]).lower(),
                column_type=_text(row["column_type"]),
                is_nullable=_text(row["is_nullable"]) == "YES",
                default_value=_optional_text(row["column_default"]),
                extra=_text(row["extra"]),
                generation_expression=_optional_text(row["generation_expression"]) or None,
                character_set=_optional_text(row["character_set"]),
                collation=_optional_text(row["collation_name"]),
                comment=comment,
                comment_flags=scan_text(comment),
                is_primary=name in primary,
                is_unique=name in single_unique,
            )
        )
    return tuple(columns)


def _build_view(row: Row | None) -> ViewInfo:
    if row is None:
        # Without SHOW VIEW the server may hide the VIEWS row entirely.
        return ViewInfo(
            definition="", definition_available=False, check_option="", security_type=""
        )
    definition = _text(row["view_definition"])
    return ViewInfo(
        definition=definition,
        definition_available=bool(definition),
        check_option=_text(row["check_option"]),
        security_type=_text(row["security_type"]),
    )


def build_snapshot(raw: RawMetadata, datasource_id: str, *, collected_at: datetime) -> SnapshotData:
    database_name, server = parse_server(raw.server)
    if len(raw.tables) > MAX_TABLES or len(raw.columns) > MAX_COLUMNS:
        raise AppError(
            METADATA_TOO_LARGE,
            f"{len(raw.tables)} tables / {len(raw.columns)} columns exceed "
            f"{MAX_TABLES} / {MAX_COLUMNS}",
            422,
        )

    def by_table(rows: Sequence[Row]) -> dict[str, list[Row]]:
        grouped: dict[str, list[Row]] = defaultdict(list)
        for row in rows:
            grouped[_text(row["table_name"])].append(row)
        return grouped

    columns, indexes, fks = by_table(raw.columns), by_table(raw.indexes), by_table(raw.foreign_keys)
    views = {_text(row["table_name"]): row for row in raw.views}
    tables = []
    for row in sorted(raw.tables, key=lambda r: _text(r["table_name"])):
        name = _text(row["table_name"])
        is_view = _text(row["table_type"]) == "VIEW"
        comment = _text(row["table_comment"])
        if is_view and comment == "VIEW":
            comment = ""  # MySQL reports the literal "VIEW" as a view's comment
        table_indexes = _build_indexes(indexes.get(name, []))
        tables.append(
            TableData(
                name=name,
                table_type="VIEW" if is_view else "BASE TABLE",
                engine=_optional_text(row["engine"]),
                comment=comment,
                comment_flags=scan_text(comment),
                row_estimate=None if row["table_rows"] is None else int(row["table_rows"]),
                columns=_build_columns(columns.get(name, []), table_indexes),
                indexes=table_indexes,
                foreign_keys=_build_foreign_keys(fks.get(name, []), database_name, name),
                view=_build_view(views.get(name)) if is_view else None,
            )
        )
    return SnapshotData(
        datasource_id=datasource_id,
        database_name=database_name,
        server=server,
        collected_at=collected_at,
        tables=tuple(tables),
    )


def _rows(conn: Connection, sql: str) -> list[Row]:
    return [dict(row) for row in conn.execute(text(sql)).mappings()]


def fetch_raw(conn: Connection) -> RawMetadata:
    server = dict(conn.execute(text(SERVER_SQL)).mappings().one())
    parse_server(server)  # fail fast before reading information_schema
    return RawMetadata(
        server=server,
        tables=_rows(conn, TABLES_SQL),
        columns=_rows(conn, COLUMNS_SQL),
        indexes=_rows(conn, INDEXES_SQL),
        foreign_keys=_rows(conn, FOREIGN_KEYS_SQL),
        views=_rows(conn, VIEWS_SQL),
    )


def collect_snapshot(engine: Engine, datasource_id: str) -> SnapshotData:
    with engine.connect() as conn:
        raw = fetch_raw(conn)
    return build_snapshot(raw, datasource_id, collected_at=datetime.now(UTC))
