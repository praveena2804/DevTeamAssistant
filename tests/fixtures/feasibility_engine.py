"""Shared Part G test helpers: fixture analyses (D-65) and catalog snapshots with chosen kits verified.

Fixture analyses are test data, never agent output in a run (D-38). Snapshots are computed by the real
catalog service from verification records; only the kit fingerprint is fixed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from devteam_core.contracts.agents import BrdAnalystOutput
from devteam_core.contracts.catalog import CatalogKey
from devteam_core.contracts.feasibility import (
    DemotionNote,
    FeasibilityReport,
    RequirementEffect,
    coverage_of,
    recommend,
)
from devteam_core.contracts.records import FeasibilityGap, FeasibilityItem
from devteam_core.contracts.sandbox import (
    RecipeOutcome,
    SandboxExecutionStatus,
    TemplateVerification,
    TemplateVerificationStatus,
    TestCounts,
)
from devteam_services import catalog as cat
from devteam_services.feasibility.rules import (
    Assessment,
    assess_all,
    coverage_of_effects,
    effects_of,
    scope_of,
)

FIXTURES = Path(__file__).resolve().parent / "feasibility"
KITS = cat.code_root() / "templates" / "target_apps"
FIXTURE_PROJECT = "prj_01KFE0000000000000000000PG"
FIXTURE_RUN = "run_01KFE0000000000000000000PG"
COUNTED = ("FR-001", "FR-002", "FR-003", "FR-004", "BR-001")
BOTH = ("html_fastapi_sqlite", "react_fastapi_sqlite")
FIXED = datetime(2026, 11, 26, tzinfo=UTC)
CHECKSUM = "a" * 64
DIGESTS = {"python_api": "sha256:" + "b" * 64}


def passed(template: str, verification_id: str = "tvf_01KFE0000000000000000000V1") -> TemplateVerification:
    ok = RecipeOutcome(
        recipe="pytest_api", status=SandboxExecutionStatus.COMPLETED, exit_code=0, reports_tests=True,
        tests=TestCounts(tests=1, passed=1, failed=0, errors=0, skipped=0),
    )  # fmt: skip
    return TemplateVerification(
        verification_id=verification_id, template_id=template, template_version="1.0.0",
        status=TemplateVerificationStatus.PASSED, recipes=(ok,), template_checksum=CHECKSUM,
        image_digests=DIGESTS, requested_by="prn_test", requested_at=FIXED, finished_at=FIXED,
    )  # fmt: skip


def snapshot(*kits: str) -> cat.CatalogSnapshot:
    """The effective catalog with the given kits verified (default both); ``snapshot("")``: none verified."""
    loaded = cat.load_catalog()
    ids = {
        "html_fastapi_sqlite": "tvf_01KFE0000000000000000000V1",
        "react_fastapi_sqlite": "tvf_01KFE0000000000000000000V2",
    }
    verifications = {t: passed(t, ids[t]) for t in (kits or BOTH) if t}
    entries = {
        e.key: cat.evaluate_entry(
            e, kits_root=KITS, verifications=verifications, fingerprint=lambda _t: (CHECKSUM, dict(DIGESTS))
        )
        for e in loaded.catalog.entries
    }
    return cat.CatalogSnapshot(catalog=loaded.catalog, catalog_hash=loaded.digest, entries=entries)


def fixture(name: str, project_id: str = FIXTURE_PROJECT, run_id: str | None = None) -> BrdAnalystOutput:
    text = (FIXTURES / f"{name}.json").read_text(encoding="utf-8").replace(FIXTURE_PROJECT, project_id)
    data = json.loads(text)
    if run_id is not None:
        data["run_id"] = run_id
    return BrdAnalystOutput.model_validate(data)


def verify_kits(settings: Any, sessions: Any, *kits: str) -> list[str]:
    """Record a passing verification for each kit with its **real current** fingerprint (kit checksum and
    locked image IDs), as the Part E worker does after a successful self-test, so the effective catalog
    honestly shows the kit as available. Returns the verification IDs."""
    from devteam_core.ids import new_id  # noqa: PLC0415
    from devteam_services.templates import current_fingerprint  # noqa: PLC0415
    from devteam_state.repositories import sandbox as sandbox_repo  # noqa: PLC0415

    ids: list[str] = []
    for template in kits or BOTH:
        checksum, digests = current_fingerprint(settings, template)
        queued = TemplateVerification(
            verification_id=new_id("tvf"), template_id=template, template_version="1.0.0",
            status=TemplateVerificationStatus.QUEUED, requested_by="prn_test", requested_at=datetime.now(UTC),
        )  # fmt: skip
        final = passed(template, queued.verification_id).model_copy(
            update={
                "template_checksum": checksum,
                "image_digests": digests,
                "requested_at": queued.requested_at,
            }
        )
        with sessions.begin() as session:
            sandbox_repo.add_verification(session, queued)
            sandbox_repo.claim_next_verification(session)
            sandbox_repo.finish_verification(session, TemplateVerification.model_validate(final.model_dump()))
        ids.append(queued.verification_id)
    return ids


def golden_records() -> dict[str, Any]:
    """The "mixed" fixture evaluated with fixed IDs and time: input of the renderer golden test."""
    analysis = fixture("mixed")
    snap = snapshot()
    scope = scope_of(analysis)
    assessments = assess_all(analysis, snap, scope, {})
    report_id = "fsr_01KFE0000000000000000000G1"
    ids = {id(a): f"fea_01KFE00000000000000000000{n}" for n, a in enumerate(assessments, start=1)}

    def item_id(a: Assessment) -> str:
        return ids[id(a)]

    items = [
        FeasibilityItem(
            item_id=item_id(a), project_id=FIXTURE_PROJECT, report_id=report_id, subject_kind=a.subject.kind,
            subject_id=a.subject.subject_id, subject_name=a.subject.name, layer=a.subject.layer,
            version_spec=a.subject.version_spec, catalog_key=a.mapped.key, status=a.status, reason=a.reason,
            alternative=a.alternative, substitution=a.substitution, requirement_ids=a.affected,
            whole_application=a.whole_application, source_refs=a.subject.source_refs,
            gap=FeasibilityGap(
                gap_id=f"gap_01KFE00000000000000000000{n}", category=a.gap.category, kind=a.gap.kind,
                description=a.gap.description, source_refs=a.gap.source_refs,
            ) if a.gap else None,
        )
        for n, a in enumerate(assessments, start=1)
    ]  # fmt: skip
    effect_map = effects_of(assessments, scope, item_id)
    effects = [
        RequirementEffect(
            report_id=report_id, project_id=FIXTURE_PROJECT, requirement_key=k, effect=kind, cause_item_ids=c
        )
        for k, (kind, c) in effect_map.items()
    ]
    counts = coverage_of_effects(effect_map, scope)
    report = FeasibilityReport(
        report_id=report_id, project_id=FIXTURE_PROJECT, run_id=FIXTURE_RUN,
        analysis_task_id="tsk_01KFE0000000000000000000G1", catalog_hash="c" * 64,
        verification_ids=snap.verification_ids, numerator=counts.numerator, denominator=counts.denominator,
        coverage_pct=coverage_of(counts.numerator, counts.denominator), threshold_pct=50,
        recommendation=recommend(counts.numerator, counts.denominator, 50, counts.pending_counted),
        item_count=len(items), needs_identified=True, blocked_keys=counts.blocked,
        pending_keys=counts.pending,
        partial_keys=counts.partial, report_path="reports/FEASIBILITY_REPORT.md", report_checksum="0" * 64,
        demoted=tuple(DemotionNote(key=d.key, reason=d.reason or "demoted") for d in snap.demoted()),
        created_at=FIXED,
    )  # fmt: skip
    return {
        "report": report.model_dump(mode="json"),
        "items": [i.model_dump(mode="json") for i in items],
        "effects": [e.model_dump(mode="json") for e in effects],
    }


__all__ = [
    "BOTH",
    "COUNTED",
    "FIXTURE_PROJECT",
    "FIXTURE_RUN",
    "KITS",
    "CatalogKey",
    "fixture",
    "golden_records",
    "passed",
    "snapshot",
    "verify_kits",
]
