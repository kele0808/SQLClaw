import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from text2sql.db import system
from text2sql.db.models import Base, Job
from text2sql.db.models.jobs import JOB_STATUSES


def _sqlite_factory() -> sessionmaker[Session]:
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (v INTEGER)"))
    return sessionmaker(bind=engine, expire_on_commit=False)


def _count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return int(session.execute(text("SELECT count(*) FROM t")).scalar_one())


def test_session_scope_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _sqlite_factory()
    monkeypatch.setattr(system, "get_sessionmaker", lambda: factory)
    with system.session_scope() as session:
        session.execute(text("INSERT INTO t (v) VALUES (1)"))
    assert _count(factory) == 1


def test_session_scope_rolls_back_and_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = _sqlite_factory()
    monkeypatch.setattr(system, "get_sessionmaker", lambda: factory)
    with pytest.raises(RuntimeError, match="boom"), system.session_scope() as session:
        session.execute(text("INSERT INTO t (v) VALUES (1)"))
        raise RuntimeError("boom")
    assert _count(factory) == 0


def test_ping_returns_false_when_unreachable() -> None:
    assert system.ping() is False


def test_engine_uses_settings_url_and_is_cached() -> None:
    assert system.get_engine().url.port == 1
    assert system.get_engine() is system.get_engine()


def test_jobs_table_shape() -> None:
    table = Base.metadata.tables["jobs"]
    assert Job.__table__ is table
    assert set(table.columns.keys()) == {
        "id",
        "kind",
        "payload",
        "status",
        "attempts",
        "max_attempts",
        "run_after",
        "locked_until",
        "last_error",
        "trace_id",
        "created_at",
        "updated_at",
    }
    assert {index.name for index in table.indexes} == {"ix_jobs_status_run_after"}
    assert [column.name for column in table.primary_key.columns] == ["id"]
    assert JOB_STATUSES == ("queued", "running", "succeeded", "failed")
