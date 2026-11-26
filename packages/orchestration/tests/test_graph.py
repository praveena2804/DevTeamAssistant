"""D.3 graph: topology conformance, exactly ten agent nodes, approval layers, gates, plan rejection.

Fast tests need no model: they stop where a real model would be needed, or reach gates by recording a
node's completion with ``update_state`` (only for steps whose output is not used). The model is pointed
at a closed loopback port, so any accidental model call fails honestly instead of being simulated.
Real end-to-end runs are the ``llm`` tests at the end.
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from devteam_agents.head.template_planner import template_plan
from devteam_core.contracts.orchestration import (
    AGENT_NODE_FOR_ROLE,
    ALLOWED_TRANSITIONS,
    ENTRY_NODE,
    NODE_KIND,
    NODE_SUBGRAPH,
    SPEC_FREEZE_NODES,
    TERMINAL_NODE,
    NodeKind,
    NodeName,
    reachable,
)
from devteam_core.contracts.records import ApprovalDecision
from devteam_core.contracts.statuses import AgentRole, ApprovalType, RunStatus, Stage, TaskStatus
from devteam_orchestration import graph as graph_module
from devteam_orchestration.execution import advance
from devteam_orchestration.graph import StateUpdate, spec_problem
from devteam_orchestration.steps import StepError
from devteam_state.models import AgentTaskRow, AuditEvent, RunEventRow
from devteam_state.repositories import audit, runs
from tests.fixtures.orchestration import Env, closed_port, frozen_spec, make_env

N = NodeName
BUILD_NODES = frozenset(n for n, s in NODE_SUBGRAPH.items() if s.value == "build") - {N.BUILD_ENTRY_GUARD}


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    built = make_env(tmp_path, ollama_url=f"http://127.0.0.1:{closed_port()}", with_graph=True)
    yield built
    built.close()


def _config(env: Env) -> dict[str, Any]:
    return {"configurable": {"thread_id": env.run_id}, "recursion_limit": 200}


def _edges(compiled: Any) -> set[tuple[str, str]]:
    drawn = compiled.get_graph()
    return {(e.source, e.target) for e in drawn.edges}


def _run(env: Env) -> Any:
    with env.sessions() as session:
        return runs.get_run(session, env.project_id, env.run_id)


def _audits(env: Env, action: str) -> list[AuditEvent]:
    with env.sessions() as session:
        return list(session.scalars(select(AuditEvent).where(AuditEvent.action == action)))


def _at(env: Env, node: NodeName, values: dict[str, Any]) -> None:
    """Record ``node`` as the last completed step (only for steps whose output the next node ignores)."""
    env.graph.update_state(
        _config(env),
        {"project_id": env.project_id, "run_id": env.run_id, "last_node": node.value, **values},
        as_node=node.value,
    )


# ------------------------------------------------------------------------------ topology (AC-D-05)


def test_compiled_edges_equal_the_transition_table(env: Env) -> None:
    expected = {(s.value, t.value) for s, targets in ALLOWED_TRANSITIONS.items() for t in targets}
    expected |= {("__start__", ENTRY_NODE.value), (TERMINAL_NODE.value, "__end__")}
    assert _edges(env.graph) == expected


def test_every_node_is_compiled_with_its_kind_and_subgraph(env: Env) -> None:
    nodes = env.graph.get_graph().nodes
    names = {n for n in nodes if not n.startswith("__")}
    assert names == {n.value for n in NodeName}
    for name in NodeName:
        metadata = nodes[name.value].metadata
        assert metadata == {"node_kind": NODE_KIND[name].value, "subgraph": NODE_SUBGRAPH[name].value}


def agent_nodes(compiled: Any) -> set[str]:
    return {
        name
        for name, node in compiled.get_graph().nodes.items()
        if (node.metadata or {}).get("node_kind") == NodeKind.AGENT.value
    }


def test_exactly_ten_agent_nodes_one_per_role(env: Env) -> None:
    found = agent_nodes(env.graph)
    assert len(found) == 10
    assert found == {AGENT_NODE_FOR_ROLE[role].value for role in AgentRole}


def test_an_eleventh_agent_node_would_be_detected() -> None:
    """Negative control for the L4 check: the counting function sees a planted extra agent node."""
    from langgraph.graph import END, START, StateGraph  # noqa: PLC0415

    builder: Any = StateGraph(dict)
    for role in AgentRole:
        builder.add_node(role.value, lambda s: {}, metadata={"node_kind": "agent"})
    builder.add_node("deployment_wizard", lambda s: {}, metadata={"node_kind": "agent"})
    names = [*(r.value for r in AgentRole), "deployment_wizard"]
    builder.add_edge(START, names[0])
    for a, b in itertools.pairwise(names):
        builder.add_edge(a, b)
    builder.add_edge(names[-1], END)
    assert len(agent_nodes(builder.compile())) == 11


def test_no_path_into_build_avoids_spec_freeze_or_the_guard(env: Env) -> None:
    """AC-D-03 topology, on the compiled graph's own edges."""
    edges: dict[NodeName, frozenset[NodeName]] = {}
    for source, target in _edges(env.graph):
        if source.startswith("__") or target.startswith("__"):
            continue
        edges[N(source)] = edges.get(N(source), frozenset()) | {N(target)}
    without_freeze = reachable(edges, ENTRY_NODE, avoid=SPEC_FREEZE_NODES)
    without_guard = reachable(edges, ENTRY_NODE, avoid=frozenset({N.BUILD_ENTRY_GUARD}))
    assert not (without_freeze & BUILD_NODES)
    assert not (without_guard & BUILD_NODES)
    assert reachable(edges, ENTRY_NODE) >= BUILD_NODES  # negative control: Build is reachable normally


