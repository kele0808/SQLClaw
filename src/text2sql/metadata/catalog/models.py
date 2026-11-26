"""SQLAlchemy models for the metadata catalog (Spec 02 4.3, 4.5-4.8, 4.11).

The tables are created by migration `0002_metadata`; `tests/integration/test_migrations.py`
checks that this module and the migration stay identical.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from text2sql.db.models.base import Base

# Snapshot tables guarded by the `metadata_snapshot_immutable` trigger function.
SNAPSHOT_TABLES = (
    "metadata_snapshot",
    "metadata_table",
    "metadata_column",
    "metadata_index",
    "metadata_foreign_key",
    "metadata_view",
)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class MetadataDatasource(Base):
    __tablename__ = "metadata_datasource"

    datasource_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dialect: Mapped[str] = mapped_column(String(16), nullable=False)
    database_name: Mapped[str] = mapped_column(String(128), nullable=False)
    server_version: Mapped[str | None] = mapped_column(String(64))
    sql_mode: Mapped[str | None] = mapped_column(Text)
    time_zone: Mapped[str | None] = mapped_column(String(64))
    system_time_zone: Mapped[str | None] = mapped_column(String(64))
    character_set: Mapped[str | None] = mapped_column(String(64))
    collation_name: Mapped[str | None] = mapped_column(String(64))
    lower_case_table_names: Mapped[int | None] = mapped_column(SmallInteger)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _created_at()


class MetadataSnapshot(Base):
    __tablename__ = "metadata_snapshot"
    __table_args__ = (
        Index("ix_metadata_snapshot_datasource_hash", "datasource_id", "content_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    datasource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("metadata_datasource.datasource_id"), nullable=False
    )
    database_name: Mapped[str] = mapped_column(String(128), nullable=False)
    server_version: Mapped[str] = mapped_column(String(64), nullable=False)
    version_major: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    version_minor: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    version_patch: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sql_mode: Mapped[str] = mapped_column(Text, nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    system_time_zone: Mapped[str] = mapped_column(String(64), nullable=False)
    character_set: Mapped[str] = mapped_column(String(64), nullable=False)
    collation_name: Mapped[str] = mapped_column(String(64), nullable=False)
    lower_case_table_names: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    table_count: Mapped[int] = mapped_column(Integer, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64))


class MetadataTable(Base):
    __tablename__ = "metadata_table"
    __table_args__ = (UniqueConstraint("snapshot_id", "table_name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_snapshot.id"), nullable=False
    )
    object_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    table_name: Mapped[str] = mapped_column(String(128), nullable=False)
    table_type: Mapped[str] = mapped_column(String(16), nullable=False)
    engine: Mapped[str | None] = mapped_column(String(64))
    table_comment: Mapped[str] = mapped_column(Text, nullable=False)
    comment_flags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    row_estimate: Mapped[int | None] = mapped_column(BigInteger)
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)


class MetadataColumn(Base):
    __tablename__ = "metadata_column"
    __table_args__ = (UniqueConstraint("table_id", "column_name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_snapshot.id"), nullable=False
    )
    table_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_table.id"), nullable=False
    )
    object_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    column_name: Mapped[str] = mapped_column(String(128), nullable=False)
    ordinal_position: Mapped[int] = mapped_column(Integer, nullable=False)
    data_type: Mapped[str] = mapped_column(String(64), nullable=False)
    column_type: Mapped[str] = mapped_column(Text, nullable=False)
    is_nullable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    default_value: Mapped[str | None] = mapped_column(Text)
    extra: Mapped[str] = mapped_column(Text, nullable=False)
    generation_expression: Mapped[str | None] = mapped_column(Text)
    character_set: Mapped[str | None] = mapped_column(String(64))
    collation_name: Mapped[str | None] = mapped_column(String(64))
    column_comment: Mapped[str] = mapped_column(Text, nullable=False)
    comment_flags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_unique: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)


class MetadataIndex(Base):
    __tablename__ = "metadata_index"
    __table_args__ = (UniqueConstraint("table_id", "index_name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_snapshot.id"), nullable=False
    )
    table_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_table.id"), nullable=False
    )
    index_name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_unique: Mapped[bool] = mapped_column(Boolean, nullable=False)
    columns: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    sub_parts: Mapped[list[int | None]] = mapped_column(ARRAY(Integer), nullable=False)
    index_type: Mapped[str] = mapped_column(String(16), nullable=False)


class MetadataForeignKey(Base):
    __tablename__ = "metadata_foreign_key"
    __table_args__ = (UniqueConstraint("table_id", "constraint_name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_snapshot.id"), nullable=False
    )
    table_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_table.id"), nullable=False
    )
    constraint_name: Mapped[str] = mapped_column(String(128), nullable=False)
    columns: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    ref_table: Mapped[str] = mapped_column(String(128), nullable=False)
    ref_columns: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    on_update: Mapped[str] = mapped_column(String(16), nullable=False)
    on_delete: Mapped[str] = mapped_column(String(16), nullable=False)


class MetadataView(Base):
    __tablename__ = "metadata_view"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_snapshot.id"), nullable=False
    )
    table_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_table.id"), nullable=False, unique=True
    )
    view_definition: Mapped[str] = mapped_column(Text, nullable=False)
    definition_available: Mapped[bool] = mapped_column(Boolean, nullable=False)
    check_option: Mapped[str] = mapped_column(String(16), nullable=False)
    security_type: Mapped[str] = mapped_column(String(16), nullable=False)


class MetadataObjectIdentity(Base):
    __tablename__ = "metadata_object_identity"
    __table_args__ = (
        Index(
            "uq_metadata_object_identity_active",
            "datasource_id",
            "object_type",
            "object_key",
            unique=True,
            postgresql_where=text("retired_at_snapshot_id IS NULL"),
        ),
    )

    object_uuid: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    datasource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("metadata_datasource.datasource_id"), nullable=False
    )
    object_type: Mapped[str] = mapped_column(String(16), nullable=False)
    object_key: Mapped[str] = mapped_column(String(300), nullable=False)
    first_snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_snapshot.id"), nullable=False
    )
    last_seen_snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_snapshot.id"), nullable=False
    )
    retired_at_snapshot_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("metadata_snapshot.id")
    )


class MetadataCommentReview(Base):
    __tablename__ = "metadata_comment_review"
    __table_args__ = (
        UniqueConstraint("datasource_id", "object_type", "object_key", "comment_hash"),
        CheckConstraint("decision IN ('ALLOW', 'REDACT')", name="decision"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    datasource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("metadata_datasource.datasource_id"), nullable=False
    )
    object_type: Mapped[str] = mapped_column(String(16), nullable=False)
    object_key: Mapped[str] = mapped_column(String(300), nullable=False)
    comment_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reviewer: Mapped[str] = mapped_column(String(128), nullable=False)
    reviewed_at: Mapped[datetime] = _created_at()
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class MetadataRelease(Base):
    __tablename__ = "metadata_release"
    __table_args__ = (
        CheckConstraint("status IN ('CANDIDATE', 'PUBLISHED', 'REJECTED')", name="status"),
        CheckConstraint(
            "evaluation_status IN ('NOT_RUN', 'PASSED', 'FAILED')", name="evaluation_status"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    datasource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("metadata_datasource.datasource_id"), nullable=False
    )
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_snapshot.id"), nullable=False
    )
    join_graph_version_id: Mapped[int | None] = mapped_column(BigInteger)
    semantic_version_id: Mapped[int | None] = mapped_column(BigInteger)
    prompt_version: Mapped[str | None] = mapped_column(String(80))
    model_version: Mapped[str | None] = mapped_column(String(160))
    db_dialect: Mapped[str] = mapped_column(String(16), nullable=False)
    db_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="CANDIDATE")
    evaluation_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="NOT_RUN"
    )
    eval_run_id: Mapped[int | None] = mapped_column(BigInteger)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = _created_at()
    trace_id: Mapped[str | None] = mapped_column(String(64))


class MetadataReleasePointer(Base):
    __tablename__ = "metadata_release_pointer"

    datasource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("metadata_datasource.datasource_id"), primary_key=True
    )
    release_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_release.id"), nullable=False
    )
    previous_release_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("metadata_release.id")
    )
    updated_at: Mapped[datetime] = _created_at()
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)


class MetadataReleaseEvent(Base):
    __tablename__ = "metadata_release_event"
    __table_args__ = (
        CheckConstraint("action IN ('ACTIVATE', 'PUBLISH', 'ROLLBACK')", name="action"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    datasource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("metadata_datasource.datasource_id"), nullable=False
    )
    from_release_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("metadata_release.id")
    )
    to_release_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_release.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    trace_id: Mapped[str | None] = mapped_column(String(64))


class MetadataStaleMark(Base):
    __tablename__ = "metadata_stale_mark"
    __table_args__ = (
        Index(
            "uq_metadata_stale_mark_open",
            "datasource_id",
            "object_type",
            "object_key",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        CheckConstraint(
            "object_type IN ('TABLE', 'COLUMN', 'EDGE', 'METRIC', 'DIMENSION', 'ENUM')",
            name="object_type",
        ),
        CheckConstraint("source IN ('SCHEMA_DIFF', 'EXECUTION_DRIFT', 'MANUAL')", name="source"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    datasource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("metadata_datasource.datasource_id"), nullable=False
    )
    object_type: Mapped[str] = mapped_column(String(16), nullable=False)
    object_key: Mapped[str] = mapped_column(String(300), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(48), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    change_event_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[str | None] = mapped_column(String(128))
    resolution_note: Mapped[str | None] = mapped_column(Text)


class MetadataValueBuild(Base):
    """One value-index build (Spec 02 4.11). Rebuilt, not immutable; old builds are pruned."""

    __tablename__ = "metadata_value_build"
    __table_args__ = (Index("ix_metadata_value_build_datasource", "datasource_id", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    datasource_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("metadata_datasource.datasource_id"), nullable=False
    )
    snapshot_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_snapshot.id"), nullable=False
    )
    indexed_column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    skipped_column_count: Mapped[int] = mapped_column(Integer, nullable=False)
    value_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created_at()
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str | None] = mapped_column(String(64))


class MetadataValueColumn(Base):
    """Every text column considered by a build, indexed or skipped with a reason."""

    __tablename__ = "metadata_value_column"
    __table_args__ = (UniqueConstraint("build_id", "column_key"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    build_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_value_build.id", ondelete="CASCADE"), nullable=False
    )
    column_key: Mapped[str] = mapped_column(String(300), nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String(32))
    value_count: Mapped[int] = mapped_column(Integer, nullable=False)


class MetadataValue(Base):
    __tablename__ = "metadata_value"
    __table_args__ = (
        Index("ix_metadata_value_build_norm", "build_id", "value_norm"),
        # pg_trgm GIN index: similarity (%) and word similarity (<%) lookups.
        Index(
            "ix_metadata_value_norm_trgm",
            "value_norm",
            postgresql_using="gin",
            postgresql_ops={"value_norm": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    build_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_value_build.id", ondelete="CASCADE"), nullable=False
    )
    column_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("metadata_value_column.id", ondelete="CASCADE"), nullable=False
    )
    tenant_key: Mapped[str | None] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_norm: Mapped[str] = mapped_column(Text, nullable=False)
