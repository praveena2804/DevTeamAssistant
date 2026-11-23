"""D.2 service steps: honest stand-ins (D-38) and the Spec Freeze service (D-39, HC-13)."""

from __future__ import annotations

import ast
import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import FixedClock
from devteam_core.contracts.orchestration import (
    NODE_KIND,
    NodeKind,
    NodeName,
    ServiceOutcome,
    ServiceOutcomeStatus,
)
from devteam_core.contracts.records import ApprovalDecision
from devteam_core.contracts.statuses import ApprovalType, Stage
from devteam_core.ids import new_id
from devteam_harness.approvals import ApprovalDeniedError, ApprovalService, load_approval_rules
from devteam_harness.iam.roles import Principal
from devteam_orchestration import services
from devteam_orchestration.services import (
    SPEC_FREEZE_PRINCIPAL,
    STAND_INS,
    SpecFreezeError,
    freeze_spec,
    stand_in_outcome,
)
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.models import ApprovalRow, AuditEvent, Project
from devteam_state.models import Principal as PrincipalRow
from devteam_state.repositories import runs
from devteam_state.repositories import spec_versions as specs
from tests.fixtures.harness import REPO_CONFIG

T0 = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
S = ServiceOutcomeStatus


# ------------------------------------------------------------------------------------- stand-ins


def test_every_stand_in_is_a_service_node_and_freeze_is_not_a_stand_in() -> None:
    assert all(NODE_KIND[node] is NodeKind.SERVICE for node in STAND_INS)
    assert NodeName.FREEZE_SPEC not in STAND_INS
    assert NodeName.FINAL_REPORT not in STAND_INS  # D.3 writes the real run summary


@pytest.mark.parametrize("node", list(STAND_INS))
def test_stand_ins_say_they_did_nothing_and_name_the_real_part(node: NodeName) -> None:
    outcome = stand_in_outcome(node, project_id=new_id("prj"), run_id=new_id("run"), clock=FixedClock(T0))
    assert outcome.stand_in is True
    assert outcome.status is not S.PERFORMED
    assert outcome.available_in_part is not None
    assert outcome.result_ref is None
    assert outcome.recorded_at == T0
    reason = outcome.reason.lower()
    assert any(word in reason for word in ("not", "no ", "nothing", "arrive"))
    for claim in ("passed", "verified", "supported", "success"):
        assert claim not in reason, claim


def test_the_outcome_contract_cannot_express_success_for_a_stand_in() -> None:
    assert {s.value for s in S} == {"performed", "not_run", "not_evaluated", "not_built", "not_published"}
    base = {
        "project_id": new_id("prj"),
        "run_id": new_id("run"),
        "node": NodeName.TEST_RUNNER,
        "reason": "x",
        "recorded_at": T0,
    }
    with pytest.raises(ValueError, match="stand-in service never performs"):
        ServiceOutcome(
            **base, status=S.PERFORMED, stand_in=True, available_in_part="M", result_ref=new_id("trn")
        )
    with pytest.raises(ValueError, match="must name the part"):
        ServiceOutcome(**base, status=S.NOT_RUN, stand_in=True)
    with pytest.raises(ValueError, match="produces nothing"):
        ServiceOutcome(
            **base, status=S.NOT_RUN, stand_in=True, available_in_part="M", result_ref=new_id("trn")
        )
    with pytest.raises(ValueError, match="never performs its effect, and a real one always does"):
        ServiceOutcome(**base, status=S.NOT_RUN, stand_in=False)
    with pytest.raises(ValueError, match="is not a service node"):
        ServiceOutcome(
            **{**base, "node": NodeName.TESTER}, status=S.NOT_RUN, stand_in=True, available_in_part="M"
        )
    with pytest.raises(ValueError, match="Input should be"):
        ServiceOutcome(**base, status="passed", stand_in=True, available_in_part="M")


def test_non_stand_in_nodes_are_refused() -> None:
    with pytest.raises(ValueError, match="has no stand-in"):
        stand_in_outcome(NodeName.FREEZE_SPEC, project_id=new_id("prj"), run_id=new_id("run"))


def test_stand_ins_use_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for node in STAND_INS:
        stand_in_outcome(node, project_id=new_id("prj"), run_id=new_id("run"))


