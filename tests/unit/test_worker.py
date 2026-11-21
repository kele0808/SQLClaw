import re
import signal
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest

from text2sql.common.tracing import get_trace_id
from text2sql.jobs import worker
from text2sql.jobs.queue import NonRetryableJobError


@dataclass
class FakeJob:
    id: int
    kind: str
    payload: dict[str, Any]
    trace_id: str | None = None


@dataclass
class Calls:
    claimed_kinds: list[list[str]] = field(default_factory=list)
    completed: list[int] = field(default_factory=list)
    failed: list[tuple[int, str, bool]] = field(default_factory=list)


def _install(monkeypatch: pytest.MonkeyPatch, job: FakeJob | None) -> Calls:
    calls = Calls()

    @contextmanager
    def fake_scope() -> Iterator[object]:
        yield object()

    def fake_claim(session: object, kinds: Sequence[str], lease_s: int) -> FakeJob | None:
        calls.claimed_kinds.append(list(kinds))
        return job

    def fake_complete(session: object, job_id: int) -> None:
        calls.completed.append(job_id)

    def fake_fail(session: object, job_id: int, error: str, *, retryable: bool = True) -> None:
        calls.failed.append((job_id, error, retryable))

    monkeypatch.setattr(worker, "session_scope", fake_scope)
    monkeypatch.setattr(worker, "claim", fake_claim)
    monkeypatch.setattr(worker, "complete", fake_complete)
    monkeypatch.setattr(worker, "fail", fake_fail)
    return calls


def test_noop_is_registered_and_duplicates_rejected() -> None:
    assert "noop" in worker.registered_kinds()
    with pytest.raises(ValueError):
        worker.register("noop")(lambda payload: None)


def test_idle_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, None)
    assert worker.run_once(lease_s=30) is False
    assert calls.claimed_kinds == [worker.registered_kinds()]


def test_success_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []
    monkeypatch.setitem(worker._HANDLERS, "t_ok", seen.append)
    calls = _install(monkeypatch, FakeJob(1, "t_ok", {"a": 1}))
    assert worker.run_once(lease_s=30) is True
    assert seen == [{"a": 1}]
    assert calls.completed == [1]
    assert calls.failed == []


def test_non_retryable_error_fails_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(payload: dict[str, Any]) -> None:
        raise NonRetryableJobError("bad payload")

    monkeypatch.setitem(worker._HANDLERS, "t_bad", handler)
    calls = _install(monkeypatch, FakeJob(2, "t_bad", {}))
    worker.run_once(lease_s=30)
    assert calls.failed == [(2, "NonRetryableJobError: bad payload", False)]
    assert calls.completed == []


def test_other_error_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(payload: dict[str, Any]) -> None:
        raise RuntimeError("boom")

    monkeypatch.setitem(worker._HANDLERS, "t_boom", handler)
    calls = _install(monkeypatch, FakeJob(3, "t_boom", {}))
    worker.run_once(lease_s=30)
    assert calls.failed == [(3, "RuntimeError: boom", True)]


def test_missing_handler_fails_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install(monkeypatch, FakeJob(4, "ghost", {}))
    worker.run_once(lease_s=30)
    assert calls.failed == [(4, "no handler registered for kind 'ghost'", False)]


def test_restores_job_trace_id(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setitem(worker._HANDLERS, "t_trace", lambda p: seen.append(get_trace_id()))
    _install(monkeypatch, FakeJob(5, "t_trace", {}, trace_id="trace-job-0001"))
    worker.run_once(lease_s=30)
    assert seen == ["trace-job-0001"]
    assert get_trace_id() is None


def test_generates_trace_id_when_job_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setitem(worker._HANDLERS, "t_trace", lambda p: seen.append(get_trace_id()))
    _install(monkeypatch, FakeJob(6, "t_trace", {}, trace_id=None))
    worker.run_once(lease_s=30)
    assert seen[0] is not None
    assert re.fullmatch(r"[0-9a-f]{32}", seen[0])


def test_run_worker_loops_until_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T2S_JOB_POLL_INTERVAL_S", "0.01")
    stop = threading.Event()
    results = iter([True, True, False])
    count = 0

    def fake_run_once(*, lease_s: int) -> bool:
        nonlocal count
        count += 1
        result = next(results)
        if not result:
            stop.set()
        return result

    monkeypatch.setattr(worker, "run_once", fake_run_once)
    worker.run_worker(stop)
    assert count == 3


def test_run_worker_survives_iteration_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("T2S_JOB_POLL_INTERVAL_S", "0.01")
    stop = threading.Event()
    count = 0

    def flaky_run_once(*, lease_s: int) -> bool:
        nonlocal count
        count += 1
        if count == 1:
            raise RuntimeError("database down")
        stop.set()
        return False

    monkeypatch.setattr(worker, "run_once", flaky_run_once)
    worker.run_worker(stop)
    assert count == 2


def test_signals_set_stop_event() -> None:
    previous_int = signal.getsignal(signal.SIGINT)
    previous_term = signal.getsignal(signal.SIGTERM)
    stop = threading.Event()
    try:
        worker.install_signal_handlers(stop)
        signal.raise_signal(signal.SIGTERM)
        assert stop.is_set()
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
