"""The run worker (D.4; LG-07; D-41): one process, one run at a time, crash-safe.

* **Lease.** The single ``worker_lease`` row: a second live worker refuses to start; a lease older than
  ``lease_ttl_s`` belongs to a dead worker and is taken over.
* **Heartbeat.** A thread renews the lease and the current run's ``heartbeat_at`` every
  ``heartbeat_interval_s`` and records metric samples (free RAM, worker memory, queue depth). If the
  lease is lost, the worker stops at the next node boundary.
* **Recovery on start.** Jobs left ``running`` by a dead worker are closed as failed and their runs get
  a ``resume`` job; runs still ``running`` (or waiting for resources) without a queued job get one. Tasks
  left ``running`` are handled when their node runs again (D-40, ``steps.recover_crash``).
* **Loop.** Claim the oldest due job, ``advance`` its run, finish the job. Nothing runs in parallel
  (Spec §7.2; D-27 (b): one at a time on this laptop).
* **Stop.** ``stop()`` (or Ctrl+C) finishes at the next node boundary; a hard kill is safe by design.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import psutil
from sqlalchemy import select

from devteam_core.contracts.events import MetricSample
from devteam_core.contracts.statuses import RunStatus
from devteam_core.ids import new_id
from devteam_core.logging import get_logger
from devteam_orchestration.execution import Advance, advance
from devteam_orchestration.runtime import Runtime
from devteam_state.models import Run, RunJob
from devteam_state.repositories import audit
from devteam_state.repositories import orchestration as orch

WORKER = "service:run_worker"
_log = get_logger("devteam_worker")
R = RunStatus


@dataclass(frozen=True)
class JobOutcome:
    job_id: str
    run_id: str
    kind: str
    result: Advance


class Worker:
    def __init__(self, rt: Runtime, graph: Any, *, owner: str | None = None) -> None:
        self.rt = rt
        self.graph = graph
        self.owner = owner or f"worker-{os.getpid()}-{new_id('wrk', rt.clock)[-8:]}"
        self.settings = rt.settings.orchestration
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._current_run: tuple[str, str] | None = None
        self._heartbeat: threading.Thread | None = None

    # ------------------------------------------------------------------------------------ lease

    def start(self, *, wait_s: float = 0.0) -> bool:
        """Take the lease and recover. ``False``: another live worker holds the lease (refuse to start).

        ``wait_s`` lets a worker restarted right after a crash wait for the dead worker's lease to go
        stale (at most one lease lifetime); a live worker keeps renewing its lease, so it is still refused.
        """
        deadline = time.monotonic() + max(0.0, wait_s)
        while True:
            with self.rt.sessions.begin() as session:
                lease = orch.acquire_lease(
                    session,
                    owner=self.owner,
                    pid=os.getpid(),
                    ttl_s=self.settings.lease_ttl_s,
                    clock=self.rt.clock,
                )
                if lease.acquired:
                    audit.append(
                        session,
                        action="worker.started",
                        outcome="success",
                        principal_id=WORKER,
                        details={
                            "owner": self.owner,
                            "pid": os.getpid(),
                            "took_over_from": lease.taken_over_from,
                        },
                        clock=self.rt.clock,
                    )
            if lease.acquired or time.monotonic() >= deadline:
                break
            time.sleep(min(self.settings.heartbeat_interval_s, max(0.1, deadline - time.monotonic())))
        if not lease.acquired:
            _log.warning("worker_refused_lease_held")
            return False
        self.recover()
        self._heartbeat = threading.Thread(target=self._beat, name="devteam-heartbeat", daemon=True)
        self._heartbeat.start()
        return True

    def stop(self) -> None:
        self._stop.set()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set() or self._lost.is_set()

    def close(self) -> None:
        self._stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=self.settings.heartbeat_interval_s + 5)
        with self.rt.sessions.begin() as session:
            orch.release_lease(session, owner=self.owner)

    def _beat(self) -> None:
        process = psutil.Process()
        while not self._stop.wait(self.settings.heartbeat_interval_s):
            try:
                with self.rt.sessions.begin() as session:
                    if not orch.renew_lease(session, owner=self.owner, clock=self.rt.clock):
                        _log.error("worker_lease_lost")
                        self._lost.set()
                        return
                    current = self._current_run
                    if current is not None:
                        run = session.get(Run, current[1])
                        if run is not None:
                            run.heartbeat_at = self.rt.clock.now()
                        samples = {
                            "free_ram_mb": int(psutil.virtual_memory().available // (1024 * 1024)),
                            "worker_rss_mb": int(process.memory_info().rss // (1024 * 1024)),
                            "queue_depth": orch.pending_jobs(session),
                        }
                        for metric, value in samples.items():
                            orch.append_event(
                                session,
                                MetricSample(
                                    event_id=new_id("evt", self.rt.clock),
                                    ts=self.rt.clock.now(),
                                    project_id=current[0],
                                    run_id=current[1],
                                    actor=WORKER,
                                    metric=metric,
                                    value=value,
                                ),
                            )
            except Exception:
                _log.exception("worker_heartbeat_failed")

    # --------------------------------------------------------------------------------- recovery

    def recover(self) -> int:
        """Re-queue work a dead worker left behind. Returns the number of runs re-queued."""
        requeued: set[str] = set()
        with self.rt.sessions.begin() as session:
            stale = self.rt.clock.now() - timedelta(seconds=self.settings.lease_ttl_s)
            for job in session.scalars(select(RunJob).where(RunJob.status == "running")):
                orch.finish_job(session, job.id, "failed", error_code="worker.crashed", clock=self.rt.clock)
                requeued.add(job.run_id)
            queued = {j.run_id for j in session.scalars(select(RunJob).where(RunJob.status == "queued"))}
            for run in session.scalars(
                select(Run).where(Run.status.in_([R.RUNNING.value, R.WAITING_FOR_RESOURCES.value]))
            ):
                beat = run.heartbeat_at
                if beat is not None and beat.tzinfo is None:
                    beat = beat.replace(tzinfo=stale.tzinfo)
                if (
                    run.status == R.RUNNING.value
                    and beat is not None
                    and beat > stale
                    and run.id not in requeued
                ):
                    continue  # another worker beat recently; nothing to recover
                requeued.add(run.id)
            for run_id in sorted(requeued - queued):
                found = session.get(Run, run_id)
                if found is None or found.status not in {R.RUNNING.value, R.WAITING_FOR_RESOURCES.value}:
                    continue
                orch.enqueue_job(
                    session, project_id=found.project_id, run_id=run_id, kind="resume", clock=self.rt.clock
                )
                audit.append(
                    session,
                    action="run.recovered",
                    outcome="success",
                    principal_id=WORKER,
                    project_id=found.project_id,
                    target_type="run",
                    target_id=run_id,
                    details={"owner": self.owner},
                    clock=self.rt.clock,
                )
        return len(requeued - queued)

    # ------------------------------------------------------------------------------------- loop

    def run_once(self) -> JobOutcome | None:
        """Process one due job, if any."""
        with self.rt.sessions.begin() as session:
            job = orch.claim_next_job(session, clock=self.rt.clock)
            if job is None:
                return None
            job_id, run_id, project_id, kind, payload = (
                job.id,
                job.run_id,
                job.project_id,
                job.kind,
                dict(job.payload),
            )
        self._current_run = (project_id, run_id)
        try:
            result = advance(
                self.rt,
                self.graph,
                project_id=project_id,
                run_id=run_id,
                kind=kind,  # type: ignore[arg-type]
                payload=payload,
                should_stop=lambda: self.stopping,
            )
        except Exception as exc:
            _log.exception("worker_job_failed", run_id=run_id)
            with self.rt.sessions.begin() as session:
                orch.finish_job(session, job_id, "failed", error_code="worker.job_error", clock=self.rt.clock)
            return JobOutcome(job_id, run_id, kind, Advance("failed", type(exc).__name__))
        finally:
            self._current_run = None
        with self.rt.sessions.begin() as session:
            if result.result == "stopped":
                orch.finish_job(session, job_id, "failed", error_code="worker.stopped", clock=self.rt.clock)
                orch.enqueue_job(
                    session, project_id=project_id, run_id=run_id, kind="resume", clock=self.rt.clock
                )
            else:
                orch.finish_job(session, job_id, "done", clock=self.rt.clock)
        _log.info("worker_job_done", run_id=run_id, kind=kind, result=result.result)
        return JobOutcome(job_id, run_id, kind, result)

    def run_forever(self) -> None:
        while not self.stopping:
            if self.run_once() is None:
                self._stop.wait(self.settings.poll_interval_s)