def test_the_diagram_is_generated_offline(env: Env) -> None:
    mermaid = env.graph.get_graph().draw_mermaid()
    assert "build_entry_guard" in mermaid and "freeze_spec" in mermaid


def test_state_updates_carry_references_only() -> None:
    StateUpdate.model_validate({"spec_version_id": "spv_" + "0" * 26, "route": "head"})
    for bad in (
        {"brd_text": "The library lends books"},  # unknown field: no content in state
        {"reason": "x" * 301},
        {"spec_checksum": "not-a-checksum"},
        {"current_task_id": "../../etc"},
    ):
        with pytest.raises(ValueError):
            StateUpdate.model_validate(bad)


# ------------------------------------------------------------------------ runs without a model


def test_a_run_without_a_reachable_model_waits_and_invents_nothing(env: Env) -> None:
    result = advance(env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="start")
    assert result.result == "waiting_for_resources"
    assert _run(env).status == RunStatus.WAITING_FOR_RESOURCES.value
    with env.sessions() as session:
        tasks = list(session.scalars(select(AgentTaskRow).where(AgentTaskRow.run_id == env.run_id)))
        assert [(t.agent_type, t.status) for t in tasks] == [("brd_analyst", "ready")]
        jobs = session.execute(text("SELECT kind, status FROM run_jobs")).all()
        assert [tuple(j) for j in jobs] == [("resume", "queued")]


def test_a_project_without_documents_is_blocked(tmp_path: Path) -> None:
    env = make_env(tmp_path, ollama_url=f"http://127.0.0.1:{closed_port()}", with_brd=False, with_graph=True)
    try:
        result = advance(env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="start")
        assert result.result == "blocked"
        assert "no extracted document" in (result.reason or "")
        assert _run(env).status == RunStatus.BLOCKED.value
    finally:
        env.close()


# -------------------------------------------------------------------- AC-D-03 runtime (Spec #7)


def test_a_forced_jump_into_build_is_refused_and_audited(env: Env) -> None:
    advance(
        env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="start"
    )  # stops at the analyst
    from langgraph.types import Command  # noqa: PLC0415

    with pytest.raises(StepError) as caught:
        env.graph.invoke(Command(goto=N.ARCHITECT.value), _config(env))
    assert caught.value.code == "orchestration.illegal_transition"
    [event] = _audits(env, "orchestration.illegal_transition")
    assert event.details["target"] == "architect"
    with env.sessions() as session:
        assert session.scalar(select(AgentTaskRow).where(AgentTaskRow.agent_type == "architect")) is None


