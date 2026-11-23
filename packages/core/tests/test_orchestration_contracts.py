"""D.1 orchestration contracts: node list, kinds, transition table, gate payloads, dispatch, reports.

The structural guarantees here are checked on the **static table**. D.3 repeats the topology checks
on the compiled LangGraph graph (LG-10).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from devteam_core.contracts import orchestration as o
from devteam_core.contracts.events import GateOpened, MetricSample, NodeStarted, RunProgress
from devteam_core.contracts.orchestration import (
    AGENT_NODE_FOR_ROLE,
    ALLOWED_TRANSITIONS,
    ENTRY_NODE,
    GATE_FOR_NODE,
    NODE_KIND,
    NODE_SUBGRAPH,
    SPEC_BOUND_NODES,
    SPEC_FREEZE_NODES,
    TERMINAL_NODE,
    NodeKind,
    NodeName,
    Subgraph,
    is_allowed_transition,
    reachable,
)
from devteam_core.contracts.records import ApprovalDecision
from devteam_core.contracts.state import RunState
from devteam_core.contracts.statuses import AgentRole, GateName, RunStatus, Stage
from devteam_core.ids import new_id
from tests.fixtures.contract_samples import SAMPLES

pytestmark = pytest.mark.contract

N = NodeName
RUN = new_id("run")
PROJECT = new_id("prj")
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
BUILD_NODES = frozenset(n for n, s in NODE_SUBGRAPH.items() if s is Subgraph.BUILD)
DELIVERY_NODES = frozenset(n for n, s in NODE_SUBGRAPH.items() if s is Subgraph.DELIVERY)


def _agent_nodes(kinds: Mapping[NodeName, NodeKind]) -> set[str]:
    return {n.value for n, k in kinds.items() if k is NodeKind.AGENT}


# ------------------------------------------------------------------------------------ node registry


def test_every_node_has_a_kind_a_subgraph_and_a_table_row() -> None:
    assert set(NODE_KIND) == set(NodeName)
    assert set(NODE_SUBGRAPH) == set(NodeName)
    assert set(ALLOWED_TRANSITIONS) == set(NodeName)
    for targets in ALLOWED_TRANSITIONS.values():
        assert targets <= set(NodeName)


def test_exactly_ten_agent_nodes_one_per_role() -> None:
    """Ten-agent layer L4 at contract level (AC-D-05; the compiled graph is checked in D.3)."""
    assert _agent_nodes(NODE_KIND) == {role.value for role in AgentRole}
    assert len(_agent_nodes(NODE_KIND)) == 10
    assert set(AGENT_NODE_FOR_ROLE) == set(AgentRole)
    for role, node in AGENT_NODE_FOR_ROLE.items():
        assert node.value == role.value and NODE_KIND[node] is NodeKind.AGENT


def test_negative_control_an_eleventh_agent_node_is_detected() -> None:
    planted = {**NODE_KIND, N.TEST_RUNNER: NodeKind.AGENT}  # a service mislabelled as an agent
    assert _agent_nodes(planted) != {role.value for role in AgentRole}


def test_deterministic_services_and_head_internals_are_never_agents() -> None:
    for node in (
        N.INTAKE,
        N.FEASIBILITY,
        N.FREEZE_SPEC,
        N.INTEGRATION_BUILD,
        N.TEST_RUNNER,
        N.COMPLETION_GATE_1,
        N.COMPLETION_GATE_2,
        N.PACKAGER,
        N.GITHUB_DEPLOYER,
        N.SCHEDULE,
        N.FAILURE_ROUTER,
        N.BUILD_ENTRY_GUARD,
        N.VERIFY_TASK,
    ):
        assert NODE_KIND[node] is not NodeKind.AGENT, node


def test_gate_nodes_map_to_gate_names() -> None:
    gates = {n for n, k in NODE_KIND.items() if k is NodeKind.GATE}
    assert gates == set(GATE_FOR_NODE)
    for node, gate in GATE_FOR_NODE.items():
        assert node.value == gate.value
    # added in Parts N and Q, not in D
    assert GateName.TEST_MAINTENANCE_APPROVAL not in GATE_FOR_NODE.values()
    assert GateName.DB_SWITCH_APPROVAL not in GATE_FOR_NODE.values()


def test_change_subgraph_declares_no_agent_nodes() -> None:
    change = [n for n, s in NODE_SUBGRAPH.items() if s is Subgraph.CHANGE_REQUEST]
    assert change and all(NODE_KIND[n] is not NodeKind.AGENT for n in change)


# ------------------------------------------------------------------------------------ table shape


def test_every_node_is_reachable_from_the_entry() -> None:
    assert reachable(ALLOWED_TRANSITIONS, ENTRY_NODE) == set(NodeName)


def test_only_the_terminal_node_has_no_successor_and_it_is_always_reachable() -> None:
    assert [n for n, t in ALLOWED_TRANSITIONS.items() if not t] == [TERMINAL_NODE]
    for node in NodeName:
        assert TERMINAL_NODE in reachable(ALLOWED_TRANSITIONS, node), node


def test_discovery_uses_fixed_edges_only() -> None:
    """Spec §7.1: no LLM routing in discovery; only gates and the finalizer have two exits."""
    for node, subgraph in NODE_SUBGRAPH.items():
        if subgraph is Subgraph.DISCOVERY and node not in {
            N.FEASIBILITY_DECISION,
            N.REQUIREMENTS_FINALIZER,
            N.PROJECT_APPROVAL,
        }:
            assert len(ALLOWED_TRANSITIONS[node]) == 1, node


def test_clarification_form_returns_only_to_the_finalizer() -> None:
    assert ALLOWED_TRANSITIONS[N.CLARIFICATION_FORM] == {N.REQUIREMENTS_FINALIZER}
    entries = {n for n, t in ALLOWED_TRANSITIONS.items() if N.CLARIFICATION_FORM in t}
    assert entries == {N.REQUIREMENTS_FINALIZER}


def test_build_workers_always_pass_post_task_verification() -> None:
    for node in ALLOWED_TRANSITIONS[N.SCHEDULE] - {N.ROUTE_AFTER_BUILD}:
        assert ALLOWED_TRANSITIONS[node] == {N.VERIFY_TASK}, node


def test_is_allowed_transition() -> None:
    assert is_allowed_transition(N.FREEZE_SPEC, N.ROUTE_AFTER_DISCOVERY)
    assert not is_allowed_transition(N.PROJECT_APPROVAL, N.ARCHITECT)
    assert not is_allowed_transition(N.FINALIZE_RUN, N.ROUTE_ENTRY)


# ------------------------------------------------------------------- approval topology (AC-D-03)


def test_build_and_delivery_are_unreachable_without_spec_freeze() -> None:
    """Spec §23 #7 (topology): no path from the entry to any build node avoids the freeze nodes."""
    without_freeze = reachable(ALLOWED_TRANSITIONS, ENTRY_NODE, avoid=SPEC_FREEZE_NODES)
    assert not without_freeze & (BUILD_NODES | DELIVERY_NODES)


