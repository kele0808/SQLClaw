"""metadata catalog: snapshots, identities, comment reviews, releases, stale marks, value index

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Spec 02 4.3: the six snapshot tables reject UPDATE and DELETE.
SNAPSHOT_TABLES = (
    "metadata_snapshot",
    "metadata_table",
    "metadata_column",
    "metadata_index",
    "metadata_foreign_key",
    "metadata_view",
)

SNAPSHOT_IMMUTABLE_FUNCTION = """
CREATE FUNCTION metadata_snapshot_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'snapshot rows are immutable';
END;
$$
"""

# Spec 02 4.7: only status, evaluation_status, eval_run_id and note may change.
RELEASE_IMMUTABLE_FUNCTION = """
CREATE FUNCTION metadata_release_components_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.datasource_id IS DISTINCT FROM OLD.datasource_id
       OR NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id
       OR NEW.join_graph_version_id IS DISTINCT FROM OLD.join_graph_version_id
       OR NEW.semantic_version_id IS DISTINCT FROM OLD.semantic_version_id
       OR NEW.prompt_version IS DISTINCT FROM OLD.prompt_version
       OR NEW.model_version IS DISTINCT FROM OLD.model_version
       OR NEW.db_dialect IS DISTINCT FROM OLD.db_dialect
       OR NEW.db_version IS DISTINCT FROM OLD.db_version
       OR NEW.created_by IS DISTINCT FROM OLD.created_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.trace_id IS DISTINCT FROM OLD.trace_id THEN
        RAISE EXCEPTION 'release components are immutable';
    END IF;
    RETURN NEW;