def test_skipping_the_guard_with_update_state_is_refused_without_a_frozen_spec(env: Env) -> None:
    _at(env, N.BUILD_ENTRY_GUARD, {"route": N.HEAD.value})
    with pytest.raises(StepError) as caught:
        env.graph.invoke(None, _config(env))
    assert caught.value.code == "build.entry_refused"
    assert _audits(env, "build.entry_refused")
    with env.sessions() as session:
        assert session.scalar(select(AgentTaskRow).where(AgentTaskRow.agent_type == "head")) is None


def test_the_guard_passes_only_a_genuinely_approved_frozen_spec(env: Env) -> None:
    assert spec_problem(env.rt, env.project_id, env.run_id) is not None  # nothing frozen yet
    frozen_spec(env)
    assert spec_problem(env.rt, env.project_id, env.run_id) is None


@pytest.mark.parametrize(
    "tamper",
    ["drop_spec_on_run", "checksum_on_run", "content_changed", "spec_of_other_run", "approval_forged"],
)
def test_the_guard_refuses_every_tampering(env: Env, tamper: str) -> None:
    spec_id, checksum, _approval = frozen_spec(env)
    with env.sessions.begin() as session:
        run = runs.get_run(session, env.project_id, env.run_id)
        assert run is not None
        if tamper == "drop_spec_on_run":
            run.spec_version_id = None
        elif tamper == "checksum_on_run":
            run.spec_checksum = "f" * 64
        elif tamper == "content_changed":  # a file-level attacker who removed the triggers
            for trigger in ("spec_versions_guard_update",):
                session.execute(text(f"DROP TRIGGER IF EXISTS {trigger}"))
            session.execute(
                text('UPDATE spec_versions SET payload = \'{"in_scope": ["FR-999"]}\' WHERE id = :i'),
                {"i": spec_id},
            )
        elif tamper == "spec_of_other_run":
            other = runs.create_run(
                session, project_id=env.project_id, run_kind="initial", stage=Stage(run.stage)
            )
            session.flush()
            session.execute(text("DROP TRIGGER IF EXISTS spec_versions_guard_update"))
            session.execute(
                text("UPDATE spec_versions SET run_id = :r WHERE id = :i"), {"r": other.id, "i": spec_id}
            )
        elif tamper == "approval_forged":
            session.execute(text("DROP TRIGGER IF EXISTS spec_versions_guard_update"))
            session.execute(
                text("UPDATE spec_versions SET approval_id = :a WHERE id = :i"),
                {"a": "apr_" + "0" * 26, "i": spec_id},
            )
    problem = spec_problem(env.rt, env.project_id, env.run_id)
    assert problem is not None, tamper
    _at(
        env,
        N.BUILD_ENTRY_GUARD,
        {"route": N.HEAD.value, "spec_version_id": spec_id, "spec_checksum": checksum},
    )
    with pytest.raises(StepError):
        env.graph.invoke(None, _config(env))
    assert _audits(env, "build.entry_refused")


def test_the_guard_node_blocks_the_run_when_the_spec_is_not_approved(env: Env) -> None:
    _at(env, N.FREEZE_SPEC, {"spec_version_id": "spv_" + "1" * 26, "spec_checksum": "a" * 64})
    with env.sessions.begin() as session:
        run = runs.get_run(session, env.project_id, env.run_id)
        assert run is not None
        run.status = RunStatus.RUNNING.value
    env.graph.invoke(None, _config(env))
    run = _run(env)
    assert run.status == RunStatus.BLOCKED.value
    assert _audits(env, "build.entry_refused")


# ------------------------------------------------------------------------------- gates (HC-13)


