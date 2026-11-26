"""The ``project_run`` LangGraph workflow (D.3; ADR-0001; Part D plan §3).

**Shape.** One compiled graph. Its nodes are exactly the closed ``NodeName`` list. Every node carries
``metadata = {"node_kind", "subgraph"}``, and the four subgraphs (discovery, build, delivery,
change_request) are the node groups of ``NODE_SUBGRAPH``. Its edges are generated from the static
``ALLOWED_TRANSITIONS`` table, so the compiled edges equal the table (conformance test). A node with
several successors names its choice in ``route``; the wrapper refuses any choice outside the table.

**Three layers keep Build behind approval (roadmap §6.5):**

1. topology: every path into Build passes a spec-freeze node and ``build_entry_guard``;
2. ``build_entry_guard`` re-reads the spec version and approval from SQLite, recomputes the checksum
   and re-verifies the approval (HC-13); any mismatch blocks the run and is audited;
3. every spec-bound node repeats that check when it starts, so a forced jump (``Command(goto=...)``,
   ``update_state(as_node=...)``) into Build is refused and audited even if the topology was bypassed.
   The executor checks the stage once more for every agent task.

**State is references only** (``StateUpdate`` validates every update; domain facts live in SQLite).
Gate nodes write nothing before ``interrupt()``; the worker records the waiting state. Agent steps run
only through the Agent Executor with the real Ollama provider (``runtime``); nothing is simulated.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devteam_agents.head.dispatcher import DispatchError, dispatch
from devteam_agents.head.failure_router import Failure, FailureSource, RouteAction, route_failure
from devteam_agents.head.scheduler import Action, next_step
from devteam_agents.head.template_planner import template_plan
from devteam_agents.head.validator import PlanContext, validate_plan
from devteam_core.contracts.agents import (
    ArchitectInput,
    BackendDeveloperInput,
    BrdAnalystInput,
    BrdAnalystOutput,
    BuildTaskInput,
    DatabaseDeveloperInput,
    DebuggerReviewerInput,
    DocumenterInput,
    FrontendDeveloperInput,
    HeadInput,
    RequirementsFinalizerInput,
    TesterInput,
)
from devteam_core.contracts.errors import ErrorCategory
from devteam_core.contracts.events import (
    NodeFinished,
    NodeStarted,
    RunSummaryRecorded,
    ServiceStepRecorded,
)
from devteam_core.contracts.orchestration import (
    ALLOWED_TRANSITIONS,
    ENTRY_NODE,
    GATE_FOR_NODE,
    NODE_KIND,
    NODE_SUBGRAPH,
    SPEC_BOUND_NODES,
    TERMINAL_NODE,
    GateResume,
    NodeKind,
    NodeName,
    RunMode,
    ServiceOutcomeStatus,
    is_allowed_transition,
)
from devteam_core.contracts.statuses import AgentRole, ApprovalType, GateName, RunStatus, Stage, TaskStatus
from devteam_core.contracts.tasks import AgentTask
from devteam_core.ids import new_id
from devteam_harness.approvals import ApprovalDeniedError
from devteam_orchestration import services
from devteam_orchestration.runtime import Runtime
from devteam_orchestration.steps import (
    ORCHESTRATOR,
    StepError,
    ensure_task,
    load_task,
    require_completed,
    run_task,
    set_task_status,
    stored_output,
    task_row_status,
)
from devteam_state.machines import transition_run
from devteam_state.models import AgentTaskRow, AuditEvent, RunEventRow
from devteam_state.repositories import audit, documents, runs
from devteam_state.repositories import audit as audit_repo
from devteam_state.repositories import feasibility as feasibility_repo
from devteam_state.repositories import orchestration as orch
from devteam_state.repositories import spec_versions as specs

N = NodeName
_NODE_VALUES = frozenset(n.value for n in NodeName)
_EVENT_CODE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")
T = TaskStatus
GUARD = "service:build_entry_guard"
ANALYSIS_PIECES = 12  # one bounded batch for the Part D analyst (Part H analyses every chunk)
ACKNOWLEDGED = "feasibility.low_coverage_acknowledged"  # written by the run command, read by the gate (D-63)
DECISION_REASON = "feasibility.decision_reason"  # cancel | revise (D-01)
REVISE_REASON = "cancelled to revise the BRD: upload the revised BRD and start a new run"

# --------------------------------------------------------------------------------------------- state

_Id = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]{1,11}_[0-9A-HJKMNP-TV-Z]{26}$")]
_Sha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_Code = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]
_Node = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,40}$")]


class GraphState(TypedDict, total=False):
    project_id: str
    run_id: str
    run_kind: str
    last_node: str
    route: str
    outcome: str
    reason_code: str
    reason: str
    analysis_task_id: str
    finalizer_task_id: str
    head_task_id: str
    documenter_task_id: str
    draft_spec_version_id: str
    draft_spec_checksum: str
    spec_version_id: str
    spec_checksum: str
    feasibility_approval_id: str
    feasibility_report_id: str
    project_approval_id: str
    publish_approval_id: str
    publish_declined: bool
    plan_id: str
    current_task_id: str
    services_done: list[str]
    build_complete: bool
    last_step: str
    failure_category: str
    failure_code: str


class StateUpdate(BaseModel):
    """Every node update is checked here: references, codes and short reasons only (no content)."""

    model_config = ConfigDict(extra="forbid")

    project_id: _Id | None = None
    run_id: _Id | None = None
    run_kind: Literal["initial", "change_request"] | None = None
    last_node: _Node | None = None
    route: _Node | None = None
    outcome: Literal["completed", "cancelled", "blocked", "failed"] | None = None
    reason_code: _Code | None = None
    reason: str | None = Field(default=None, max_length=300)
    analysis_task_id: _Id | None = None
    finalizer_task_id: _Id | None = None
    head_task_id: _Id | None = None
    documenter_task_id: _Id | None = None
    draft_spec_version_id: _Id | None = None
    draft_spec_checksum: _Sha | None = None
    spec_version_id: _Id | None = None
    spec_checksum: _Sha | None = None
    feasibility_approval_id: _Id | None = None
    feasibility_report_id: _Id | None = None
    project_approval_id: _Id | None = None
    publish_approval_id: _Id | None = None
    publish_declined: bool | None = None
    plan_id: _Id | None = None
    current_task_id: _Id | None = None
    services_done: list[_Node] | None = Field(default=None, max_length=10)
    build_complete: bool | None = None
    last_step: Literal["task", "service"] | None = None
    failure_category: _Code | None = None
    failure_code: _Code | None = None


NODE_STAGE: dict[NodeName, Stage] = {
    N.INTAKE: Stage.INTAKE,
    N.BRD_ANALYST: Stage.ANALYSIS,
    N.FEASIBILITY: Stage.FEASIBILITY,
    N.FEASIBILITY_DECISION: Stage.FEASIBILITY,
    N.REQUIREMENTS_FINALIZER: Stage.REQUIREMENTS,
    N.CLARIFICATION_FORM: Stage.REQUIREMENTS,
    N.PROJECT_APPROVAL: Stage.REQUIREMENTS,
    N.FREEZE_SPEC: Stage.REQUIREMENTS,
    **dict.fromkeys([n for n, s in NODE_SUBGRAPH.items() if s.value == "build"], Stage.BUILD),
    **dict.fromkeys([n for n, s in NODE_SUBGRAPH.items() if s.value == "delivery"], Stage.DELIVERY),
    **dict.fromkeys(
        [n for n, s in NODE_SUBGRAPH.items() if s.value == "change_request"], Stage.CHANGE_REQUEST
    ),
}

GATE_APPROVAL: dict[GateName, ApprovalType] = {
    GateName.FEASIBILITY_DECISION: ApprovalType.FEASIBILITY_DECISION,
    GateName.PROJECT_APPROVAL: ApprovalType.SPEC_APPROVAL,
    GateName.PUBLISH_APPROVAL: ApprovalType.PUBLISH_GITHUB,
    GateName.CHANGE_APPROVAL: ApprovalType.SPEC_APPROVAL,
}

BUILD_INPUTS: dict[AgentRole, type[BuildTaskInput]] = {
    AgentRole.ARCHITECT: ArchitectInput,
    AgentRole.DATABASE_DEVELOPER: DatabaseDeveloperInput,
    AgentRole.BACKEND_DEVELOPER: BackendDeveloperInput,
    AgentRole.FRONTEND_DEVELOPER: FrontendDeveloperInput,
    AgentRole.TESTER: TesterInput,
    AgentRole.DEBUGGER_REVIEWER: DebuggerReviewerInput,
}


class IllegalTransitionError(StepError):
    def __init__(self, message: str) -> None:
        super().__init__("orchestration.illegal_transition", message, status="blocked")


# ------------------------------------------------------------------------------------------ helpers


def emit(rt: Runtime, event: Any) -> None:
    with rt.sessions.begin() as session:
        orch.append_event(session, event)


def _base(rt: Runtime, state: GraphState) -> dict[str, Any]:
    return {
        "event_id": new_id("evt", rt.clock),
        "ts": rt.clock.now(),
        "project_id": state["project_id"],
        "run_id": state["run_id"],
        "actor": ORCHESTRATOR,
    }


def _audit(rt: Runtime, state: GraphState, action: str, outcome: Any, **details: Any) -> None:
    with rt.sessions.begin() as session:
        audit.append(
            session,
            action=action,
            outcome=outcome,
            principal_id=ORCHESTRATOR,
            project_id=state["project_id"],
            target_type="run",
            target_id=state["run_id"],
            details=details,
            clock=rt.clock,
        )


def spec_problem(rt: Runtime, project_id: str, run_id: str) -> str | None:  # noqa: PLR0911 - one per reason
    """Why this run may not do build/delivery work now, or ``None`` (re-read from SQLite every time)."""
    with rt.sessions() as session:
        run = runs.get_run(session, project_id, run_id)
        if run is None:
            return "the run does not exist in this project"
        if not run.spec_version_id or not run.spec_checksum:
            return "no approved, frozen specification is recorded for this run"
        row = specs.get_spec_version(session, project_id, run.spec_version_id)
        if row is None or row.run_id != run_id:
            return "the recorded specification version does not belong to this run"
        if row.status != specs.FROZEN:
            return "the specification version is not frozen"
        if not specs.checksum_matches(row) or row.checksum != run.spec_checksum:
            return "the specification no longer matches its approved checksum"
        approval_id, checksum, version_id = row.approval_id, row.checksum, row.id
    if not approval_id:
        return "the frozen specification has no approval recorded"
    try:
        rt.approvals.verify(
            approval_id,
            project_id=project_id,
            run_id=run_id,
            approval_type=ApprovalType.SPEC_APPROVAL,
            spec_version_id=version_id,
            spec_checksum=checksum,
            verifier=GUARD,
        )
    except ApprovalDeniedError as exc:
        return f"the specification approval failed verification ({exc.code})"
    return None


def stand_in(rt: Runtime, state: GraphState, node: NodeName) -> None:
    outcome = services.stand_in_outcome(
        node, project_id=state["project_id"], run_id=state["run_id"], clock=rt.clock
    )
    emit(
        rt,
        ServiceStepRecorded(
            **_base(rt, state),
            node=node,
            status=outcome.status,
            stand_in=True,
            available_in_part=outcome.available_in_part,
        ),
    )


def _finish(outcome: str, code: str, reason: str) -> dict[str, Any]:
    return {"route": N.FINALIZE_RUN.value, "outcome": outcome, "reason_code": code, "reason": reason[:300]}


# ------------------------------------------------------------------------------------------ wrapper

NodeFn = Callable[[GraphState], dict[str, Any]]


def wrap(rt: Runtime, name: NodeName, fn: NodeFn) -> NodeFn:
    kind = NODE_KIND[name]
    targets = ALLOWED_TRANSITIONS[name]

    def run(state: GraphState) -> dict[str, Any]:
        previous = state.get("last_node")
        legal = (previous is None and name is ENTRY_NODE) or (
            previous in _NODE_VALUES and is_allowed_transition(N(previous), name)
        )
        if not legal:
            _audit(
                rt, state, "orchestration.illegal_transition", "denied", source=previous, target=name.value
            )
            raise IllegalTransitionError(f"transition {previous} -> {name.value} is not allowed")
        if name in SPEC_BOUND_NODES and name is not N.BUILD_ENTRY_GUARD:
            problem = spec_problem(rt, state["project_id"], state["run_id"])
            if problem is not None:
                _audit(rt, state, "build.entry_refused", "denied", node=name.value, reason=problem)
                raise StepError("build.entry_refused", problem)
        # gates write nothing before interrupt(); the worker records the waiting state
        update = fn(state) if kind is NodeKind.GATE else _observed(rt, state, name, kind, fn)
        update = dict(update)
        update["last_node"] = name.value
        route = update.get("route")
        if len(targets) == 1:
            update["route"] = next(iter(targets)).value
        elif len(targets) > 1 and (route is None or route not in {t.value for t in targets}):
            _audit(rt, state, "orchestration.illegal_transition", "denied", source=name.value, target=route)
            raise IllegalTransitionError(f"{name.value} chose a successor outside the table ({route})")
        try:
            StateUpdate.model_validate(update)
        except ValidationError:
            raise StepError(
                "orchestration.state_invalid", f"{name.value} produced a state update that is not allowed"
            ) from None
        return update

    return run


def _observed(rt: Runtime, state: GraphState, name: NodeName, kind: NodeKind, fn: NodeFn) -> dict[str, Any]:
    started = time.monotonic()
    with rt.sessions.begin() as session:
        orch.set_run_position(
            session,
            state["project_id"],
            state["run_id"],
            current_node=name.value,
            pending_gate=None,
            clock=rt.clock,
        )
        run = runs.get_run(session, state["project_id"], state["run_id"])
        if run is not None and name in NODE_STAGE:
            run.stage = NODE_STAGE[name].value
    emit(rt, NodeStarted(**_base(rt, state), node=name, node_kind=kind, run_mode=RunMode.STUB))
    with rt.tracer.span(
        f"graph.node.{name.value}", project_id=state["project_id"], run_id=state["run_id"]
    ) as span:
        try:
            update = fn(state)
        except StepError as exc:
            span.set("outcome", "error")
            span.set("error.code", exc.code)
            _finished(rt, state, name, kind, started, "error", exc.code)
            raise
        except Exception as exc:
            code = getattr(exc, "code", None)
            _finished(
                rt, state, name, kind, started, "error", code if isinstance(code, str) else "node.error"
            )
            raise
        span.set("outcome", "ok")
    _finished(rt, state, name, kind, started, "ok", None)
    return update


def _finished(  # noqa: PLR0917
    rt: Runtime,
    state: GraphState,
    name: NodeName,
    kind: NodeKind,
    started: float,
    outcome: Any,
    code: str | None,
) -> None:
    safe = code if code is not None and _EVENT_CODE.fullmatch(code) else None
    emit(
        rt,
        NodeFinished(
            **_base(rt, state),
            node=name,
            node_kind=kind,
            outcome=outcome,
            duration_ms=int((time.monotonic() - started) * 1000),
            error_code=safe,
        ),
    )


# ---------------------------------------------------------------------------------------- the nodes


class Nodes:
    """Node implementations. Each returns a small state update (references only)."""

    def __init__(self, rt: Runtime) -> None:
        self.rt = rt

    # ---------------------------------------------------------------------------- parent graph

    def route_entry(self, state: GraphState) -> dict[str, Any]:
        with self.rt.sessions() as session:
            run = runs.get_run(session, state["project_id"], state["run_id"])
            kind = run.run_kind if run is not None else "initial"
        target = N.CHANGE_INTAKE if kind == "change_request" else N.INTAKE
        return {"route": target.value, "run_kind": kind, "services_done": []}

    def route_after_discovery(self, state: GraphState) -> dict[str, Any]:
        if state.get("spec_version_id"):
            return {"route": N.BUILD_ENTRY_GUARD.value}
        return _finish("blocked", "spec.not_frozen", "the specification was not frozen")

    def route_after_change(self, state: GraphState) -> dict[str, Any]:
        if state.get("spec_version_id"):
            return {"route": N.BUILD_ENTRY_GUARD.value}
        return _finish("blocked", "spec.not_frozen", "the change specification was not frozen")

    def route_after_build(self, state: GraphState) -> dict[str, Any]:
        if state.get("build_complete"):
            return {"route": N.COMPLETION_GATE_1.value}
        counts = self._task_counts(state)
        return _finish(
            "blocked",
            "build.incomplete",
            f"the build did not complete: {counts[0]} of {counts[1]} tasks completed",
        )

    def finalize_run(self, state: GraphState) -> dict[str, Any]:
        outcome = state.get("outcome") or "completed"
        target = {
            "completed": RunStatus.COMPLETED,
            "cancelled": RunStatus.CANCELLED,
            "blocked": RunStatus.BLOCKED,
            "failed": RunStatus.FAILED,
        }[outcome]
        finish_run(self.rt, state["project_id"], state["run_id"], target, state.get("reason") or outcome)
        return {"outcome": outcome}

    # ------------------------------------------------------------------------------- discovery

    def intake(self, state: GraphState) -> dict[str, Any]:
        # D-42 (a): extraction happens during upload (Part B behaviour unchanged); nothing is re-extracted.
        with self.rt.sessions() as session:
            docs = documents.list_for_project(session, state["project_id"])
        ready = [d for d in docs if d.status == "extracted" and d.chunk_count > 0]
        if not ready:
            raise StepError("intake.no_document", "the project has no extracted document to analyse")
        return {}

    def brd_analyst(self, state: GraphState) -> dict[str, Any]:
        rt, project_id, run_id = self.rt, state["project_id"], state["run_id"]
        with rt.sessions() as session:
            docs = [d for d in documents.list_for_project(session, project_id) if d.status == "extracted"]
            doc_ids: list[str] = []
            chunk_ids: list[str] = []
            for doc in docs:
                if len(chunk_ids) >= ANALYSIS_PIECES:
                    break
                chunks, _ = documents.list_chunks(
                    session, project_id, doc.id, offset=0, limit=ANALYSIS_PIECES
                )
                taken = [c.id for c in chunks][: ANALYSIS_PIECES - len(chunk_ids)]
                if taken:
                    doc_ids.append(doc.id)
                    chunk_ids.extend(taken)
        if not chunk_ids:
            raise StepError("intake.no_document", "the project has no extracted text to analyse")

        def build(task_id: str) -> AgentTask:
            return AgentTask(
                task_id=task_id,
                project_id=project_id,
                run_id=run_id,
                agent_type=AgentRole.BRD_ANALYST,
                operation_id="brd_analyst.analyse_section",
                objective=f"Analyse the first {len(chunk_ids)} pieces of the BRD",
                done_criteria=("checklist and cited requirements returned",),
            )

        task_id = ensure_task(rt, node=N.BRD_ANALYST.value, scope="analysis", build=build)
        given = BrdAnalystInput(
            project_id=project_id, run_id=run_id, document_ids=tuple(doc_ids), chunk_ids=tuple(chunk_ids)
        )
        outcome = run_task(
            rt, project_id=project_id, task_id=task_id, stage=Stage.ANALYSIS, given=given, spec_approved=False
        )
        require_completed(outcome, "The BRD analysis")
        return {"analysis_task_id": task_id}

    def feasibility(self, state: GraphState) -> dict[str, Any]:
        """Part G: the real Feasibility Gate service (deterministic; no model); one report per analysis.

        Idempotent on resume: the effect ledger names the report once it is stored, and a crash between
        storing the report and confirming the effect finds that report again instead of writing another.
        """
        rt, project_id, run_id = self.rt, state["project_id"], state["run_id"]
        analysis_task_id = state["analysis_task_id"]
        key = orch.effect_key(run_id, N.FEASIBILITY.value, f"report_{analysis_task_id}")
        with rt.sessions.begin() as session:
            effect = orch.begin_effect(
                session,
                project_id=project_id,
                run_id=run_id,
                node=N.FEASIBILITY.value,
                key=key,
                clock=rt.clock,
            )
            report_id = effect.result_ref
            if report_id is None:
                existing = feasibility_repo.latest_report(session, project_id, run_id)
                if existing is not None and existing.report.analysis_task_id == analysis_task_id:
                    report_id = existing.report.report_id  # stored before a crash: reuse it
        if report_id is None:
            report_id = self._evaluate_feasibility(project_id, run_id, analysis_task_id)
        with rt.sessions.begin() as session:
            effect = orch.begin_effect(
                session,
                project_id=project_id,
                run_id=run_id,
                node=N.FEASIBILITY.value,
                key=key,
                clock=rt.clock,
            )
            if effect.result_ref is None:
                orch.advance_effect(session, key, "confirmed", result_ref=report_id, clock=rt.clock)
        emit(
            rt,
            ServiceStepRecorded(
                **_base(rt, state), node=N.FEASIBILITY, status=ServiceOutcomeStatus.PERFORMED, stand_in=False
            ),
        )
        return {"feasibility_report_id": report_id}

    def _evaluate_feasibility(self, project_id: str, run_id: str, analysis_task_id: str) -> str:
        rt = self.rt
        raw = stored_output(rt, project_id, analysis_task_id)
        if raw is None:
            raise StepError("feasibility.no_analysis", "the BRD analysis has no stored output to check")
        try:
            analysis = BrdAnalystOutput.model_validate(raw)
        except ValidationError:
            raise StepError("feasibility.bad_analysis", "the stored BRD analysis is not valid") from None
        with rt.sessions() as session:
            snapshot = rt.catalog_snapshot(session)
        stored = rt.feasibility_gate().evaluate(
            project_id=project_id,
            run_id=run_id,
            analysis_task_id=analysis_task_id,
            analysis=analysis,
            snapshot=snapshot,
            write=rt.files_for(project_id).write_text,
        )
        return stored.report.report_id

    def requirements_finalizer(self, state: GraphState) -> dict[str, Any]:
        rt, project_id, run_id = self.rt, state["project_id"], state["run_id"]
        analysis = stored_output(rt, project_id, state["analysis_task_id"]) or {}
        analysed = tuple(r["requirement_id"] for r in analysis.get("requirements", []))
        # Part G: requirements blocked by feasibility leave the scope before the spec is drafted (reason kept)
        blocked, item_ids = self._feasibility_scope(state)
        keys = tuple(k for k in analysed if k not in blocked)

        def build(task_id: str) -> AgentTask:
            return AgentTask(
                task_id=task_id,
                project_id=project_id,
                run_id=run_id,
                agent_type=AgentRole.REQUIREMENTS_FINALIZER,
                operation_id="requirements_finalizer.consolidate",
                objective="Consolidate the accepted requirements into the scope to approve",
                requirement_ids=keys,
                done_criteria=("scope decision returned",),
            )

        task_id = ensure_task(rt, node=N.REQUIREMENTS_FINALIZER.value, scope="consolidate", build=build)
        given = RequirementsFinalizerInput(
            project_id=project_id, run_id=run_id, requirement_ids=keys, feasibility_item_ids=item_ids
        )
        outcome = run_task(
            rt,
            project_id=project_id,
            task_id=task_id,
            stage=Stage.REQUIREMENTS,
            given=given,
            spec_approved=False,
        )
        output = require_completed(outcome, "The requirements consolidation")
        excluded = set(output.get("excluded_requirement_ids", []))
        in_scope = sorted(k for k in keys if k not in excluded)
        payload = {
            "in_scope": in_scope,
            "excluded": sorted(excluded),
            "analysis_task_id": state["analysis_task_id"],
            "finalizer_task_id": task_id,
            "blocked_by_feasibility": sorted(k for k in analysed if k in blocked),
            "feasibility_report_id": state.get("feasibility_report_id"),
        }
        key = orch.effect_key(run_id, N.REQUIREMENTS_FINALIZER.value, "spec_draft")
        with rt.sessions.begin() as session:
            effect = orch.begin_effect(
                session,
                project_id=project_id,
                run_id=run_id,
                node=N.REQUIREMENTS_FINALIZER.value,
                key=key,
                clock=rt.clock,
            )
            if effect.result_ref is None:
                draft = specs.create_draft(
                    session, project_id=project_id, run_id=run_id, payload=payload, clock=rt.clock
                )
                orch.advance_effect(session, key, "confirmed", result_ref=draft.id, clock=rt.clock)
                draft_id, checksum = draft.id, draft.checksum
            else:
                row = specs.get_spec_version(session, project_id, effect.result_ref)
                assert row is not None  # noqa: S101 - written in the same transaction as the effect
                draft_id, checksum = row.id, row.checksum
        # No critical-gap producer exists before Part I, so the clarification form is never needed here.
        return {
            "finalizer_task_id": task_id,
            "draft_spec_version_id": draft_id,
            "draft_spec_checksum": checksum,
            "route": N.PROJECT_APPROVAL.value,
        }

    def _feasibility_scope(self, state: GraphState) -> tuple[frozenset[str], tuple[str, ...]]:
        """Blocked keys and item IDs of the feasibility report the gate decision was made on."""
        report_id = state.get("feasibility_report_id")
        if not report_id:
            raise StepError("feasibility.no_report", "no feasibility report is recorded for this run")
        with self.rt.sessions() as session:
            stored = feasibility_repo.get_report(session, state["project_id"], report_id)
        if stored is None or stored.report.run_id != state["run_id"]:
            raise StepError("feasibility.no_report", "the feasibility report does not belong to this run")
        return frozenset(stored.report.blocked_keys), tuple(i.item_id for i in stored.items)[:500]

    def freeze_spec(self, state: GraphState) -> dict[str, Any]:
        rt, project_id, run_id = self.rt, state["project_id"], state["run_id"]
        draft_id = state["draft_spec_version_id"]
        services.freeze_spec(
            rt.sessions,
            rt.approvals,
            project_id=project_id,
            run_id=run_id,
            spec_version_id=draft_id,
            approval_id=state["project_approval_id"],
            clock=rt.clock,
        )
        with rt.sessions.begin() as session:
            row = specs.get_spec_version(session, project_id, draft_id)
            run = runs.get_run(session, project_id, run_id)
            assert row is not None and run is not None  # noqa: S101 - verified by freeze_spec
            run.spec_version_id, run.spec_checksum = row.id, row.checksum
            checksum, payload = row.checksum, dict(row.payload)
        self._write_requirements(state, payload)
        return {"spec_version_id": draft_id, "spec_checksum": checksum}

    def _write_requirements(self, state: GraphState, payload: dict[str, Any]) -> None:
        """``generated/spec/REQUIREMENTS.md``: the frozen scope with the analyst's cited descriptions."""
        analysis = stored_output(self.rt, state["project_id"], payload["analysis_task_id"]) or {}
        described = {r["requirement_id"]: r["description"] for r in analysis.get("requirements", [])}
        lines = [
            "# Approved requirements (frozen)",
            "",
            "Written by the Spec Freeze service from the approved specification version. Each line was",
            "extracted from the BRD by the BRD Analyst and cites its source piece in the platform records.",
            "",
        ]
        lines += [
            f"- **{key}**: {described.get(key, '(no description recorded)')}" for key in payload["in_scope"]
        ]
        if not payload["in_scope"]:
            lines.append("- (no requirements were recorded in scope)")
        files = self.rt.files_for(state["project_id"])
        files.write_text("generated/spec/REQUIREMENTS.md", "\n".join(lines) + "\n")

    # ----------------------------------------------------------------------------------- gates

    def gate(self, name: NodeName) -> NodeFn:
        gate = GATE_FOR_NODE[name]

        def run(state: GraphState) -> dict[str, Any]:
            from langgraph.types import interrupt  # noqa: PLC0415 - only nodes call interrupt()

            payload: dict[str, Any] = {"gate": gate.value, "run_id": state["run_id"]}
            if gate is GateName.FEASIBILITY_DECISION:
                payload["report_id"] = state.get("feasibility_report_id")  # a reference only
            raw = interrupt(payload)
            return self._decide(state, name, gate, raw)

        return run

    def _decide(  # noqa: PLR0912 - one branch per gate rule
        self, state: GraphState, name: NodeName, gate: GateName, raw: Any
    ) -> dict[str, Any]:
        rt = self.rt
        try:
            resume = GateResume.model_validate(raw)
        except ValidationError:
            _audit(rt, state, "gate.refused", "denied", gate=gate.value, code="gate.bad_resume")
            raise StepError(
                "gate.bad_resume", "the gate was resumed without a valid decision reference"
            ) from None
        if resume.run_id != state["run_id"] or resume.gate is not gate:
            _audit(rt, state, "gate.refused", "denied", gate=gate.value, code="gate.wrong_target")
            raise StepError("gate.wrong_target", "the decision belongs to another run or gate")
        if gate is GateName.CLARIFICATION_FORM:
            raise StepError("gate.unavailable", "clarification submissions are available from Part I")
        if gate is GateName.CHANGE_APPROVAL:
            raise StepError("gate.unavailable", "change requests are available from Part Q")
        approval_type = GATE_APPROVAL[gate]
        if gate is GateName.PROJECT_APPROVAL:
            version, checksum = state.get("draft_spec_version_id"), state.get("draft_spec_checksum")
        elif gate is GateName.PUBLISH_APPROVAL:
            version, checksum = state.get("spec_version_id"), state.get("spec_checksum")
        else:
            version, checksum = None, None
        assert resume.approval_id is not None  # noqa: S101 - GateResume requires it for these gates
        try:
            rt.approvals.verify(
                resume.approval_id,
                project_id=state["project_id"],
                run_id=state["run_id"],
                approval_type=approval_type,
                spec_version_id=version,
                spec_checksum=checksum,
                verifier=GUARD,
            )
        except ApprovalDeniedError as exc:
            if exc.code != "approval.not_approved":  # forged, stale, other run: fail closed
                raise StepError(exc.code, f"the decision could not be verified ({exc.code})") from None
            if gate is GateName.PUBLISH_APPROVAL:
                return {"route": N.FINAL_REPORT.value, "publish_declined": True}
            if gate is GateName.FEASIBILITY_DECISION and self._revise(state, resume.approval_id):
                return _finish("cancelled", "feasibility.revise", REVISE_REASON)
            return _finish("cancelled", "gate.declined", f"{gate.value.replace('_', ' ')} was declined")
        if gate is GateName.FEASIBILITY_DECISION:
            problem = self._proceed_problem(state)
            if problem is not None:
                _audit(rt, state, "feasibility.proceed_refused", "denied", code=problem[0])
                return _finish("blocked", problem[0], problem[1])
        field = {
            GateName.FEASIBILITY_DECISION: "feasibility_approval_id",
            GateName.PROJECT_APPROVAL: "project_approval_id",
            GateName.PUBLISH_APPROVAL: "publish_approval_id",
        }[gate]
        approved_next = {
            GateName.FEASIBILITY_DECISION: N.REQUIREMENTS_FINALIZER,
            GateName.PROJECT_APPROVAL: N.FREEZE_SPEC,
            GateName.PUBLISH_APPROVAL: N.GITHUB_DEPLOYER,
        }[gate]
        return {field: resume.approval_id, "route": approved_next.value}

    def _proceed_problem(self, state: GraphState) -> tuple[str, str] | None:
        """D-63, re-checked at the gate (the run command checks first): never proceed at "nothing to build";
        below the threshold only with the human's recorded acknowledgement of this very report."""
        report_id = state.get("feasibility_report_id")
        with self.rt.sessions() as session:
            stored = (
                feasibility_repo.get_report(session, state["project_id"], report_id) if report_id else None
            )
            if stored is None or stored.report.run_id != state["run_id"]:
                return "feasibility.no_report", "no feasibility report is recorded for this run"
            report = stored.report
            if not report.proceed_allowed:
                return (
                    "feasibility.proceed_disabled",
                    "nothing in scope can be built, so the run cannot proceed",
                )
            if report.needs_acknowledgement:
                event = audit_repo.latest_for_target(
                    session,
                    actions=(ACKNOWLEDGED,),
                    target_type="feasibility_report",
                    target_id=report.report_id,
                )
                if event is None or event.project_id != state["project_id"]:
                    return (
                        "feasibility.acknowledgement_missing",
                        "coverage is below the threshold and the low coverage was not acknowledged",
                    )
        return None

    def _revise(self, state: GraphState, approval_id: str | None) -> bool:
        """D-01: a declined feasibility decision is a cancellation; "revise" names why (a new run follows)."""
        with self.rt.sessions() as session:
            event = audit_repo.latest_for_target(
                session, actions=(DECISION_REASON,), target_type="approval", target_id=approval_id or "-"
            )
        if event is None or event.project_id != state["project_id"]:
            return False
        return (event.details or {}).get("reason") == "revise"

    # ----------------------------------------------------------------------------------- build

    def build_entry_guard(self, state: GraphState) -> dict[str, Any]:
        problem = spec_problem(self.rt, state["project_id"], state["run_id"])
        if problem is None and state.get("spec_version_id"):
            _audit(self.rt, state, "build.entry_allowed", "success", spec_version_id=state["spec_version_id"])
            return {"route": N.HEAD.value}
        reason = problem or "no frozen specification in the run state"
        _audit(self.rt, state, "build.entry_refused", "denied", node=N.BUILD_ENTRY_GUARD.value, reason=reason)
        return _finish("blocked", "build.entry_refused", reason)

    def head(self, state: GraphState) -> dict[str, Any]:
        rt, project_id, run_id = self.rt, state["project_id"], state["run_id"]
        spec_id, checksum = state["spec_version_id"], state["spec_checksum"]
        with rt.sessions() as session:
            row = specs.get_spec_version(session, project_id, spec_id)
            assert row is not None  # noqa: S101 - checked by the wrapper
            in_scope = tuple(row.payload.get("in_scope", ()))
        plan_id = self._persist_plan(state, in_scope)
        if plan_id is None:
            return _finish("blocked", "plan.rejected", "the build plan failed validation")

        def build(task_id: str) -> AgentTask:
            return AgentTask(
                task_id=task_id,
                project_id=project_id,
                run_id=run_id,
                agent_type=AgentRole.HEAD,
                operation_id="head.plan",
                objective="Summarise the validated build plan",
                requirement_ids=in_scope[:200],
                done_criteria=("progress summary returned",),
                spec_version_id=spec_id,
            )

        task_id = ensure_task(rt, node=N.HEAD.value, scope="plan_summary", build=build)
        given = HeadInput(
            project_id=project_id,
            run_id=run_id,
            spec_version_id=spec_id,
            spec_checksum=checksum,
            in_scope_keys=in_scope,
        )
        outcome = run_task(
            rt, project_id=project_id, task_id=task_id, stage=Stage.BUILD, given=given, spec_approved=True
        )
        require_completed(outcome, "The Head Agent's plan step")
        return {"head_task_id": task_id, "plan_id": plan_id, "route": N.SCHEDULE.value, "services_done": []}

    def _persist_plan(self, state: GraphState, in_scope: tuple[str, ...]) -> str | None:
        """Template plan → validator → stored plan and task rows, exactly once per spec version."""
        rt, project_id, run_id, spec_id = (
            self.rt,
            state["project_id"],
            state["run_id"],
            state["spec_version_id"],
        )
        key = orch.effect_key(run_id, N.HEAD.value, f"plan_{spec_id}")
        with rt.sessions.begin() as session:
            effect = orch.begin_effect(
                session, project_id=project_id, run_id=run_id, node=N.HEAD.value, key=key, clock=rt.clock
            )
            if effect.result_ref is not None:
                return effect.result_ref
            plan_id = new_id("pln", rt.clock)
            candidate = template_plan(
                project_id=project_id,
                run_id=run_id,
                spec_version_id=spec_id,
                in_scope_keys=in_scope,
                write_scopes=rt.write_scopes,
                make_id=lambda prefix: new_id(prefix, rt.clock),
            )
            checked = validate_plan(
                candidate,
                PlanContext(
                    plan_id=plan_id,
                    project_id=project_id,
                    run_id=run_id,
                    spec_version_id=spec_id,
                    in_scope_keys=frozenset(in_scope),
                    write_scopes=rt.write_scopes,
                ),
            )
            report = checked.report.model_dump(mode="json")
            if checked.plan is None:
                audit.append(
                    session,
                    action="plan.rejected",
                    outcome="denied",
                    principal_id=ORCHESTRATOR,
                    project_id=project_id,
                    target_type="plan",
                    target_id=plan_id,
                    details={"run_id": run_id, "issues": [i.code.value for i in checked.report.issues][:20]},
                    clock=rt.clock,
                )
                return None
            orch.add_plan(
                session,
                plan_id=plan_id,
                project_id=project_id,
                run_id=run_id,
                spec_version_id=spec_id,
                source="template",
                payload=checked.plan.model_dump(mode="json"),
                report=report,
                clock=rt.clock,
            )
            for task in checked.plan.tasks:
                runs.add_task(session, task, clock=rt.clock)
            orch.advance_effect(session, key, "confirmed", result_ref=plan_id, clock=rt.clock)
            audit.append(
                session,
                action="plan.accepted",
                outcome="success",
                principal_id=ORCHESTRATOR,
                project_id=project_id,
                target_type="plan",
                target_id=plan_id,
                details={"run_id": run_id, "tasks": len(checked.plan.tasks), "source": "template"},
                clock=rt.clock,
            )
            return plan_id

    def _plan_tasks(self, state: GraphState) -> list[AgentTask]:
        with self.rt.sessions() as session:
            plan = orch.latest_plan(session, state["project_id"], state["run_id"])
            if plan is None:
                return []
            ids = [t["task_id"] for t in plan.payload["tasks"]]
            extra = list(
                session.scalars(
                    select(AgentTaskRow.id).where(
                        AgentTaskRow.run_id == state["run_id"],
                        AgentTaskRow.agent_type == AgentRole.DEBUGGER_REVIEWER.value,
                    )
                )
            )
            rows = [runs.get_task(session, state["project_id"], tid) for tid in [*ids, *extra]]
            return [runs.load_task_contract(r) for r in rows if r is not None]

    def _task_counts(self, state: GraphState) -> tuple[int, int]:
        tasks = self._plan_tasks(state)
        return sum(t.status is T.COMPLETED for t in tasks), len(tasks)

    def schedule(self, state: GraphState) -> dict[str, Any]:
        done = frozenset(N(s) for s in state.get("services_done", []))
        for _ in range(1_000):
            tasks = self._plan_tasks(state)
            decision = next_step(tasks, done)
            if decision.action is Action.SKIP:
                for task_id in decision.skipped:
                    set_task_status(self.rt, state["project_id"], task_id, T.SKIPPED, decision.reason)
                continue
            if decision.action is Action.DISPATCH:
                return {"route": decision.node.value, "current_task_id": decision.task_id}
            if decision.action is Action.RUN_SERVICE:
                return {"route": decision.node.value}
            return {"route": N.ROUTE_AFTER_BUILD.value, "build_complete": decision.all_completed}
        raise StepError("schedule.no_progress", "the scheduler made no progress")

    def build_agent(self, role: AgentRole) -> NodeFn:
        def run(state: GraphState) -> dict[str, Any]:
            rt, project_id, run_id = self.rt, state["project_id"], state["run_id"]
            task = load_task(rt, project_id, state["current_task_id"])
            if task.agent_type is not role:
                raise IllegalTransitionError(
                    f"task {task.task_id} belongs to {task.agent_type.value}, not {role.value}"
                )
            if task.status in {T.PENDING, T.READY}:
                try:
                    dispatch(task, now=rt.clock.now())  # Head dispatcher: attempt bound and ledger key
                except DispatchError as exc:
                    raise StepError("dispatch.refused", str(exc)) from None
            given = BUILD_INPUTS[role](
                project_id=project_id, run_id=run_id, task=task, spec_version_id=state["spec_version_id"]
            )
            outcome = run_task(
                rt,
                project_id=project_id,
                task_id=task.task_id,
                stage=Stage.BUILD,
                given=given,
                spec_approved=True,
            )
            update: dict[str, Any] = {"last_step": "task", "failure_category": None, "failure_code": None}
            if outcome.status is not T.COMPLETED and outcome.error is not None:
                update["failure_category"] = outcome.error.category.value
                update["failure_code"] = outcome.error.code
            return update

        return run

    def build_service(self, node: NodeName) -> NodeFn:
        def run(state: GraphState) -> dict[str, Any]:
            stand_in(self.rt, state, node)
            done = [*state.get("services_done", []), node.value]
            return {"services_done": list(dict.fromkeys(done)), "last_step": "service"}

        return run

    def verify_task(self, state: GraphState) -> dict[str, Any]:
        if state.get("last_step") == "service":
            return {"route": N.SCHEDULE.value}
        rt, project_id, task_id = self.rt, state["project_id"], state["current_task_id"]
        status = task_row_status(rt, project_id, task_id)
        if status is not T.COMPLETED:
            return {
                "route": N.FAILURE_ROUTER.value,
                "failure_category": state.get("failure_category") or ErrorCategory.FATAL.value,
                "failure_code": state.get("failure_code") or "task.not_completed",
            }
        problem = self._check_files(project_id, stored_output(rt, project_id, task_id) or {})
        if problem is not None:
            _audit(rt, state, "task.verification_failed", "denied", task_id=task_id, code=problem)
            return {
                "route": N.FAILURE_ROUTER.value,
                "failure_category": ErrorCategory.INVALID_OUTPUT.value,
                "failure_code": problem,
            }
        return {"route": N.SCHEDULE.value}

    def _check_files(self, project_id: str, output: dict[str, Any]) -> str | None:
        """Declared files exist with the reported checksum and contain no forbidden claim (Spec §6.1)."""
        declared = [f for f in output.get("files", []) if isinstance(f, dict)]
        for key in ("test_plan", "openapi"):
            if isinstance(output.get(key), dict):
                declared.append(output[key])
        files = self.rt.files_for(project_id)
        for item in declared:
            try:
                text = files.read_text(str(item["path"]))
            except Exception:
                return "verify.file_missing"
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != item.get("sha256"):
                return "verify.checksum_mismatch"
            if any(claim.regex.search(text) for claim in self.rt.guardrails.output.forbidden_claims):
                return "verify.forbidden_claim"
        return None

    def failure_router(self, state: GraphState) -> dict[str, Any]:
        rt, project_id, task_id = self.rt, state["project_id"], state["current_task_id"]
        task = load_task(rt, project_id, task_id)
        code = state.get("failure_code") or "task.failed"
        if task.status is T.COMPLETED:  # verification failed after completion: cannot be re-attempted
            return _finish(
                "blocked", code, f"post-task verification failed for {task.agent_type.value} ({code})"
            )
        route = route_failure(
            Failure(
                task=task,
                source=FailureSource.AGENT_TASK,
                category=ErrorCategory(state.get("failure_category") or "fatal"),
                code=code,
            ),
            reattempt_allowance=rt.budgets.retries.task_reattempts,
            write_scopes=rt.write_scopes,
        )
        _audit(
            rt,
            state,
            "failure.routed",
            "success",
            task_id=task_id,
            decision=route.action.value,
            rule=route.rule,
        )
        if route.action is RouteAction.RETRY_TASK and task.status is T.FAILED:
            set_task_status(rt, project_id, task_id, T.READY, route.reason)
        return {"route": N.SCHEDULE.value}

    # -------------------------------------------------------------------------------- delivery

    def completion_gate(self, node: NodeName) -> NodeFn:
        def run(state: GraphState) -> dict[str, Any]:
            stand_in(self.rt, state, node)  # writes no verification status (Part N)
            return {}

        return run

    def documenter(self, state: GraphState) -> dict[str, Any]:
        rt, project_id, run_id = self.rt, state["project_id"], state["run_id"]
        tasks = self._plan_tasks(state)
        completed = [t for t in tasks if t.status is T.COMPLETED]
        facts = (
            f"{len(completed)} of {len(tasks)} build tasks completed by "
            + ", ".join(sorted({t.agent_type.value for t in completed}) or ["no agent"])
            + ". Integration build: not run (sandbox arrives in Part E). Tests: not run (Part M). "
            "Completion gate: not evaluated (Part N). Package: not built (Part O). Nothing was verified."
        )
        spec_id = state["spec_version_id"]

        def build(task_id: str) -> AgentTask:
            return AgentTask(
                task_id=task_id,
                project_id=project_id,
                run_id=run_id,
                agent_type=AgentRole.DOCUMENTER,
                operation_id="documenter.write_section",
                objective=facts,
                permitted_paths=rt.write_scopes[AgentRole.DOCUMENTER],
                done_criteria=("run notes written from the recorded facts only",),
                spec_version_id=spec_id,
            )

        task_id = ensure_task(rt, node=N.DOCUMENTER.value, scope="run_notes", build=build)
        task = load_task(rt, project_id, task_id)
        given = DocumenterInput(
            project_id=project_id,
            run_id=run_id,
            spec_version_id=spec_id,
            task=task,
            evidence_refs=tuple(t.task_id for t in completed)[:200],
        )
        outcome = run_task(
            rt, project_id=project_id, task_id=task_id, stage=Stage.DELIVERY, given=given, spec_approved=True
        )
        require_completed(outcome, "The documentation step")
        return {"documenter_task_id": task_id}

    def final_report(self, state: GraphState) -> dict[str, Any]:
        rt, project_id, run_id = self.rt, state["project_id"], state["run_id"]
        key = orch.effect_key(run_id, N.FINAL_REPORT.value, "summary")
        with rt.sessions.begin() as session:
            effect = orch.begin_effect(
                session,
                project_id=project_id,
                run_id=run_id,
                node=N.FINAL_REPORT.value,
                key=key,
                clock=rt.clock,
            )
            if effect.phase == "confirmed":
                return {"outcome": "completed"}
            tasks = list(session.scalars(select(AgentTaskRow).where(AgentTaskRow.run_id == run_id)))
            model_calls = _model_calls(session, project_id, run_id)
            stand_ins = (
                session.scalar(
                    select(func.count())
                    .select_from(RunEventRow)
                    .where(RunEventRow.run_id == run_id, RunEventRow.kind == "service.recorded")
                )
                or 0
            )
            orch.append_event(
                session,
                RunSummaryRecorded(
                    **_base(rt, state),
                    status=RunStatus.COMPLETED,
                    run_mode=RunMode.STUB,
                    completed_tasks=sum(t.status == T.COMPLETED.value for t in tasks),
                    total_tasks=len(tasks),
                    model_calls=model_calls,
                    stand_in_steps=stand_ins,
                ),
            )
            orch.advance_effect(session, key, "confirmed", result_ref=run_id, clock=rt.clock)
        return {"outcome": "completed"}

    # --------------------------------------------------------------------- change (skeleton, Part Q)

    def service_stand_in(self, node: NodeName) -> NodeFn:
        def run(state: GraphState) -> dict[str, Any]:
            stand_in(self.rt, state, node)
            return {}

        return run

    def freeze_change_spec(self, state: GraphState) -> dict[str, Any]:
        raise StepError("change.unavailable", "change requests are available from Part Q")


