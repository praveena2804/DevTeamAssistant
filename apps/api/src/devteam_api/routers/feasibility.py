"""Feasibility report of a run (Part G G.5; PART-G-PLAN §6.7-§6.8). Read-only.

The API reads SQLite only: the report and its items and effects are the stored, append-only records written
by the Feasibility Gate service. ``report.md`` is rendered again from those records and must equal the stored
checksum, so the download is exactly what the run recorded (the workspace file is not read). Decisions are
taken through the existing gate-decision route (``acknowledge_low_coverage``, ``reason``; D-01, D-63).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from devteam_api.deps import DbSession, ProjectUser, api_error, limit_rate
from devteam_api.routers.runs import RUN_PATH, load_run
from devteam_api.schemas import ERROR_RESPONSES, ErrorResponse
from devteam_services.feasibility.render import render_report
from devteam_state.repositories import feasibility as repo

router = APIRouter(
    prefix="/api", tags=["feasibility"], dependencies=[Depends(limit_rate)], responses=ERROR_RESPONSES
)
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "No feasibility report"}
}


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GapOut(_Out):
    category: str
    kind: str
    description: str


class FeasibilityItemOut(_Out):
    item_id: str
    subject_kind: str
    subject_name: str
    layer: str | None
    version_spec: str | None
    catalog_key: str | None
    status: str
    reason: str | None
    alternative: str | None
    substitution: str
    requirement_ids: list[str]
    whole_application: bool
    gap: GapOut | None
    source: str | None  # "<document> p. N" of the first cited piece


class EffectOut(_Out):
    requirement_key: str
    effect: str
    cause_item_ids: list[str]


class DemotedOut(_Out):
    key: str
    reason: str


class FeasibilityOut(_Out):
    report_id: str
    run_id: str
    created_at: datetime
    catalog_hash: str
    verification_ids: list[str]
    numerator: int
    denominator: int
    coverage_pct: float | None
    threshold_pct: int
    recommendation: str
    proceed_allowed: bool
    needs_acknowledgement: bool
    needs_identified: bool
    blocked_keys: list[str]
    pending_keys: list[str]
    partial_keys: list[str]
    demoted: list[DemotedOut]
    items: list[FeasibilityItemOut]
    effects: list[EffectOut]


def _latest(db: DbSession, project_id: str, run_id: str) -> repo.StoredReport:
    load_run(db, project_id, run_id)  # 404 for an unknown run or another project's run
    stored = repo.latest_report(db, project_id, run_id)
    if stored is None:
        raise api_error(
            status.HTTP_404_NOT_FOUND, "feasibility.not_found", "No feasibility report for this run yet."
        )
    return stored


@router.get(
    f"{RUN_PATH}/feasibility", response_model=FeasibilityOut, name="run.feasibility", responses=NOT_FOUND
)
def read_feasibility(project_id: str, run_id: str, db: DbSession, _: ProjectUser) -> FeasibilityOut:
    stored = _latest(db, project_id, run_id)
    report = stored.report
    items = [
        FeasibilityItemOut(
            item_id=i.item_id, subject_kind=i.subject_kind.value, subject_name=i.subject_name,
            layer=i.layer.value if i.layer else None, version_spec=i.version_spec,
            catalog_key=i.catalog_key.value if i.catalog_key else None, status=i.status.value,
            reason=i.reason,
            alternative=i.alternative.value if i.alternative else None, substitution=i.substitution.value,
            requirement_ids=list(i.requirement_ids), whole_application=i.whole_application,
            gap=GapOut(category=i.gap.category.value, kind=i.gap.kind.value, description=i.gap.description)
            if i.gap else None,
            source=(
                f"{i.source_refs[0].document_id} p. {i.source_refs[0].page_start}"
                if i.source_refs and i.source_refs[0].page_start else None
            ),
        )
        for i in stored.items
    ]  # fmt: skip
    return FeasibilityOut(
        report_id=report.report_id, run_id=report.run_id, created_at=report.created_at,
        catalog_hash=report.catalog_hash, verification_ids=list(report.verification_ids),
        numerator=report.numerator, denominator=report.denominator, coverage_pct=report.coverage_pct,
        threshold_pct=report.threshold_pct, recommendation=report.recommendation.value,
        proceed_allowed=report.proceed_allowed, needs_acknowledgement=report.needs_acknowledgement,
        needs_identified=report.needs_identified, blocked_keys=list(report.blocked_keys),
        pending_keys=list(report.pending_keys), partial_keys=list(report.partial_keys),
        demoted=[DemotedOut(key=d.key.value, reason=d.reason) for d in report.demoted], items=items,
        effects=[
            EffectOut(
                requirement_key=e.requirement_key,
                effect=e.effect.value,
                cause_item_ids=list(e.cause_item_ids),
            )
            for e in stored.effects
        ],
    )  # fmt: skip


@router.get(
    f"{RUN_PATH}/feasibility/report.md",
    name="run.feasibility_report",
    response_class=Response,
    responses={**NOT_FOUND, 200: {"content": {"text/markdown": {}}, "description": "FEASIBILITY_REPORT.md"}},
)
def download_report(project_id: str, run_id: str, db: DbSession, _: ProjectUser) -> Response:
    stored = _latest(db, project_id, run_id)
    text = render_report(stored.report, stored.items, stored.effects)
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != stored.report.report_checksum:
        raise api_error(
            status.HTTP_409_CONFLICT,
            "feasibility.checksum_mismatch",
            "The report no longer matches its record.",
        )
    return Response(
        content=text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="FEASIBILITY_REPORT.md"'},
    )