def _to_feasibility_gate(env: Env) -> None:
    # Part G (DV-035): the analysis step is recorded as done with a stored analysis that lists nothing, so the
    # real feasibility node runs (honest "no technology needs identified" report); the finalizer has no keys
    env.store_analysis("tsk_" + "0" * 26)
    _at(env, N.BRD_ANALYST, {"services_done": [], "analysis_task_id": "tsk_" + "0" * 26})
    with env.sessions.begin() as session:
        run = runs.get_run(session, env.project_id, env.run_id)
        assert run is not None
        run.status = RunStatus.RUNNING.value
    result = advance(env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="resume")
    assert result.result == "waiting_for_human"
    assert result.pending_gate == "feasibility_decision"


def test_a_gate_interrupts_without_writing_first_and_records_the_wait(env: Env) -> None:
    _to_feasibility_gate(env)
    run = _run(env)
    assert run.status == RunStatus.WAITING_FOR_HUMAN.value
    assert run.pending_gate == "feasibility_decision"
    with env.sessions() as session:
        events = list(session.scalars(select(RunEventRow).where(RunEventRow.run_id == env.run_id)))
    kinds = [e.kind for e in events]
    assert kinds.count("gate.opened") == 1
    # the gate itself wrote nothing before interrupt() (Part G: the feasibility service node ran before it)
    started = [e.payload.get("node") for e in events if e.kind == "node.started"]
    assert "feasibility_decision" not in started and started == ["feasibility"]
    again = advance(env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="resume")
    assert again.result == "waiting_for_human"  # a plain resume cannot pass a gate


def test_an_approved_gate_continues_and_a_declined_gate_cancels(env: Env, tmp_path: Path) -> None:
    _to_feasibility_gate(env)
    approval = env.approve(ApprovalType.FEASIBILITY_DECISION)
    result = advance(
        env.rt,
        env.graph,
        project_id=env.project_id,
        run_id=env.run_id,
        kind="gate_decision",
        payload={"run_id": env.run_id, "gate": "feasibility_decision", "approval_id": approval},
    )
    assert result.result == "waiting_for_resources"  # the next step needs the model, which is offline
    state = env.graph.get_state(_config(env)).values
    assert state["feasibility_approval_id"] == approval

    other = make_env(tmp_path / "b", ollama_url=f"http://127.0.0.1:{closed_port()}", with_graph=True)
    try:
        _to_feasibility_gate(other)
        declined = other.approve(ApprovalType.FEASIBILITY_DECISION, decision=ApprovalDecision.REJECTED)
        result = advance(
            other.rt,
            other.graph,
            project_id=other.project_id,
            run_id=other.run_id,
            kind="gate_decision",
            payload={"run_id": other.run_id, "gate": "feasibility_decision", "approval_id": declined},
        )
        assert result.result == "cancelled"
        assert _run(other).status == RunStatus.CANCELLED.value
    finally:
        other.close()


@pytest.mark.parametrize("case", ["forged", "wrong_type", "other_run", "bad_payload", "wrong_gate"])
def test_an_unverifiable_gate_decision_blocks_the_run_and_is_audited(env: Env, case: str) -> None:
    _to_feasibility_gate(env)
    payload: dict[str, Any] = {"run_id": env.run_id, "gate": "feasibility_decision"}
    if case == "forged":
        payload["approval_id"] = "apr_" + "7" * 26
    elif case == "wrong_type":
        payload["approval_id"] = env.approve(  # Part G: a substitution approval is item-bound (DV-035)
            ApprovalType.SUBSTITUTION, subject_ref=f"substitution:fea_{'0' * 26}@{'a' * 64}"
        )
    elif case == "other_run":
        with env.sessions.begin() as session:
            other = runs.create_run(
                session, project_id=env.project_id, run_kind="initial", stage=Stage(_run(env).stage)
            ).id
        payload["approval_id"] = env.rt.approvals.record(
            env.owner,
            project_id=env.project_id,
            run_id=other,
            approval_type=ApprovalType.FEASIBILITY_DECISION,
            decision=ApprovalDecision.APPROVED,
        ).approval_id
    elif case == "bad_payload":
        payload = {"run_id": env.run_id, "gate": "feasibility_decision", "decision": "approved"}
    elif case == "wrong_gate":
        payload = {
            "run_id": env.run_id,
            "gate": "publish_approval",
            "approval_id": env.approve(ApprovalType.FEASIBILITY_DECISION),
        }
    if case == "bad_payload":
        with pytest.raises(ValueError):
            advance(
                env.rt,
                env.graph,
                project_id=env.project_id,
                run_id=env.run_id,
                kind="gate_decision",
                payload=payload,
            )
        return
    result = advance(
        env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="gate_decision", payload=payload
    )
    assert result.result == "blocked", result
    assert _run(env).status == RunStatus.BLOCKED.value
    assert _audits(env, "approval.verify_failed") or _audits(env, "gate.refused")