def test_post_discovery_routers_are_entered_only_from_a_spec_freeze() -> None:
    """A cancel must not share a router with the approved path (it would leave a topological bypass)."""
    for router in (N.ROUTE_AFTER_DISCOVERY, N.ROUTE_AFTER_CHANGE):
        sources = {n for n, t in ALLOWED_TRANSITIONS.items() if router in t}
        assert sources <= SPEC_FREEZE_NODES, (router, sources)


def test_build_and_delivery_are_unreachable_without_the_build_entry_guard() -> None:
    without_guard = reachable(ALLOWED_TRANSITIONS, ENTRY_NODE, avoid=frozenset({N.BUILD_ENTRY_GUARD}))
    assert not without_guard & ((BUILD_NODES - {N.BUILD_ENTRY_GUARD}) | DELIVERY_NODES)


def test_spec_bound_nodes_cover_everything_after_the_guard() -> None:
    assert (BUILD_NODES - {N.BUILD_ENTRY_GUARD}) | DELIVERY_NODES == SPEC_BOUND_NODES


@pytest.mark.security
@pytest.mark.parametrize(
    ("source", "shortcut"),
    [
        (N.FEASIBILITY_DECISION, N.ARCHITECT),
        (N.PROJECT_APPROVAL, N.HEAD),
        (N.REQUIREMENTS_FINALIZER, N.BUILD_ENTRY_GUARD),
        (N.CHANGE_IMPACT, N.ROUTE_AFTER_CHANGE),
        (N.FEASIBILITY_DECISION, N.ROUTE_AFTER_DISCOVERY),  # the first-draft cancel edge (corrected)
        (N.CHANGE_APPROVAL, N.ROUTE_AFTER_CHANGE),
    ],
)
def test_negative_control_a_planted_shortcut_is_detected(source: NodeName, shortcut: NodeName) -> None:
    planted = {**ALLOWED_TRANSITIONS, source: ALLOWED_TRANSITIONS[source] | {shortcut}}
    leaked = reachable(planted, ENTRY_NODE, avoid=SPEC_FREEZE_NODES) & (BUILD_NODES | DELIVERY_NODES)
    assert leaked, f"planted {source} -> {shortcut} was not detected"


