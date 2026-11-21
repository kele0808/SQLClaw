import threading
from collections import Counter
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, update

from text2sql.common.tracing import get_trace_id, reset_trace_id, set_trace_id
from text2sql.db.models import Job
from text2sql.db.system import session_scope
from text2sql.jobs import worker
from text2sql.jobs.queue import NonRetryableJobError, enqueue

pytestmark = pytest.mark.integration


def _make_due(job_id: int) -> None:
    with session_scope() as session:
        session.execute(
            update(Job).where(Job.id == job_id).values(run_after=func.now() - timedelta(seconds=1))
        )


def _get(job_id: int) -> Job:
    with session_scope() as session:
        job = session.get(Job, job_id)
        assert job is not None
        return job


def test_200_noop_jobs_4_threads_each_runs_exactly_once(
    migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    executed: Counter[int] = Counter()
    lock = threading.Lock()

    def counting_noop(payload: dict[str, Any]) -> None:
        with lock:
            executed[int(payload["n"])] += 1

    monkeypatch.setitem(worker._HANDLERS, "noop", counting_noop)
    with session_scope() as session:
        for n in range(200):
            enqueue(session, "noop", {"n": n})

    def drain() -> None:
        while worker.run_once(lease_s=300):
            pass

    threads = [threading.Thread(target=drain) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert executed == Counter({n: 1 for n in range(200)})
    with session_scope() as session:
        rows = session.execute(
            select(Job.status, Job.attempts, func.count()).group_by(Job.status, Job.attempts)
        ).all()
    assert [tuple(row) for row in rows] == [("succeeded", 1, 200)]


def test_failing_job_retries_then_fails(
    migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def always_fails(payload: dict[str, Any]) -> None:
        raise RuntimeError("still broken")

    monkeypatch.setitem(worker._HANDLERS, "t_retry", always_fails)
    with session_scope() as session:
        job_id = enqueue(session, "t_retry", {}, max_attempts=3)

    for expected_attempts in (1, 2):
        assert worker.run_once(lease_s=300) is True
        job = _get(job_id)
        assert (job.status, job.attempts) == ("queued", expected_attempts)
        assert worker.run_once(lease_s=300) is False  # backoff not yet due
        _make_due(job_id)

    assert worker.run_once(lease_s=300) is True
    job = _get(job_id)
    assert (job.status, job.attempts) == ("failed", 3)
    assert job.last_error == "RuntimeError: still broken"


def test_non_retryable_job_fails_on_first_attempt(
    migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def rejects(payload: dict[str, Any]) -> None:
        raise NonRetryableJobError("datasource mismatch")

    monkeypatch.setitem(worker._HANDLERS, "t_reject", rejects)
    with session_scope() as session:
        job_id = enqueue(session, "t_reject", {}, max_attempts=3)
    assert worker.run_once(lease_s=300) is True
    job = _get(job_id)
    assert (job.status, job.attempts) == ("failed", 1)
    assert job.last_error == "NonRetryableJobError: datasource mismatch"


def test_worker_restores_enqueue_trace_id(
    migrated_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str | None] = []
    monkeypatch.setitem(worker._HANDLERS, "t_trace", lambda p: seen.append(get_trace_id()))
    token = set_trace_id("trace-worker-001")
    try:
        with session_scope() as session:
            enqueue(session, "t_trace", {})
    finally:
        reset_trace_id(token)
    assert worker.run_once(lease_s=300) is True
    assert seen == ["trace-worker-001"]