# ----------------------------------------------------------------- plan rejection (Spec #10)


def test_an_invalid_plan_is_rejected_audited_and_blocks_the_run(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec_id, checksum, _ = frozen_spec(env, ("FR-001", "FR-002"))
    real = template_plan

    def cyclic(**kwargs: Any) -> Any:
        tasks = list(real(**kwargs))
        tasks[0] = tasks[0].model_copy(update={"depends_on": (tasks[-1].task_id,)})
        return tuple(tasks)

    monkeypatch.setattr(graph_module, "template_plan", cyclic)
    _at(
        env,
        N.BUILD_ENTRY_GUARD,
        {"route": N.HEAD.value, "spec_version_id": spec_id, "spec_checksum": checksum},
    )
    with env.sessions.begin() as session:
        run = runs.get_run(session, env.project_id, env.run_id)
        assert run is not None
        run.status = RunStatus.RUNNING.value
    env.graph.invoke(None, _config(env))
    assert _run(env).status == RunStatus.BLOCKED.value
    [rejected] = _audits(env, "plan.rejected")
    assert "cycle" in rejected.details["issues"]
    with env.sessions() as session:
        assert session.scalar(select(AgentTaskRow).where(AgentTaskRow.agent_type == "architect")) is None
        assert audit.verify_chain(session).ok


# --------------------------------------------------------------------- real local model (AC-D-01)


def _approve_gate(env: Env, gate: str) -> str:
    state = env.graph.get_state(_config(env)).values
    if gate == "feasibility_decision":
        return env.approve(ApprovalType.FEASIBILITY_DECISION)
    if gate == "project_approval":
        return env.approve(
            ApprovalType.SPEC_APPROVAL,
            spec_version_id=state["draft_spec_version_id"],
            spec_checksum=state["draft_spec_checksum"],
        )
    return env.approve(
        ApprovalType.PUBLISH_GITHUB,
        spec_version_id=state["spec_version_id"],
        spec_checksum=state["spec_checksum"],
    )


def drive(env: Env, *, stop_at: str | None = None) -> Any:
    result = advance(env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="start")
    while result.result == "waiting_for_human" and result.pending_gate != stop_at:
        gate = result.pending_gate
        assert gate is not None
        approval = _approve_gate(env, gate)
        result = advance(
            env.rt,
            env.graph,
            project_id=env.project_id,
            run_id=env.run_id,
            kind="gate_decision",
            payload={"run_id": env.run_id, "gate": gate, "approval_id": approval},
        )
    return result


@pytest.mark.llm
@pytest.mark.slow
def test_a_full_run_with_the_real_local_model_completes_unverified(tmp_path: Path) -> None:
    env = make_env(tmp_path, with_graph=True)
    try:
        result = drive(env)
        assert result.result == "completed", result
        run = _run(env)
        assert run.status == RunStatus.COMPLETED.value and run.spec_version_id
        with env.sessions() as session:
            tasks = {
                t.agent_type: t.status
                for t in session.scalars(select(AgentTaskRow).where(AgentTaskRow.run_id == env.run_id))
            }
            assert set(tasks) == {r.value for r in AgentRole} - {"debugger_reviewer"}  # nothing needed repair
            assert set(tasks.values()) == {TaskStatus.COMPLETED.value}
            calls = list(session.scalars(select(AuditEvent).where(AuditEvent.action == "model.called")))
            assert len(calls) >= 9
            assert {c.details["provider"] for c in calls} == {"ollama"}
            events = [
                e.payload
                for e in session.scalars(select(RunEventRow).where(RunEventRow.run_id == env.run_id))
            ]
            [summary] = [e for e in events if e["kind"] == "run.summary"]
            assert summary["verified"] is False
            services = [e for e in events if e["kind"] == "service.recorded"]
            stand_ins = [e for e in services if e["node"] != "feasibility"]
            assert stand_ins and all(e["stand_in"] and e["status"] != "performed" for e in stand_ins)
            [feasibility] = [e for e in services if e["node"] == "feasibility"]  # real since Part G
            assert feasibility["stand_in"] is False and feasibility["status"] == "performed"
            assert audit.verify_chain(session).ok
            project_status = session.execute(
                text("SELECT status FROM projects WHERE id = :p"), {"p": env.project_id}
            ).scalar()
            assert project_status == "active"  # no verification status was written
        generated = env.rt.workspace(env.project_id) / "generated"
        for path in (
            "docs/ARCHITECTURE.md",
            "backend/DESIGN.md",
            "tests/TEST_PLAN.md",
            "docs/RUN_NOTES.md",
            "spec/REQUIREMENTS.md",
        ):
            assert (generated / path).is_file(), path
        assert "not verified" in (generated / "backend/DESIGN.md").read_text(encoding="utf-8")
    finally:
        env.close()


@pytest.mark.llm
@pytest.mark.slow
def test_a_restarted_process_resumes_from_the_checkpoint_without_repeating_work(tmp_path: Path) -> None:
    """Scenario #23 (in-process form): a new runtime and graph continue; finished steps are not redone."""
    env = make_env(tmp_path, with_graph=True)
    try:
        result = drive(env, stop_at="project_approval")
        assert result.pending_gate == "project_approval"
        with env.sessions() as session:
            calls_before = len(
                list(session.scalars(select(AuditEvent).where(AuditEvent.action == "model.called")))
            )
        env.close()
        from devteam_orchestration.execution import open_checkpointer  # noqa: PLC0415
        from devteam_orchestration.graph import build_graph  # noqa: PLC0415
        from devteam_orchestration.runtime import build_runtime  # noqa: PLC0415

        env.rt = build_runtime(env.settings, env.sessions)
        env.checkpointer = open_checkpointer(env.settings.checkpoints_db_path)
        env.graph = build_graph(env.rt, env.checkpointer)
        state = env.graph.get_state(_config(env))
        assert state.next == ("project_approval",)
        approval = _approve_gate(env, "project_approval")
        result = advance(
            env.rt,
            env.graph,
            project_id=env.project_id,
            run_id=env.run_id,
            kind="gate_decision",
            payload={"run_id": env.run_id, "gate": "project_approval", "approval_id": approval},
        )
        assert result.pending_gate == "publish_approval"
        with env.sessions() as session:
            for role in ("brd_analyst", "requirements_finalizer"):
                row = session.scalar(select(AgentTaskRow).where(AgentTaskRow.agent_type == role))
                assert row is not None and row.attempt_count == 1, role  # not re-executed
            calls_after = len(
                list(session.scalars(select(AuditEvent).where(AuditEvent.action == "model.called")))
            )
            assert calls_after - calls_before == 7  # head + 5 build agents + documenter only
    finally:
        env.close()


def test_the_committed_diagram_matches_the_compiled_graph() -> None:
    from devteam_orchestration.diagram import DOC, render  # noqa: PLC0415
    from tests.fixtures.harness import REPO_ROOT  # noqa: PLC0415

    assert (REPO_ROOT / DOC).read_text(encoding="utf-8") == render()
    assert "draw_mermaid_png" not in Path(graph_module.__file__).read_text(encoding="utf-8")


def test_the_graph_and_execution_modules_never_load_the_fake_provider() -> None:
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    from tests.fixtures.harness import REPO_ROOT  # noqa: PLC0415

    code = (
        "import sys; import devteam_orchestration.graph, devteam_orchestration.execution, "
        "devteam_orchestration.commands, devteam_orchestration.diagram; "
        "print('devteam_harness.llm_gateway.providers.fake' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=REPO_ROOT
    )
    assert result.stdout.strip() == "False"