# -------------------------------------------------------------------------------- gate payloads


@pytest.mark.security
def test_gate_resume_carries_exactly_one_reference_of_the_right_kind() -> None:
    approval = new_id("apr")
    o.GateResume(run_id=RUN, gate=GateName.PUBLISH_APPROVAL, approval_id=approval)
    o.GateResume(run_id=RUN, gate=GateName.CLARIFICATION_FORM, submission_id=new_id("ca"))
    bad: list[dict[str, object]] = [
        {"gate": GateName.PROJECT_APPROVAL},
        {"gate": GateName.PROJECT_APPROVAL, "submission_id": new_id("ca")},
        {"gate": GateName.PROJECT_APPROVAL, "approval_id": approval, "submission_id": new_id("ca")},
        {"gate": GateName.CLARIFICATION_FORM, "approval_id": approval},
        {"gate": GateName.PROJECT_APPROVAL, "approval_id": "apr_forged"},
        {"gate": GateName.PROJECT_APPROVAL, "approval_id": approval, "decision": "approved"},
    ]
    for fields in bad:
        with pytest.raises(ValidationError):
            o.GateResume.model_validate({"run_id": RUN, **fields})


def test_gate_decision_uses_the_f4_values_only() -> None:
    assert {d.value for d in ApprovalDecision} == {"approved", "rejected", "cancelled"}
    with pytest.raises(ValidationError):
        o.GateDecision.model_validate({"run_id": RUN, "gate": "project_approval", "decision": "edit"})


# ------------------------------------------------------------------------------------ dispatch


def _dispatch(**overrides: object) -> o.DispatchRecord:
    task = new_id("tsk")
    fields: dict[str, object] = {
        "project_id": PROJECT,
        "run_id": RUN,
        "task_id": task,
        "agent_type": AgentRole.BACKEND_DEVELOPER,
        "node": N.BACKEND_DEVELOPER,
        "attempt": 1,
        "idempotency_key": o.attempt_key(RUN, "backend_developer", task, 1),
        "permitted_paths": ("generated/backend/",),
        "done_criteria": ("endpoint implemented",),
        "dispatched_at": NOW,
    }
    fields.update(overrides)
    return o.DispatchRecord.model_validate(fields)


def test_dispatch_record_is_consistent() -> None:
    record = _dispatch()
    assert record.idempotency_key == f"{RUN}:backend_developer:{record.task_id}:1"


