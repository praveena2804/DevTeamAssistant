"""C.4 Approval Service (HC-13, HS-14; Working Plan §10.3 checks that apply without a graph).

Wrong role, forged ID, mismatched version, stale checksum, double submission, agents/services approving,
and approval rows written around the service are all refused **and** audited.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from devteam_core.contracts.records import ApprovalDecision
from devteam_core.contracts.statuses import ApprovalType, Stage
from devteam_core.errors import ConfigError
from devteam_core.ids import new_id
from devteam_harness.approvals import (
    ApprovalDeniedError,
    ApprovalService,
    load_approval_rules,
    parse_approval_rules,
)
from devteam_harness.iam.principals import ServiceName, ServicePrincipal, issue_agent_principal
from devteam_harness.iam.roles import Principal
from devteam_state.models import ApprovalRow
from devteam_state.repositories import audit, runs
from tests.fixtures.harness import REPO_CONFIG, Stack, make_stack

pytestmark = pytest.mark.security

CHECKSUM = "a" * 64
OTHER_CHECKSUM = "b" * 64


@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return make_stack(tmp_path)


@pytest.fixture
def service(stack: Stack) -> ApprovalService:
    return ApprovalService(load_approval_rules(REPO_CONFIG), stack.sessions)


def _human(stack: Stack, *roles: str) -> Principal:
    return Principal(id=stack.owner_id, kind="human", display_name="Owner", roles=frozenset(roles))


def _approve(service: ApprovalService, stack: Stack, who: object, **kw: object) -> object:
    args: dict[str, object] = {
        "project_id": stack.project_id,
        "run_id": stack.run_id,
        "approval_type": ApprovalType.SPEC_APPROVAL,
        "decision": ApprovalDecision.APPROVED,
        "spec_version_id": stack.spec_version_id,
        "spec_checksum": CHECKSUM,
    }
    args.update(kw)
    return service.record(who, **args)  # type: ignore[arg-type]


def _denied_codes(stack: Stack, action: str) -> list[str]:
    return [str(e.details["code"]) for e in stack.audit(action)]


# ----------------------------------------------------------------------------------- config


def test_repository_rules_cover_every_type_with_human_roles_only() -> None:
    rules = load_approval_rules(REPO_CONFIG)
    assert set(rules.rules) == set(ApprovalType)
    assert all(rule.bind_spec for t, rule in rules.rules.items() if t is ApprovalType.SPEC_APPROVAL)


@pytest.mark.parametrize(
    ("yaml_text", "match"),
    [
        ("version: 1\napprovals:\n  spec_approval: {roles: [project_admin], bind_spec: true}\n", "missing"),
        ("version: 1\napprovals:\n  spec_approval: {roles: [agent], bind_spec: true}\n", "roles"),
        ("version: 2\napprovals: {}\n", "version"),
        ("version: [", "not valid YAML"),
    ],
)
def test_bad_rules_fail_closed(yaml_text: str, match: str) -> None:
    with pytest.raises(ConfigError, match=match):
        parse_approval_rules(yaml_text.encode())


def test_spec_bound_types_cannot_be_configured_unbound() -> None:
    raw = (REPO_CONFIG / "approvals.yaml").read_text(encoding="utf-8")
    loose = raw.replace(
        "publish_github:       {roles: [project_admin], bind_spec: true}",
        "publish_github:       {roles: [project_admin], bind_spec: false}",
    )
    assert loose != raw
    with pytest.raises(ConfigError, match="must be bound"):
        parse_approval_rules(loose.encode())


def test_missing_rules_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load_approval_rules(tmp_path)


# ----------------------------------------------------------------------------------- record


def test_a_human_with_the_right_role_records_an_approval(stack: Stack, service: ApprovalService) -> None:
    approval = _approve(service, stack, _human(stack, "project_admin"))
    recorded = stack.audit("approval.recorded")
    assert len(recorded) == 1 and recorded[0].details["approval_id"] == approval.approval_id  # type: ignore[attr-defined]
    assert recorded[0].principal_id == stack.owner_id


def test_agents_and_services_can_never_approve(stack: Stack, service: ApprovalService) -> None:
    agent = issue_agent_principal(stack.add_task())
    for who in (
        agent,
        ServicePrincipal(ServiceName.EXECUTOR),
        ServicePrincipal(ServiceName.APPROVAL_SERVICE),
    ):
        with pytest.raises(ApprovalDeniedError) as exc:
            _approve(service, stack, who)
        assert exc.value.code == "approval.not_human"
    assert _denied_codes(stack, "approval.denied") == ["approval.not_human"] * 3
    assert stack.audit("approval.recorded") == []


@pytest.mark.parametrize(
    ("roles", "approval_type", "code"),
    [
        (("project_user",), ApprovalType.SPEC_APPROVAL, "approval.wrong_role"),
        (("reviewer",), ApprovalType.PUBLISH_GITHUB, "approval.wrong_role"),
        (("project_admin",), ApprovalType.TEST_MAINTENANCE, "approval.wrong_role"),
        ((), ApprovalType.SPEC_APPROVAL, "approval.not_human"),
    ],
)
def test_wrong_role_is_refused(
    stack: Stack, service: ApprovalService, roles: tuple[str, ...], approval_type: ApprovalType, code: str
) -> None:
    with pytest.raises(ApprovalDeniedError) as exc:
        _approve(service, stack, _human(stack, *roles), approval_type=approval_type)
    assert exc.value.code == code
    assert _denied_codes(stack, "approval.denied") == [code]


def test_spec_bound_approval_must_name_version_and_checksum(stack: Stack, service: ApprovalService) -> None:
    with pytest.raises(ApprovalDeniedError, match="checksum") as exc:
        _approve(service, stack, _human(stack, "project_admin"), spec_checksum=None)
    assert exc.value.code == "approval.unbound"


def test_unknown_run_and_unregistered_principal_are_refused(stack: Stack, service: ApprovalService) -> None:
    with pytest.raises(ApprovalDeniedError) as run_exc:
        _approve(service, stack, _human(stack, "project_admin"), run_id=new_id("run"))
    assert run_exc.value.code == "approval.unknown_run"
    ghost = Principal(
        id=new_id("prn"), kind="human", display_name="Ghost", roles=frozenset({"project_admin"})
    )
    with pytest.raises(ApprovalDeniedError) as ghost_exc:
        _approve(service, stack, ghost)
    assert ghost_exc.value.code == "approval.unknown_principal"


def test_double_submission_is_refused(stack: Stack, service: ApprovalService) -> None:
    admin = _human(stack, "project_admin")
    _approve(service, stack, admin)
    with pytest.raises(ApprovalDeniedError) as exc:
        _approve(service, stack, admin, decision=ApprovalDecision.REJECTED)
    assert exc.value.code == "approval.duplicate"
    assert len(stack.audit("approval.recorded")) == 1


def test_database_also_refuses_a_duplicate_decision(stack: Stack, service: ApprovalService) -> None:
    approval = _approve(service, stack, _human(stack, "project_admin"))
    with pytest.raises(DatabaseError), stack.sessions.begin() as session:  # unique decision_key
        row = session.get(ApprovalRow, approval.approval_id)  # type: ignore[attr-defined]
        session.add(
            ApprovalRow(
                **{
                    c: getattr(row, c)
                    for c in (
                        "project_id",
                        "run_id",
                        "approval_type",
                        "spec_version_id",
                        "spec_checksum",
                        "decision_key",
                        "decision",
                        "decided_by",
                        "decided_at",
                        "audit_event_id",
                    )
                },
                id=new_id("apr"),
            )
        )


def test_approvals_are_append_only(stack: Stack, service: ApprovalService) -> None:
    approval = _approve(service, stack, _human(stack, "project_admin"), decision=ApprovalDecision.REJECTED)
    for statement in ("UPDATE approvals SET decision = 'approved'", "DELETE FROM approvals"):
        with pytest.raises(DatabaseError, match="append-only"), stack.sessions.begin() as session:
            session.execute(text(statement))
    with pytest.raises(ApprovalDeniedError) as exc:
        _verify(service, stack, approval.approval_id)  # type: ignore[attr-defined]
    assert exc.value.code == "approval.not_approved"


# ----------------------------------------------------------------------------------- verify


def _verify(service: ApprovalService, stack: Stack, approval_id: str, **kw: object) -> object:
    args: dict[str, object] = {
        "project_id": stack.project_id,
        "run_id": stack.run_id,
        "approval_type": ApprovalType.SPEC_APPROVAL,
        "spec_version_id": stack.spec_version_id,
        "spec_checksum": CHECKSUM,
    }
    args.update(kw)
    return service.verify(approval_id, **args)  # type: ignore[arg-type]


def test_a_genuine_approval_verifies(stack: Stack, service: ApprovalService) -> None:
    approval = _approve(service, stack, _human(stack, "project_admin"))
    assert _verify(service, stack, approval.approval_id) == approval  # type: ignore[attr-defined]


def test_verify_refusals(stack: Stack, service: ApprovalService) -> None:
    approval_id = _approve(service, stack, _human(stack, "project_admin")).approval_id  # type: ignore[attr-defined]
    with stack.sessions.begin() as session:
        other_run = runs.create_run(
            session, project_id=stack.project_id, run_kind="initial", stage=Stage.BUILD
        ).id
    cases = [
        ({"approval_id": new_id("apr")}, "approval.unknown"),  # forged ID
        ({"approval_id": "apr_' OR 1=1 --"}, "approval.unknown"),
        ({"run_id": other_run}, "approval.wrong_run"),
        ({"approval_type": ApprovalType.DB_SWITCH}, "approval.wrong_type"),
        ({"spec_version_id": new_id("spv")}, "approval.version_mismatch"),
        ({"spec_checksum": OTHER_CHECKSUM}, "approval.stale_checksum"),
        ({"spec_checksum": None}, "approval.stale_checksum"),
    ]
    for change, code in cases:
        target = str(change.pop("approval_id", approval_id))
        with pytest.raises(ApprovalDeniedError) as exc:
            _verify(service, stack, target, **change)
        assert exc.value.code == code, change
    assert _denied_codes(stack, "approval.verify_failed") == [code for _, code in cases]


def test_a_row_written_around_the_service_is_detected_as_forged(
    stack: Stack, service: ApprovalService
) -> None:
    genuine = _approve(service, stack, _human(stack, "project_admin"))
    evidence = stack.audit("approval.recorded")[0].id
    forged_id = new_id("apr")
    with stack.sessions.begin() as session:  # direct insert, reusing a real audit event as "evidence"
        session.add(
            ApprovalRow(
                id=forged_id,
                project_id=stack.project_id,
                run_id=stack.run_id,
                approval_type=ApprovalType.PUBLISH_GITHUB.value,
                spec_version_id=stack.spec_version_id,
                spec_checksum=CHECKSUM,
                decision_key=f"{stack.run_id}:publish_github:{stack.spec_version_id}",
                decision="approved",
                decided_by=stack.owner_id,
                decided_at=datetime.now(UTC),
                audit_event_id=evidence,
            )
        )
    with pytest.raises(ApprovalDeniedError) as exc:
        _verify(service, stack, forged_id, approval_type=ApprovalType.PUBLISH_GITHUB)
    assert exc.value.code == "approval.forged"
    assert _verify(service, stack, genuine.approval_id) == genuine  # type: ignore[attr-defined]
    with stack.sessions() as session:
        assert audit.verify_chain(session).ok
