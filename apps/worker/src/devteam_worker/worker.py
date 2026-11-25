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

import json
import os
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import psutil
from sqlalchemy import select

from devteam_core.contracts.events import MetricSample
from devteam_core.contracts.sandbox import TemplateVerificationStatus
from devteam_core.contracts.statuses import RunStatus
from devteam_core.ids import new_id
from devteam_core.logging import get_logger
from devteam_harness.errors import HarnessError, ResourceUnavailableError
from devteam_orchestration.execution import Advance, advance
from devteam_orchestration.runtime import Runtime
from devteam_services.templates import refresh_stale, run_verification
from devteam_state.models import Run, RunJob
from devteam_state.repositories import audit
from devteam_state.repositories import model_calls as model_calls_repo
from devteam_state.repositories import orchestration as orch
from devteam_state.repositories import rag as rag_repo
from devteam_state.repositories import sandbox as sandbox_repo

WORKER = "service:run_worker"
SANDBOX_STATUS_FILE = "sandbox_status.json"  # under data/; read by the API (the API never contacts Docker)
SANDBOX_STATUS_INTERVAL_S = 60.0
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
        self._sandbox_checked = 0.0
        self._models_checked = -1e9  # first idle pass records the model inventory (Part F.6)

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
        self.refresh_sandbox(sweep=True)
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
            idle = self.run_once() is None
            if idle and not self.run_verification_once() and not self.run_index_job_once():
                if time.monotonic() - self._sandbox_checked >= SANDBOX_STATUS_INTERVAL_S:
                    self.refresh_sandbox()
                if time.monotonic() - self._models_checked >= SANDBOX_STATUS_INTERVAL_S:
                    self._models_checked = time.monotonic()  # not in start(): a down Ollama can take seconds
                    self.refresh_models()
                self._stop.wait(self.settings.poll_interval_s)

    # ------------------------------------------------------------------------- sandbox (Part E)

    def refresh_sandbox(self, *, sweep: bool = False) -> dict[str, Any]:
        """Check the WSL engine (and, at start, remove leftovers of crashed runs); write the status file the
        API reads, so the API itself never contacts Docker."""
        sandbox = self.rt.sandbox
        status: dict[str, Any] = {"configured": sandbox is not None}
        if sandbox is not None:
            status.update(sandbox.engine_status())
            if sweep and status.get("reachable"):
                removed = sandbox.sweep()
                status["swept"] = list(removed) if removed else [0, 0]
                if removed and any(removed):
                    with self.rt.sessions.begin() as session:
                        audit.append(
                            session,
                            action="sandbox.swept",
                            outcome="success",
                            principal_id=WORKER,
                            details={"containers": removed[0], "volumes": removed[1]},
                            clock=self.rt.clock,
                        )
            if sweep:
                try:
                    status["stale_marked"] = refresh_stale(self.rt.settings, self.rt.sessions)
                except Exception:  # sandbox housekeeping must never stop the run worker
                    _log.exception("sandbox_stale_refresh_failed")
                    status["stale_marked"] = None
        status["checked_at"] = self.rt.clock.now().isoformat(timespec="seconds")
        target = self.rt.settings.data_dir / SANDBOX_STATUS_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(status, indent=2), encoding="utf-8")
        temporary.replace(target)
        self._sandbox_checked = time.monotonic()
        return status

    def refresh_models(self) -> bool:
        """Record the local model inventory (tags, digests, Ollama version) for the read-only Settings page
        (Part F.6): the API reads this table and never contacts Ollama. ``False`` if Ollama is unreachable."""
        server = self.rt.provider
        router = self.rt.router
        if router is None:
            return False
        try:
            installed = server.list_models()
            version = server.version()
        except HarnessError:
            return False
        with self.rt.sessions.begin() as session:
            model_calls_repo.replace_inventory(
                session,
                installed,
                router.all_models(),
                server_version=version,
                checked_at=self.rt.clock.now(),
            )
        return True

    def run_index_job_once(self) -> bool:
        """Run one queued RAG index job (Part F.5), if any. Without a vector store it ends Blocked."""
        with self.rt.sessions.begin() as session:
            job = rag_repo.claim_next_index_job(session, now=self.rt.clock.now())
            if job is None:
                return False
            job_id, project_id, kind = job.id, job.project_id, job.kind
        status, code, detail = "done", None, None
        try:
            if self.rt.ingestor is None:
                raise ResourceUnavailableError("rag.store_unavailable", "the vector store is not available")
            result = self.rt.ingestor.index_project(project_id, rebuild=kind == "rebuild")
            detail = (
                f"indexed {len(result.indexed)}, unchanged {len(result.unchanged)}, "
                f"removed {len(result.removed)}, "
                f"skipped {len(result.skipped)}, chunks {result.chunks}"
            )
        except ResourceUnavailableError as exc:
            status, code, detail = "blocked", exc.code, exc.message
        except HarnessError as exc:
            status, code, detail = "failed", exc.code, exc.message
        except Exception as exc:
            _log.exception("index_job_failed", job_id=job_id)
            status, code, detail = "failed", "rag.index_error", type(exc).__name__
        with self.rt.sessions.begin() as session:
            rag_repo.finish_index_job(
                session, job_id, status=status, now=self.rt.clock.now(), error_code=code, detail=detail
            )
            audit.append(
                session, action="rag.indexed", outcome="success" if status == "done" else "error",
                principal_id="service:rag_ingestor", project_id=project_id,
                details={"job_id": job_id, "kind": kind, "status": status, "error_code": code},
                clock=self.rt.clock,
            )  # fmt: skip
        _log.info("index_job_done", job_id=job_id, status=status)
        return True

    def run_verification_once(self) -> bool:
        """Run one queued starter-kit verification (D-49), if any. Runs are always served first."""
        sandbox = self.rt.sandbox
        if sandbox is None:
            return False
        with self.rt.sessions.begin() as session:
            verification = sandbox_repo.claim_next_verification(session, clock=self.rt.clock)
        if verification is None:
            return False
        _log.info("verification_started", verification_id=verification.verification_id)
        try:
            result = run_verification(
                verification, sandbox, self.rt.settings, self.rt.sessions, clock=self.rt.clock
            )
        except Exception as exc:
            _log.exception("verification_failed", verification_id=verification.verification_id)
            blocked = verification.model_copy(
                update={
                    "status": TemplateVerificationStatus.BLOCKED,
                    "finished_at": self.rt.clock.now(),
                    "reason": f"the verification stopped with an error ({type(exc).__name__})",
                }
            )
            with self.rt.sessions.begin() as session:
                sandbox_repo.finish_verification(session, blocked)
            return True
        _log.info("verification_done", verification_id=result.verification_id, status=result.status.value)
        return True
