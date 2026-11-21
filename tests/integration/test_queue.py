from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, func, select, update

from text2sql.common.tracing import reset_trace_id, set_trace_id
from text2sql.db.models import Job
from text2sql.db.system import get_sessionmaker, session_scope
from text2sql.jobs.queue import (
    JobNotFoundError,
    claim,
    complete,
    enqueue,
    fail,
)

pytestmark = pytest.mark.integration


def _get(job_id: int) -> Job:
    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        return job


def _make_due(job_id: int) -> None:
    with session_scope() as session:
        session.execute(
            update(Job).where(Job.id == job_id).values(run_after=func.now() - timedelta(seconds=1))
        )


def _enqueue(max_attempts: int = 3) -> int:
    with session_scope() as session:
        return enqueue(session, "noop", {"n": 1}, max_attempts=max_attempts)


def test_enqueue_records_queued_job_and_trace_id(migrated_db: Engine) -> None:
    token = set_trace_id("trace-enqueue-01")
    try:
        job_id = _enqueue()
    finally:
        reset_trace_id(token)
    job = _get(job_id)
    assert job.status == "queued"
    assert job.attempts == 0
    assert job.max_attempts == 3
    assert job.payload == {"n": 1}
    assert job.trace_id == "trace-enqueue-01"


def test_claim_marks_running_and_sets_lease(migrated_db: Engine) -> None:
    job_id = _enqueue()
    with session_scope() as session:
        job = claim(session, ["noop"], lease_s=300)
        assert job is not None
        assert job.id == job_id
    job = _get(job_id)
    assert job.status == "running"
    assert job.attempts == 1
    assert job.locked_until is not None
    assert job.locked_until > datetime.now(UTC) + timedelta(seconds=250)


def test_claim_filters_kind_and_respects_run_after(migrated_db: Engine) -> None:
    with session_scope() as session:
        enqueue(
            session,
            "noop",
            {},
            run_after=datetime.now(UTC) + timedelta(hours=1),
        )
        enqueue(session, "other", {})
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is None
        assert claim(session, [], lease_s=300) is None


def test_claim_orders_by_run_after_then_id(migrated_db: Engine) -> None:
    first = _enqueue()
    second = _enqueue()
    with session_scope() as session:
        claimed = claim(session, ["noop"], lease_s=300)
        assert claimed is not None
        assert claimed.id == first
    with session_scope() as session:
        claimed = claim(session, ["noop"], lease_s=300)
        assert claimed is not None
        assert claimed.id == second


def test_concurrent_claims_skip_locked_rows(migrated_db: Engine) -> None:
    first = _enqueue()
    second = _enqueue()
    factory = get_sessionmaker()
    with factory() as session_a, factory() as session_b:
        job_a = claim(session_a, ["noop"], lease_s=300)
        job_b = claim(session_b, ["noop"], lease_s=300)
        assert job_a is not None
        assert job_b is not None
        assert {job_a.id, job_b.id} == {first, second}
        session_a.commit()
        session_b.commit()


def test_expired_lease_can_be_reclaimed(migrated_db: Engine) -> None:
    job_id = _enqueue()
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is not None
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is None
    with session_scope() as session:
        session.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(locked_until=func.now() - timedelta(seconds=1))
        )
    with session_scope() as session:
        job = claim(session, ["noop"], lease_s=300)
        assert job is not None
        assert job.id == job_id
    assert _get(job_id).attempts == 2


def test_complete(migrated_db: Engine) -> None:
    job_id = _enqueue()
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is not None
    with session_scope() as session:
        complete(session, job_id)
    job = _get(job_id)
    assert job.status == "succeeded"
    assert job.locked_until is None


def test_fail_retries_with_backoff_then_fails(migrated_db: Engine) -> None:
    job_id = _enqueue(max_attempts=2)
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is not None
    before = datetime.now(UTC)
    with session_scope() as session:
        fail(session, job_id, "RuntimeError: first")
    job = _get(job_id)
    assert job.status == "queued"
    assert job.locked_until is None
    assert job.last_error == "RuntimeError: first"
    assert job.run_after >= before + timedelta(seconds=1)  # 2^1 s backoff

    _make_due(job_id)
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is not None
    with session_scope() as session:
        fail(session, job_id, "RuntimeError: second")
    job = _get(job_id)
    assert job.status == "failed"
    assert job.attempts == 2
    assert job.last_error == "RuntimeError: second"


def test_fail_non_retryable_fails_immediately(migrated_db: Engine) -> None:
    job_id = _enqueue()
    with session_scope() as session:
        assert claim(session, ["noop"], lease_s=300) is not None
    with session_scope() as session:
        fail(session, job_id, "x" * 2500, retryable=False)
    job = _get(job_id)
    assert job.status == "failed"
    assert job.attempts == 1
    assert job.last_error == "x" * 2000


def test_unknown_job_raises(migrated_db: Engine) -> None:
    with session_scope() as session:
        with pytest.raises(JobNotFoundError):
            complete(session, 999_999)
        with pytest.raises(JobNotFoundError):
            fail(session, 999_999, "nope")


def test_job_count_is_isolated_per_test(migrated_db: Engine) -> None:
    with session_scope() as session:
        assert session.execute(select(func.count()).select_from(Job)).scalar_one() == 0
