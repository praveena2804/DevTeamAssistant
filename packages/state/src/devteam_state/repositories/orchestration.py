"""Repositories for the D.1 orchestration tables: run control, jobs, worker lease, events, effects, plans.

Every project-scoped query takes ``project_id``. The worker logic built on top of these (D.4) relies on
the guarantees here:

* a job is claimed by at most one caller (status moves queued → running inside the caller's write
  transaction; SQLite serialises writers);
* the worker lease has exactly one row; a live owner keeps it, a stale one can be taken over;
* event ``seq`` numbers are gap-free and strictly increasing per run;
* a node effect's phase only moves forward (also enforced by a database trigger).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.events import EventBase
from devteam_core.ids import new_id
from devteam_state.models import (
    AgentOutputRow,
    NodeEffect,
    Run,
    RunEventRow,
    RunJob,
    TaskPlanRow,
    WorkerLease,
)

ControlRequest = Literal["none", "pause", "cancel"]
JobKind = Literal["start", "resume", "gate_decision"]
JobStatus = Literal["queued", "running", "done", "failed", "cancelled"]
Phase = Literal["intent", "performed", "confirmed"]

CONTROL_REQUESTS: frozenset[str] = frozenset({"none", "pause", "cancel"})
JOB_KINDS: frozenset[str] = frozenset({"start", "resume", "gate_decision"})
FINAL_JOB_STATUSES: frozenset[str] = frozenset({"done", "failed", "cancelled"})
_PHASE_ORDER = {"intent": 0, "performed": 1, "confirmed": 2}
LEASE_ROW_ID = 1


class OrchestrationStateError(ValueError):
    """A request against the orchestration tables was refused."""


def _run(session: Session, project_id: str, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None or run.project_id != project_id:
        raise OrchestrationStateError("run not found for this project")
    return run


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes for stored UTC values; compare everything as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ----------------------------------------------------------------------------------- run control


def set_control_request(session: Session, project_id: str, run_id: str, request: ControlRequest) -> None:
    if request not in CONTROL_REQUESTS:
        raise OrchestrationStateError(f"unknown control request {request!r}")
    _run(session, project_id, run_id).control_request = request
    session.flush()


def get_control_request(session: Session, project_id: str, run_id: str) -> str:
    return _run(session, project_id, run_id).control_request


def set_run_position(
    session: Session,
    project_id: str,
    run_id: str,
    *,
    current_node: str | None,
    pending_gate: str | None,
    clock: Clock = system_clock,
) -> None:
    run = _run(session, project_id, run_id)
    run.current_node = current_node
    run.pending_gate = pending_gate
    run.updated_at = clock.now()
    session.flush()


# ------------------------------------------------------------------------------------------- jobs


def enqueue_job(
    session: Session,
    *,
    project_id: str,
    run_id: str,
    kind: JobKind,
    payload: dict[str, Any] | None = None,
    delay_s: float = 0.0,
    clock: Clock = system_clock,
) -> RunJob:
    if kind not in JOB_KINDS:
        raise OrchestrationStateError(f"unknown job kind {kind!r}")
    _run(session, project_id, run_id)
    now = clock.now()
    job = RunJob(
        id=new_id("job", clock),
        project_id=project_id,
        run_id=run_id,
        kind=kind,
        payload=payload or {},
        status="queued",
        available_at=now + timedelta(seconds=max(0.0, delay_s)),
        attempts=0,
        created_at=now,
        updated_at=now,
    )
    session.add(job)
    session.flush()
    return job


def claim_next_job(session: Session, *, clock: Clock = system_clock) -> RunJob | None:
    """Oldest queued job that is due; it becomes ``running`` (call inside the worker's write transaction)."""
    now = clock.now()
    for job in session.scalars(
        select(RunJob).where(RunJob.status == "queued").order_by(RunJob.available_at, RunJob.created_at)
    ):
        if _aware(job.available_at) <= now:
            job.status = "running"
            job.attempts += 1
            job.updated_at = now
            session.flush()
            return job
    return None


def finish_job(
    session: Session,
    job_id: str,
    status: JobStatus,
    *,
    error_code: str | None = None,
    clock: Clock = system_clock,
) -> RunJob:
    if status not in FINAL_JOB_STATUSES:
        raise OrchestrationStateError("a job can only finish as done, failed or cancelled")
    job = session.get(RunJob, job_id)
    if job is None or job.status != "running":
        raise OrchestrationStateError("only a running job can be finished")
    job.status = status
    job.error_code = error_code
    job.updated_at = clock.now()
    session.flush()
    return job


def pending_jobs(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(RunJob).where(RunJob.status == "queued")) or 0


# ---------------------------------------------------------------------------------- worker lease


@dataclass(frozen=True, slots=True)
class LeaseResult:
    acquired: bool
    taken_over_from: str | None = None  # owner of a stale lease that was replaced


def acquire_lease(
    session: Session, *, owner: str, pid: int, ttl_s: float, clock: Clock = system_clock
) -> LeaseResult:
    """Take the single worker lease if it is free, already ours, or stale (older than ``ttl_s``)."""
    now = clock.now()
    lease = session.get(WorkerLease, LEASE_ROW_ID)
    if lease is None:
        try:
            with session.begin_nested():
                session.add(
                    WorkerLease(id=LEASE_ROW_ID, owner=owner, pid=pid, acquired_at=now, heartbeat_at=now)
                )
        except IntegrityError:
            return LeaseResult(acquired=False)  # another worker inserted it first
        return LeaseResult(acquired=True)
    if lease.owner == owner:
        lease.heartbeat_at = now
        session.flush()
        return LeaseResult(acquired=True)
    if now - _aware(lease.heartbeat_at) <= timedelta(seconds=ttl_s):
        return LeaseResult(acquired=False)
    previous = lease.owner
    lease.owner, lease.pid, lease.acquired_at, lease.heartbeat_at = owner, pid, now, now
    session.flush()
    return LeaseResult(acquired=True, taken_over_from=previous)


def renew_lease(session: Session, *, owner: str, clock: Clock = system_clock) -> bool:
    """Heartbeat. ``False`` means the lease was lost (another worker took it over): stop work."""
    lease = session.get(WorkerLease, LEASE_ROW_ID)
    if lease is None or lease.owner != owner:
        return False
    lease.heartbeat_at = clock.now()
    session.flush()
    return True


def release_lease(session: Session, *, owner: str) -> bool:
    lease = session.get(WorkerLease, LEASE_ROW_ID)
    if lease is None or lease.owner != owner:
        return False
    session.delete(lease)
    session.flush()
    return True


# ----------------------------------------------------------------------------------------- events


def append_event(session: Session, event: EventBase) -> int:
    """Store a validated progress/audit event for its run; returns the run's next ``seq``."""
    if event.project_id is None or event.run_id is None:
        raise OrchestrationStateError("a run event needs project_id and run_id")
    _run(session, event.project_id, event.run_id)
    payload = event.model_dump(mode="json")
    current = session.scalar(select(func.max(RunEventRow.seq)).where(RunEventRow.run_id == event.run_id))
    seq = (current or 0) + 1
    session.add(
        RunEventRow(
            project_id=event.project_id,
            run_id=event.run_id,
            seq=seq,
            kind=str(payload["kind"]),
            payload=payload,
            ts=event.ts,
        )
    )
    session.flush()
    return seq


def events_after(
    session: Session, project_id: str, run_id: str, after_seq: int = 0, limit: int = 500
) -> list[RunEventRow]:
    _run(session, project_id, run_id)
    return list(
        session.scalars(
            select(RunEventRow)
            .where(RunEventRow.run_id == run_id, RunEventRow.seq > after_seq)
            .order_by(RunEventRow.seq)
            .limit(max(1, min(limit, 5_000)))
        )
    )


# ----------------------------------------------------------------------------------- node effects


def effect_key(run_id: str, node: str, scope: str, seq: int = 1) -> str:
    """Two-phase idempotency key (plan §3.5): ``run_id:node:scope:seq``."""
    return f"{run_id}:{node}:{scope}:{seq}"


def begin_effect(
    session: Session, *, project_id: str, run_id: str, node: str, key: str, clock: Clock = system_clock
) -> NodeEffect:
    """Record the intent to perform an effect, or return the existing record for this key (resume)."""
    existing = session.scalars(select(NodeEffect).where(NodeEffect.key == key)).first()
    if existing is not None:
        if existing.project_id != project_id or existing.run_id != run_id or existing.node != node:
            raise OrchestrationStateError("the effect key belongs to another run or node")
        return existing
    _run(session, project_id, run_id)
    now = clock.now()
    effect = NodeEffect(
        id=new_id("eff", clock),
        project_id=project_id,
        run_id=run_id,
        node=node,
        key=key,
        phase="intent",
        created_at=now,
        updated_at=now,
    )
    session.add(effect)
    session.flush()
    return effect


def advance_effect(
    session: Session, key: str, phase: Phase, *, result_ref: str | None = None, clock: Clock = system_clock
) -> NodeEffect:
    effect = session.scalars(select(NodeEffect).where(NodeEffect.key == key)).first()
    if effect is None:
        raise OrchestrationStateError("unknown effect key")
    if phase not in _PHASE_ORDER or _PHASE_ORDER[phase] <= _PHASE_ORDER[effect.phase]:
        raise OrchestrationStateError("a node effect only moves forward: intent, performed, confirmed")
    effect.phase = phase
    if result_ref is not None:
        effect.result_ref = result_ref
    effect.updated_at = clock.now()
    session.flush()
    return effect


def unconfirmed_effects(session: Session, run_id: str) -> list[NodeEffect]:
    """Effects to reconcile on recovery (left at ``intent`` or ``performed`` by a crash)."""
    return list(
        session.scalars(
            select(NodeEffect)
            .where(NodeEffect.run_id == run_id, NodeEffect.phase != "confirmed")
            .order_by(NodeEffect.created_at)
        )
    )


# ----------------------------------------------------------------------------------------- plans


def add_plan(
    session: Session,
    *,
    plan_id: str,
    project_id: str,
    run_id: str,
    spec_version_id: str,
    source: Literal["template", "model"],
    payload: dict[str, Any],
    report: dict[str, Any],
    clock: Clock = system_clock,
) -> TaskPlanRow:
    _run(session, project_id, run_id)
    row = TaskPlanRow(
        id=plan_id,
        project_id=project_id,
        run_id=run_id,
        spec_version_id=spec_version_id,
        source=source,
        payload=payload,
        report=report,
        created_at=clock.now(),
    )
    session.add(row)
    session.flush()
    return row


def latest_plan(session: Session, project_id: str, run_id: str) -> TaskPlanRow | None:
    return session.scalars(
        select(TaskPlanRow)
        .where(TaskPlanRow.project_id == project_id, TaskPlanRow.run_id == run_id)
        .order_by(TaskPlanRow.created_at.desc(), TaskPlanRow.id.desc())
    ).first()


# --------------------------------------------------------------------------------- agent outputs


def save_agent_output(
    session: Session,
    *,
    project_id: str,
    run_id: str,
    task_id: str,
    agent_type: str,
    payload: dict[str, Any],
    clock: Clock = system_clock,
) -> AgentOutputRow:
    """Store a task's validated output once. A repeat (resume after a crash) returns the stored row."""
    _run(session, project_id, run_id)
    existing = session.get(AgentOutputRow, task_id)
    if existing is not None:
        if existing.project_id != project_id or existing.run_id != run_id:
            raise OrchestrationStateError("the output belongs to another project or run")
        return existing
    row = AgentOutputRow(
        task_id=task_id,
        project_id=project_id,
        run_id=run_id,
        agent_type=agent_type,
        payload=payload,
        created_at=clock.now(),
    )
    session.add(row)
    session.flush()
    return row


def get_agent_output(session: Session, project_id: str, task_id: str) -> AgentOutputRow | None:
    row = session.get(AgentOutputRow, task_id)
    return row if row is not None and row.project_id == project_id else None
