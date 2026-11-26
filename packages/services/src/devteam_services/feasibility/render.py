"""``FEASIBILITY_REPORT.md``, rendered from the stored records only (PART-G-PLAN §6.7).

Plain Python, no template engine. The output is byte-stable for the same records (golden test). Every text
that came from the BRD (subject names, quotes, reasons that repeat them) is escaped, so a hostile name can
never become Markdown or HTML structure.
"""

from __future__ import annotations

from collections.abc import Sequence

from devteam_core.contracts.base import SourceRef
from devteam_core.contracts.feasibility import CoverageRecommendation, FeasibilityReport, RequirementEffect
from devteam_core.contracts.records import FeasibilityItem, SubstitutionState
from devteam_core.contracts.statuses import FeasibilityStatus

S = FeasibilityStatus
STATUS_ORDER = (S.NOT_SUPPORTED, S.SUPPORTED_WITH_SUBSTITUTION, S.PARTIAL, S.SUPPORTED)
STATUS_TEXT = {
    S.SUPPORTED: "supported",
    S.SUPPORTED_WITH_SUBSTITUTION: "supported with substitution",
    S.PARTIAL: "partial",
    S.NOT_SUPPORTED: "not supported",
}
RECOMMENDATION_TEXT = {
    CoverageRecommendation.PROCEED: "Proceed: coverage is at or above the threshold.",
    CoverageRecommendation.STOP_RECOMMENDED: (
        "**Stop recommended**: coverage is below the threshold. You may still proceed with the buildable "
        "scope, but only after explicitly acknowledging this."
    ),
    CoverageRecommendation.NOTHING_TO_BUILD: (
        "**Nothing can be built**: no in-scope requirement is buildable. Proceeding is disabled; cancel, or "
        "revise the BRD and start a new run."
    ),
    CoverageRecommendation.NOT_APPLICABLE: (
        "Coverage is not applicable: the analysis recorded no in-scope functional requirement or business "
        "rule."
    ),
}
SUPPORTED_MEANS = (
    '"Supported" means the platform has a verified starter kit and verification recipes for the capability '
    "(Capability Catalog, effective status). Generating application code for it arrives with the developer "
    "agents (Parts K-L). Nothing in this report was built, run or tested."
)


_ENTITIES = str.maketrans({c: f"&#{ord(c)};" for c in "&<>\"'|`[]*_#~!\\"})


def esc(text: str) -> str:
    """Escape untrusted text for a Markdown table cell in one pass (also neutralises HTML)."""
    return " ".join(text.split()).translate(_ENTITIES)


def _source(refs: Sequence[SourceRef]) -> str:
    if not refs:
        return "—"
    ref = refs[0]
    where = f"p. {ref.page_start}" if ref.page_start else ref.chunk_id
    more = f" (+{len(refs) - 1})" if len(refs) > 1 else ""
    return f"{ref.document_id} {where}{more}"


def _affected(item: FeasibilityItem) -> str:
    if item.whole_application:
        return f"all in-scope requirements ({len(item.requirement_ids)})"
    return ", ".join(item.requirement_ids) or "—"


def _keys(keys: Sequence[str]) -> str:
    return ", ".join(keys) if keys else "none"


def render_report(
    report: FeasibilityReport,
    items: Sequence[FeasibilityItem],
    effects: Sequence[RequirementEffect],
) -> str:
    items = sorted(items, key=lambda i: (i.subject_name.casefold(), i.item_id))  # order-independent output
    effects = sorted(effects, key=lambda e: e.requirement_key)
    coverage = "n/a" if report.coverage_pct is None else f"{report.coverage_pct:.1f}%"
    lines = [
        "# Feasibility report",
        "",
        "Written by the Feasibility Gate (deterministic service; no model was used) from the stored records.",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Project | {report.project_id} |",
        f"| Run | {report.run_id} |",
        f"| Report | {report.report_id} |",
        f"| Date (UTC) | {report.created_at.isoformat(timespec='seconds')} |",
        f"| Capability Catalog | sha256 {report.catalog_hash} |",
        f"| Template verifications relied on | {_keys(report.verification_ids)} |",
        "",
        "## Coverage",
        "",
        "```text",
        "coverage = buildable in-scope FR and BR keys / in-scope FR and BR keys x 100",
        f"         = {report.numerator} / {report.denominator} = {coverage}"
        f"   (threshold {report.threshold_pct}%)",
        "```",
        "",
        "Buildable means supported, or every substitution it relies on is approved. Partial items are listed "
        "separately and never counted as buildable. Non-functional requirements are reported, not counted.",
        "",
        "## Recommendation",
        "",
        RECOMMENDATION_TEXT[report.recommendation],
        "",
    ]
    if not report.needs_identified:
        lines += [
            "**No technology needs identified.** The analysis listed no technology, integration, login "
            "method or deployment target, so nothing was checked against the catalog and nothing is "
            "blocked *because nothing was identified*. Technology extraction from the BRD arrives with "
            "the BRD Analyst in Part H.",
            "",
        ]
    lines += ["## Items by status", ""]
    if items:
        lines += [
            "| Subject | Source | Catalog key | Status | Reason | Alternative | Affected requirements |",
            "|---|---|---|---|---|---|---|",
        ]
        ordered = sorted(
            items, key=lambda i: (STATUS_ORDER.index(i.status), i.subject_name.casefold(), i.item_id)
        )
        for item in ordered:
            key = f"`{item.catalog_key.value}`" if item.catalog_key else "not in the catalog"
            alternative = (
                f"`{item.alternative.value}` ({item.substitution.value})" if item.alternative else "—"
            )
            lines.append(
                f"| {esc(item.subject_name)} | {_source(item.source_refs)} | {key} "
                f"| {STATUS_TEXT[item.status]} | {esc(item.reason) if item.reason else '—'} | {alternative} "
                f"| {_affected(item)} |"
            )
    else:
        lines.append("No items.")
    blocked = [e for e in effects if e.effect.value == "blocked"]
    lines += ["", "## Blocked requirements", ""]
    lines += [f"- **{e.requirement_key}**: blocked by {', '.join(e.cause_item_ids)}" for e in blocked] or [
        "None."
    ]
    partial = [i for i in items if i.status is S.PARTIAL]
    lines += ["", "## Partial items", ""]
    lines += [f"- {esc(i.subject_name)}: {esc(i.reason or '')}" for i in partial] or ["None."]
    offered = [i for i in items if i.substitution is not SubstitutionState.NONE]
    lines += ["", "## Substitutions", ""]
    lines += [
        f"- {esc(i.subject_name)} → `{i.alternative.value if i.alternative else ''}`: "
        + (
            "approved (approval recorded)"
            if i.substitution is SubstitutionState.APPROVED
            else "offered — not applied"
        )
        for i in offered
    ] or ["None."]
    gaps = [i for i in items if i.gap is not None]
    lines += ["", "## Gaps raised for the clarification form", ""]
    lines += [
        f"- {i.gap.category.value} / {i.gap.kind.value}: {esc(i.gap.description)}" for i in gaps if i.gap
    ] or ["None."]
    lines += ["", "## Catalog entries demoted at evaluation time", ""]
    lines += [f"- `{d.key.value}`: {esc(d.reason)}" for d in report.demoted] or ["None."]
    lines += ["", '## What "supported" means today', "", SUPPORTED_MEANS, ""]
    return "\n".join(lines)


__all__ = ["esc", "render_report"]
