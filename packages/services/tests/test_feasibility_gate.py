"""Feasibility Gate service: persistence, report, audit and substitution integrity (Part G G.3; AC-G-01,
AC-G-03, AC-G-04). A substitution is applied only with a verified approval bound to that item and checksum."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from devteam_core.contracts.agents import BrdAnalystOutput
from devteam_core.contracts.catalog import CatalogKey
from devteam_core.contracts.feasibility import CoverageRecommendation
from devteam_core.contracts.records import ApprovalDecision, SubstitutionState
from devteam_core.contracts.statuses import ApprovalType, Stage
from devteam_core.ids import new_id
from devteam_harness.approvals import ApprovalDeniedError, ApprovalService, load_approval_rules
from devteam_harness.iam.roles import Principal
from devteam_services import catalog as cat
from devteam_services.feasibility import FeasibilityGate, SubstitutionRefusedError
from devteam_services.feasibility.render import esc, render_report
from devteam_state.models import AuditEvent
from devteam_state.repositories import feasibility as repo
from devteam_state.repositories import runs
from tests.fixtures.feasibility_engine import COUNTED, fixture, snapshot
from tests.fixtures.harness import REPO_CONFIG, Stack, make_stack

R = CoverageRecommendation
K = CatalogKey
REPORT = "reports/FEASIBILITY_REPORT.md"


class Files:
    def __init__(self) -> None:
        self.written: dict[str, str] = {}

    def __call__(self, path: str, text: str) -> None:
        self.written[path] = text


@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return make_stack(tmp_path)


def gate(stack: Stack, threshold: int = 50) -> FeasibilityGate:
    approvals = ApprovalService(load_approval_rules(REPO_CONFIG), stack.sessions)
    return FeasibilityGate(stack.sessions, approvals, threshold_pct=threshold)


def run_gate(
    stack: Stack, name: str, snap: cat.CatalogSnapshot | None = None, threshold: int = 50
) -> tuple[repo.StoredReport, Files]:
    files = Files()
    stored = gate(stack, threshold).evaluate(
        project_id=stack.project_id, run_id=stack.run_id, analysis_task_id=new_id("tsk"),
        analysis=fixture(name, stack.project_id, stack.run_id), snapshot=snap or snapshot(), write=files,
    )  # fmt: skip
    return stored, files


def actions(stack: Stack, prefix: str) -> list[AuditEvent]:
    with stack.sessions() as session:
        rows = session.query(AuditEvent).order_by(AuditEvent.seq).all()
    return [r for r in rows if r.action.startswith(prefix)]


# ------------------------------------------------------------------------------ persistence and report


def test_power_apps_report_is_persisted_rendered_and_audited(stack: Stack) -> None:
    """AC-G-01 and AC-G-03: blocked requirements identified; the formula is in the report."""
    stored, files = run_gate(stack, "power_apps")
    report = stored.report
    assert report.recommendation is R.NOTHING_TO_BUILD and not report.proceed_allowed
    assert (report.numerator, report.denominator, report.coverage_pct) == (0, 5, 0.0)
    assert report.blocked_keys == COUNTED and report.needs_identified
    text = files.written[REPORT]
    assert hashlib.sha256(text.encode()).hexdigest() == report.report_checksum
    assert "= 0 / 5 = 0.0%   (threshold 50%)" in text  # AC-G-03: the formula with its numbers
    assert "**FR-001**: blocked by" in text and "Power Apps" in text and "Nothing can be built" in text
    with stack.sessions() as session:
        again = repo.latest_report(session, stack.project_id, stack.run_id)
    assert again is not None and again.report == report
    (event,) = actions(stack, "feasibility.evaluated")
    assert event.principal_id == "service:feasibility_gate" and event.details["coverage_pct"] == 0.0


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("all_supported", R.PROCEED),
        ("none", R.PROCEED),
        ("angular", R.STOP_RECOMMENDED),  # 0%, but the React offer can make it buildable (DV-034)
        ("mixed", R.STOP_RECOMMENDED),
        ("power_apps", R.NOTHING_TO_BUILD),
    ],
)
def test_the_recommendation_follows_coverage_and_threshold(stack: Stack, name: str, expected: R) -> None:
    stored, _ = run_gate(stack, name)
    assert stored.report.recommendation is expected
    assert stored.report.needs_acknowledgement is (expected is R.STOP_RECOMMENDED)
    assert stored.report.proceed_allowed is (expected is not R.NOTHING_TO_BUILD)


def test_exactly_the_threshold_proceeds_and_one_point_more_stops(stack: Stack) -> None:
    data = fixture("mixed", stack.project_id, stack.run_id).model_dump(mode="json")
    data["technology_needs"] = [
        n for n in data["technology_needs"] if n["layer"] in {"backend", "authentication"}
    ]
    analysis = BrdAnalystOutput.model_validate(data)  # FastAPI + Azure AD (FR-004) + Stripe (FR-003): 3 of 5
    for threshold, expected in ((60, R.PROCEED), (61, R.STOP_RECOMMENDED)):
        stored = gate(stack, threshold).evaluate(
            project_id=stack.project_id, run_id=stack.run_id, analysis_task_id=new_id("tsk"),
            analysis=analysis, snapshot=snapshot(), write=Files(),
        )  # fmt: skip
        assert stored.report.coverage_pct == 60.0 and stored.report.recommendation is expected


def test_no_requirements_means_coverage_is_not_applicable(stack: Stack) -> None:
    data = fixture("power_apps", stack.project_id, stack.run_id).model_dump(mode="json")
    data["requirements"] = [r for r in data["requirements"] if r["type"] == "non_functional"]
    data["business_rules"] = []
    stored = gate(stack).evaluate(
        project_id=stack.project_id, run_id=stack.run_id, analysis_task_id=new_id("tsk"),
        analysis=BrdAnalystOutput.model_validate(data), snapshot=snapshot(), write=(files := Files()),
    )  # fmt: skip
    assert stored.report.coverage_pct is None and stored.report.recommendation is R.NOT_APPLICABLE
    assert "= 0 / 0 = n/a" in files.written[REPORT]


def test_a_report_without_needs_says_so_plainly(stack: Stack) -> None:
    stored, files = run_gate(stack, "none")
    assert not stored.report.needs_identified and stored.report.coverage_pct == 100.0
    assert "No technology needs identified" in files.written[REPORT]


def test_an_analysis_of_another_project_is_refused(stack: Stack) -> None:
    with pytest.raises(ValueError, match="another project"):
        gate(stack).evaluate(
            project_id=stack.project_id, run_id=stack.run_id, analysis_task_id=new_id("tsk"),
            analysis=fixture("none"), snapshot=snapshot(), write=Files(),
        )  # fmt: skip


def test_a_rejected_model_key_is_audited_and_never_added(stack: Stack) -> None:
    data = fixture("angular", stack.project_id, stack.run_id).model_dump(mode="json")
    data["technology_needs"][0]["catalog_key"] = "angular_supported_now"
    stored = gate(stack).evaluate(
        project_id=stack.project_id, run_id=stack.run_id, analysis_task_id=new_id("tsk"),
        analysis=BrdAnalystOutput.model_validate(data), snapshot=snapshot(), write=Files(),
    )  # fmt: skip
    assert stored.items[0].catalog_key is K.ANGULAR  # the alias decides; the proposal is ignored
    (event,) = actions(stack, "catalog.key_rejected")
    assert event.outcome == "denied" and event.details["proposed"] == "angular_supported_now"


def test_demoted_catalog_entries_are_listed_in_the_report(stack: Stack) -> None:
    stored, files = run_gate(stack, "postgresql")
    assert {K.POSTGRESQL_DOCKER_LOCAL, K.GITHUB_PAGES} <= {d.key for d in stored.report.demoted}
    assert "`postgresql_docker_local`: not available yet: arrives in Part K" in files.written[REPORT]


def test_hostile_names_are_escaped(stack: Stack) -> None:
    assert (
        esc("<script>|`x`[a](b)*_#'") == "&#60;script&#62;&#124;&#96;x&#96;&#91;a&#93;(b)&#42;&#95;&#35;&#39;"
    )
    data = fixture("angular", stack.project_id, stack.run_id).model_dump(mode="json")
    data["technology_needs"][0]["name"] = "<img src=x onerror=alert(1)> | `rm`"
    files = Files()
    gate(stack).evaluate(
        project_id=stack.project_id, run_id=stack.run_id, analysis_task_id=new_id("tsk"),
        analysis=BrdAnalystOutput.model_validate(data), snapshot=snapshot(), write=files,
    )  # fmt: skip
    text = files.written[REPORT]
    assert "<img" not in text and "&#60;img src=x onerror=alert(1)&#62; &#124; &#96;rm&#96;" in text
    assert "&&#" not in text  # never double-escaped


def test_the_report_is_byte_stable_for_the_same_records(stack: Stack) -> None:
    stored, files = run_gate(stack, "mixed")
    assert render_report(stored.report, stored.items, stored.effects) == files.written[REPORT]
    # the database returns rows in its own order: the output must not depend on it (found in G.5)
    shuffled = render_report(stored.report, stored.items[::-1], stored.effects[::-1])
    assert shuffled == files.written[REPORT]


GOLDEN = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "feasibility" / "golden_report.md"


def test_the_rendering_matches_the_golden_file() -> None:
    """Fixed records → fixed bytes (re-create with DEVTEAM_UPDATE_GOLDEN=1 only for an intended change)."""
    import json  # noqa: PLC0415
    import os  # noqa: PLC0415

    from devteam_core.contracts.feasibility import FeasibilityReport, RequirementEffect  # noqa: PLC0415
    from devteam_core.contracts.records import FeasibilityItem  # noqa: PLC0415

    records = json.loads((GOLDEN.parent / "golden_records.json").read_text(encoding="utf-8"))
    report = FeasibilityReport.model_validate(records["report"])
    items = [FeasibilityItem.model_validate(i) for i in records["items"]]
    effects = [RequirementEffect.model_validate(e) for e in records["effects"]]
    text = render_report(report, items, effects)
    if os.environ.get("DEVTEAM_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(text, encoding="utf-8", newline="\n")
    assert text == GOLDEN.read_text(encoding="utf-8")


# ------------------------------------------------------------------- substitution integrity (AC-G-04)


def _human(stack: Stack) -> Principal:
    return Principal(id=stack.owner_id, kind="human", display_name="Owner", roles=frozenset({"project_user"}))


def _record(
    stack: Stack, ref: str, decision: ApprovalDecision = ApprovalDecision.APPROVED, run_id: str | None = None
) -> str:
    approvals = ApprovalService(load_approval_rules(REPO_CONFIG), stack.sessions)
    return approvals.record(
        _human(stack), project_id=stack.project_id, run_id=run_id or stack.run_id,
        approval_type=ApprovalType.SUBSTITUTION, decision=decision, subject_ref=ref,
    ).approval_id  # fmt: skip


def _apply(stack: Stack, item_id: str, approval_id: str) -> repo.StoredReport:
    return gate(stack).apply_substitution(
        project_id=stack.project_id, run_id=stack.run_id, item_id=item_id, approval_id=approval_id,
        analysis_task_id=new_id("tsk"), analysis=fixture("angular", stack.project_id, stack.run_id),
        snapshot=snapshot(), write=Files(),
    )  # fmt: skip


def _ref(stored: repo.StoredReport) -> tuple[str, str]:
    (item,) = stored.items
    return item.item_id, repo.substitution_ref(item.item_id, stored.checksums[item.item_id])


def test_no_substitution_is_applied_without_an_approval(stack: Stack) -> None:
    stored, _ = run_gate(stack, "angular")
    item_id, _ref_text = _ref(stored)
    with pytest.raises(ApprovalDeniedError) as refused:  # a forged approval ID
        _apply(stack, item_id, new_id("apr"))
    assert refused.value.code == "approval.unknown"
    again, _ = run_gate(stack, "angular")  # re-evaluation without an approval: still only offered
    assert again.items[0].substitution is SubstitutionState.OFFERED and again.report.numerator == 0


def test_a_rejected_approval_does_not_apply(stack: Stack) -> None:
    stored, _ = run_gate(stack, "angular")
    item_id, ref = _ref(stored)
    approval = _record(stack, ref, ApprovalDecision.REJECTED)
    with pytest.raises(ApprovalDeniedError) as refused:
        _apply(stack, item_id, approval)
    assert refused.value.code == "approval.not_approved"
    assert gate(stack).verified_substitutions(stack.project_id, stack.run_id) == {}


def test_an_approval_for_another_item_or_a_changed_item_does_not_apply(stack: Stack) -> None:
    first, _ = run_gate(stack, "angular")
    second, _ = run_gate(stack, "angular")
    _old_id, old_ref = _ref(first)
    new_id_, _new_ref = _ref(second)
    approval = _record(stack, old_ref)
    with pytest.raises(ApprovalDeniedError) as refused:  # bound to the first report's item
        _apply(stack, new_id_, approval)
    assert refused.value.code == "approval.subject_mismatch"
    stale = _record(stack, repo.substitution_ref(new_id_, "e" * 64))  # the checksum of a changed item
    with pytest.raises(ApprovalDeniedError) as refused:
        _apply(stack, new_id_, stale)
    assert refused.value.code == "approval.subject_mismatch"


def test_an_item_that_offers_nothing_or_does_not_exist_is_refused(stack: Stack) -> None:
    stored, _ = run_gate(stack, "power_apps")  # not supported: nothing offered
    item_id, ref = _ref(stored)
    approval = _record(stack, ref)
    with pytest.raises(SubstitutionRefusedError) as refused:
        _apply(stack, item_id, approval)
    assert refused.value.code == "substitution.not_offered"
    with pytest.raises(SubstitutionRefusedError) as unknown:
        _apply(stack, new_id("fea"), approval)
    assert unknown.value.code == "substitution.unknown_item"
    codes = [e.details["code"] for e in actions(stack, "feasibility.substitution_refused")]
    assert codes == ["substitution.not_offered"]


def test_an_approval_from_another_run_never_applies(stack: Stack) -> None:
    stored, _ = run_gate(stack, "angular")
    item_id, ref = _ref(stored)
    with stack.sessions.begin() as session:
        other = runs.create_run(
            session, project_id=stack.project_id, run_kind="initial", stage=Stage.INTAKE
        ).id
    approval = _record(stack, ref, run_id=other)
    with pytest.raises(ApprovalDeniedError) as refused:
        _apply(stack, item_id, approval)
    assert refused.value.code == "approval.wrong_run"
    assert gate(stack).verified_substitutions(stack.project_id, other) == {}  # the item is not that run's


def test_a_valid_item_bound_approval_applies_and_re_evaluates(stack: Stack) -> None:
    stored, _ = run_gate(stack, "angular")
    item_id, ref = _ref(stored)
    approval = _record(stack, ref)
    applied = _apply(stack, item_id, approval)
    assert applied.report.report_id != stored.report.report_id  # a new report; the old one is unchanged
    (new_item,) = applied.items
    assert new_item.substitution is SubstitutionState.APPROVED and new_item.alternative is K.REACT_VITE
    assert applied.report.numerator == 5 and applied.report.recommendation is R.PROCEED
    with stack.sessions() as session:
        old = repo.get_report(session, stack.project_id, stored.report.report_id)
    assert old is not None and old.items[0].substitution is SubstitutionState.OFFERED
    (event,) = actions(stack, "feasibility.substitution_applied")
    assert event.details["approval_id"] == approval and event.details["alternative"] == "react_vite"
    subject = stored.items[0].subject_id
    assert gate(stack).verified_substitutions(stack.project_id, stack.run_id) == {subject: K.REACT_VITE}


def test_a_hand_written_approval_row_is_never_honoured(stack: Stack) -> None:
    """Negative control at the database level: a row written around the Approval Service is refused."""
    from datetime import UTC, datetime  # noqa: PLC0415

    from devteam_state.models import ApprovalRow  # noqa: PLC0415

    stored, _ = run_gate(stack, "angular")
    _item_id, ref = _ref(stored)
    with stack.sessions.begin() as session:
        session.add(
            ApprovalRow(
                id=new_id("apr"), project_id=stack.project_id, run_id=stack.run_id,
                approval_type="substitution", decision_key=f"{stack.run_id}:substitution:{ref}",
                decision="approved", decided_by=stack.owner_id, decided_at=datetime.now(UTC),
                audit_event_id=new_id("aud"), subject_ref=ref,
            )
        )  # fmt: skip
    assert gate(stack).verified_substitutions(stack.project_id, stack.run_id) == {}
    assert actions(stack, "approval.verify_failed")[-1].details["code"] == "approval.forged"