@pytest.mark.security
@pytest.mark.parametrize(
    "overrides",
    [
        {"node": N.FRONTEND_DEVELOPER},  # another agent's node
        {"agent_type": AgentRole.HEAD, "node": N.HEAD},  # Head is not plannable
        {"agent_type": AgentRole.BRD_ANALYST, "node": N.BRD_ANALYST},  # discovery role
        {"idempotency_key": "anything"},
        {"attempt": 5},
        {"permitted_paths": ("../outside/",)},
        {"done_criteria": ()},
    ],
)
def test_dispatch_record_rejects_inconsistent_handoffs(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _dispatch(**overrides)


# ------------------------------------------------------------------------- plan validation report


def test_plan_report_ok_means_no_issues() -> None:
    issue = o.PlanIssue(code=o.PlanIssueCode.CYCLE, detail="a -> b -> a")
    plan = new_id("pln")
    o.PlanValidationReport(plan_id=plan, run_id=RUN, ok=True)
    o.PlanValidationReport(plan_id=plan, run_id=RUN, ok=False, issues=(issue,))
    with pytest.raises(ValidationError):
        o.PlanValidationReport(plan_id=plan, run_id=RUN, ok=True, issues=(issue,))
    with pytest.raises(ValidationError):
        o.PlanValidationReport(plan_id=plan, run_id=RUN, ok=False)


def test_every_validator_rejection_reason_has_a_code() -> None:
    assert {c.value for c in o.PlanIssueCode} >= {
        "cycle",
        "unknown_agent",
        "missing_dependency",
        "uncovered_requirement",
        "path_outside_scope",
    }


# ------------------------------------------------------------------------------- RunState v2


def test_run_state_v2_requires_a_run_mode() -> None:
    data = SAMPLES["RunState"]().model_dump()
    data.pop("run_mode")
    with pytest.raises(ValidationError):
        RunState.model_validate(data)


def test_clarification_form_cannot_reopen_after_its_round_was_used() -> None:
    base = SAMPLES["RunState"]().model_dump()
    RunState.model_validate({**base, "pending_gate": GateName.CLARIFICATION_FORM})
    RunState.model_validate({**base, "clarification_round_used": True})
    with pytest.raises(ValidationError, match="only once"):
        RunState.model_validate(
            {**base, "pending_gate": GateName.CLARIFICATION_FORM, "clarification_round_used": True}
        )


# ---------------------------------------------------------------------------------- events


def _event(**fields: object) -> dict[str, object]:
    return {"event_id": new_id("evt"), "ts": NOW, "actor": "service:orchestrator", **fields}


def test_progress_events_belong_to_a_project_and_run() -> None:
    with pytest.raises(ValidationError, match="project and a run"):
        GateOpened.model_validate(_event(gate="project_approval"))
    with pytest.raises(ValidationError, match="project and a run"):
        NodeStarted.model_validate(
            _event(project_id=PROJECT, node="head", node_kind="agent", run_mode="stub")
        )


def test_progress_counts_are_consistent_and_metrics_bounded() -> None:
    ids = {"project_id": PROJECT, "run_id": RUN}
    with pytest.raises(ValidationError):
        RunProgress.model_validate(
            _event(
                **ids,
                stage=Stage.BUILD,
                status=RunStatus.RUNNING,
                run_mode="stub",
                completed_tasks=3,
                total_tasks=2,
            )
        )
    with pytest.raises(ValidationError):
        MetricSample.model_validate(_event(**ids, metric="free_ram_mb", value=-1))
    with pytest.raises(ValidationError):
        MetricSample.model_validate(_event(**ids, metric="prompt_text", value=1))


@pytest.mark.security
def test_progress_events_cannot_carry_content() -> None:
    """Unknown fields are rejected, so document or prompt text cannot ride along (D-30)."""
    with pytest.raises(ValidationError):
        GateOpened.model_validate(
            _event(project_id=PROJECT, run_id=RUN, gate="project_approval", summary="BRD says ...")
        )
