"""D-46: the cross-process LLM/sandbox resource lease (Spec §7.2: never a model and a container at once).

One SQLite row (``resource_lock``) is the lease. An LLM call or a sandbox run acquires it before it
starts and releases it when it really ends; a background heartbeat keeps it fresh while held. A holder
that died (no heartbeat for ``ttl_s``) is taken over. Anyone who cannot get the lease within ``wait_s``
receives ``ResourceUnavailableError`` (``waiting for resources``) and nothing runs.

The in-process ``MODEL_LOCK`` in the gateway stays as an inner guard; this lease works across the API,
the worker and owner-run scripts.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.ids import new_ulid
from devteam_harness.errors import ResourceUnavailableError
from devteam_state.repositories import sandbox as repo

Holder = Literal["llm", "sandbox"]


class LeaseHandle:
    """A held lease. Release exactly once; the heartbeat stops on release."""

    def __init__(self, lease: ResourceLease, holder: Holder, owner: str) -> None:
        self._lease = lease
        self.holder = holder
        self.owner = owner
        self._stop = threading.Event()
        self._released = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._beat, name=f"devteam-lease-{holder}", daemon=True)
        self._thread.start()

    def _beat(self) -> None:
        interval = max(self._lease.ttl_s / 4, 0.05)
        while not self._stop.wait(interval):
            try:
                with self._lease.sessions.begin() as session:
                    if not repo.renew_resource_lock(session, owner=self.owner, clock=self._lease.clock):
                        return  # lost (taken over after a stall); the holder's work ends on its own
            except Exception:  # noqa: S112 - a missed heartbeat must not kill the holder's work
                continue

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self._stop.set()
        self._thread.join(timeout=5)
        with self._lease.sessions.begin() as session:
            repo.release_resource_lock(session, owner=self.owner)

    def __enter__(self) -> LeaseHandle:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class ResourceLease:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        *,
        ttl_s: float,
        wait_s: float,
        poll_s: float = 0.25,
        clock: Clock = system_clock,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.sessions = sessions
        self.ttl_s = ttl_s
        self.wait_s = wait_s
        self.poll_s = poll_s
        self.clock = clock
        self._sleep = sleep
        self._monotonic = monotonic

    def try_acquire(self, holder: Holder) -> LeaseHandle | None:
        owner = f"{holder}:{os.getpid()}:{new_ulid(self.clock)}"
        with self.sessions.begin() as session:
            result = repo.acquire_resource_lock(
                session, holder=holder, owner=owner, pid=os.getpid(), ttl_s=self.ttl_s, clock=self.clock
            )
        return LeaseHandle(self, holder, owner) if result.acquired else None

    def acquire(self, holder: Holder, *, wait_s: float | None = None) -> LeaseHandle:
        deadline = self._monotonic() + (self.wait_s if wait_s is None else wait_s)
        while True:
            handle = self.try_acquire(holder)
            if handle is not None:
                return handle
            if self._monotonic() >= deadline:
                raise ResourceUnavailableError(
                    "resource.busy", "the machine is busy with another model call or sandbox run"
                )
            self._sleep(self.poll_s)

    def holder_now(self) -> str | None:
        with self.sessions() as session:
            row = repo.current_resource_lock(session)
            return None if row is None else row.holder


__all__ = ["Holder", "LeaseHandle", "ResourceLease"]