END;
$$
"""


def upgrade() -> None:
    # Value index similarity search (Spec 02 4.11). pg_trgm is a trusted extension
    # since PostgreSQL 13: the database owner can create it without superuser.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "metadata_datasource",
        sa.Column("datasource_id", sa.String(length=64), nullable=False),
        sa.Column("dialect", sa.String(length=16), nullable=False),
        sa.Column("database_name", sa.String(length=128), nullable=False),
        sa.Column("server_version", sa.String(length=64), nullable=True),
        sa.Column("sql_mode", sa.Text(), nullable=True),
        sa.Column("time_zone", sa.String(length=64), nullable=True),
        sa.Column("system_time_zone", sa.String(length=64), nullable=True),
        sa.Column("character_set", sa.String(length=64), nullable=True),
        sa.Column("collation_name", sa.String(length=64), nullable=True),
        sa.Column("lower_case_table_names", sa.SmallInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("datasource_id", name=op.f("pk_metadata_datasource")),
    )
    op.create_table(
        "metadata_comment_review",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("datasource_id", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("object_key", sa.String(length=300), nullable=False),
        sa.Column("comment_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.CheckConstraint(
            "decision IN ('ALLOW', 'REDACT')", name=op.f("ck_metadata_comment_review_decision")
        ),
        sa.ForeignKeyConstraint(
            ["datasource_id"],
            ["metadata_datasource.datasource_id"],
            name=op.f("fk_metadata_comment_review_datasource_id_metadata_datasource"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_comment_review")),
        sa.UniqueConstraint(
            "datasource_id",
            "object_type",
            "object_key",
            "comment_hash",
            name=op.f("uq_metadata_comment_review_datasource_id"),
        ),
    )
    op.create_table(
        "metadata_snapshot",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("datasource_id", sa.String(length=64), nullable=False),
        sa.Column("database_name", sa.String(length=128), nullable=False),
        sa.Column("server_version", sa.String(length=64), nullable=False),
        sa.Column("version_major", sa.SmallInteger(), nullable=False),
        sa.Column("version_minor", sa.SmallInteger(), nullable=False),
        sa.Column("version_patch", sa.SmallInteger(), nullable=False),
        sa.Column("sql_mode", sa.Text(), nullable=False),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("system_time_zone", sa.String(length=64), nullable=False),
        sa.Column("character_set", sa.String(length=64), nullable=False),
        sa.Column("collation_name", sa.String(length=64), nullable=False),
        sa.Column("lower_case_table_names", sa.SmallInteger(), nullable=False),
        sa.Column("content_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("table_count", sa.Integer(), nullable=False),
        sa.Column("column_count", sa.Integer(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["datasource_id"],
            ["metadata_datasource.datasource_id"],
            name=op.f("fk_metadata_snapshot_datasource_id_metadata_datasource"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_snapshot")),
    )
    op.create_index(
        "ix_metadata_snapshot_datasource_hash",
        "metadata_snapshot",
        ["datasource_id", "content_hash"],
        unique=False,
    )
    op.create_table(
        "metadata_stale_mark",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("datasource_id", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("object_key", sa.String(length=300), nullable=False),
        sa.Column("reason_code", sa.String(length=48), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("change_event_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(length=128), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "object_type IN ('TABLE', 'COLUMN', 'EDGE', 'METRIC', 'DIMENSION', 'ENUM')",
            name=op.f("ck_metadata_stale_mark_object_type"),
        ),
        sa.CheckConstraint(
            "source IN ('SCHEMA_DIFF', 'EXECUTION_DRIFT', 'MANUAL')",
            name=op.f("ck_metadata_stale_mark_source"),
        ),
        sa.ForeignKeyConstraint(
            ["datasource_id"],
            ["metadata_datasource.datasource_id"],
            name=op.f("fk_metadata_stale_mark_datasource_id_metadata_datasource"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_stale_mark")),
    )
    op.create_index(
        "uq_metadata_stale_mark_open",
        "metadata_stale_mark",
        ["datasource_id", "object_type", "object_key"],
        unique=True,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.create_table(
        "metadata_object_identity",
        sa.Column("object_uuid", sa.Uuid(), nullable=False),
        sa.Column("datasource_id", sa.String(length=64), nullable=False),
        sa.Column("object_type", sa.String(length=16), nullable=False),
        sa.Column("object_key", sa.String(length=300), nullable=False),
        sa.Column("first_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("retired_at_snapshot_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(
            ["datasource_id"],
            ["metadata_datasource.datasource_id"],
            name=op.f("fk_metadata_object_identity_datasource_id_metadata_datasource"),
        ),
        sa.ForeignKeyConstraint(
            ["first_snapshot_id"],
            ["metadata_snapshot.id"],
            name=op.f("fk_metadata_object_identity_first_snapshot_id_metadata_snapshot"),
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_snapshot_id"],
            ["metadata_snapshot.id"],
            name=op.f("fk_metadata_object_identity_last_seen_snapshot_id_metadata_snapshot"),
        ),
        sa.ForeignKeyConstraint(
            ["retired_at_snapshot_id"],
            ["metadata_snapshot.id"],
            name=op.f("fk_metadata_object_identity_retired_at_snapshot_id_metadata_snapshot"),
        ),
        sa.PrimaryKeyConstraint("object_uuid", name=op.f("pk_metadata_object_identity")),
    )
    op.create_index(
        "uq_metadata_object_identity_active",
        "metadata_object_identity",
        ["datasource_id", "object_type", "object_key"],
        unique=True,
        postgresql_where=sa.text("retired_at_snapshot_id IS NULL"),
    )
    op.create_table(
        "metadata_release",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("datasource_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("join_graph_version_id", sa.BigInteger(), nullable=True),
        sa.Column("semantic_version_id", sa.BigInteger(), nullable=True),
        sa.Column("prompt_version", sa.String(length=80), nullable=True),
        sa.Column("model_version", sa.String(length=160), nullable=True),
        sa.Column("db_dialect", sa.String(length=16), nullable=False),
        sa.Column("db_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="CANDIDATE", nullable=False),
        sa.Column(
            "evaluation_status", sa.String(length=16), server_default="NOT_RUN", nullable=False
        ),
        sa.Column("eval_run_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "evaluation_status IN ('NOT_RUN', 'PASSED', 'FAILED')",
            name=op.f("ck_metadata_release_evaluation_status"),
        ),
        sa.CheckConstraint(
            "status IN ('CANDIDATE', 'PUBLISHED', 'REJECTED')",
            name=op.f("ck_metadata_release_status"),
        ),
        sa.ForeignKeyConstraint(
            ["datasource_id"],
            ["metadata_datasource.datasource_id"],
            name=op.f("fk_metadata_release_datasource_id_metadata_datasource"),
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["metadata_snapshot.id"],
            name=op.f("fk_metadata_release_snapshot_id_metadata_snapshot"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_release")),
    )
    op.create_table(
        "metadata_table",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("object_uuid", sa.Uuid(), nullable=False),
        sa.Column("table_name", sa.String(length=128), nullable=False),
        sa.Column("table_type", sa.String(length=16), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=True),
        sa.Column("table_comment", sa.Text(), nullable=False),
        sa.Column(
            "comment_flags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("row_estimate", sa.BigInteger(), nullable=True),
        sa.Column("content_hash", sa.CHAR(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["metadata_snapshot.id"],
            name=op.f("fk_metadata_table_snapshot_id_metadata_snapshot"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_table")),
        sa.UniqueConstraint(
            "snapshot_id", "table_name", name=op.f("uq_metadata_table_snapshot_id")
        ),
    )
    op.create_table(
        "metadata_column",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=False),
        sa.Column("object_uuid", sa.Uuid(), nullable=False),
        sa.Column("column_name", sa.String(length=128), nullable=False),
        sa.Column("ordinal_position", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.String(length=64), nullable=False),
        sa.Column("column_type", sa.Text(), nullable=False),
        sa.Column("is_nullable", sa.Boolean(), nullable=False),
        sa.Column("default_value", sa.Text(), nullable=True),
        sa.Column("extra", sa.Text(), nullable=False),
        sa.Column("generation_expression", sa.Text(), nullable=True),
        sa.Column("character_set", sa.String(length=64), nullable=True),
        sa.Column("collation_name", sa.String(length=64), nullable=True),
        sa.Column("column_comment", sa.Text(), nullable=False),
        sa.Column(
            "comment_flags",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("is_unique", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.CHAR(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["metadata_snapshot.id"],
            name=op.f("fk_metadata_column_snapshot_id_metadata_snapshot"),
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["metadata_table.id"],
            name=op.f("fk_metadata_column_table_id_metadata_table"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_column")),
        sa.UniqueConstraint("table_id", "column_name", name=op.f("uq_metadata_column_table_id")),
    )
    op.create_table(
        "metadata_foreign_key",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=False),
        sa.Column("constraint_name", sa.String(length=128), nullable=False),
        sa.Column("columns", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("ref_table", sa.String(length=128), nullable=False),
        sa.Column("ref_columns", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("on_update", sa.String(length=16), nullable=False),
        sa.Column("on_delete", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["metadata_snapshot.id"],
            name=op.f("fk_metadata_foreign_key_snapshot_id_metadata_snapshot"),
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["metadata_table.id"],
            name=op.f("fk_metadata_foreign_key_table_id_metadata_table"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_foreign_key")),
        sa.UniqueConstraint(
            "table_id", "constraint_name", name=op.f("uq_metadata_foreign_key_table_id")
        ),
    )
    op.create_table(
        "metadata_index",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=False),
        sa.Column("index_name", sa.String(length=128), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("is_unique", sa.Boolean(), nullable=False),
        sa.Column("columns", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("sub_parts", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("index_type", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["metadata_snapshot.id"],
            name=op.f("fk_metadata_index_snapshot_id_metadata_snapshot"),
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["metadata_table.id"],
            name=op.f("fk_metadata_index_table_id_metadata_table"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_index")),
        sa.UniqueConstraint("table_id", "index_name", name=op.f("uq_metadata_index_table_id")),
    )
    op.create_table(
        "metadata_release_event",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("datasource_id", sa.String(length=64), nullable=False),
        sa.Column("from_release_id", sa.BigInteger(), nullable=True),
        sa.Column("to_release_id", sa.BigInteger(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.CheckConstraint(
            "action IN ('ACTIVATE', 'PUBLISH', 'ROLLBACK')",
            name=op.f("ck_metadata_release_event_action"),
        ),
        sa.ForeignKeyConstraint(
            ["datasource_id"],
            ["metadata_datasource.datasource_id"],
            name=op.f("fk_metadata_release_event_datasource_id_metadata_datasource"),
        ),
        sa.ForeignKeyConstraint(
            ["from_release_id"],
            ["metadata_release.id"],
            name=op.f("fk_metadata_release_event_from_release_id_metadata_release"),
        ),
        sa.ForeignKeyConstraint(
            ["to_release_id"],
            ["metadata_release.id"],
            name=op.f("fk_metadata_release_event_to_release_id_metadata_release"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_release_event")),
    )
    op.create_table(
        "metadata_release_pointer",
        sa.Column("datasource_id", sa.String(length=64), nullable=False),
        sa.Column("release_id", sa.BigInteger(), nullable=False),
        sa.Column("previous_release_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.ForeignKeyConstraint(
            ["datasource_id"],
            ["metadata_datasource.datasource_id"],
            name=op.f("fk_metadata_release_pointer_datasource_id_metadata_datasource"),
        ),
        sa.ForeignKeyConstraint(
            ["previous_release_id"],
            ["metadata_release.id"],
            name=op.f("fk_metadata_release_pointer_previous_release_id_metadata_release"),
        ),
        sa.ForeignKeyConstraint(
            ["release_id"],
            ["metadata_release.id"],
            name=op.f("fk_metadata_release_pointer_release_id_metadata_release"),
        ),
        sa.PrimaryKeyConstraint("datasource_id", name=op.f("pk_metadata_release_pointer")),
    )
    op.create_table(
        "metadata_view",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("table_id", sa.BigInteger(), nullable=False),
        sa.Column("view_definition", sa.Text(), nullable=False),
        sa.Column("definition_available", sa.Boolean(), nullable=False),
        sa.Column("check_option", sa.String(length=16), nullable=False),
        sa.Column("security_type", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["metadata_snapshot.id"],
            name=op.f("fk_metadata_view_snapshot_id_metadata_snapshot"),
        ),
        sa.ForeignKeyConstraint(
            ["table_id"],
            ["metadata_table.id"],
            name=op.f("fk_metadata_view_table_id_metadata_table"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_view")),
        sa.UniqueConstraint("table_id", name=op.f("uq_metadata_view_table_id")),
    )

    op.create_table(
        "metadata_value_build",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("datasource_id", sa.String(length=64), nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("indexed_column_count", sa.Integer(), nullable=False),
        sa.Column("skipped_column_count", sa.Integer(), nullable=False),
        sa.Column("value_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["datasource_id"],
            ["metadata_datasource.datasource_id"],
            name=op.f("fk_metadata_value_build_datasource_id_metadata_datasource"),
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["metadata_snapshot.id"],
            name=op.f("fk_metadata_value_build_snapshot_id_metadata_snapshot"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_value_build")),
    )
    op.create_index(
        "ix_metadata_value_build_datasource",
        "metadata_value_build",
        ["datasource_id", "id"],
        unique=False,
    )
    op.create_table(
        "metadata_value_column",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("column_key", sa.String(length=300), nullable=False),
        sa.Column("skip_reason", sa.String(length=32), nullable=True),
        sa.Column("value_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["metadata_value_build.id"],
            name=op.f("fk_metadata_value_column_build_id_metadata_value_build"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_value_column")),
        sa.UniqueConstraint(
            "build_id", "column_key", name=op.f("uq_metadata_value_column_build_id")
        ),
    )
    op.create_table(
        "metadata_value",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("build_id", sa.BigInteger(), nullable=False),
        sa.Column("column_id", sa.BigInteger(), nullable=False),
        sa.Column("tenant_key", sa.String(length=64), nullable=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("value_norm", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["build_id"],
            ["metadata_value_build.id"],
            name=op.f("fk_metadata_value_build_id_metadata_value_build"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["column_id"],
            ["metadata_value_column.id"],
            name=op.f("fk_metadata_value_column_id_metadata_value_column"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metadata_value")),
    )
    op.create_index(
        "ix_metadata_value_build_norm", "metadata_value", ["build_id", "value_norm"], unique=False
    )
    op.create_index(
        "ix_metadata_value_norm_trgm",
        "metadata_value",
        ["value_norm"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"value_norm": "gin_trgm_ops"},
    )

    op.execute(SNAPSHOT_IMMUTABLE_FUNCTION)
    for table in SNAPSHOT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION metadata_snapshot_immutable()"
        )
    op.execute(RELEASE_IMMUTABLE_FUNCTION)
    op.execute(
        "CREATE TRIGGER metadata_release_components_immutable BEFORE UPDATE ON metadata_release "
        "FOR EACH ROW EXECUTE FUNCTION metadata_release_components_immutable()"
    )


def downgrade() -> None:
    op.drop_index("ix_metadata_value_norm_trgm", table_name="metadata_value")
    op.drop_index("ix_metadata_value_build_norm", table_name="metadata_value")
    op.drop_table("metadata_value")
    op.drop_table("metadata_value_column")
    op.drop_index("ix_metadata_value_build_datasource", table_name="metadata_value_build")
    op.drop_table("metadata_value_build")
    op.drop_table("metadata_view")
    op.drop_table("metadata_release_pointer")
    op.drop_table("metadata_release_event")
    op.drop_table("metadata_index")
    op.drop_table("metadata_foreign_key")
    op.drop_table("metadata_column")
    op.drop_table("metadata_table")
    op.drop_table("metadata_release")
    op.drop_index(
        "uq_metadata_object_identity_active",
        table_name="metadata_object_identity",
        postgresql_where=sa.text("retired_at_snapshot_id IS NULL"),
    )
    op.drop_table("metadata_object_identity")
    op.drop_index(
        "uq_metadata_stale_mark_open",
        table_name="metadata_stale_mark",
        postgresql_where=sa.text("resolved_at IS NULL"),
    )
    op.drop_table("metadata_stale_mark")
    op.drop_index("ix_metadata_snapshot_datasource_hash", table_name="metadata_snapshot")
    op.drop_table("metadata_snapshot")
    op.drop_table("metadata_comment_review")
    op.drop_table("metadata_datasource")
    op.execute("DROP FUNCTION metadata_release_components_immutable()")
    op.execute("DROP FUNCTION metadata_snapshot_immutable()")
    # pg_trgm stays installed: other objects may use it, and upgrade uses IF NOT EXISTS.
