"""D-46 resource lease: exclusive across threads and processes, heartbeat, stale takeover, wait limit."""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest

from devteam_harness.budgets.resource_lease import ResourceLease
from devteam_harness.errors import ResourceUnavailableError
from devteam_state import create_sqlite_engine, session_factory, upgrade


def _lease(db: Path, ttl_s: float = 5) -> ResourceLease:
    return ResourceLease(session_factory(create_sqlite_engine(db)), ttl_s=ttl_s, wait_s=0.5, poll_s=0.05)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "state.db"
    upgrade(path)
    return path


def test_second_holder_waits_then_gets_resource_unavailable(db: Path) -> None:
    lease = _lease(db)
    with lease.acquire("sandbox"):
        assert lease.holder_now() == "sandbox"
        with pytest.raises(ResourceUnavailableError) as info:
            lease.acquire("llm", wait_s=0.3)
        assert info.value.code == "resource.busy"
    assert lease.holder_now() is None
    with lease.acquire("llm"):
        assert lease.holder_now() == "llm"


def test_heartbeat_keeps_a_long_holder_alive(db: Path) -> None:
    lease = _lease(db, ttl_s=0.4)
    with lease.acquire("sandbox"):
        time.sleep(1.2)  # three TTLs: without heartbeats the lease would look dead
        assert lease.try_acquire("llm") is None


def test_dead_holder_is_taken_over(db: Path) -> None:
    lease = _lease(db, ttl_s=0.3)
    handle = lease.acquire("llm")
    handle._stop.set()  # simulate a crashed process: heartbeat stops, row stays
    handle._thread.join()
    time.sleep(0.5)
    taker = lease.acquire("sandbox", wait_s=1)
    assert lease.holder_now() == "sandbox"
    taker.release()
    handle.release()  # releasing a lost lease is harmless
    assert lease.holder_now() is None


def _hold_in_child(
    db: str, ready: multiprocessing.synchronize.Event, done: multiprocessing.synchronize.Event
) -> None:
    lease = ResourceLease(session_factory(create_sqlite_engine(Path(db))), ttl_s=5, wait_s=1)
    with lease.acquire("llm"):
        ready.set()
        done.wait(10)


def test_lease_is_exclusive_across_processes(db: Path) -> None:
    ctx = multiprocessing.get_context("spawn")
    ready, done = ctx.Event(), ctx.Event()
    child = ctx.Process(target=_hold_in_child, args=(str(db), ready, done))
    child.start()
    try:
        assert ready.wait(60), "child process never acquired the lease"
        lease = _lease(db)
        with pytest.raises(ResourceUnavailableError):
            lease.acquire("sandbox", wait_s=0.5)
        done.set()
        child.join(30)
        assert child.exitcode == 0
        with lease.acquire("sandbox", wait_s=5):
            assert lease.holder_now() == "sandbox"
    finally:
        done.set()
        child.join(5)
        if child.is_alive():
            child.kill()
