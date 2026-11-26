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
  Part G (D-01, D-63): proceeding past the feasibility decision is refused when nothing can be built, and
  below the coverage threshold needs ``acknowledge_low_coverage`` (recorded, and re-checked by the gate);
  a decline names its reason, ``cancel`` or ``revise`` (revise = cancel, then a new run with the revised BRD).
* ``accept_substitution``: records a ``substitution`` approval bound to one offered feasibility item and its
  checksum (AC-G-04). The clarification form that offers it to the user arrives in Part I.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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
from devteam_state.repositories import feasibility as feasibility_repo
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
    acknowledge_low_coverage: bool = False,
    reason: Literal["cancel", "revise"] | None = None,
    clock: Clock = system_clock,
) -> Approval:
    approval_type = DECIDABLE_GATES.get(gate)
    if approval_type is None:
        raise CommandError("gate.unavailable", f"The {gate.value} gate cannot be decided in Part D.")
    if gate is not GateName.FEASIBILITY_DECISION and (acknowledge_low_coverage or reason is not None):
        raise CommandError("gate.bad_decision", "Only the feasibility decision takes these options.", 422)
    with sessions() as session:
        run = _run(session, project_id, run_id)
        if R(run.status) is not R.WAITING_FOR_HUMAN or run.pending_gate != gate.value:
            raise CommandError("gate.not_pending", "This gate is not waiting for a decision.")
        version_id: str | None = None
        checksum: str | None = None
        report_id: str | None = None
        if gate is GateName.FEASIBILITY_DECISION:
            report_id = _check_feasibility(session, project_id, run_id, decision, acknowledge_low_coverage)
        if gate is GateName.PROJECT_APPROVAL:
            draft = draft_spec_for(session, project_id, run_id)
            if draft is None:
                raise CommandError("gate.no_spec", "There is no specification draft to approve.")
            version_id, checksum = draft.id, draft.checksum
        elif gate is GateName.PUBLISH_APPROVAL:
            version_id, checksum = run.spec_version_id, run.spec_checksum
    if report_id is not None and acknowledge_low_coverage and decision is ApprovalDecision.APPROVED:
        _record(
            sessions, principal.id, project_id, ACKNOWLEDGED, "feasibility_report", report_id, run_id, clock
        )
    approval = approvals.record(
        principal,
        project_id=project_id,
        run_id=run_id,
        approval_type=approval_type,
        decision=decision,
        spec_version_id=version_id,
        spec_checksum=checksum,
    )
    if gate is GateName.FEASIBILITY_DECISION and decision is not ApprovalDecision.APPROVED:
        details: dict[str, object] = {"reason": reason or "cancel", "report_id": report_id}
        _record(sessions, principal.id, project_id, DECISION_REASON, "approval", approval.approval_id, run_id,
                clock, details)  # fmt: skip
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


ACKNOWLEDGED = "feasibility.low_coverage_acknowledged"
DECISION_REASON = "feasibility.decision_reason"


def _check_feasibility(
    session: Session, project_id: str, run_id: str, decision: ApprovalDecision, acknowledged: bool
) -> str:
    """D-63: the decision is made on this run's latest report; the gate re-checks the same rules."""
    stored = feasibility_repo.latest_report(session, project_id, run_id)
    if stored is None:
        raise CommandError("feasibility.no_report", "There is no feasibility report for this run yet.")
    report = stored.report
    if decision is ApprovalDecision.APPROVED:
        if not report.proceed_allowed:
            raise CommandError(
                "feasibility.proceed_disabled", "Nothing in scope can be built: cancel, or revise the BRD."
            )
        if report.needs_acknowledgement and not acknowledged:
            raise CommandError(
                "feasibility.acknowledgement_required",
                "Coverage is below the threshold: confirm that you want to proceed anyway.",
                422,
            )
    elif acknowledged:
        raise CommandError("gate.bad_decision", "Acknowledging low coverage only applies to proceeding.", 422)
    return report.report_id


def _record(  # noqa: PLR0917 - private helper; every argument is required context
    sessions: sessionmaker[Session],
    principal_id: str,
    project_id: str,
    action: str,
    target_type: str,
    target_id: str,
    run_id: str,
    clock: Clock,
    details: dict[str, object] | None = None,
) -> None:
    with sessions.begin() as session:
        audit.append(
            session,
            action=action,
            outcome="success",
            principal_id=principal_id,
            project_id=project_id,
            target_type=target_type,
            target_id=target_id,
            details={"run_id": run_id, **(details or {})},
            clock=clock,
        )


def accept_substitution(
    sessions: sessionmaker[Session],
    approvals: ApprovalService,
    principal: Principal,
    *,
    project_id: str,
    run_id: str,
    item_id: str,
    decision: ApprovalDecision,
) -> Approval:
    """Record a human substitution decision bound to one offered item and its checksum (AC-G-04).

    Service-level in Part G; the clarification form that shows the offer arrives in Part I. Applying it is
    the Feasibility Gate's job, which re-verifies this approval (``FeasibilityGate.apply_substitution``).
    """
    with sessions() as session:
        _run(session, project_id, run_id)
        found = feasibility_repo.item_with_checksum(session, project_id, item_id)
    if found is None or found[2] != run_id:
        raise CommandError("substitution.unknown_item", "No such feasibility item in this run.", 404)
    item, checksum, _ = found
    if item.alternative is None:
        raise CommandError("substitution.not_offered", "No substitution was offered for this item.")
    return approvals.record(
        principal,
        project_id=project_id,
        run_id=run_id,
        approval_type=ApprovalType.SUBSTITUTION,
        decision=decision,
        subject_ref=feasibility_repo.substitution_ref(item_id, checksum),
    )


__all__ = [
    "CommandError",
    "accept_substitution",
    "active_run",
    "draft_spec_for",
    "request_cancel",
    "request_pause",
    "request_resume",
    "start_run",
    "submit_gate_decision",
]
