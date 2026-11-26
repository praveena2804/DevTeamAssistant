"""Feasibility Gate service (Spec §5.3; roadmap §13 Part G G.3; PART-G-PLAN §3, §6, §7).

A deterministic service, not an agent (CLAUDE.md §4): principal ``service:feasibility_gate``, no model, no
network, no sandbox (import contract). It reads the BRD Analyst's validated output, checks every technology
need, integration, login method and deployment target against the *effective* Capability Catalog, records
an append-only report (items, requirement effects, coverage) and renders ``FEASIBILITY_REPORT.md`` from
those records.

Substitutions are **offered, never applied** by evaluation. One counts as approved only when a
``substitution`` approval bound to that exact item and its checksum verifies through the Approval Service
(HC-13); a forged, rejected, other-run, other-item or stale approval is refused and audited (AC-G-04).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.agents import BrdAnalystOutput
from devteam_core.contracts.catalog import CatalogKey
from devteam_core.contracts.feasibility import (
    DemotionNote,
    FeasibilityReport,
    RequirementEffect,
    coverage_of,
    recommend,
)
from devteam_core.contracts.records import (
    ApprovalDecision,
    FeasibilityGap,
    FeasibilityItem,
    SubstitutionState,
)
from devteam_core.contracts.statuses import ApprovalType, FeasibilityStatus
from devteam_core.ids import new_id
from devteam_harness.approvals import ApprovalDeniedError, ApprovalService
from devteam_harness.iam.principals import ServiceName, ServicePrincipal
from devteam_services.catalog import CatalogSnapshot, record_status_changes
from devteam_services.feasibility.render import render_report
from devteam_services.feasibility.rules import (
    Assessment,
    assess_all,
    coverage_of_effects,
    effects_of,
    scope_of,
)
from devteam_state.models import ApprovalRow
from devteam_state.repositories import audit
from devteam_state.repositories import feasibility as repo

PRINCIPAL = ServicePrincipal(ServiceName.FEASIBILITY_GATE).id
REPORT_FILE = "FEASIBILITY_REPORT.md"
PLACEHOLDER = "0" * 64

Writer = Callable[[str, str], object]  # (workspace-relative path, text): the Controlled File Service


class SubstitutionRefusedError(ValueError):
    """The item cannot take a substitution (not offered, unknown, or from another run)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AppliedSubstitution:
    item_id: str
    subject_id: str
    alternative: CatalogKey
    approval_id: str


def _parse_ref(ref: str) -> tuple[str, str] | None:
    body = ref.removeprefix("substitution:")
    item_id, _, checksum = body.partition("@")
    return (item_id, checksum) if item_id and checksum and body != ref else None


