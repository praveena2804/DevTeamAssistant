"""Feasibility report records (Part G G.2; D-02, D-61, D-63; PART-G-PLAN §6.6-§6.7).

Written only by the Feasibility Gate service (``service:feasibility_gate``), append-only: a re-evaluation
(for example after a substitution is approved) creates a new report and never edits an old one.

Coverage (D-02 (a)):

    denominator = in-scope FR keys + in-scope BR keys        (NFRs reported, not counted)
    numerator   = keys whose effect is ``buildable``          (supported, or all cause substitutions approved)
    coverage    = numerator / denominator x 100, one decimal  (None when the denominator is 0)

``partial`` is reported separately and never counted as buildable. Recommendation (D-63 (a)): ``proceed`` at
or above the threshold, ``stop_recommended`` below it (proceeding needs an explicit acknowledgement),
``nothing_to_build`` at 0% when nothing can become buildable either (proceed disabled), ``not_applicable``
when there is nothing to measure.

DV-034: an FR/BR key waiting for an *offered* substitution is not buildable (D-02), but it can become
buildable when the user accepts the substitution in the clarification form, which comes after this gate
(D-01). So 0% with pending substitutions is ``stop_recommended``, not ``nothing_to_build``; otherwise a BRD
whose only problem is, say, Angular could never reach the form where React is accepted.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from devteam_core.contracts._base import (
    AwareDatetime,
    Contract,
    FeasibilityItemId,
    FeasibilityReportId,
    ProjectId,
    RelPath,
    RunId,
    Sha256,
    ShortText,
    TaskId,
    TraceKey,
)
from devteam_core.contracts.catalog import CatalogKey
from devteam_core.contracts.sandbox import TemplateVerificationId

_MANY = 500


class RequirementEffectKind(StrEnum):
    """Precedence per requirement: blocked > pending_substitution > partial > buildable (PART-G-PLAN §6.4)."""

    BUILDABLE = "buildable"
    PARTIAL = "partial"
    PENDING_SUBSTITUTION = "pending_substitution"
    BLOCKED = "blocked"


EFFECT_PRECEDENCE: dict[RequirementEffectKind, int] = {
    RequirementEffectKind.BUILDABLE: 0,
    RequirementEffectKind.PARTIAL: 1,
    RequirementEffectKind.PENDING_SUBSTITUTION: 2,
    RequirementEffectKind.BLOCKED: 3,
}


class CoverageRecommendation(StrEnum):
    PROCEED = "proceed"
    STOP_RECOMMENDED = "stop_recommended"
    NOTHING_TO_BUILD = "nothing_to_build"  # 0% with requirements to build: proceed is disabled
    NOT_APPLICABLE = "not_applicable"  # no in-scope FR/BR to measure


class RequirementEffect(Contract):
    report_id: FeasibilityReportId
    project_id: ProjectId
    requirement_key: TraceKey
    effect: RequirementEffectKind
    cause_item_ids: tuple[FeasibilityItemId, ...] = Field(default=(), max_length=_MANY)

    @model_validator(mode="after")
    def _caused(self) -> Self:
        if self.effect is not RequirementEffectKind.BUILDABLE and not self.cause_item_ids:
            raise ValueError("a requirement that is not buildable must name the items that cause it")
        return self


class DemotionNote(Contract):
    """A catalog entry that was demoted when the report was made (shown in the report)."""

    key: CatalogKey
    reason: ShortText


def coverage_of(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator * 100 / denominator, 1)


def recommend(
    numerator: int, denominator: int, threshold_pct: int, pending: int = 0
) -> CoverageRecommendation:
    """``pending``: counted (FR/BR) keys waiting for an offered substitution (DV-034)."""
    coverage = coverage_of(numerator, denominator)
    if coverage is None:
        return CoverageRecommendation.NOT_APPLICABLE
    if numerator == 0 and pending == 0:
        return CoverageRecommendation.NOTHING_TO_BUILD
    if coverage >= threshold_pct:
        return CoverageRecommendation.PROCEED
    return CoverageRecommendation.STOP_RECOMMENDED


class FeasibilityReport(Contract):
    report_id: FeasibilityReportId
    project_id: ProjectId
    run_id: RunId
    analysis_task_id: TaskId
    catalog_hash: Sha256
    verification_ids: tuple[TemplateVerificationId, ...] = Field(default=(), max_length=50)
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=0)
    coverage_pct: float | None = Field(default=None, ge=0, le=100)
    threshold_pct: int = Field(ge=1, le=100)
    recommendation: CoverageRecommendation
    item_count: int = Field(ge=0)
    needs_identified: bool  # False: the analysis listed no technology need or integration (honest headline)
    blocked_keys: tuple[TraceKey, ...] = Field(default=(), max_length=_MANY)
    pending_keys: tuple[TraceKey, ...] = Field(default=(), max_length=_MANY)
    partial_keys: tuple[TraceKey, ...] = Field(default=(), max_length=_MANY)
    demoted: tuple[DemotionNote, ...] = Field(default=(), max_length=100)
    report_path: RelPath
    report_checksum: Sha256
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _formula(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("the numerator cannot exceed the denominator")
        if self.coverage_pct != coverage_of(self.numerator, self.denominator):
            raise ValueError("coverage_pct must equal numerator / denominator x 100 (one decimal)")
        pending = sum(1 for key in self.pending_keys if not key.startswith("NFR-"))
        if self.recommendation is not recommend(
            self.numerator, self.denominator, self.threshold_pct, pending
        ):
            raise ValueError("the recommendation does not follow from the coverage and the threshold")
        if not self.needs_identified and self.item_count:
            raise ValueError("items exist, so needs were identified")
        return self

    @property
    def proceed_allowed(self) -> bool:
        return self.recommendation is not CoverageRecommendation.NOTHING_TO_BUILD

    @property
    def needs_acknowledgement(self) -> bool:
        return self.recommendation is CoverageRecommendation.STOP_RECOMMENDED


FEASIBILITY_MODELS: tuple[type[Contract], ...] = (RequirementEffect, DemotionNote, FeasibilityReport)

__all__ = [
    "EFFECT_PRECEDENCE",
    "FEASIBILITY_MODELS",
    "CoverageRecommendation",
    "DemotionNote",
    "FeasibilityReport",
    "RequirementEffect",
    "RequirementEffectKind",
    "coverage_of",
    "recommend",
]
