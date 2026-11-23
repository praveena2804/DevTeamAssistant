"""Orchestration contracts (LG-01, roadmap §6.1-§6.5, Part D plan §3.2-§3.8).

* ``NodeName``: the closed list of graph nodes. Routing functions may only return members.
* ``NODE_KIND`` / ``NODE_SUBGRAPH``: every node's kind (agent, service, gate, guard, router) and owner.
* ``AGENT_NODE_FOR_ROLE``: exactly one agent node per ``AgentRole`` (ten-agent layer L4).
* ``ALLOWED_TRANSITIONS``: the static allowed-transition table. The graph wrappers (D.3) check every
  actual transition against it; a conformance test checks the compiled graph's edges against it.
* ``GateResume``: the only payload a gate accepts on resume (IDs only; the gate re-verifies them).
* ``DispatchRecord``: the Head dispatcher's hand-off of one task to one agent node.
* ``PlanValidationReport``: the Head plan validator's result (every problem, with a code).

The table is data, not behaviour, so it lives with the other contracts. Its structural guarantees
(every node reachable, build nodes reachable only through spec freeze and the build-entry guard) are
asserted by tests on the table itself and again on the compiled graph.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import Literal, Self

from pydantic import Field, model_validator

from devteam_core.contracts._base import (
    ApprovalId,
    AwareDatetime,
    Contract,
    PathScope,
    PlanId,
    ProjectId,
    RecordRef,
    RunId,
    ShortText,
    TaskId,
)
from devteam_core.contracts.records import ApprovalDecision
from devteam_core.contracts.statuses import BUILD_PLAN_ROLES, AgentRole, GateName

_LIST = 200


class NodeKind(StrEnum):
    """Node kinds, tagged in node metadata (roadmap §6.1, §4.4 L4)."""

    AGENT = "agent"
    SERVICE = "service"
    GATE = "gate"
    GUARD = "guard"
    ROUTER = "router"


class Subgraph(StrEnum):
    PROJECT_RUN = "project_run"  # the parent graph
    DISCOVERY = "discovery"
    BUILD = "build"
    DELIVERY = "delivery"
    CHANGE_REQUEST = "change_request"


class NodeName(StrEnum):
    """Every node of the ``project_run`` graph and its four subgraphs (Part D plan §3.2)."""

    # parent graph
    ROUTE_ENTRY = "route_entry"
    ROUTE_AFTER_DISCOVERY = "route_after_discovery"
    ROUTE_AFTER_CHANGE = "route_after_change"
    ROUTE_AFTER_BUILD = "route_after_build"
    FINALIZE_RUN = "finalize_run"
    # discovery
    INTAKE = "intake"
    BRD_ANALYST = "brd_analyst"
    FEASIBILITY = "feasibility"
    FEASIBILITY_DECISION = "feasibility_decision"
    REQUIREMENTS_FINALIZER = "requirements_finalizer"
    CLARIFICATION_FORM = "clarification_form"
    PROJECT_APPROVAL = "project_approval"
    FREEZE_SPEC = "freeze_spec"
    # build
    BUILD_ENTRY_GUARD = "build_entry_guard"
    HEAD = "head"
    SCHEDULE = "schedule"
    ARCHITECT = "architect"
    DATABASE_DEVELOPER = "database_developer"
    BACKEND_DEVELOPER = "backend_developer"
    FRONTEND_DEVELOPER = "frontend_developer"
    TESTER = "tester"
    DEBUGGER_REVIEWER = "debugger_reviewer"
    INTEGRATION_BUILD = "integration_build"
    TEST_RUNNER = "test_runner"
    VERIFY_TASK = "verify_task"
    FAILURE_ROUTER = "failure_router"
    # delivery
    COMPLETION_GATE_1 = "completion_gate_1"
    DOCUMENTER = "documenter"
    PACKAGER = "packager"
    COMPLETION_GATE_2 = "completion_gate_2"
    PUBLISH_APPROVAL = "publish_approval"
    GITHUB_DEPLOYER = "github_deployer"
    FINAL_REPORT = "final_report"
    # change request (skeleton until Part Q)
    CHANGE_INTAKE = "change_intake"
    CHANGE_IMPACT = "change_impact"
    CHANGE_APPROVAL = "change_approval"
    FREEZE_CHANGE_SPEC = "freeze_change_spec"


N = NodeName
K = NodeKind
S = Subgraph

NODE_KIND: Mapping[NodeName, NodeKind] = MappingProxyType(
    {
        N.ROUTE_ENTRY: K.ROUTER,
        N.ROUTE_AFTER_DISCOVERY: K.ROUTER,
        N.ROUTE_AFTER_CHANGE: K.ROUTER,
        N.ROUTE_AFTER_BUILD: K.ROUTER,
        N.FINALIZE_RUN: K.SERVICE,
        N.INTAKE: K.SERVICE,
        N.BRD_ANALYST: K.AGENT,
        N.FEASIBILITY: K.SERVICE,
        N.FEASIBILITY_DECISION: K.GATE,
        N.REQUIREMENTS_FINALIZER: K.AGENT,
        N.CLARIFICATION_FORM: K.GATE,
        N.PROJECT_APPROVAL: K.GATE,
        N.FREEZE_SPEC: K.SERVICE,
        N.BUILD_ENTRY_GUARD: K.GUARD,
        N.HEAD: K.AGENT,
        N.SCHEDULE: K.ROUTER,
        N.ARCHITECT: K.AGENT,
        N.DATABASE_DEVELOPER: K.AGENT,
        N.BACKEND_DEVELOPER: K.AGENT,
        N.FRONTEND_DEVELOPER: K.AGENT,
        N.TESTER: K.AGENT,
        N.DEBUGGER_REVIEWER: K.AGENT,
        N.INTEGRATION_BUILD: K.SERVICE,
        N.TEST_RUNNER: K.SERVICE,
        N.VERIFY_TASK: K.GUARD,
        N.FAILURE_ROUTER: K.ROUTER,
        N.COMPLETION_GATE_1: K.SERVICE,
        N.DOCUMENTER: K.AGENT,
        N.PACKAGER: K.SERVICE,
        N.COMPLETION_GATE_2: K.SERVICE,
        N.PUBLISH_APPROVAL: K.GATE,
        N.GITHUB_DEPLOYER: K.SERVICE,
        N.FINAL_REPORT: K.SERVICE,
        N.CHANGE_INTAKE: K.SERVICE,
        N.CHANGE_IMPACT: K.SERVICE,
        N.CHANGE_APPROVAL: K.GATE,
        N.FREEZE_CHANGE_SPEC: K.SERVICE,
    }
)

_PARENT = (N.ROUTE_ENTRY, N.ROUTE_AFTER_DISCOVERY, N.ROUTE_AFTER_CHANGE, N.ROUTE_AFTER_BUILD, N.FINALIZE_RUN)
_DISCOVERY = (
    N.INTAKE,
    N.BRD_ANALYST,
    N.FEASIBILITY,
    N.FEASIBILITY_DECISION,
    N.REQUIREMENTS_FINALIZER,
    N.CLARIFICATION_FORM,
    N.PROJECT_APPROVAL,
    N.FREEZE_SPEC,
)
_BUILD = (
    N.BUILD_ENTRY_GUARD,
    N.HEAD,
    N.SCHEDULE,
    N.ARCHITECT,
    N.DATABASE_DEVELOPER,
    N.BACKEND_DEVELOPER,
    N.FRONTEND_DEVELOPER,
    N.TESTER,
    N.DEBUGGER_REVIEWER,
    N.INTEGRATION_BUILD,
    N.TEST_RUNNER,
    N.VERIFY_TASK,
    N.FAILURE_ROUTER,
)
_DELIVERY = (
    N.COMPLETION_GATE_1,
    N.DOCUMENTER,
    N.PACKAGER,
    N.COMPLETION_GATE_2,
    N.PUBLISH_APPROVAL,
    N.GITHUB_DEPLOYER,
    N.FINAL_REPORT,
)
_CHANGE = (N.CHANGE_INTAKE, N.CHANGE_IMPACT, N.CHANGE_APPROVAL, N.FREEZE_CHANGE_SPEC)

NODE_SUBGRAPH: Mapping[NodeName, Subgraph] = MappingProxyType(
    {
        **dict.fromkeys(_PARENT, S.PROJECT_RUN),
        **dict.fromkeys(_DISCOVERY, S.DISCOVERY),
        **dict.fromkeys(_BUILD, S.BUILD),
        **dict.fromkeys(_DELIVERY, S.DELIVERY),
        **dict.fromkeys(_CHANGE, S.CHANGE_REQUEST),
    }
)

# Ten-agent layer L4: one agent node per role, named exactly like the role.
AGENT_NODE_FOR_ROLE: Mapping[AgentRole, NodeName] = MappingProxyType(
    {role: N(role.value) for role in AgentRole}
)

GATE_FOR_NODE: Mapping[NodeName, GateName] = MappingProxyType(
    {
        N.FEASIBILITY_DECISION: GateName.FEASIBILITY_DECISION,
        N.CLARIFICATION_FORM: GateName.CLARIFICATION_FORM,
        N.PROJECT_APPROVAL: GateName.PROJECT_APPROVAL,
        N.PUBLISH_APPROVAL: GateName.PUBLISH_APPROVAL,
        N.CHANGE_APPROVAL: GateName.CHANGE_APPROVAL,
    }
)

# Nodes that may execute only against an approved, frozen spec (roadmap §6.5).
SPEC_BOUND_NODES = frozenset(_BUILD[1:] + _DELIVERY)  # every build node after the guard, all delivery
# The only nodes that freeze a spec version (the only writers of RunState.spec_version_id).
SPEC_FREEZE_NODES = frozenset({N.FREEZE_SPEC, N.FREEZE_CHANGE_SPEC})

_BUILD_WORKERS = frozenset(
    {
        N.ARCHITECT,
        N.DATABASE_DEVELOPER,
        N.BACKEND_DEVELOPER,
        N.FRONTEND_DEVELOPER,
        N.TESTER,
        N.DEBUGGER_REVIEWER,
        N.INTEGRATION_BUILD,
        N.TEST_RUNNER,
    }
)


def _to(*targets: NodeName) -> frozenset[NodeName]:
    return frozenset(targets)


ALLOWED_TRANSITIONS: Mapping[NodeName, frozenset[NodeName]] = MappingProxyType(
    {
        # parent
        N.ROUTE_ENTRY: _to(N.INTAKE, N.CHANGE_INTAKE),
        N.ROUTE_AFTER_DISCOVERY: _to(N.BUILD_ENTRY_GUARD, N.FINALIZE_RUN),
        N.ROUTE_AFTER_CHANGE: _to(N.BUILD_ENTRY_GUARD, N.FINALIZE_RUN),
        N.ROUTE_AFTER_BUILD: _to(N.COMPLETION_GATE_1, N.FINALIZE_RUN),
        N.FINALIZE_RUN: _to(),  # terminal: the graph ends here
        # discovery (fixed edges, no LLM routing)
        N.INTAKE: _to(N.BRD_ANALYST),
        N.BRD_ANALYST: _to(N.FEASIBILITY),
        N.FEASIBILITY: _to(N.FEASIBILITY_DECISION),
        # A cancel at a gate ends the run directly. It never passes through route_after_discovery /
        # route_after_change, so those routers are reachable only through a spec freeze (topology).
        N.FEASIBILITY_DECISION: _to(N.REQUIREMENTS_FINALIZER, N.FINALIZE_RUN),
        N.REQUIREMENTS_FINALIZER: _to(N.CLARIFICATION_FORM, N.PROJECT_APPROVAL),
        N.CLARIFICATION_FORM: _to(N.REQUIREMENTS_FINALIZER),
        N.PROJECT_APPROVAL: _to(N.FREEZE_SPEC, N.FINALIZE_RUN),
        N.FREEZE_SPEC: _to(N.ROUTE_AFTER_DISCOVERY),
        # build (Head Agent is the hub)
        N.BUILD_ENTRY_GUARD: _to(N.HEAD, N.FINALIZE_RUN),
        N.HEAD: _to(N.SCHEDULE, N.FINALIZE_RUN),
        N.SCHEDULE: _BUILD_WORKERS | {N.ROUTE_AFTER_BUILD},
        **{worker: _to(N.VERIFY_TASK) for worker in _BUILD_WORKERS},
        N.VERIFY_TASK: _to(N.SCHEDULE, N.FAILURE_ROUTER),
        N.FAILURE_ROUTER: _to(N.SCHEDULE, N.FINALIZE_RUN),
        # delivery
        N.COMPLETION_GATE_1: _to(N.DOCUMENTER),
        N.DOCUMENTER: _to(N.PACKAGER),
        N.PACKAGER: _to(N.COMPLETION_GATE_2),
        N.COMPLETION_GATE_2: _to(N.PUBLISH_APPROVAL),
        N.PUBLISH_APPROVAL: _to(N.GITHUB_DEPLOYER, N.FINAL_REPORT),
        N.GITHUB_DEPLOYER: _to(N.FINAL_REPORT),
        N.FINAL_REPORT: _to(N.FINALIZE_RUN),
        # change request (skeleton): hands over to the same build subgraph
        N.CHANGE_INTAKE: _to(N.CHANGE_IMPACT),
        N.CHANGE_IMPACT: _to(N.CHANGE_APPROVAL),
        N.CHANGE_APPROVAL: _to(N.FREEZE_CHANGE_SPEC, N.FINALIZE_RUN),
        N.FREEZE_CHANGE_SPEC: _to(N.ROUTE_AFTER_CHANGE),
    }
)

ENTRY_NODE = N.ROUTE_ENTRY
TERMINAL_NODE = N.FINALIZE_RUN


def is_allowed_transition(source: NodeName, target: NodeName) -> bool:
    """True if ``source → target`` is in the static table (the graph wrappers audit violations)."""
    return target in ALLOWED_TRANSITIONS.get(source, frozenset())


def reachable(
    transitions: Mapping[NodeName, frozenset[NodeName]],
    start: NodeName,
    *,
    avoid: frozenset[NodeName] = frozenset(),
) -> frozenset[NodeName]:
    """Nodes reachable from ``start`` without passing through any node in ``avoid`` (start included).

    Used to prove approval topology: with the spec-freeze nodes (or the build-entry guard) removed, no
    build node may be reachable from the entry (AC-D-03, Spec §23 #7). D.3 applies the same check to
    the compiled graph's edges.
    """
    seen: set[NodeName] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node in seen or node in avoid:
            continue
        seen.add(node)
        stack.extend(transitions.get(node, frozenset()))
    return frozenset(seen)


# ----------------------------------------------------------------------------------------- run mode


class RunMode(StrEnum):
    """How a run is labelled (D-38, as revised by the owner on 2026-11-23).

    A stub run is labelled everywhere and can never produce a test result or a project verification
    status. Its agents never receive fake, mocked or prepared AI output: AI work goes only through the
    real LLM Gateway and a genuinely configured provider, and where none is available the agent step
    is reported as blocked rather than simulated.
    """

    STUB = "stub"


# ----------------------------------------------------------------------------------- gates / resume


class GateDecision(Contract):
    """A human decision submitted for the pending gate (validated by HC-13 before anything resumes)."""

    run_id: RunId
    gate: GateName
    decision: ApprovalDecision
    comment: ShortText | None = None


class GateResume(Contract):
    """The only value passed to ``Command(resume=...)``: references, never the decision itself.

    The gate node re-reads the approval (or the clarification submission) by ID and re-verifies it, so
    a forged or replayed payload is rejected (Part D plan §3.4).
    """

    run_id: RunId
    gate: GateName
    approval_id: ApprovalId | None = None
    submission_id: RecordRef | None = None

    @model_validator(mode="after")
    def _one_reference(self) -> Self:
        if self.gate is GateName.CLARIFICATION_FORM:
            if self.submission_id is None or self.approval_id is not None:
                raise ValueError("the clarification form resumes with a submission ID only")
        elif self.approval_id is None or self.submission_id is not None:
            raise ValueError(f"the {self.gate.value} gate resumes with an approval ID only")
        return self


# --------------------------------------------------------------------------------------- dispatch


def attempt_key(run_id: str, node: str, task_id: str, attempt: int) -> str:
    """Attempt-ledger idempotency key (roadmap §6.7): ``run_id:node:task_id:attempt``."""
    return f"{run_id}:{node}:{task_id}:{attempt}"


class DispatchRecord(Contract):
    """The Head dispatcher's hand-off of one build task to its agent node (roadmap §4.3 rule 3)."""

    project_id: ProjectId
    run_id: RunId
    task_id: TaskId
    agent_type: AgentRole
    node: NodeName
    attempt: int = Field(ge=1, le=4)
    idempotency_key: str = Field(max_length=200)
    input_artifact_refs: tuple[RecordRef, ...] = Field(default=(), max_length=_LIST)
    permitted_paths: tuple[PathScope, ...] = Field(default=(), max_length=50)
    done_criteria: tuple[ShortText, ...] = Field(min_length=1, max_length=50)
    dispatched_at: AwareDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.agent_type not in BUILD_PLAN_ROLES:
            raise ValueError(f"agent '{self.agent_type.value}' cannot be dispatched in a build plan")
        if self.node is not AGENT_NODE_FOR_ROLE[self.agent_type]:
            raise ValueError("the node does not belong to the task's agent")
        if self.idempotency_key != attempt_key(self.run_id, self.node.value, self.task_id, self.attempt):
            raise ValueError("idempotency_key must be run_id:node:task_id:attempt")
        return self


# -------------------------------------------------------------------------------- plan validation


class PlanIssueCode(StrEnum):
    """Every reason the Head plan validator can reject a plan (AC-D-04, Spec §23 #10)."""

    SCHEMA_INVALID = "schema_invalid"
    EMPTY_PLAN = "empty_plan"
    TOO_MANY_TASKS = "too_many_tasks"
    DUPLICATE_TASK_ID = "duplicate_task_id"
    UNKNOWN_AGENT = "unknown_agent"
    ROLE_NOT_PLANNABLE = "role_not_plannable"
    MISSING_DEPENDENCY = "missing_dependency"
    CYCLE = "cycle"
    PATH_OUTSIDE_SCOPE = "path_outside_scope"
    UNCOVERED_REQUIREMENT = "uncovered_requirement"
    MIXED_SPEC_VERSION = "mixed_spec_version"
    # added in D.2
    FOREIGN_TASK = "foreign_task"  # the task names another project or run
    TASK_NOT_NEW = "task_not_new"  # a new plan may hold only pending, unattempted tasks
    UNKNOWN_REQUIREMENT = "unknown_requirement"  # a key that is not in the approved spec's scope


class PlanIssue(Contract):
    code: PlanIssueCode
    task_id: TaskId | None = None
    detail: ShortText


class PlanValidationReport(Contract):
    plan_id: PlanId
    run_id: RunId
    ok: bool
    issues: tuple[PlanIssue, ...] = Field(default=(), max_length=1_000)
    source: Literal["template", "model"] = "template"

    @model_validator(mode="after")
    def _ok_means_no_issues(self) -> Self:
        if self.ok == bool(self.issues):
            raise ValueError("ok must be true exactly when there are no issues")
        return self


# -------------------------------------------------------------------------------- service outcomes


class ServiceOutcomeStatus(StrEnum):
    """What a deterministic service step did (D.2).

    There is deliberately no value for "passed", "supported", "built" or "verified": until the real
    service exists (the part named in ``ServiceOutcome.available_in_part``), a step can only say that
    it did **not** do its job. Real results come from the real services (E, G, M, N, O, P).
    """

    PERFORMED = "performed"  # a real effect, e.g. a spec version frozen (result_ref names it)
    NOT_RUN = "not_run"
    NOT_EVALUATED = "not_evaluated"
    NOT_BUILT = "not_built"
    NOT_PUBLISHED = "not_published"


ServicePart = Literal["E", "G", "M", "N", "O", "P", "Q"]


class ServiceOutcome(Contract):
    """The recorded result of one service node. A stand-in can never claim success (D-38)."""

    project_id: ProjectId
    run_id: RunId
    node: NodeName
    status: ServiceOutcomeStatus
    stand_in: bool
    reason: ShortText
    available_in_part: ServicePart | None = None
    result_ref: RecordRef | None = None
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def _honest(self) -> Self:
        if NODE_KIND[self.node] is not NodeKind.SERVICE:
            raise ValueError(f"'{self.node.value}' is not a service node")
        performed = self.status is ServiceOutcomeStatus.PERFORMED
        if self.stand_in == performed:
            raise ValueError("a stand-in service never performs its effect, and a real one always does")
        if self.stand_in and self.available_in_part is None:
            raise ValueError("a stand-in outcome must name the part that delivers the real service")
        if performed and self.result_ref is None:
            raise ValueError("a performed effect must reference what it produced")
        if self.stand_in and self.result_ref is not None:
            raise ValueError("a stand-in produces nothing to reference")
        return self