@dataclass
class FeasibilityGate:
    sessions: sessionmaker[Session]
    approvals: ApprovalService
    threshold_pct: int = 50
    report_dir: str = "reports"
    clock: Clock = field(default=system_clock)

    @property
    def report_path(self) -> str:
        return f"{self.report_dir}/{REPORT_FILE}"

    # ---------------------------------------------------------------------------------------- evaluate

    def evaluate(
        self,
        *,
        project_id: str,
        run_id: str,
        analysis_task_id: str,
        analysis: BrdAnalystOutput,
        snapshot: CatalogSnapshot,
        write: Writer,
        excluded: frozenset[str] = frozenset(),
    ) -> repo.StoredReport:
        """Evaluate, render and persist one append-only report. Approved substitutions are re-verified."""
        if analysis.project_id != project_id:
            raise ValueError("the analysis belongs to another project")
        approved = self.verified_substitutions(project_id, run_id)
        scope = scope_of(analysis, excluded)
        assessments = assess_all(analysis, snapshot, scope, approved)
        report_id = new_id("fsr", self.clock)
        ids = {id(a): new_id("fea", self.clock) for a in assessments}

        def item_id(a: Assessment) -> str:
            return ids[id(a)]

        items = [self._item(a, item_id(a), project_id, report_id) for a in assessments]
        effect_map = effects_of(assessments, scope, item_id)
        effects = [
            RequirementEffect(
                report_id=report_id,
                project_id=project_id,
                requirement_key=key,
                effect=kind,
                cause_item_ids=causes,
            )
            for key, (kind, causes) in effect_map.items()
        ]
        counts = coverage_of_effects(effect_map, scope)
        report = FeasibilityReport(
            report_id=report_id,
            project_id=project_id,
            run_id=run_id,
            analysis_task_id=analysis_task_id,
            catalog_hash=snapshot.catalog_hash,
            verification_ids=snapshot.verification_ids,
            numerator=counts.numerator,
            denominator=counts.denominator,
            coverage_pct=coverage_of(counts.numerator, counts.denominator),
            threshold_pct=self.threshold_pct,
            recommendation=recommend(
                counts.numerator, counts.denominator, self.threshold_pct, counts.pending_counted
            ),
            item_count=len(items),
            needs_identified=bool(analysis.technology_needs or analysis.integrations),
            blocked_keys=counts.blocked,
            pending_keys=counts.pending,
            partial_keys=counts.partial,
            demoted=tuple(DemotionNote(key=d.key, reason=d.reason or "demoted") for d in snapshot.demoted()),
            report_path=self.report_path,
            report_checksum=PLACEHOLDER,
            created_at=self.clock.now(),
        )
        text = render_report(report, items, effects)
        report = FeasibilityReport.model_validate(
            {**report.model_dump(), "report_checksum": hashlib.sha256(text.encode("utf-8")).hexdigest()}
        )
        write(self.report_path, text)
        with self.sessions.begin() as session:
            stored = repo.add_report(session, report, items, effects)
            self._audit_evaluation(session, report, assessments)
            record_status_changes(session, snapshot, clock=self.clock)
        return stored

    def _item(self, a: Assessment, item_id: str, project_id: str, report_id: str) -> FeasibilityItem:
        gap = (
            FeasibilityGap(
                gap_id=new_id("gap", self.clock),
                category=a.gap.category,
                kind=a.gap.kind,
                description=a.gap.description,
                source_refs=a.gap.source_refs,
            )
            if a.gap is not None
            else None
        )
        return FeasibilityItem(
            item_id=item_id,
            project_id=project_id,
            report_id=report_id,
            subject_kind=a.subject.kind,
            subject_id=a.subject.subject_id,
            subject_name=a.subject.name,
            layer=a.subject.layer,
            version_spec=a.subject.version_spec,
            catalog_key=a.mapped.key,
            status=a.status,
            reason=a.reason,
            alternative=a.alternative,
            substitution=a.substitution,
            requirement_ids=a.affected,
            whole_application=a.whole_application,
            gap=gap,
            source_refs=a.subject.source_refs,
        )

    def _audit_evaluation(
        self, session: Session, report: FeasibilityReport, assessments: list[Assessment]
    ) -> None:
        for a in assessments:
            if a.mapped.rejected_proposal is not None:  # HC-01: an unknown key is refused, never added
                audit.append(
                    session, action="catalog.key_rejected", outcome="denied", principal_id=PRINCIPAL,
                    project_id=report.project_id, target_type="feasibility_report",
                    target_id=report.report_id,
                    details={"subject_id": a.subject.subject_id, "proposed": a.mapped.rejected_proposal},
                    clock=self.clock,
                )  # fmt: skip
        audit.append(
            session,
            action="feasibility.evaluated",
            outcome="success",
            principal_id=PRINCIPAL,
            project_id=report.project_id,
            target_type="run",
            target_id=report.run_id,
            details={
                "report_id": report.report_id,
                "coverage_pct": report.coverage_pct,
                "numerator": report.numerator,
                "denominator": report.denominator,
                "recommendation": report.recommendation.value,
                "item_count": report.item_count,
                "catalog_hash": report.catalog_hash,
                "report_checksum": report.report_checksum,
            },
            clock=self.clock,
        )

    # ----------------------------------------------------------------------------------- substitutions

    def verified_substitutions(self, project_id: str, run_id: str) -> dict[str, CatalogKey]:
        """Subject ID → approved alternative, for approvals that verify **now** (others ignored, audited)."""
        with self.sessions() as session:
            rows = session.scalars(
                select(ApprovalRow).where(
                    ApprovalRow.project_id == project_id,
                    ApprovalRow.run_id == run_id,
                    ApprovalRow.approval_type == ApprovalType.SUBSTITUTION.value,
                    ApprovalRow.decision == ApprovalDecision.APPROVED.value,
                    ApprovalRow.subject_ref.is_not(None),
                )
            ).all()
            candidates = [(row.id, row.subject_ref) for row in rows]
        approved: dict[str, CatalogKey] = {}
        for approval_id, ref in candidates:
            assert ref is not None  # noqa: S101 - filtered above
            try:
                applied = self._check(project_id, run_id, approval_id, ref)
            except (ApprovalDeniedError, SubstitutionRefusedError):
                continue
            approved[applied.subject_id] = applied.alternative
        return approved

    def _check(self, project_id: str, run_id: str, approval_id: str, ref: str) -> AppliedSubstitution:
        parsed = _parse_ref(ref)
        if parsed is None:
            raise SubstitutionRefusedError(
                "substitution.bad_subject", "the approval names no feasibility item"
            )
        item_id, _checksum = parsed
        with self.sessions() as session:
            found = repo.item_with_checksum(session, project_id, item_id)
        if found is None:
            raise SubstitutionRefusedError(
                "substitution.unknown_item", "no such feasibility item in this project"
            )
        item, stored_checksum, item_run = found
        if item_run != run_id:
            raise SubstitutionRefusedError("substitution.other_run", "the item belongs to another run")
        offered = (
            item.status is FeasibilityStatus.SUPPORTED_WITH_SUBSTITUTION and item.alternative is not None
        )
        if not offered or item.substitution is SubstitutionState.NONE or item.alternative is None:
            raise SubstitutionRefusedError(
                "substitution.not_offered", "no substitution was offered for this item"
            )
        # HC-13: type, project, run, decision, recorder evidence, and the item AND its checksum
        self.approvals.verify(
            approval_id,
            project_id=project_id,
            run_id=run_id,
            approval_type=ApprovalType.SUBSTITUTION,
            verifier=PRINCIPAL,
            subject_ref=repo.substitution_ref(item.item_id, stored_checksum),
        )
        return AppliedSubstitution(item.item_id, item.subject_id, item.alternative, approval_id)

    def apply_substitution(
        self,
        *,
        project_id: str,
        run_id: str,
        item_id: str,
        approval_id: str,
        analysis_task_id: str,
        analysis: BrdAnalystOutput,
        snapshot: CatalogSnapshot,
        write: Writer,
    ) -> repo.StoredReport:
        """Apply one offered substitution **only** with its verified approval, then re-evaluate (new report).

        Raises ``ApprovalDeniedError`` (audited by the Approval Service) or ``SubstitutionRefusedError``.
        """
        with self.sessions() as session:
            found = repo.item_with_checksum(session, project_id, item_id)
        if found is None:
            raise SubstitutionRefusedError(
                "substitution.unknown_item", "no such feasibility item in this project"
            )
        expected = repo.substitution_ref(item_id, found[1])
        try:
            applied = self._check(project_id, run_id, approval_id, expected)
        except SubstitutionRefusedError as refused:
            self._refused(project_id, run_id, item_id, approval_id, refused.code)
            raise
        with self.sessions.begin() as session:
            audit.append(
                session, action="feasibility.substitution_applied", outcome="success", principal_id=PRINCIPAL,
                project_id=project_id, target_type="feasibility_item", target_id=item_id,
                details={
                    "run_id": run_id, "approval_id": approval_id, "alternative": applied.alternative.value,
                },
                clock=self.clock,
            )  # fmt: skip
        return self.evaluate(
            project_id=project_id,
            run_id=run_id,
            analysis_task_id=analysis_task_id,
            analysis=analysis,
            snapshot=snapshot,
            write=write,
        )

    def _refused(self, project_id: str, run_id: str, item_id: str, approval_id: str, code: str) -> None:
        with self.sessions.begin() as session:
            audit.append(
                session, action="feasibility.substitution_refused", outcome="denied", principal_id=PRINCIPAL,
                project_id=project_id, target_type="feasibility_item", target_id=item_id[:64],
                details={"run_id": run_id[:40], "approval_id": approval_id[:40], "code": code},
                clock=self.clock,
            )  # fmt: skip


__all__ = [
    "PRINCIPAL",
    "REPORT_FILE",
    "AppliedSubstitution",
    "FeasibilityGate",
    "SubstitutionRefusedError",
    "Writer",
]
