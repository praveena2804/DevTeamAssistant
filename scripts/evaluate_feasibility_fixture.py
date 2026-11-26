"""Evaluate a **fixture** analysis with the real Feasibility Gate rules and Capability Catalog (D-65).

    uv run python scripts/evaluate_feasibility_fixture.py <fixture.json> --out <dir>
    uv run python scripts/evaluate_feasibility_fixture.py <fixture> --out <dir> --state-db data/state.db

This is a demonstration aid, not a run. The analysis is a test fixture (the Part D BRD Analyst extracts no
technologies; that arrives in Part H), so nothing is written to the platform database: no project, no run,
no report record (D-38). The report is written only to ``--out`` and carries a banner saying so.

Effective catalog status is honest: with ``--state-db`` the template verifications in that database are used
(opened read-only); without it, no verification is assumed, so every starter-kit entry shows as demoted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from devteam_core.config import load_settings
from devteam_core.contracts.agents import BrdAnalystOutput
from devteam_core.contracts.feasibility import (
    DemotionNote,
    FeasibilityReport,
    RequirementEffect,
    coverage_of,
    recommend,
)
from devteam_core.contracts.records import FeasibilityGap, FeasibilityItem
from devteam_core.ids import new_id
from devteam_services.catalog import (
    CatalogSnapshot,
    default_fingerprint,
    effective_catalog,
    evaluate_entry,
    load_catalog,
)
from devteam_services.feasibility.render import render_report
from devteam_services.feasibility.rules import (
    Assessment,
    assess_all,
    coverage_of_effects,
    effects_of,
    scope_of,
)
from devteam_services.integration_builder import templates_root

BANNER = (
    "> **FIXTURE EVALUATION — not a run.** The analysis below is a test fixture (D-65), evaluated with "
    "the real Feasibility Gate rules and Capability Catalog. Nothing was stored as platform records.\n\n"
)


def snapshot(state_db: Path | None) -> CatalogSnapshot:
    loaded = load_catalog()
    if state_db is None:
        entries = {
            e.key: evaluate_entry(
                e, kits_root=templates_root(), verifications={}, fingerprint=lambda _t: ("", {})
            )
            for e in loaded.catalog.entries
        }
        return CatalogSnapshot(catalog=loaded.catalog, catalog_hash=loaded.digest, entries=entries)
    engine = create_engine(f"sqlite:///file:{state_db.resolve().as_posix()}?mode=ro&uri=true")
    try:
        with Session(engine) as session:
            return effective_catalog(
                loaded, session, kits_root=templates_root(), fingerprint=default_fingerprint(load_settings())
            )
    finally:
        engine.dispose()


def evaluate(analysis: BrdAnalystOutput, snap: CatalogSnapshot, threshold: int) -> str:
    scope = scope_of(analysis)
    assessments = assess_all(analysis, snap, scope, {})
    report_id = new_id("fsr")
    ids = {id(a): new_id("fea") for a in assessments}

    def item_id(a: Assessment) -> str:
        return ids[id(a)]

    items = [
        FeasibilityItem(
            item_id=item_id(a), project_id=analysis.project_id, report_id=report_id,
            subject_kind=a.subject.kind,
            subject_id=a.subject.subject_id, subject_name=a.subject.name, layer=a.subject.layer,
            version_spec=a.subject.version_spec, catalog_key=a.mapped.key, status=a.status, reason=a.reason,
            alternative=a.alternative, substitution=a.substitution, requirement_ids=a.affected,
            whole_application=a.whole_application, source_refs=a.subject.source_refs,
            gap=FeasibilityGap(
                gap_id=new_id("gap"), category=a.gap.category, kind=a.gap.kind, description=a.gap.description,
                source_refs=a.gap.source_refs,
            ) if a.gap else None,
        )
        for a in assessments
    ]  # fmt: skip
    effect_map = effects_of(assessments, scope, item_id)
    effects = [
        RequirementEffect(
            report_id=report_id,
            project_id=analysis.project_id,
            requirement_key=k,
            effect=kind,
            cause_item_ids=c,
        )
        for k, (kind, c) in effect_map.items()
    ]
    counts = coverage_of_effects(effect_map, scope)
    from datetime import UTC, datetime  # noqa: PLC0415

    report = FeasibilityReport(
        report_id=report_id, project_id=analysis.project_id, run_id=analysis.run_id,
        analysis_task_id=new_id("tsk"), catalog_hash=snap.catalog_hash,
        verification_ids=snap.verification_ids,
        numerator=counts.numerator, denominator=counts.denominator,
        coverage_pct=coverage_of(counts.numerator, counts.denominator), threshold_pct=threshold,
        recommendation=recommend(counts.numerator, counts.denominator, threshold, counts.pending_counted),
        item_count=len(items), needs_identified=bool(analysis.technology_needs or analysis.integrations),
        blocked_keys=counts.blocked, pending_keys=counts.pending, partial_keys=counts.partial,
        demoted=tuple(DemotionNote(key=d.key, reason=d.reason or "demoted") for d in snap.demoted()),
        report_path="reports/FEASIBILITY_REPORT.md", report_checksum="0" * 64, created_at=datetime.now(UTC),
    )  # fmt: skip
    return BANNER + render_report(report, items, effects)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("fixture", type=Path, help="a validated BrdAnalystOutput JSON fixture")
    parser.add_argument("--out", type=Path, required=True, help="folder for FEASIBILITY_REPORT.md")
    parser.add_argument("--state-db", type=Path, default=None, help="read template verifications (read-only)")
    parser.add_argument("--threshold", type=int, default=load_settings().feasibility.min_coverage_pct)
    args = parser.parse_args(argv)
    analysis = BrdAnalystOutput.model_validate(json.loads(args.fixture.read_text(encoding="utf-8")))
    text = evaluate(analysis, snapshot(args.state_db), args.threshold)
    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "FEASIBILITY_REPORT.md"
    target.write_text(text, encoding="utf-8", newline="\n")
    digest = hashlib.sha256(text.encode()).hexdigest()[:12]
    print(f"wrote {target} (sha256 {digest}; fixture evaluation, not a run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
