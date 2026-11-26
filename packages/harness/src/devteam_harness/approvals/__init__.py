"""Approval Service (HC-13, HS-14): approvals are enforced in code, never by prompts.

Recording (:meth:`ApprovalService.record`) refuses, and audits as ``approval.denied``:

- any principal that is not a human (agents and services never approve, Spec §4);
- a human without a role that ``config/approvals.yaml`` allows for that approval type;
- an approval type that must be bound to a spec version but is not (no version or no checksum);
- a run that does not exist in the project;
- a second decision for the same run, type and spec version (double submission). The database
  enforces this too (unique ``decision_key``), and approvals are append-only (triggers);
- (Part G) a substitution approval that is not bound to exactly one feasibility item (``subject_ref``
  ``substitution:<item_id>@<item_checksum>``), or a subject on any other approval type.

Verifying (:meth:`ApprovalService.verify`) is what a gate or guard calls before acting. It refuses, and
audits as ``approval.verify_failed``:

- an unknown approval ID (forged);
- an approval for another project, run or type;
- a decision other than ``approved``;
- a different spec version (mismatch) or checksum (stale) when the type is spec-bound;
- (Part G) a different subject (another item, or the same item after it changed) when the type is
  subject-bound;
- a row that was not written by this service: its ``approval.recorded`` audit event must exist, carry
  a valid hash, and match the row field by field, and the decider must be a stored human principal.

There are no API endpoints yet; they come with each gate (I, N, P).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.events import ApprovalRecorded
from devteam_core.contracts.records import Approval, ApprovalDecision
from devteam_core.contracts.statuses import ApprovalType
from devteam_core.errors import ConfigError
from devteam_core.ids import is_valid_id, new_id
from devteam_harness.errors import PolicyDeniedError
from devteam_harness.iam.principals import AnyPrincipal, has_approval_authority
from devteam_harness.iam.roles import Principal, Role
from devteam_state.audit_chain import link_hash
from devteam_state.models import ApprovalRow, AuditEvent
from devteam_state.models import Principal as PrincipalRow
from devteam_state.repositories import audit, runs
from devteam_state.repositories.execution import add_approval, approval_decision_key, find_decision

FILE = "approvals.yaml"
# Ceiling in code: these types can never be configured as unbound (a spec-less approval could be reused).
MUST_BIND = frozenset(
    {
        ApprovalType.SPEC_APPROVAL,
        ApprovalType.PUBLISH_GITHUB,
        ApprovalType.DESTRUCTIVE_DB,
        ApprovalType.DB_SWITCH,
    }
)
SERVICE = "service:approval_service"
# Part G (AC-G-04): these types are always bound to one subject (a feasibility item and its checksum).
MUST_SUBJECT = frozenset({ApprovalType.SUBSTITUTION})
SUBJECT_REF = re.compile(r"^substitution:fea_[0-7][0-9A-HJKMNP-TV-Z]{25}@[0-9a-f]{64}$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ApprovalRule(_Strict):
    roles: tuple[Role, ...] = Field(min_length=1)
    bind_spec: bool


class _ApprovalFile(_Strict):
    version: Literal[1]
    approvals: dict[ApprovalType, ApprovalRule]

    @model_validator(mode="after")
    def _complete(self) -> Self:
        missing = set(ApprovalType) - set(self.approvals)
        if missing:
            raise ValueError(f"every approval type needs a rule; missing {sorted(m.value for m in missing)}")
        loose = [t.value for t in MUST_BIND if not self.approvals[t].bind_spec]
        if loose:
            raise ValueError(f"these approval types must be bound to a spec version: {sorted(loose)}")
        return self


@dataclass(frozen=True, slots=True)
class ApprovalRules:
    rules: Mapping[ApprovalType, ApprovalRule]
    digest: str


def parse_approval_rules(raw: bytes) -> ApprovalRules:
    try:
        parsed = _ApprovalFile.model_validate(yaml.safe_load(raw.decode("utf-8")))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"{FILE} is not valid YAML: {type(exc).__name__}") from None
    except ValidationError as exc:
        first = exc.errors(include_input=False, include_url=False)[0]
        location = ".".join(str(p) for p in first["loc"]) or "<root>"
        raise ConfigError(f"{FILE} is invalid at {location}: {first['msg']}") from None
    return ApprovalRules(
        rules=MappingProxyType(dict(parsed.approvals)), digest=hashlib.sha256(raw).hexdigest()
    )


def load_approval_rules(config_dir: Path) -> ApprovalRules:
    try:
        raw = (config_dir / FILE).read_bytes()
    except OSError:
        raise ConfigError(f"{FILE} is missing: approvals cannot be checked") from None
    return parse_approval_rules(raw)


class ApprovalDeniedError(PolicyDeniedError):
    def __init__(self, code: str, message: str, audit_event_id: str) -> None:
        super().__init__(code, message, rule_id=code)
        self.audit_event_id = audit_event_id


def _to_contract(row: ApprovalRow) -> Approval:
    return Approval(
        approval_id=row.id,
        project_id=row.project_id,
        run_id=row.run_id,
        approval_type=ApprovalType(row.approval_type),
        spec_version_id=row.spec_version_id,
        spec_checksum=row.spec_checksum,
        decision=ApprovalDecision(row.decision),
        decided_by=row.decided_by,
        # SQLite returns naive datetimes; every stored timestamp is UTC (devteam_core.clock)
        decided_at=row.decided_at if row.decided_at.tzinfo else row.decided_at.replace(tzinfo=UTC),
    )


class ApprovalService:
    def __init__(self, rules: ApprovalRules, sessions: sessionmaker[Session], *, clock: Clock = system_clock):
        self._rules = rules
        self._sessions = sessions
        self._clock = clock

    # ------------------------------------------------------------------------------------- helpers

    def _deny(  # noqa: PLR0917 - private helper; every argument is required context
        self,
        action: str,
        code: str,
        message: str,
        principal_id: str | None,
        project_id: str | None,
        details: dict[str, object],
    ) -> ApprovalDeniedError:
        """Audit the refusal in its own transaction (the caller's work, if any, is already rolled back)."""
        with self._sessions.begin() as session:
            event = audit.append(
                session,
                action=action,
                outcome="denied",
                principal_id=(principal_id or SERVICE)[:80],
                project_id=project_id if project_id and is_valid_id(project_id, "prj") else None,
                target_type="approval",
                details={
                    "code": code,
                    "reason": message[:200],
                    "rules_digest": self._rules.digest,
                    **details,
                },
                clock=self._clock,
            )
            event_id = event.id
        return ApprovalDeniedError(code, message, event_id)

    # -------------------------------------------------------------------------------------- record

    def record(
        self,
        principal: AnyPrincipal,
        *,
        project_id: str,
        run_id: str,
        approval_type: ApprovalType,
        decision: ApprovalDecision,
        spec_version_id: str | None = None,
        spec_checksum: str | None = None,
        subject_ref: str | None = None,
    ) -> Approval:
        rule = self._rules.rules[approval_type]
        principal_id = getattr(principal, "id", None)
        context: dict[str, object] = {
            "approval_type": approval_type.value,
            "decision": decision.value,
            "run_id": run_id[:40],
            "spec_version_id": (spec_version_id or "")[:40] or None,
        }

        def refuse(code: str, message: str) -> ApprovalDeniedError:
            return self._deny("approval.denied", code, message, principal_id, project_id, context)

        if not isinstance(principal, Principal) or not has_approval_authority(principal):
            raise refuse(
                "approval.not_human", "only a human principal can approve (agents and services never do)"
            )
        if not set(principal.roles) & {r.value for r in rule.roles}:
            raise refuse("approval.wrong_role", f"{approval_type.value} needs one of the configured roles")
        if rule.bind_spec and (spec_version_id is None or spec_checksum is None):
            raise refuse("approval.unbound", f"{approval_type.value} must name the spec version and checksum")
        if approval_type in MUST_SUBJECT and (subject_ref is None or not SUBJECT_REF.fullmatch(subject_ref)):
            raise refuse("approval.unbound", f"{approval_type.value} must name exactly one feasibility item")
        if approval_type not in MUST_SUBJECT and subject_ref is not None:
            raise refuse("approval.unexpected_subject", f"{approval_type.value} is not bound to a subject")
        context["subject_ref"] = subject_ref

        approval_id = new_id("apr", self._clock)
        try:
            with self._sessions.begin() as session:
                problem = self._record_problem(
                    session, principal.id, project_id, run_id, approval_type, spec_version_id or subject_ref
                )
                if problem is None:
                    now = self._clock.now()
                    contract = Approval(
                        approval_id=approval_id,
                        project_id=project_id,
                        run_id=run_id,
                        approval_type=approval_type,
                        spec_version_id=spec_version_id,
                        spec_checksum=spec_checksum,
                        decision=decision,
                        decided_by=principal.id,
                        decided_at=now,
                    )
                    event = audit.append(
                        session,
                        action="approval.recorded",
                        outcome="success",
                        principal_id=principal.id,
                        project_id=project_id,
                        target_type="approval",
                        target_id=approval_id,
                        details=_evidence(contract, self._clock, subject_ref),
                        clock=self._clock,
                    )
                    add_approval(
                        session,
                        ApprovalRow(
                            id=approval_id,
                            project_id=project_id,
                            run_id=run_id,
                            approval_type=approval_type.value,
                            spec_version_id=spec_version_id,
                            spec_checksum=spec_checksum,
                            decision_key=approval_decision_key(
                                run_id, approval_type.value, spec_version_id or subject_ref
                            ),
                            decision=decision.value,
                            decided_by=principal.id,
                            decided_at=now,
                            audit_event_id=event.id,
                            subject_ref=subject_ref,
                        ),
                    )
                    return contract
        except IntegrityError:
            problem = ("approval.duplicate", "a decision for this run, type and spec version already exists")
        raise refuse(*problem)

    def _record_problem(  # noqa: PLR0917
        self,
        session: Session,
        principal_id: str,
        project_id: str,
        run_id: str,
        approval_type: ApprovalType,
        spec_version_id: str | None,
    ) -> tuple[str, str] | None:
        if session.get(PrincipalRow, principal_id) is None:
            return "approval.unknown_principal", "the approving principal is not registered"
        if runs.get_run(session, project_id, run_id) is None:
            return "approval.unknown_run", "the run does not exist in this project"
        if find_decision(session, run_id, approval_type.value, spec_version_id) is not None:
            return "approval.duplicate", "a decision for this run, type and spec version already exists"
        return None

    # -------------------------------------------------------------------------------------- verify

    def verify(
        self,
        approval_id: str,
        *,
        project_id: str,
        run_id: str,
        approval_type: ApprovalType,
        spec_version_id: str | None = None,
        spec_checksum: str | None = None,
        verifier: str = SERVICE,
        subject_ref: str | None = None,
    ) -> Approval:
        """Return the approval if, and only if, it authorises exactly this action now."""
        rule = self._rules.rules[approval_type]
        with self._sessions() as session:
            row = session.get(ApprovalRow, approval_id) if is_valid_id(approval_id, "apr") else None
            problem = self._verify_problem(
                session, row, project_id, run_id, approval_type, rule, spec_version_id, spec_checksum
            )
            if problem is None and row is not None:
                problem = _subject_problem(row, approval_type, subject_ref)
            contract = _to_contract(row) if row is not None and problem is None else None
        if problem is not None or contract is None:
            code, message = problem or ("approval.unknown", "no such approval")
            raise self._deny(
                "approval.verify_failed",
                code,
                message,
                verifier,
                project_id,
                {
                    "approval_id": approval_id[:40],
                    "approval_type": approval_type.value,
                    "run_id": run_id[:40],
                },
            )
        return contract

    def _verify_problem(  # noqa: PLR0911, PLR0917 - one return per refusal reason
        self,
        session: Session,
        row: ApprovalRow | None,
        project_id: str,
        run_id: str,
        approval_type: ApprovalType,
        rule: ApprovalRule,
        spec_version_id: str | None,
        spec_checksum: str | None,
    ) -> tuple[str, str] | None:
        if row is None:
            return "approval.unknown", "no such approval (unknown or forged ID)"
        if row.project_id != project_id or row.run_id != run_id:
            return "approval.wrong_run", "the approval belongs to another project or run"
        if row.approval_type != approval_type.value:
            return "approval.wrong_type", "the approval is for a different action"
        if row.decision != ApprovalDecision.APPROVED.value:
            return "approval.not_approved", f"the decision was '{row.decision}'"
        if rule.bind_spec:
            if spec_version_id is None or row.spec_version_id != spec_version_id:
                return "approval.version_mismatch", "the approval was given for a different spec version"
            if spec_checksum is None or row.spec_checksum != spec_checksum:
                return "approval.stale_checksum", "the spec changed after it was approved"
        if not _has_evidence(session, row):
            return "approval.forged", "the approval was not recorded by the Approval Service"
        return None


def _subject_problem(
    row: ApprovalRow, approval_type: ApprovalType, subject_ref: str | None
) -> tuple[str, str] | None:
    """Part G: a subject-bound approval authorises only its own subject (item and checksum)."""
    if approval_type not in MUST_SUBJECT and row.subject_ref is None:
        return None
    if subject_ref is None or row.subject_ref != subject_ref:
        return "approval.subject_mismatch", "the approval was given for another item, or the item changed"
    return None


def _evidence(contract: Approval, clock: Clock, subject_ref: str | None = None) -> dict[str, object]:
    event = ApprovalRecorded(
        event_id=new_id("evt", clock),
        ts=contract.decided_at,
        project_id=contract.project_id,
        run_id=contract.run_id,
        actor=contract.decided_by,
        approval_id=contract.approval_id,
        approval_type=contract.approval_type,
        decision=contract.decision,
    )
    details = event.model_dump(mode="json", exclude={"schema_version", "ts", "event_id"})
    details["spec_version_id"] = contract.spec_version_id
    details["spec_checksum"] = contract.spec_checksum
    if subject_ref is not None:  # Part G; earlier evidence is unchanged
        details["subject_ref"] = subject_ref
    return details


def _has_evidence(session: Session, row: ApprovalRow) -> bool:
    event = session.get(AuditEvent, row.audit_event_id)
    if event is None or event.action != "approval.recorded" or event.seq is None or event.prev_hash is None:
        return False
    if event.hash != link_hash(event.prev_hash, event, event.seq):
        return False
    d = event.details or {}
    expected = {
        "approval_id": row.id,
        "approval_type": row.approval_type,
        "decision": row.decision,
        "run_id": row.run_id,
        "project_id": row.project_id,
        "actor": row.decided_by,
        "spec_version_id": row.spec_version_id,
        "spec_checksum": row.spec_checksum,
        "subject_ref": row.subject_ref,
    }
    if any(d.get(key) != value for key, value in expected.items()):
        return False
    if event.principal_id != row.decided_by or event.target_id != row.id:
        return False
    human = session.get(PrincipalRow, row.decided_by)
    return human is not None and human.kind == "human"


__all__ = [
    "MUST_BIND",
    "MUST_SUBJECT",
    "ApprovalDeniedError",
    "ApprovalRule",
    "ApprovalRules",
    "ApprovalService",
    "load_approval_rules",
    "parse_approval_rules",
]