def test_the_services_module_imports_no_network_process_or_fake_model_code() -> None:
    tree = ast.parse(Path(services.__file__).read_text(encoding="utf-8"))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    banned = ("socket", "httpx", "requests", "urllib", "subprocess", "devteam_harness.llm_gateway")
    assert not [m for m in imported if m.startswith(banned)], imported
    assert "devteam_harness.approvals" in imported  # negative control: the scan sees real imports


# ------------------------------------------------------------------------------------ spec freeze


class Env:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.sessions = sessions
        self.approvals = ApprovalService(load_approval_rules(REPO_CONFIG), sessions)
        self.owner, self.project, self.other_project = new_id("prn"), new_id("prj"), new_id("prj")
        with sessions.begin() as s:
            s.add(PrincipalRow(id=self.owner, kind="human", display_name="Owner", created_at=T0))
            for pid in (self.project, self.other_project):
                s.add(
                    Project(
                        id=pid,
                        name=pid,
                        name_key=pid.lower(),
                        description="",
                        status="active",
                        created_by=self.owner,
                        created_at=T0,
                        updated_at=T0,
                    )
                )
            s.flush()
            self.run = runs.create_run(
                s, project_id=self.project, run_kind="initial", stage=Stage.REQUIREMENTS
            ).id
            self.run2 = runs.create_run(
                s, project_id=self.project, run_kind="initial", stage=Stage.REQUIREMENTS
            ).id
            self.spec = specs.create_draft(
                s, project_id=self.project, run_id=self.run, payload={"in_scope": ["FR-001"]}
            )
            self.spec_id, self.checksum = self.spec.id, self.spec.checksum

    def human(self) -> Principal:
        return Principal(
            id=self.owner, kind="human", display_name="Owner", roles=frozenset({"project_admin"})
        )

    def approve(self, **overrides: object) -> str:
        args: dict[str, object] = {
            "project_id": self.project,
            "run_id": self.run,
            "approval_type": ApprovalType.SPEC_APPROVAL,
            "decision": ApprovalDecision.APPROVED,
            "spec_version_id": self.spec_id,
            "spec_checksum": self.checksum,
        }
        args.update(overrides)
        return self.approvals.record(self.human(), **args).approval_id  # type: ignore[arg-type]

    def freeze(self, approval_id: str, **overrides: str) -> ServiceOutcome:
        return freeze_spec(
            self.sessions,
            self.approvals,
            approval_id=approval_id,
            project_id=overrides.get("project_id", self.project),
            run_id=overrides.get("run_id", self.run),
            spec_version_id=overrides.get("spec_version_id", self.spec_id),
        )

    def status(self) -> str:
        with self.sessions() as s:
            row = specs.get_spec_version(s, self.project, self.spec_id)
            assert row is not None
            return row.status

    def audit(self, action: str) -> list[AuditEvent]:
        with self.sessions() as s:
            return list(s.scalars(select(AuditEvent).where(AuditEvent.action == action)))


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    db = tmp_path / "state.db"
    upgrade(db)
    engine = create_sqlite_engine(db)
    yield Env(session_factory(engine))
    engine.dispose()


def test_an_approved_draft_is_frozen_and_audited(env: Env) -> None:
    approval = env.approve()
    outcome = env.freeze(approval)
    assert outcome.status is S.PERFORMED
    assert outcome.stand_in is False
    assert outcome.node is NodeName.FREEZE_SPEC
    assert outcome.result_ref == env.spec_id
    assert env.status() == specs.FROZEN
    with env.sessions() as s:
        row = specs.get_spec_version(s, env.project, env.spec_id)
        assert row is not None
        assert (row.approval_id, row.approved_by) == (approval, env.owner)
        assert specs.checksum_matches(row)
    [event] = env.audit("spec.frozen")
    assert event.principal_id == SPEC_FREEZE_PRINCIPAL
    assert event.target_id == env.spec_id


def test_repeating_the_freeze_after_a_crash_changes_nothing(env: Env) -> None:
    approval = env.approve()
    first = env.freeze(approval)
    again = env.freeze(approval)
    assert again.status is S.PERFORMED and again.result_ref == first.result_ref
    assert len(env.audit("spec.frozen")) == 1  # the effect happened once


