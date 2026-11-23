"""Run-command interface for the API (D.4, LG-08; D-41): the API writes jobs and flags, never runs a graph.

Nothing here imports LangGraph or the graph. Each command checks the run's state, changes it only
through the run state machine, and leaves the work to the worker:

* ``start_run``: a new run (mode ``stub``) and a ``start`` job. Refused while the project has an active
  run, or has no extracted document. Change-request runs are refused until Part Q.
* ``request_pause`` / ``request_cancel``: a running run gets a control flag that the worker applies at
  the next node boundary (cooperative, roadmap §6.4). A run that is not executing (created, paused or
  waiting) is cancelled at once; its last checkpoint is kept.
* ``request_resume``: a paused run, or one waiting for resources, gets a ``resume`` job.
* ``submit_gate_decision``: the human decision is recorded through the Approval Service (HC-13: role,
  run, spec version + checksum binding, duplicate refusal, audit) and only then a ``gate_decision`` job
  is queued with the approval **ID**; the gate re-verifies it when it resumes.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.orchestration import RunMode
from devteam_core.contracts.records import Approval, ApprovalDecision
from devteam_core.contracts.statuses import ApprovalType, GateName, RunStatus, Stage
from devteam_harness.approvals import ApprovalService
from devteam_harness.iam import Principal
from devteam_state.machines import transition_run
from devteam_state.models import Run, SpecVersionRow
from devteam_state.repositories import audit, documents, runs
from devteam_state.repositories import orchestration as orch

R = RunStatus
ACTIVE = frozenset({R.CREATED, R.RUNNING, R.PAUSED, R.WAITING_FOR_HUMAN, R.WAITING_FOR_RESOURCES})
DECIDABLE_GATES: dict[GateName, ApprovalType] = {
    GateName.FEASIBILITY_DECISION: ApprovalType.FEASIBILITY_DECISION,
    GateName.PROJECT_APPROVAL: ApprovalType.SPEC_APPROVAL,
    GateName.PUBLISH_APPROVAL: ApprovalType.PUBLISH_GITHUB,
}


@dataclass
class CommandError(Exception):
    code: str
    message: str
    status: int = 409

    def __str__(self) -> str:
        return self.message


def _run(session: Session, project_id: str, run_id: str) -> Run:
    run = runs.get_run(session, project_id, run_id)
    if run is None:
        raise CommandError("run.not_found", "Run not found.", 404)
    return run


def active_run(session: Session, project_id: str) -> Run | None:
    return session.scalars(
        select(Run).where(Run.project_id == project_id, Run.status.in_([s.value for s in ACTIVE]))
    ).first()


def start_run(
    session: Session,
    *,
    project_id: str,
    principal_id: str,
    run_kind: str = "initial",
    clock: Clock = system_clock,
) -> Run:
    if run_kind != "initial":
        raise CommandError("run.change_unavailable", "Change-request runs are available from Part Q.")
    if active_run(session, project_id) is not None:
        raise CommandError("run.active", "This project already has an active run.")
    ready = [
        d
        for d in documents.list_for_project(session, project_id)
        if d.status == "extracted" and d.chunk_count
    ]
    if not ready:
        raise CommandError("run.no_document", "Upload a BRD before starting a run.")
    run = runs.create_run(session, project_id=project_id, run_kind="initial", stage=Stage.INTAKE, clock=clock)
    run.mode = RunMode.STUB.value
    orch.enqueue_job(session, project_id=project_id, run_id=run.id, kind="start", clock=clock)
    audit.append(
        session,
        action="run.started",
        outcome="success",
        principal_id=principal_id,
        project_id=project_id,
        target_type="run",
        target_id=run.id,
        details={"mode": run.mode},
        clock=clock,
    )
    return run


def request_pause(session: Session, *, project_id: str, run_id: str) -> Run:
    run = _run(session, project_id, run_id)
    if R(run.status) is not R.RUNNING:
        raise CommandError("run.not_running", "Only a running run can be paused.")
    orch.set_control_request(session, project_id, run_id, "pause")
    return run


def request_resume(
    session: Session, *, project_id: str, run_id: str, actor: str, clock: Clock = system_clock
) -> Run:
    run = _run(session, project_id, run_id)
    if R(run.status) not in {R.PAUSED, R.WAITING_FOR_RESOURCES}:
        raise CommandError(
            "run.not_resumable", "Only a paused run, or one waiting for resources, can be resumed."
        )
    orch.set_control_request(session, project_id, run_id, "none")
    orch.enqueue_job(session, project_id=project_id, run_id=run_id, kind="resume", clock=clock)
    return run


def request_cancel(
    session: Session,
    *,
    project_id: str,
    run_id: str,
    actor: str,
    denial_sessions: sessionmaker[Session] | None = None,
    clock: Clock = system_clock,
) -> Run:
    run = _run(session, project_id, run_id)
    status = R(run.status)
    if status not in ACTIVE:
        raise CommandError("run.finished", "The run has already finished.")
    if status is R.RUNNING:
        orch.set_control_request(session, project_id, run_id, "cancel")
        return run
    transition_run(
        session,
        run,
        R.CANCELLED,
        actor=actor,
        reason="cancelled on request",
        denial_sessions=denial_sessions,
        clock=clock,
    )
    orch.set_run_position(session, project_id, run_id, current_node=None, pending_gate=None, clock=clock)
    return run


def draft_spec_for(session: Session, project_id: str, run_id: str) -> SpecVersionRow | None:
    return session.scalars(
        select(SpecVersionRow)
        .where(
            SpecVersionRow.project_id == project_id,
            SpecVersionRow.run_id == run_id,
            SpecVersionRow.status == "draft",
        )
        .order_by(SpecVersionRow.version.desc())
    ).first()


def submit_gate_decision(
    sessions: sessionmaker[Session],
    approvals: ApprovalService,
    principal: Principal,
    *,
    project_id: str,
    run_id: str,
    gate: GateName,
    decision: ApprovalDecision,
    clock: Clock = system_clock,
) -> Approval:
    approval_type = DECIDABLE_GATES.get(gate)
    if approval_type is None:
        raise CommandError("gate.unavailable", f"The {gate.value} gate cannot be decided in Part D.")
    with sessions() as session:
        run = _run(session, project_id, run_id)
        if R(run.status) is not R.WAITING_FOR_HUMAN or run.pending_gate != gate.value:
            raise CommandError("gate.not_pending", "This gate is not waiting for a decision.")
        version_id: str | None = None
        checksum: str | None = None
        if gate is GateName.PROJECT_APPROVAL:
            draft = draft_spec_for(session, project_id, run_id)
            if draft is None:
                raise CommandError("gate.no_spec", "There is no specification draft to approve.")
            version_id, checksum = draft.id, draft.checksum
        elif gate is GateName.PUBLISH_APPROVAL:
            version_id, checksum = run.spec_version_id, run.spec_checksum
    approval = approvals.record(
        principal,
        project_id=project_id,
        run_id=run_id,
        approval_type=approval_type,
        decision=decision,
        spec_version_id=version_id,
        spec_checksum=checksum,
    )
    with sessions.begin() as session:
        orch.enqueue_job(
            session,
            project_id=project_id,
            run_id=run_id,
            kind="gate_decision",
            payload={"run_id": run_id, "gate": gate.value, "approval_id": approval.approval_id},
            clock=clock,
        )
    return approval


__all__ = [
    "CommandError",
    "active_run",
    "draft_spec_for",
    "request_cancel",
    "request_pause",
    "request_resume",
    "start_run",
    "submit_gate_decision",
]
