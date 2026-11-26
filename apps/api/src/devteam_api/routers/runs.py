"""Run control, gates, progress, trace and history (Part D, LG-08; plan §3.7).

The API never runs a graph or an agent (D-41; import-linter): it calls the orchestration command
interface, which writes jobs and flags for the worker. Every route requires sign-in and the
``project_user`` role; mutations are rate limited and audited. Gate decisions are recorded through the
Approval Service (HC-13), which checks the decider's own role for the approval type.

Responses carry metadata only (IDs, statuses, counts, durations, paths with checksums); no prompt, model
answer, document text or file content. The single exception is the approval gate, which shows the
requirement keys and one-line descriptions being approved, so the person approving can read them.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, sessionmaker

from devteam_api.deps import Audit, DbSession, ProjectUser, api_error, limit_rate
from devteam_api.routers.projects import load_project
from devteam_api.run_schemas import (
    FileRef,
    GateDecisionIn,
    GateDecisionOut,
    GateInfo,
    HistoryItem,
    MetricsOut,
    NodeTiming,
    RunDetail,
    RunEventOut,
    RunOut,
    RunStart,
    SpanOut,
    TaskOut,
    TraceOut,
)
from devteam_api.schemas import ERROR_RESPONSES, ErrorResponse
from devteam_core.contracts.records import ApprovalDecision
from devteam_core.contracts.statuses import GateName, RunStatus
from devteam_core.ids import is_valid_id
from devteam_harness.approvals import ApprovalDeniedError, ApprovalRules
from devteam_orchestration import commands
from devteam_state.models import AgentOutputRow, AgentTaskRow, AuditEvent, Run, RunEventRow
from devteam_state.repositories import orchestration as orch
from devteam_state.repositories import runs as runs_repo
from devteam_state.repositories.execution import spans_for_trace

router = APIRouter(
    prefix="/api", tags=["runs"], dependencies=[Depends(limit_rate)], responses=ERROR_RESPONSES
)
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"model": ErrorResponse, "description": "Not found"}}
CONFLICT: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Not found"},
    409: {"model": ErrorResponse, "description": "Not allowed in the run's current state"},
}
TERMINAL = {s.value for s in (RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.BLOCKED)}
STREAM_SECONDS = 600
RUN_PATH = "/projects/{project_id}/runs/{run_id}"


def _command_error(exc: commands.CommandError) -> Exception:
    return api_error(exc.status, exc.code, exc.message)


def load_run(db: Session, project_id: str, run_id: str) -> Run:
    load_project(db, project_id)
    run = runs_repo.get_run(db, project_id, run_id) if is_valid_id(run_id, "run") else None
    if run is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "run.not_found", "Run not found.")
    return run


def _out(run: Run) -> RunOut:
    return RunOut(
        id=run.id,
        project_id=run.project_id,
        run_kind=run.run_kind,
        status=run.status,
        stage=run.stage,
        mode=run.mode,
        current_node=run.current_node,
        pending_gate=run.pending_gate,
        control_request=run.control_request,
        spec_version_id=run.spec_version_id,
        created_at=run.created_at,
        updated_at=run.updated_at,
    )


# ------------------------------------------------------------------------------------- runs


@router.post(
    "/projects/{project_id}/runs",
    response_model=RunOut,
    status_code=status.HTTP_201_CREATED,
    name="run.start",
    responses=CONFLICT,
)
def start_run(project_id: str, body: RunStart, db: DbSession, scope: Audit, principal: ProjectUser) -> RunOut:
    load_project(db, project_id)
    try:
        run = commands.start_run(db, project_id=project_id, principal_id=principal.id, run_kind=body.run_kind)
    except commands.CommandError as exc:
        raise _command_error(exc) from None
    scope.set(target_type="run", target_id=run.id, mode=run.mode)
    return _out(run)


@router.get("/projects/{project_id}/runs", response_model=list[RunOut], name="run.list", responses=NOT_FOUND)
def list_runs(project_id: str, db: DbSession, _: ProjectUser) -> list[RunOut]:
    load_project(db, project_id)
    rows = db.scalars(select(Run).where(Run.project_id == project_id).order_by(Run.created_at.desc()))
    return [_out(r) for r in rows]


@router.get(RUN_PATH, response_model=RunDetail, name="run.read", responses=NOT_FOUND)
def read_run(project_id: str, run_id: str, db: DbSession, _: ProjectUser) -> RunDetail:
    run = load_run(db, project_id, run_id)
    tasks = list(db.scalars(select(AgentTaskRow).where(AgentTaskRow.run_id == run.id)))
    last = db.scalars(
        select(RunEventRow.seq).where(RunEventRow.run_id == run.id).order_by(RunEventRow.seq.desc())
    ).first()
    blocker = None
    if run.status in TERMINAL and run.status != RunStatus.COMPLETED.value:
        reason = db.scalars(
            select(AuditEvent)
            .where(AuditEvent.action == "run.transitioned", AuditEvent.target_id == run.id)
            .order_by(AuditEvent.seq.desc())
        ).first()
        blocker = (
            str(reason.details.get("reason")) if reason is not None and reason.details.get("reason") else None
        )
    return RunDetail(
        **_out(run).model_dump(exclude={"verified", "label"}),
        completed_tasks=sum(t.status == "completed" for t in tasks),
        total_tasks=len(tasks),
        last_event_seq=last or 0,
        blocker=blocker,
    )


@router.post(f"{RUN_PATH}/pause", response_model=RunOut, name="run.pause", responses=CONFLICT)
def pause_run(project_id: str, run_id: str, db: DbSession, scope: Audit, _: ProjectUser) -> RunOut:
    load_run(db, project_id, run_id)
    scope.set(target_type="run", target_id=run_id)
    try:
        return _out(commands.request_pause(db, project_id=project_id, run_id=run_id))
    except commands.CommandError as exc:
        raise _command_error(exc) from None


@router.post(f"{RUN_PATH}/resume", response_model=RunOut, name="run.resume", responses=CONFLICT)
def resume_run(project_id: str, run_id: str, db: DbSession, scope: Audit, principal: ProjectUser) -> RunOut:
    load_run(db, project_id, run_id)
    scope.set(target_type="run", target_id=run_id)
    try:
        return _out(commands.request_resume(db, project_id=project_id, run_id=run_id, actor=principal.id))
    except commands.CommandError as exc:
        raise _command_error(exc) from None


@router.post(f"{RUN_PATH}/cancel", response_model=RunOut, name="run.cancel", responses=CONFLICT)
def cancel_run(  # noqa: PLR0917 - FastAPI injects every parameter
    project_id: str, run_id: str, request: Request, db: DbSession, scope: Audit, principal: ProjectUser
) -> RunOut:
    load_run(db, project_id, run_id)
    scope.set(target_type="run", target_id=run_id)
    try:
        run = commands.request_cancel(
            db,
            project_id=project_id,
            run_id=run_id,
            actor=principal.id,
            denial_sessions=request.app.state.session_factory,
        )
    except commands.CommandError as exc:
        raise _command_error(exc) from None
    return _out(run)


# ------------------------------------------------------------------------------------ gates


@router.get(f"{RUN_PATH}/gate", response_model=GateInfo, name="run.gate", responses=CONFLICT)
def read_gate(project_id: str, run_id: str, request: Request, db: DbSession, _: ProjectUser) -> GateInfo:
    run = load_run(db, project_id, run_id)
    if run.status != RunStatus.WAITING_FOR_HUMAN.value or run.pending_gate is None:
        raise api_error(status.HTTP_409_CONFLICT, "gate.not_pending", "No gate is waiting for a decision.")
    gate = GateName(run.pending_gate)
    approval_type = commands.DECIDABLE_GATES.get(gate)
    if approval_type is None:
        raise api_error(
            status.HTTP_409_CONFLICT, "gate.unavailable", "This gate cannot be decided in Part D."
        )
    rules: ApprovalRules = request.app.state.approval_rules
    roles = sorted(r.value for r in rules.rules[approval_type].roles)
    info = GateInfo(run_id=run.id, gate=gate.value, approval_type=approval_type.value, roles=roles)
    notes = {
        GateName.FEASIBILITY_DECISION: [
            "Read the feasibility report before deciding: it lists what can be built and what cannot.",
            "Proceed continues with the buildable scope only; or cancel (or revise the BRD, then a new run).",
        ],
        GateName.PROJECT_APPROVAL: [
            "Approving freezes exactly this specification version; building can start only after it.",
            "Each requirement was extracted from your BRD by a local AI model and cites its source.",
        ],
        GateName.PUBLISH_APPROVAL: [
            "Nothing will be published: the GitHub Deployer arrives in Part P. Rejecting skips the step.",
        ],
    }[gate]
    info.notes = notes
    if gate is GateName.PROJECT_APPROVAL:
        draft = commands.draft_spec_for(db, project_id, run_id)
        if draft is not None:
            info.spec_version_id, info.spec_checksum = draft.id, draft.checksum
            analysis_id = draft.payload.get("analysis_task_id")
            output = db.get(AgentOutputRow, analysis_id) if analysis_id else None
            described = {
                r["requirement_id"]: r["description"]
                for r in (output.payload if output else {}).get("requirements", [])
            }
            info.requirements = [
                {"key": k, "description": described.get(k, "")} for k in draft.payload.get("in_scope", [])
            ]
    elif gate is GateName.PUBLISH_APPROVAL:
        info.spec_version_id, info.spec_checksum = run.spec_version_id, run.spec_checksum
    return info


@router.post(
    f"{RUN_PATH}/gates/{{gate}}/decision",
    response_model=GateDecisionOut,
    name="run.gate_decision",
    responses={**CONFLICT, 403: {"model": ErrorResponse, "description": "Decision refused"}},
)
def decide_gate(  # noqa: PLR0917 - FastAPI injects every parameter
    project_id: str,
    run_id: str,
    gate: str,
    body: GateDecisionIn,
    request: Request,
    db: DbSession,
    scope: Audit,
    principal: ProjectUser,
) -> GateDecisionOut:
    load_run(db, project_id, run_id)
    scope.set(
        target_type="run", target_id=run_id, gate=gate[:40], decision=body.decision,
        acknowledge_low_coverage=body.acknowledge_low_coverage, reason=body.reason,
    )  # fmt: skip
    if gate not in {g.value for g in GateName}:
        raise api_error(status.HTTP_404_NOT_FOUND, "gate.not_found", "Unknown gate.")
    db.commit()  # release the read transaction: the decision is recorded in its own transactions
    try:
        approval = commands.submit_gate_decision(
            request.app.state.session_factory,
            request.app.state.approvals,
            principal,
            project_id=project_id,
            run_id=run_id,
            gate=GateName(gate),
            decision=ApprovalDecision(body.decision),
            acknowledge_low_coverage=body.acknowledge_low_coverage,
            reason=body.reason,
        )
    except commands.CommandError as exc:
        raise _command_error(exc) from None
    except ApprovalDeniedError as exc:
        raise api_error(status.HTTP_403_FORBIDDEN, exc.code, exc.message) from None
    scope.set(approval_id=approval.approval_id)
    return GateDecisionOut(
        run_id=run_id, gate=gate, approval_id=approval.approval_id, decision=approval.decision.value
    )


# ------------------------------------------------------------------------------- tasks/events


@router.get(f"{RUN_PATH}/tasks", response_model=list[TaskOut], name="run.tasks", responses=NOT_FOUND)
def list_tasks(project_id: str, run_id: str, db: DbSession, _: ProjectUser) -> list[TaskOut]:
    run = load_run(db, project_id, run_id)
    rows = db.scalars(
        select(AgentTaskRow).where(AgentTaskRow.run_id == run.id).order_by(AgentTaskRow.created_at)
    )
    out: list[TaskOut] = []
    for row in rows:
        stored = db.get(AgentOutputRow, row.id)
        payload = stored.payload if stored is not None else {}
        declared = [*payload.get("files", []), *payload.get("documents", [])]
        if isinstance(payload.get("test_plan"), dict):
            declared.append(payload["test_plan"])
        out.append(
            TaskOut(
                task_id=row.id,
                agent=row.agent_type,
                operation_id=row.operation_id,
                status=row.status,
                attempts=row.attempt_count,
                max_attempts=row.max_attempts,
                depends_on=list(row.payload.get("depends_on", [])),
                requirement_ids=list(row.payload.get("requirement_ids", [])),
                blocker_reason=row.payload.get("blocker_reason"),
                files=[FileRef(path=f["path"], sha256=f["sha256"]) for f in declared if isinstance(f, dict)],
                agent_returned_output=stored is not None,
            )
        )
    return out


@router.get(f"{RUN_PATH}/events", response_model=list[RunEventOut], name="run.events", responses=NOT_FOUND)
def list_events(
    project_id: str, run_id: str, db: DbSession, _: ProjectUser, after: int = 0
) -> list[RunEventOut]:
    load_run(db, project_id, run_id)
    rows = orch.events_after(db, project_id, run_id, max(0, after), limit=1_000)
    return [RunEventOut(seq=r.seq, kind=r.kind, ts=r.ts, payload=r.payload) for r in rows]


def _sse(factory: sessionmaker[Session], project_id: str, run_id: str, after: int) -> Iterator[str]:
    last = after
    deadline = time.monotonic() + STREAM_SECONDS
    yield "retry: 3000\n\n"
    while time.monotonic() < deadline:
        with factory() as session:
            rows = orch.events_after(session, project_id, run_id, last, limit=200)
            run = runs_repo.get_run(session, project_id, run_id)
            finished = run is None or run.status in TERMINAL
            batch = [(r.seq, r.kind, r.payload) for r in rows]
        for seq, kind, payload in batch:
            last = seq
            yield f"id: {seq}\nevent: {kind}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
        if finished and not batch:
            yield "event: end\ndata: {}\n\n"
            return
        if not batch:
            yield ": keep-alive\n\n"
            time.sleep(1.0)


@router.get(
    f"{RUN_PATH}/events/stream",
    name="run.events_stream",
    responses={**NOT_FOUND, 200: {"content": {"text/event-stream": {}}, "description": "Server-sent events"}},
    response_class=StreamingResponse,
)
def stream_events(
    project_id: str, run_id: str, request: Request, db: DbSession, _: ProjectUser, after: int = 0
) -> StreamingResponse:
    load_run(db, project_id, run_id)
    last_id = request.headers.get("last-event-id", "")
    start = int(last_id) if last_id.isdigit() else max(0, after)
    return StreamingResponse(
        _sse(request.app.state.session_factory, project_id, run_id, start),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ------------------------------------------------------------------------ trace/history/metrics


@router.get(f"{RUN_PATH}/trace", response_model=TraceOut, name="run.trace", responses=NOT_FOUND)
def read_trace(project_id: str, run_id: str, db: DbSession, _: ProjectUser) -> TraceOut:
    run = load_run(db, project_id, run_id)
    spans = spans_for_trace(db, run.trace_id) if run.trace_id else []
    return TraceOut(
        run_id=run.id,
        trace_id=run.trace_id,
        spans=[
            SpanOut(
                span_id=s.span_id,
                parent_span_id=s.parent_span_id,
                name=s.name,
                start_ts=s.start_ts,
                duration_ms=s.duration_ms,
                status=s.status,
                attributes=dict(s.attributes or {}),
            )
            for s in spans
            if s.project_id in {None, project_id}
        ],
    )


@router.get(f"{RUN_PATH}/history", response_model=list[HistoryItem], name="run.history", responses=NOT_FOUND)
def read_history(project_id: str, run_id: str, db: DbSession, _: ProjectUser) -> list[HistoryItem]:
    run = load_run(db, project_id, run_id)
    task_ids = list(db.scalars(select(AgentTaskRow.id).where(AgentTaskRow.run_id == run.id)))
    rows = db.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.project_id == project_id,
            or_(AuditEvent.target_id == run.id, AuditEvent.target_id.in_(task_ids)),
        )
        .order_by(AuditEvent.seq)
        .limit(2_000)
    )
    return [
        HistoryItem(
            ts=r.ts,
            action=r.action,
            outcome=r.outcome,
            principal_id=r.principal_id,
            target_type=r.target_type,
            target_id=r.target_id,
            details=dict(r.details or {}),
        )
        for r in rows
    ]


@router.get(f"{RUN_PATH}/metrics", response_model=MetricsOut, name="run.metrics", responses=NOT_FOUND)
def read_metrics(project_id: str, run_id: str, db: DbSession, _: ProjectUser) -> MetricsOut:
    run = load_run(db, project_id, run_id)
    calls = [
        r.details
        for r in db.scalars(
            select(AuditEvent).where(AuditEvent.project_id == project_id, AuditEvent.action == "model.called")
        )
        if isinstance(r.details, dict) and r.details.get("run_id") == run.id
    ]
    timings: dict[str, list[int]] = {}
    samples: dict[str, int] = {}
    for event in orch.events_after(db, project_id, run.id, 0, limit=5_000):
        payload = event.payload
        if event.kind == "node.finished":
            timings.setdefault(str(payload["node"]), []).append(int(payload["duration_ms"]))
        elif event.kind == "metric.sampled":
            samples[str(payload["metric"])] = int(payload["value"])
    return MetricsOut(
        run_id=run.id,
        model_calls=len(calls),
        input_tokens=sum(int(c.get("input_tokens") or 0) for c in calls),
        output_tokens=sum(int(c.get("output_tokens") or 0) for c in calls),
        model_ms=sum(int(c.get("duration_ms") or 0) for c in calls),
        nodes=[NodeTiming(node=n, runs=len(v), total_ms=sum(v)) for n, v in sorted(timings.items())],
        latest_samples=samples,
        queue_depth=orch.pending_jobs(db),
    )