def _model_calls(session: Session, project_id: str, run_id: str) -> int:
    rows = session.scalars(
        select(AuditEvent.details).where(
            AuditEvent.project_id == project_id, AuditEvent.action == "model.called"
        )
    )
    return sum(1 for details in rows if isinstance(details, dict) and details.get("run_id") == run_id)


def finish_run(rt: Runtime, project_id: str, run_id: str, target: RunStatus, reason: str) -> None:
    """Move the run to a terminal status through the run state machine (idempotent)."""
    with rt.sessions.begin() as session:
        run = runs.get_run(session, project_id, run_id)
        if (
            run is None
            or RunStatus(run.status) is target
            or RunStatus(run.status)
            in {
                RunStatus.COMPLETED,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
                RunStatus.BLOCKED,
            }
        ):
            return
        if (
            RunStatus(run.status) in {RunStatus.WAITING_FOR_HUMAN, RunStatus.PAUSED}
            and target is not RunStatus.CANCELLED
        ):
            transition_run(
                session,
                run,
                RunStatus.RUNNING,
                actor=ORCHESTRATOR,
                reason="finishing",
                denial_sessions=rt.sessions,
                clock=rt.clock,
            )
        if RunStatus(run.status) is RunStatus.WAITING_FOR_RESOURCES and target not in {
            RunStatus.CANCELLED,
            RunStatus.BLOCKED,
        }:
            transition_run(
                session,
                run,
                RunStatus.RUNNING,
                actor=ORCHESTRATOR,
                reason="finishing",
                denial_sessions=rt.sessions,
                clock=rt.clock,
            )
        transition_run(
            session,
            run,
            target,
            actor=ORCHESTRATOR,
            reason=reason[:200],
            denial_sessions=rt.sessions,
            clock=rt.clock,
        )
        orch.set_run_position(
            session, project_id, run_id, current_node=None, pending_gate=None, clock=rt.clock
        )


