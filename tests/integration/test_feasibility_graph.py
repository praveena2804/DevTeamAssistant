"""Part G G.4: the real feasibility node and the ``feasibility_decision`` gate in the compiled graph.

AC-G-01 through the graph.

A **test-only BRD Analyst double** stores a validated fixture analysis for the analysis task (D-65); runs
never do this (D-38). Everything after it is real: the Feasibility Gate service, the effective catalog, the
graph, the Approval Service, the run commands and the gate's re-checks. The model is offline, so the
Requirements Finalizer step ends *waiting for resources*, which is enough to prove what it was given.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from devteam_core.contracts.orchestration import NodeName
from devteam_core.contracts.records import ApprovalDecision, SubstitutionState
from devteam_core.contracts.statuses import AgentRole, ApprovalType, GateName, RunStatus
from devteam_orchestration import commands
from devteam_orchestration.execution import advance
from devteam_state.models import AgentTaskRow, AuditEvent, FeasibilityReportRow, Run
from devteam_state.repositories import feasibility as repo
from tests.fixtures.feasibility_engine import fixture, verify_kits
from tests.fixtures.orchestration import Env, closed_port, make_env

pytestmark = pytest.mark.integration
N = NodeName
TASK = "tsk_01KFE0000000000000000000T1"


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    environment = make_env(tmp_path, ollama_url=f"http://127.0.0.1:{closed_port()}", with_graph=True)
    yield environment
    environment.close()


def _config(env: Env) -> dict[str, Any]:
    return {"configurable": {"thread_id": env.run_id}}


def to_gate(env: Env, scenario: str, *, kits_verified: bool = True) -> repo.StoredReport:
    """Analyst double: store the fixture analysis; the real graph then runs feasibility and opens the gate."""
    if kits_verified:  # as after a successful Part E self-test of both starter kits
        verify_kits(env.settings, env.sessions)
    env.store_analysis(TASK, fixture(scenario, env.project_id, env.run_id).model_dump(mode="json"))
    env.graph.update_state(
        _config(env),
        {"project_id": env.project_id, "run_id": env.run_id, "last_node": N.BRD_ANALYST.value,
         "services_done": [], "analysis_task_id": TASK},
        as_node=N.BRD_ANALYST.value,
    )  # fmt: skip
    with env.sessions.begin() as session:
        run = session.get(Run, env.run_id)
        assert run is not None
        run.status = RunStatus.RUNNING.value
    result = advance(env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="resume")
    assert result.result == "waiting_for_human" and result.pending_gate == "feasibility_decision"
    with env.sessions() as session:
        stored = repo.latest_report(session, env.project_id, env.run_id)
    assert stored is not None
    return stored


def decide(env: Env, decision: ApprovalDecision, **options: Any) -> str:
    return commands.submit_gate_decision(
        env.sessions, env.rt.approvals, env.owner, project_id=env.project_id, run_id=env.run_id,
        gate=GateName.FEASIBILITY_DECISION, decision=decision, **options,
    ).approval_id  # fmt: skip


def resume_gate(env: Env, approval_id: str) -> Any:
    payload = {"run_id": env.run_id, "gate": "feasibility_decision", "approval_id": approval_id}
    return advance(env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="gate_decision",
                   payload=payload)  # fmt: skip


def run_row(env: Env) -> Run:
    with env.sessions() as session:
        run = session.get(Run, env.run_id)
        assert run is not None
        session.expunge(run)
        return run


def audits(env: Env, action: str) -> list[AuditEvent]:
    with env.sessions() as session:
        return list(
            session.scalars(select(AuditEvent).where(AuditEvent.action == action).order_by(AuditEvent.seq))
        )


# -------------------------------------------------------------------------------------------- AC-G-01


def test_power_apps_is_flagged_its_requirements_blocked_and_proceeding_is_refused(env: Env) -> None:
    """Spec scenario #3 through the real graph: report + gate; nothing to build, so only cancel/revise."""
    stored = to_gate(env, "power_apps")
    assert stored.report.blocked_keys == ("FR-001", "FR-002", "FR-003", "FR-004", "BR-001")
    assert stored.items[0].catalog_key is not None and stored.items[0].catalog_key.value == "power_apps"
    report_file = env.rt.workspace(env.project_id) / "reports" / "FEASIBILITY_REPORT.md"
    assert "Power Apps (deployment platform) is not supported" in report_file.read_text(encoding="utf-8")
    state = env.graph.get_state(_config(env))
    assert state.values["feasibility_report_id"] == stored.report.report_id
    assert state.tasks[0].interrupts[0].value["report_id"] == stored.report.report_id  # a reference only
    with pytest.raises(commands.CommandError) as refused:
        decide(env, ApprovalDecision.APPROVED)
    assert refused.value.code == "feasibility.proceed_disabled"
    approval = decide(env, ApprovalDecision.REJECTED, reason="revise")
    result = resume_gate(env, approval)
    assert result.result == "cancelled"
    run = run_row(env)
    assert run.status == RunStatus.CANCELLED.value
    (reason,) = audits(env, "feasibility.decision_reason")
    assert reason.details["reason"] == "revise" and reason.target_id == approval


