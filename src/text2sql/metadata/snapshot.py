"""Frozen domain model of a schema snapshot (Spec 02 4.4), shared by Specs 04-09.

`SnapshotData` is what the collector produces (no database ids, no UUIDs);
`SchemaSnapshot` is what the repository loads (with `snapshot_id` and the
`object_uuid` of every table and column).
"""

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from text2sql.common.errors import METADATA_UNSUPPORTED_VERSION, AppError

ObjectType = Literal["TABLE", "COLUMN", "EDGE", "METRIC", "DIMENSION", "ENUM"]

MIN_SERVER_VERSION = (5, 7, 8)

_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class ServerInfo(_Frozen):
    server_version: str
    version: tuple[int, int, int]
    sql_mode: str
    time_zone: str
    system_time_zone: str
    character_set: str
    collation: str
    lower_case_table_names: int


class ColumnData(_Frozen):
    name: str
    ordinal_position: int
    data_type: str
    column_type: str
    is_nullable: bool
    default_value: str | None
    extra: str
    generation_expression: str | None
    character_set: str | None
    collation: str | None
    comment: str
    comment_flags: tuple[str, ...]
    is_primary: bool
    is_unique: bool


class ColumnInfo(ColumnData):
    object_uuid: UUID


class IndexInfo(_Frozen):
    name: str
    is_primary: bool
    is_unique: bool
    columns: tuple[str, ...]
    sub_parts: tuple[int | None, ...]
    index_type: str


class ForeignKeyInfo(_Frozen):
    name: str
    columns: tuple[str, ...]
    ref_table: str
    ref_columns: tuple[str, ...]
    on_update: str
    on_delete: str


class ViewInfo(_Frozen):
    definition: str
    definition_available: bool
    check_option: str
    security_type: str


class TableData(_Frozen):
    name: str
    table_type: Literal["BASE TABLE", "VIEW"]
    engine: str | None
    comment: str
    comment_flags: tuple[str, ...]
    row_estimate: int | None
    columns: tuple[ColumnData, ...]
    indexes: tuple[IndexInfo, ...]
    foreign_keys: tuple[ForeignKeyInfo, ...]
    view: ViewInfo | None


class TableInfo(TableData):
    columns: tuple[ColumnInfo, ...]
    object_uuid: UUID


class SnapshotData(_Frozen):
    datasource_id: str
    database_name: str
    server: ServerInfo
    collected_at: datetime
    tables: tuple[TableData, ...]


class SchemaSnapshot(_Frozen):
    snapshot_id: int
    datasource_id: str
    database_name: str
    server: ServerInfo
    content_hash: str
    collected_at: datetime
    tables: tuple[TableInfo, ...]

    def table(self, name: str) -> TableInfo | None:
        """Match by `lower_case_table_names`: 0 is case-sensitive, 1 and 2 are not."""
        case_sensitive = self.server.lower_case_table_names == 0
        for table in self.tables:
            if table.name == name or (not case_sensitive and table.name.lower() == name.lower()):
                return table
        return None

    def column(self, table: str, column: str) -> ColumnInfo | None:
        found = self.table(table)
        if found is None:
            return None
        for col in found.columns:
            if col.name.lower() == column.lower():
                return col
        return None

    def unique_column_sets(self, table: str) -> tuple[frozenset[str], ...]:
        """PK and every unique index; prefix indexes do not prove uniqueness."""
        found = self.table(table)
        if found is None:
            return ()
        return tuple(
            frozenset(index.columns)
            for index in found.indexes
            if index.is_unique and all(part is None for part in index.sub_parts)
        )

    def target_key(self) -> Literal["mysql57", "mysql80"]:
        return target_key(self.server.version)


def parse_version(server_version: str) -> tuple[int, int, int]:
    """`"5.7.44-log"` -> `(5, 7, 44)`; the suffix stays in `server_version`."""
    match = _VERSION_PATTERN.match(server_version)
    if match is None:
        raise AppError(
            METADATA_UNSUPPORTED_VERSION, f"cannot parse server version {server_version!r}", 422
        )
    major, minor, patch = (int(part) for part in match.groups())
    return (major, minor, patch)


def check_supported_version(version: tuple[int, int, int]) -> None:
    if version < MIN_SERVER_VERSION:
        raise AppError(
            METADATA_UNSUPPORTED_VERSION,
            f"MySQL {'.'.join(map(str, version))} is older than 5.7.8",
            422,
        )


def target_key(version: tuple[int, int, int]) -> Literal["mysql57", "mysql80"]:
    if version[:2] == (5, 7):
        return "mysql57"
    if version[:2] == (8, 0):
        return "mysql80"
    raise AppError(
        METADATA_UNSUPPORTED_VERSION,
        f"no dialect target for MySQL {'.'.join(map(str, version))}",
        422,
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_table(table: TableData) -> dict[str, Any]:
    body = table.model_dump(mode="json", exclude={"row_estimate"})
    body["columns"] = sorted(body["columns"], key=lambda c: c["ordinal_position"])
    body["indexes"] = sorted(body["indexes"], key=lambda i: i["name"])
    body["foreign_keys"] = sorted(body["foreign_keys"], key=lambda f: f["name"])
    return body


def column_content_hash(column: ColumnData) -> str:
    return _sha256(column.model_dump(mode="json"))


def table_content_hash(table: TableData) -> str:
    """Hash of the table structure; `row_estimate` fluctuates and is excluded."""
    return _sha256(_canonical_table(table))


def snapshot_content_hash(data: SnapshotData) -> str:
    """Spec 02 4.3: sha256 of canonical JSON without `collected_at` and `row_estimate`."""
    body = data.model_dump(mode="json", exclude={"collected_at", "tables"})
    body["tables"] = [_canonical_table(t) for t in sorted(data.tables, key=lambda t: t.name)]
    return _sha256(body)
