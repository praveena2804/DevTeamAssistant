"""Part G G.2: migration 0010 (up/down), append-only feasibility records, repository round trip and checksums,
and item-bound substitution approvals in the Approval Service (HC-13; AC-G-04 at the approval level)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DatabaseError

from devteam_core.contracts.catalog import CatalogKey
from devteam_core.contracts.feasibility import (
    CoverageRecommendation,
    FeasibilityReport,
    RequirementEffect,
    RequirementEffectKind,
)
from devteam_core.contracts.records import (
    ApprovalDecision,
    FeasibilityItem,
    FeasibilitySubjectKind,
    SubstitutionState,
    TechnologyLayer,
    TechnologyNeed,
)
from devteam_core.contracts.statuses import ApprovalType, FeasibilityStatus
from devteam_core.ids import new_id
from devteam_harness.approvals import ApprovalDeniedError, ApprovalService, load_approval_rules
from devteam_harness.iam.roles import Principal
from devteam_state import create_sqlite_engine, current_revision, downgrade, upgrade
from devteam_state.repositories import feasibility as repo
from tests.fixtures.harness import REPO_CONFIG, Stack, make_stack

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)
TABLES = {"feasibility_reports", "feasibility_items", "feasibility_requirement_effects"}


@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return make_stack(tmp_path)


def make_records(stack: Stack) -> tuple[FeasibilityReport, list[FeasibilityItem], list[RequirementEffect]]:
    report_id = new_id("fsr")
    item = FeasibilityItem(
        item_id=new_id("fea"), project_id=stack.project_id, report_id=report_id,
        subject_kind=FeasibilitySubjectKind.TECHNOLOGY_NEED, subject_id=new_id("tec"), subject_name="Angular",
        layer=TechnologyLayer.FRONTEND, catalog_key=CatalogKey.ANGULAR,
        status=FeasibilityStatus.SUPPORTED_WITH_SUBSTITUTION, reason="Angular is not supported",
        alternative=CatalogKey.REACT_VITE, substitution=SubstitutionState.OFFERED,
        requirement_ids=("FR-001", "FR-002"), whole_application=True,
    )  # fmt: skip
    effects = [
        RequirementEffect(
            report_id=report_id,
            project_id=stack.project_id,
            requirement_key=key,
            effect=RequirementEffectKind.PENDING_SUBSTITUTION,
            cause_item_ids=(item.item_id,),
        )
        for key in ("FR-001", "FR-002")
    ]
    report = FeasibilityReport(
        report_id=report_id, project_id=stack.project_id, run_id=stack.run_id, analysis_task_id=new_id("tsk"),
        catalog_hash="c" * 64, numerator=0, denominator=2, coverage_pct=0.0, threshold_pct=50,
        recommendation=CoverageRecommendation.STOP_RECOMMENDED, item_count=1, needs_identified=True,
        pending_keys=("FR-001", "FR-002"), report_path="reports/FEASIBILITY_REPORT.md",
        report_checksum="d" * 64, created_at=NOW,
    )  # fmt: skip
    return report, [item], effects


# -------------------------------------------------------------------------------------------- migration


def test_0010_upgrades_and_downgrades_cleanly(tmp_path: Path) -> None:
    db = tmp_path / "state.db"
    upgrade(db)
    engine = create_sqlite_engine(db)
    try:
        assert current_revision(engine) == "0010_feasibility"
        assert set(inspect(engine).get_table_names()) >= TABLES
        assert "subject_ref" in {c["name"] for c in inspect(engine).get_columns("approvals")}
    finally:
        engine.dispose()
    downgrade(db, "0009_rag_index")
    engine = create_sqlite_engine(db)
    try:
        assert not TABLES & set(inspect(engine).get_table_names())
        assert "subject_ref" not in {c["name"] for c in inspect(engine).get_columns("approvals")}
        with engine.connect() as conn:  # the approvals append-only triggers survive the column drop
            names = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))}
        assert {"approvals_no_update", "approvals_no_delete"} <= names
    finally:
        engine.dispose()
    upgrade(db)
    engine = create_sqlite_engine(db)
    try:
        assert current_revision(engine) == "0010_feasibility"
    finally:
        engine.dispose()


def test_feasibility_records_round_trip_with_item_checksums(stack: Stack) -> None:
    report, items, effects = make_records(stack)
    with stack.sessions.begin() as session:
        stored = repo.add_report(session, report, items, effects)
    with stack.sessions() as session:
        loaded = repo.get_report(session, stack.project_id, report.report_id)
        latest = repo.latest_report(session, stack.project_id, stack.run_id)
        other = repo.get_report(session, new_id("prj"), report.report_id)
        found = repo.item_with_checksum(session, stack.project_id, items[0].item_id)
    assert loaded is not None and latest is not None and other is None  # another project never sees it
    assert loaded.report == report and loaded.items == tuple(items) and loaded.effects == tuple(effects)
    assert loaded.checksums == stored.checksums == {items[0].item_id: repo.item_checksum(items[0])}
    assert found == (items[0], repo.item_checksum(items[0]), stack.run_id)


@pytest.mark.parametrize("table", sorted(TABLES))
def test_feasibility_records_are_append_only(stack: Stack, table: str) -> None:
    report, items, effects = make_records(stack)
    with stack.sessions.begin() as session:
        repo.add_report(session, report, items, effects)
    for statement in (f"UPDATE {table} SET project_id = project_id", f"DELETE FROM {table}"):
        with pytest.raises(DatabaseError, match="append-only"), stack.sessions.begin() as session:
            session.execute(text(statement))


def test_a_changed_item_is_detected_when_read(stack: Stack) -> None:
    """Negative control for the checksum: with the trigger removed, an edited item is refused on read."""
    report, items, effects = make_records(stack)
    with stack.sessions.begin() as session:
        repo.add_report(session, report, items, effects)
        session.execute(text("DROP TRIGGER feasibility_items_no_update"))
        session.execute(
            text("UPDATE feasibility_items SET payload = json_set(payload, '$.subject_name', 'Vue')")
        )
    with pytest.raises(repo.FeasibilityRecordError, match="checksum"), stack.sessions() as session:
        repo.get_report(session, stack.project_id, report.report_id)


def test_inconsistent_records_are_refused(stack: Stack) -> None:
    report, items, effects = make_records(stack)
    stray = effects[0].model_copy(update={"cause_item_ids": (new_id("fea"),)})
    with pytest.raises(repo.FeasibilityRecordError, match="outside the report"), stack.sessions.begin() as s:
        repo.add_report(s, report, items, [stray])
    wrong = items[0].model_copy(update={"report_id": new_id("fsr")})
    with pytest.raises(repo.FeasibilityRecordError, match="belongs"), stack.sessions.begin() as s:
        repo.add_report(s, report, [wrong], [])
    with pytest.raises(repo.FeasibilityRecordError, match="item_count"), stack.sessions.begin() as s:
        repo.add_report(s, report, [], [])


def test_v1_technology_needs_are_read_as_v2() -> None:
    """Stored Part D analyses (v1, without the new fields) stay valid (D-60)."""
    v1 = {
        "schema_version": 1, "tech_id": new_id("tec"), "project_id": new_id("prj"), "layer": "frontend",
        "name": "React", "provenance": "brd_fact",
        "source_refs": [{"document_id": new_id("doc"), "chunk_id": "chk_" + "a" * 26}],
    }  # fmt: skip
    need = TechnologyNeed.model_validate(v1)
    assert need.schema_version == 2 and need.requirement_ids == () and need.catalog_key is None


# --------------------------------------------------------------------------- substitution approvals (HC-13)


def _human(stack: Stack) -> Principal:
    return Principal(id=stack.owner_id, kind="human", display_name="Owner", roles=frozenset({"project_user"}))


def test_a_substitution_approval_is_bound_to_one_item_and_its_checksum(stack: Stack) -> None:
    report, items, effects = make_records(stack)
    with stack.sessions.begin() as session:
        stored = repo.add_report(session, report, items, effects)
    ref = repo.substitution_ref(items[0].item_id, stored.checksums[items[0].item_id])
    service = ApprovalService(load_approval_rules(REPO_CONFIG), stack.sessions)
    base = {
        "project_id": stack.project_id,
        "run_id": stack.run_id,
        "approval_type": ApprovalType.SUBSTITUTION,
    }
    for bad in (None, "substitution:fea_x@y", f"substitution:{items[0].item_id}"):
        with pytest.raises(ApprovalDeniedError) as refused:
            service.record(_human(stack), **base, decision=ApprovalDecision.APPROVED, subject_ref=bad)  # type: ignore[arg-type]
        assert refused.value.code == "approval.unbound"
    with pytest.raises(ApprovalDeniedError) as refused:
        service.record(
            _human(stack), project_id=stack.project_id, run_id=stack.run_id,
            approval_type=ApprovalType.FEASIBILITY_DECISION, decision=ApprovalDecision.APPROVED,
            subject_ref=ref,
        )  # fmt: skip
    assert refused.value.code == "approval.unexpected_subject"

    approval = service.record(_human(stack), **base, decision=ApprovalDecision.APPROVED, subject_ref=ref)  # type: ignore[arg-type]
    assert service.verify(approval.approval_id, **base, subject_ref=ref).approval_id == approval.approval_id  # type: ignore[arg-type]
    changed = repo.substitution_ref(items[0].item_id, "e" * 64)
    other_item = repo.substitution_ref(new_id("fea"), stored.checksums[items[0].item_id])
    for wrong in (None, changed, other_item):
        with pytest.raises(ApprovalDeniedError) as refused:
            service.verify(approval.approval_id, **base, subject_ref=wrong)  # type: ignore[arg-type]
        assert refused.value.code == "approval.subject_mismatch"
    with pytest.raises(ApprovalDeniedError) as refused:  # the same item cannot be decided twice
        service.record(_human(stack), **base, decision=ApprovalDecision.REJECTED, subject_ref=ref)  # type: ignore[arg-type]
    assert refused.value.code == "approval.duplicate"
    assert [e.details["code"] for e in stack.audit("approval.verify_failed")] == [
        "approval.subject_mismatch"
    ] * 3