def test_low_coverage_needs_an_explicit_acknowledgement_then_proceeds(env: Env) -> None:
    stored = to_gate(env, "angular")
    assert stored.report.needs_acknowledgement and stored.report.proceed_allowed
    with pytest.raises(commands.CommandError) as refused:
        decide(env, ApprovalDecision.APPROVED)
    assert refused.value.code == "feasibility.acknowledgement_required"
    approval = decide(env, ApprovalDecision.APPROVED, acknowledge_low_coverage=True)
    (ack,) = audits(env, "feasibility.low_coverage_acknowledged")
    assert ack.target_id == stored.report.report_id and ack.principal_id == env.owner.id
    result = resume_gate(env, approval)
    assert result.result == "waiting_for_resources"  # the finalizer needs the (offline) model
    assert env.graph.get_state(_config(env)).values["feasibility_approval_id"] == approval


def test_blocked_requirements_leave_the_scope_given_to_the_finalizer(env: Env) -> None:
    stored = to_gate(env, "mixed")  # Stripe blocks FR-003, Azure AD blocks FR-004
    assert set(stored.report.blocked_keys) == {"FR-003", "FR-004"}
    resume_gate(env, decide(env, ApprovalDecision.APPROVED, acknowledge_low_coverage=True))
    with env.sessions() as session:
        task = session.scalars(
            select(AgentTaskRow).where(
                AgentTaskRow.run_id == env.run_id,
                AgentTaskRow.agent_type == AgentRole.REQUIREMENTS_FINALIZER.value,
            )
        ).one()
    given = set(task.payload["requirement_ids"])
    assert given == {"FR-001", "FR-002", "NFR-001"}  # FR-003 and FR-004 left the scope (blocked)


def test_the_gate_rechecks_the_rules_when_the_command_was_bypassed(env: Env) -> None:
    """Defence in depth: an approval recorded around the run command is still refused at the gate."""
    to_gate(env, "power_apps")
    forced = env.approve(ApprovalType.FEASIBILITY_DECISION)  # straight through the Approval Service
    result = resume_gate(env, forced)
    assert result.result == "blocked"
    assert run_row(env).status == RunStatus.BLOCKED.value
    assert audits(env, "feasibility.proceed_refused")[0].details["code"] == "feasibility.proceed_disabled"


def test_the_gate_refuses_low_coverage_without_a_recorded_acknowledgement(env: Env) -> None:
    to_gate(env, "angular")
    result = resume_gate(env, env.approve(ApprovalType.FEASIBILITY_DECISION))
    assert result.result == "blocked"
    assert (
        audits(env, "feasibility.proceed_refused")[0].details["code"] == "feasibility.acknowledgement_missing"
    )


def test_a_plain_decline_cancels_with_the_default_reason(env: Env) -> None:
    to_gate(env, "angular")
    approval = decide(env, ApprovalDecision.REJECTED)
    assert resume_gate(env, approval).result == "cancelled"
    assert audits(env, "feasibility.decision_reason")[0].details["reason"] == "cancel"


def test_options_are_refused_on_other_gates_and_acknowledgement_on_a_decline(env: Env) -> None:
    to_gate(env, "angular")
    with pytest.raises(commands.CommandError) as refused:
        decide(env, ApprovalDecision.REJECTED, acknowledge_low_coverage=True)
    assert refused.value.code == "gate.bad_decision"
    with pytest.raises(commands.CommandError) as other_gate:
        commands.submit_gate_decision(
            env.sessions, env.rt.approvals, env.owner, project_id=env.project_id, run_id=env.run_id,
            gate=GateName.PROJECT_APPROVAL, decision=ApprovalDecision.APPROVED, reason="revise",
        )  # fmt: skip
    assert other_gate.value.code == "gate.bad_decision"