def _refused(env: Env, approval_id: str, code: str, **overrides: str) -> None:
    with pytest.raises(ApprovalDeniedError) as caught:
        env.freeze(approval_id, **overrides)
    assert caught.value.code == code
    assert env.status() == specs.DRAFT
    assert env.audit("spec.frozen") == []
    codes = [e.details["code"] for e in env.audit("approval.verify_failed")]
    assert code in codes
    denied = env.audit("approval.verify_failed")[-1]
    assert denied.principal_id == SPEC_FREEZE_PRINCIPAL


def test_a_forged_approval_id_is_refused_and_audited(env: Env) -> None:
    _refused(env, new_id("apr"), "approval.unknown")


def test_a_rejected_decision_does_not_freeze(env: Env) -> None:
    _refused(env, env.approve(decision=ApprovalDecision.REJECTED), "approval.not_approved")


def test_an_approval_of_another_kind_does_not_freeze(env: Env) -> None:
    approval = env.approve(approval_type=ApprovalType.PUBLISH_GITHUB)
    _refused(env, approval, "approval.wrong_type")


def test_an_approval_for_another_run_does_not_freeze(env: Env) -> None:
    approval = env.approve(run_id=env.run2)
    _refused(env, approval, "approval.wrong_run")


def test_an_approval_of_different_content_does_not_freeze(env: Env) -> None:
    approval = env.approve(spec_checksum="0" * 64)  # approved something other than the stored draft
    _refused(env, approval, "approval.stale_checksum")


def test_an_approval_for_another_version_does_not_freeze(env: Env) -> None:
    with env.sessions.begin() as s:
        second = specs.create_draft(
            s, project_id=env.project, run_id=env.run, payload={"in_scope": ["FR-002"]}
        )
        second_id, second_sum = second.id, second.checksum
    approval = env.approve(spec_version_id=second_id, spec_checksum=second_sum)
    _refused(env, approval, "approval.version_mismatch")


def test_an_approval_row_written_around_the_service_is_refused(env: Env) -> None:
    env.approve(approval_type=ApprovalType.FEASIBILITY_DECISION, spec_version_id=None, spec_checksum=None)
    evidence = env.audit("approval.recorded")[0].id  # a genuine event, reused as fake "evidence"
    forged = new_id("apr")
    with env.sessions.begin() as s:  # direct insert, bypassing the Approval Service
        s.add(
            ApprovalRow(
                id=forged,
                project_id=env.project,
                run_id=env.run,
                approval_type=ApprovalType.SPEC_APPROVAL.value,
                spec_version_id=env.spec_id,
                spec_checksum=env.checksum,
                decision_key=f"{env.run}:spec_approval:{env.spec_id}",
                decision="approved",
                decided_by=env.owner,
                decided_at=T0,
                audit_event_id=evidence,
            )
        )
    _refused(env, forged, "approval.forged")


def test_a_spec_from_another_run_or_project_is_not_frozen(env: Env) -> None:
    approval = env.approve()
    with pytest.raises(SpecFreezeError, match="does not belong"):
        env.freeze(approval, run_id=env.run2)
    with pytest.raises(SpecFreezeError, match="does not belong"):
        env.freeze(approval, project_id=env.other_project)
    with pytest.raises(SpecFreezeError, match="does not belong"):
        env.freeze(approval, spec_version_id=new_id("spv"))
    assert env.status() == specs.DRAFT


def test_a_frozen_spec_is_not_refrozen_under_another_approval(env: Env) -> None:
    env.freeze(env.approve())
    with pytest.raises(SpecFreezeError, match="different approval"):
        env.freeze(new_id("apr"))


def test_freezing_changes_only_the_spec_version_and_the_audit_log(env: Env) -> None:
    approval = env.approve()
    with env.sessions() as s:
        before = {
            t: s.scalar(text(f"SELECT count(*) FROM {t}")) for t in ("approvals", "runs", "agent_tasks")
        }
    env.freeze(approval)
    with env.sessions() as s:
        after = {t: s.scalar(text(f"SELECT count(*) FROM {t}")) for t in before}
    assert after == before
