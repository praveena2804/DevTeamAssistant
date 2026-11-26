"""Feasibility reports, items and requirement effects (Part G G.2; migration 0010). Append-only.

Every contract is re-validated at the write boundary and again when read back, so a stored row can never be
returned as something the contract would refuse. Each item carries a checksum of its canonical JSON: a
substitution approval names ``substitution:<item_id>@<checksum>`` and is refused if the item changed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from devteam_core.contracts.feasibility import FeasibilityReport, RequirementEffect, RequirementEffectKind
from devteam_core.contracts.records import FeasibilityItem
from devteam_state.models import FeasibilityEffectRow, FeasibilityItemRow, FeasibilityReportRow


class FeasibilityRecordError(ValueError):
    """The records to store are inconsistent (other project, other report, unknown cause item)."""


def item_checksum(item: FeasibilityItem) -> str:
    canonical = json.dumps(item.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def substitution_ref(item_id: str, checksum: str) -> str:
    return f"substitution:{item_id}@{checksum}"


@dataclass(frozen=True)
class StoredReport:
    report: FeasibilityReport
    items: tuple[FeasibilityItem, ...]
    effects: tuple[RequirementEffect, ...]
    checksums: dict[str, str]  # item_id -> item checksum

    def item(self, item_id: str) -> FeasibilityItem | None:
        return next((i for i in self.items if i.item_id == item_id), None)


def _check(report: FeasibilityReport, items: list[FeasibilityItem], effects: list[RequirementEffect]) -> None:
    ids = {i.item_id for i in items}
    if len(ids) != len(items):
        raise FeasibilityRecordError("duplicate item IDs")
    if len({e.requirement_key for e in effects}) != len(effects):
        raise FeasibilityRecordError("one effect per requirement")
    owners = [(i.project_id, i.report_id) for i in items] + [(e.project_id, e.report_id) for e in effects]
    for project_id, report_id in owners:
        if project_id != report.project_id or report_id != report.report_id:
            raise FeasibilityRecordError("every item and effect belongs to the report's project and report")
    for effect in effects:
        if set(effect.cause_item_ids) - ids:
            raise FeasibilityRecordError(f"{effect.requirement_key} names a cause item outside the report")
    if report.item_count != len(items):
        raise FeasibilityRecordError("item_count does not match the items")


def add_report(
    session: Session,
    report: FeasibilityReport,
    items: list[FeasibilityItem],
    effects: list[RequirementEffect],
) -> StoredReport:
    report = FeasibilityReport.model_validate(report.model_dump())
    items = [FeasibilityItem.model_validate(i.model_dump()) for i in items]
    effects = [RequirementEffect.model_validate(e.model_dump()) for e in effects]
    _check(report, items, effects)
    session.add(
        FeasibilityReportRow(
            id=report.report_id,
            project_id=report.project_id,
            run_id=report.run_id,
            analysis_task_id=report.analysis_task_id,
            catalog_hash=report.catalog_hash,
            verification_ids=list(report.verification_ids),
            numerator=report.numerator,
            denominator=report.denominator,
            coverage_pct=report.coverage_pct,
            threshold_pct=report.threshold_pct,
            recommendation=report.recommendation.value,
            item_count=report.item_count,
            payload=report.model_dump(mode="json"),
            report_path=report.report_path,
            report_checksum=report.report_checksum,
            created_at=report.created_at,
        )
    )
    session.flush()  # the report row exists before items reference it
    checksums: dict[str, str] = {}
    for item in items:
        checksums[item.item_id] = item_checksum(item)
        session.add(
            FeasibilityItemRow(
                id=item.item_id,
                report_id=item.report_id,
                project_id=item.project_id,
                subject_kind=item.subject_kind.value,
                subject_id=item.subject_id,
                subject_name=item.subject_name,
                catalog_key=item.catalog_key.value if item.catalog_key else None,
                status=item.status.value,
                reason=item.reason,
                alternative=item.alternative.value if item.alternative else None,
                requirement_ids=list(item.requirement_ids),
                gap=item.gap.model_dump(mode="json") if item.gap else None,
                payload=item.model_dump(mode="json"),
                item_checksum=checksums[item.item_id],
                created_at=report.created_at,
            )
        )
    for effect in effects:
        session.add(
            FeasibilityEffectRow(
                report_id=effect.report_id,
                requirement_key=effect.requirement_key,
                project_id=effect.project_id,
                effect=effect.effect.value,
                cause_item_ids=list(effect.cause_item_ids),
            )
        )
    session.flush()
    return StoredReport(report, tuple(items), tuple(effects), checksums)


def _load(session: Session, row: FeasibilityReportRow) -> StoredReport:
    report = FeasibilityReport.model_validate(row.payload)
    item_rows = session.scalars(
        select(FeasibilityItemRow)
        .where(FeasibilityItemRow.report_id == row.id)
        .order_by(FeasibilityItemRow.id)
    ).all()
    items = tuple(FeasibilityItem.model_validate(r.payload) for r in item_rows)
    checksums = {r.id: r.item_checksum for r in item_rows}
    for item in items:
        if item_checksum(item) != checksums[item.item_id]:
            raise FeasibilityRecordError(f"feasibility item {item.item_id} does not match its checksum")
    effect_rows = session.scalars(
        select(FeasibilityEffectRow)
        .where(FeasibilityEffectRow.report_id == row.id)
        .order_by(FeasibilityEffectRow.requirement_key)
    ).all()
    effects = tuple(
        RequirementEffect(
            report_id=r.report_id,
            project_id=r.project_id,
            requirement_key=r.requirement_key,
            effect=RequirementEffectKind(r.effect),
            cause_item_ids=tuple(r.cause_item_ids),
        )
        for r in effect_rows
    )
    return StoredReport(report, items, effects, checksums)


def get_report(session: Session, project_id: str, report_id: str) -> StoredReport | None:
    row = session.get(FeasibilityReportRow, report_id)
    if row is None or row.project_id != project_id:
        return None
    return _load(session, row)


def latest_report(session: Session, project_id: str, run_id: str) -> StoredReport | None:
    row = session.scalars(
        select(FeasibilityReportRow)
        .where(FeasibilityReportRow.project_id == project_id, FeasibilityReportRow.run_id == run_id)
        .order_by(FeasibilityReportRow.created_at.desc(), FeasibilityReportRow.id.desc())
        .limit(1)
    ).first()
    return None if row is None else _load(session, row)


def item_with_checksum(
    session: Session, project_id: str, item_id: str
) -> tuple[FeasibilityItem, str, str] | None:
    """An item, its stored checksum and its report's run ID (for substitution approvals)."""
    row = session.get(FeasibilityItemRow, item_id)
    if row is None or row.project_id != project_id:
        return None
    report = session.get(FeasibilityReportRow, row.report_id)
    assert report is not None  # noqa: S101 - foreign key
    return FeasibilityItem.model_validate(row.payload), row.item_checksum, report.run_id


__all__ = [
    "FeasibilityRecordError",
    "StoredReport",
    "add_report",
    "get_report",
    "item_checksum",
    "item_with_checksum",
    "latest_report",
    "substitution_ref",
]