# ------------------------------------------------------------------------------------- idempotency


def test_a_crash_after_storing_the_report_resumes_without_a_second_report(env: Env) -> None:
    """The report was stored but the node never confirmed it (crash): the resumed node reuses it."""
    analysis = fixture("angular", env.project_id, env.run_id)
    with env.sessions() as session:
        snapshot = env.rt.catalog_snapshot(session)
    before_crash = env.rt.feasibility_gate().evaluate(
        project_id=env.project_id, run_id=env.run_id, analysis_task_id=TASK, analysis=analysis,
        snapshot=snapshot, write=env.rt.files_for(env.project_id).write_text,
    )  # fmt: skip
    stored = to_gate(env, "angular")
    assert stored.report.report_id == before_crash.report.report_id
    with env.sessions() as session:
        count = len(
            session.scalars(
                select(FeasibilityReportRow).where(FeasibilityReportRow.run_id == env.run_id)
            ).all()
        )
    assert count == 1
    again = advance(env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="resume")
    assert again.result == "waiting_for_human"  # resuming at the gate never re-evaluates


def test_a_missing_analysis_blocks_instead_of_inventing_one(env: Env) -> None:
    env.graph.update_state(
        _config(env),
        {"project_id": env.project_id, "run_id": env.run_id, "last_node": N.BRD_ANALYST.value,
         "services_done": [], "analysis_task_id": "tsk_01KFE0000000000000000000T9"},
        as_node=N.BRD_ANALYST.value,
    )  # fmt: skip
    with env.sessions.begin() as session:
        run = session.get(Run, env.run_id)
        assert run is not None
        run.status = RunStatus.RUNNING.value
    result = advance(env.rt, env.graph, project_id=env.project_id, run_id=env.run_id, kind="resume")
    assert result.result == "blocked" and run_row(env).status == RunStatus.BLOCKED.value


# ---------------------------------------------------------------------------------- substitutions


def test_a_substitution_is_accepted_only_for_an_offered_item_of_this_run(env: Env) -> None:
    stored = to_gate(env, "angular")
    (item,) = stored.items
    approval = commands.accept_substitution(
        env.sessions, env.rt.approvals, env.owner, project_id=env.project_id, run_id=env.run_id,
        item_id=item.item_id, decision=ApprovalDecision.APPROVED,
    )  # fmt: skip
    with env.sessions() as session:
        snapshot = env.rt.catalog_snapshot(session)
    applied = env.rt.feasibility_gate().apply_substitution(
        project_id=env.project_id, run_id=env.run_id, item_id=item.item_id, approval_id=approval.approval_id,
        analysis_task_id=TASK, analysis=fixture("angular", env.project_id, env.run_id), snapshot=snapshot,
        write=env.rt.files_for(env.project_id).write_text,
    )  # fmt: skip
    assert applied.items[0].substitution is SubstitutionState.APPROVED
    assert applied.report.recommendation.value == "proceed" and applied.report.numerator == 5
    with pytest.raises(commands.CommandError) as unknown:
        commands.accept_substitution(
            env.sessions, env.rt.approvals, env.owner, project_id=env.project_id, run_id=env.run_id,
            item_id="fea_01KFE0000000000000000000X1", decision=ApprovalDecision.APPROVED,
        )  # fmt: skip
    assert unknown.value.code == "substitution.unknown_item"


def test_without_verified_kits_nothing_is_supported_and_the_report_says_why(env: Env) -> None:
    """Honest demotion reaches the run: no verified kit, so React is not offered for Angular."""
    stored = to_gate(env, "angular", kits_verified=False)
    (item,) = stored.items
    assert item.status.value == "not_supported" and item.alternative is None
    assert {d.key.value for d in stored.report.demoted} >= {"react_vite", "html_css_js", "sqlite"}
    with pytest.raises(commands.CommandError) as no_offer:
        commands.accept_substitution(
            env.sessions, env.rt.approvals, env.owner, project_id=env.project_id, run_id=env.run_id,
            item_id=item.item_id, decision=ApprovalDecision.APPROVED,
        )  # fmt: skip
    assert no_offer.value.code == "substitution.not_offered"
