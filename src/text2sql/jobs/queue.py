"""PostgreSQL job queue (FOR UPDATE SKIP LOCKED). Callers own the transaction."""

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, aliased

from text2sql.common.tracing import get_trace_id
from text2sql.db.models import Job

MAX_ERROR_LENGTH = 2000
MAX_KIND_LENGTH = 64


class NonRetryableJobError(Exception):
    """Raised by a handler when retrying is pointless; the job fails at once."""


class JobNotFoundError(LookupError):
    """The job id does not exist."""


def backoff_seconds(attempts: int) -> int:
    return int(2**attempts)


def truncate_error(error: str) -> str:
    return error[:MAX_ERROR_LENGTH]


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any],
    *,
    max_attempts: int = 3,
    run_after: datetime | None = None,
) -> int:
    if not kind or len(kind) > MAX_KIND_LENGTH:
        raise ValueError(f"kind must be 1..{MAX_KIND_LENGTH} characters")
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    job = Job(
        kind=kind,
        payload=payload,
        status="queued",
        attempts=0,
        max_attempts=max_attempts,
        trace_id=get_trace_id(),
    )
    if run_after is not None:
        job.run_after = run_after
    session.add(job)
    session.flush()
    return job.id


def claim(session: Session, kinds: Sequence[str], lease_s: int) -> Job | None:
    if not kinds:
        return None
    candidate = aliased(Job, name="candidate")
    next_id = (
        select(candidate.id)
        .where(
            candidate.kind.in_(list(kinds)),
            or_(
                and_(candidate.status == "queued", candidate.run_after <= func.now()),
                and_(
                    candidate.status == "running",
                    candidate.locked_until < func.now(),
                ),
            ),
        )
        .order_by(candidate.run_after, candidate.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    stmt = (
        update(Job)
        .where(Job.id == next_id)
        .values(
            status="running",
            attempts=Job.attempts + 1,
            locked_until=func.now() + timedelta(seconds=lease_s),
            updated_at=func.now(),
        )
        .returning(Job)
    )
    return session.scalars(stmt).one_or_none()


def complete(session: Session, job_id: int) -> None:
    stmt = (
        update(Job)
        .where(Job.id == job_id)
        .values(status="succeeded", locked_until=None, updated_at=func.now())
        .returning(Job.id)
    )
    if session.execute(stmt).scalar_one_or_none() is None:
        raise JobNotFoundError(job_id)


def fail(session: Session, job_id: int, error: str, *, retryable: bool = True) -> None:
    row = session.execute(
        select(Job.attempts, Job.max_attempts).where(Job.id == job_id).with_for_update()
    ).one_or_none()
    if row is None:
        raise JobNotFoundError(job_id)
    attempts, max_attempts = row
    values: dict[str, Any] = {
        "last_error": truncate_error(error),
        "locked_until": None,
        "updated_at": func.now(),
    }
    if retryable and attempts < max_attempts:
        values["status"] = "queued"
        values["run_after"] = func.now() + timedelta(seconds=backoff_seconds(attempts))
    else:
        values["status"] = "failed"
    session.execute(update(Job).where(Job.id == job_id).values(**values))
