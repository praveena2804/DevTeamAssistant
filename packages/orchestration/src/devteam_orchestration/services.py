"""Service steps of the Part D workflow (D.2). Deterministic; no model, no sandbox, no network.

Two kinds:

* **Stand-ins** for services built in later parts (integration/build, test runner,
  completion gates, packager, GitHub deployer, change intake/impact). A stand-in does nothing and
  says so: its ``ServiceOutcome`` can only be "not run", "not evaluated", "not built" or "not
  published", and names the part that delivers the real service (D-38). It never writes a test
  result, evidence, a project verification status or a package, and it has no network code.
* **Spec Freeze** (real record, D-39): freezes the approved draft spec version after re-verifying the
  approval through the Approval Service (HC-13), bound to the version and its checksum.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.orchestration import (
    NodeName,
    ServiceOutcome,
    ServiceOutcomeStatus,
    ServicePart,
)
from devteam_core.contracts.statuses import ApprovalType
from devteam_harness.approvals import ApprovalService
from devteam_harness.iam.principals import ServiceName
from devteam_state.repositories import audit
from devteam_state.repositories import spec_versions as specs

N = NodeName
S = ServiceOutcomeStatus

SPEC_FREEZE_PRINCIPAL = f"service:{ServiceName.SPEC_FREEZE.value}"

STAND_INS: Mapping[NodeName, tuple[ServiceOutcomeStatus, ServicePart, str]] = MappingProxyType(
    {
        N.INTEGRATION_BUILD: (
            S.NOT_RUN,
            "E",
            "Nothing was built or run: the Docker sandbox arrives in Part E",
        ),
        N.TEST_RUNNER: (
            S.NOT_RUN,
            "M",
            "No tests were run and no test result was recorded: the Test Runner arrives in Part M",
        ),
        N.COMPLETION_GATE_1: (
            S.NOT_EVALUATED,
            "N",
            "Not evaluated and no verification status written: the Completion Gate arrives in Part N",
        ),
        N.COMPLETION_GATE_2: (
            S.NOT_EVALUATED,
            "N",
            "Not evaluated and no verification status written: the Completion Gate arrives in Part N",
        ),
        N.PACKAGER: (S.NOT_BUILT, "O", "No package was built: the Packager arrives in Part O"),
        N.GITHUB_DEPLOYER: (
            S.NOT_PUBLISHED,
            "P",
            "Nothing was published and no network was used: the GitHub Deployer arrives in Part P",
        ),
        N.CHANGE_INTAKE: (S.NOT_EVALUATED, "Q", "Change requests arrive in Part Q"),
        N.CHANGE_IMPACT: (S.NOT_EVALUATED, "Q", "Change impact analysis arrives in Part Q"),
    }
)


def stand_in_outcome(
    node: NodeName, *, project_id: str, run_id: str, clock: Clock = system_clock
) -> ServiceOutcome:
    """The honest outcome of a stand-in service step (it did nothing)."""
    try:
        status, part, reason = STAND_INS[node]
    except KeyError:
        raise ValueError(f"'{node.value}' has no stand-in") from None
    return ServiceOutcome(
        project_id=project_id,
        run_id=run_id,
        node=node,
        status=status,
        stand_in=True,
        reason=reason,
        available_in_part=part,
        recorded_at=clock.now(),
    )


class SpecFreezeError(RuntimeError):
    """The spec version cannot be frozen for this run."""


def freeze_spec(
    sessions: sessionmaker[Session],
    approvals: ApprovalService,
    *,
    project_id: str,
    run_id: str,
    spec_version_id: str,
    approval_id: str,
    clock: Clock = system_clock,
) -> ServiceOutcome:
    """Freeze the draft spec version approved by ``approval_id``. Safe to repeat after a crash.

    The approval is re-verified (type ``spec_approval``, this project and run, this version and its
    stored checksum); a refusal raises ``ApprovalDeniedError`` and is audited by the Approval Service.
    """
    with sessions() as session:
        row = specs.get_spec_version(session, project_id, spec_version_id)
        if row is None or row.run_id != run_id:
            raise SpecFreezeError("the spec version does not belong to this project and run")
        checksum, status, frozen_by = row.checksum, row.status, row.approval_id

    if status == specs.FROZEN:
        if frozen_by != approval_id:
            raise SpecFreezeError("the spec version was frozen under a different approval")
        return _performed(project_id, run_id, spec_version_id, clock)  # repeated after a crash

    approval = approvals.verify(
        approval_id,
        project_id=project_id,
        run_id=run_id,
        approval_type=ApprovalType.SPEC_APPROVAL,
        spec_version_id=spec_version_id,
        spec_checksum=checksum,
        verifier=SPEC_FREEZE_PRINCIPAL,
    )
    with sessions.begin() as session:
        try:
            frozen = specs.freeze(
                session,
                project_id=project_id,
                spec_version_id=spec_version_id,
                approval_id=approval.approval_id,
                approved_by=approval.decided_by,
                clock=clock,
            )
        except specs.SpecVersionError as error:
            raise SpecFreezeError(str(error)) from None
        audit.append(
            session,
            action="spec.frozen",
            outcome="success",
            principal_id=SPEC_FREEZE_PRINCIPAL,
            project_id=project_id,
            target_type="spec_version",
            target_id=spec_version_id,
            details={"run_id": run_id, "version": frozen.version, "approval_id": approval.approval_id},
            clock=clock,
        )
    return _performed(project_id, run_id, spec_version_id, clock)


def _performed(project_id: str, run_id: str, spec_version_id: str, clock: Clock) -> ServiceOutcome:
    return ServiceOutcome(
        project_id=project_id,
        run_id=run_id,
        node=N.FREEZE_SPEC,
        status=S.PERFORMED,
        stand_in=False,
        reason="the approved spec version is frozen",
        result_ref=spec_version_id,
        recorded_at=clock.now(),
    )