# ------------------------------------------------------------------------------------------- build


def node_functions(rt: Runtime) -> dict[NodeName, NodeFn]:
    n = Nodes(rt)
    table: dict[NodeName, NodeFn] = {
        N.ROUTE_ENTRY: n.route_entry,
        N.ROUTE_AFTER_DISCOVERY: n.route_after_discovery,
        N.ROUTE_AFTER_CHANGE: n.route_after_change,
        N.ROUTE_AFTER_BUILD: n.route_after_build,
        N.FINALIZE_RUN: n.finalize_run,
        N.INTAKE: n.intake,
        N.BRD_ANALYST: n.brd_analyst,
        N.FEASIBILITY: n.feasibility,
        N.REQUIREMENTS_FINALIZER: n.requirements_finalizer,
        N.FREEZE_SPEC: n.freeze_spec,
        N.BUILD_ENTRY_GUARD: n.build_entry_guard,
        N.HEAD: n.head,
        N.SCHEDULE: n.schedule,
        N.INTEGRATION_BUILD: n.build_service(N.INTEGRATION_BUILD),
        N.TEST_RUNNER: n.build_service(N.TEST_RUNNER),
        N.VERIFY_TASK: n.verify_task,
        N.FAILURE_ROUTER: n.failure_router,
        N.COMPLETION_GATE_1: n.completion_gate(N.COMPLETION_GATE_1),
        N.DOCUMENTER: n.documenter,
        N.PACKAGER: n.service_stand_in(N.PACKAGER),
        N.COMPLETION_GATE_2: n.completion_gate(N.COMPLETION_GATE_2),
        N.GITHUB_DEPLOYER: n.service_stand_in(N.GITHUB_DEPLOYER),
        N.FINAL_REPORT: n.final_report,
        N.CHANGE_INTAKE: n.service_stand_in(N.CHANGE_INTAKE),
        N.CHANGE_IMPACT: n.service_stand_in(N.CHANGE_IMPACT),
        N.FREEZE_CHANGE_SPEC: n.freeze_change_spec,
    }
    for gate_node in GATE_FOR_NODE:
        table[gate_node] = n.gate(gate_node)
    for role in BUILD_INPUTS:
        table[N(role.value)] = n.build_agent(role)
    missing = set(NodeName) - set(table)
    if missing:
        raise RuntimeError(f"nodes without an implementation: {sorted(m.value for m in missing)}")
    return table


def _route(state: GraphState) -> str:
    return state["route"]


def build_graph(rt: Runtime, checkpointer: Any) -> Any:
    from langgraph.graph import END, START, StateGraph  # noqa: PLC0415 - LangGraph only here

    builder: Any = StateGraph(GraphState)
    functions = node_functions(rt)
    for name in NodeName:
        builder.add_node(
            name.value,
            wrap(rt, name, functions[name]),
            metadata={"node_kind": NODE_KIND[name].value, "subgraph": NODE_SUBGRAPH[name].value},
        )
    builder.add_edge(START, ENTRY_NODE.value)
    for name in NodeName:
        targets = ALLOWED_TRANSITIONS[name]
        if name is TERMINAL_NODE:
            builder.add_edge(name.value, END)
        elif len(targets) == 1:
            builder.add_edge(name.value, next(iter(targets)).value)
        else:
            builder.add_conditional_edges(name.value, _route, {t.value: t.value for t in sorted(targets)})
    return builder.compile(checkpointer=checkpointer, name="project_run")
