"""Part G G.5 API: the feasibility report of a run (read-only, project-isolated), the byte-exact report
download, and the gate decision v2 (D-01 cancel/revise, D-63 low-coverage acknowledgement) over HTTP."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from devteam_core.config import Settings
from devteam_core.contracts.statuses import RunStatus, Stage
from devteam_core.ids import new_id
from devteam_harness.approvals import ApprovalService, load_approval_rules
from devteam_services.feasibility import FeasibilityGate
from devteam_state import create_sqlite_engine, session_factory
from devteam_state.repositories import runs
from tests.conftest import REPO_ROOT, audit_rows, bearer, create_project
from tests.fixtures.feasibility_engine import fixture, snapshot

pytestmark = pytest.mark.integration


def make_run(
    client: TestClient, settings: Settings, scenario: str | None, *, waiting: bool = True
) -> tuple[str, str]:
    project_id = create_project(client, new_id("nam"))
    engine = create_sqlite_engine(settings.state_db_path)
    sessions = session_factory(engine)
    try:
        with sessions.begin() as session:
            run = runs.create_run(session, project_id=project_id, run_kind="initial", stage=Stage.FEASIBILITY)
            if waiting:
                run.status = RunStatus.WAITING_FOR_HUMAN.value
                run.pending_gate = "feasibility_decision"
            run_id = run.id
        if scenario is not None:
            gate = FeasibilityGate(
                sessions, ApprovalService(load_approval_rules(REPO_ROOT / "config"), sessions)
            )
            gate.evaluate(
                project_id=project_id, run_id=run_id, analysis_task_id=new_id("tsk"),
                analysis=fixture(scenario, project_id, run_id), snapshot=snapshot(),
                write=lambda _p, _t: None,
            )  # fmt: skip
    finally:
        engine.dispose()
    return project_id, run_id


def url(project_id: str, run_id: str, tail: str = "/feasibility") -> str:
    return f"/api/projects/{project_id}/runs/{run_id}{tail}"


def test_the_report_is_returned_with_items_effects_and_the_formula(
    client: TestClient, isolated_settings: Settings
) -> None:
    project_id, run_id = make_run(client, isolated_settings, "mixed")
    body = client.get(url(project_id, run_id), headers=bearer(client)).json()
    assert (body["numerator"], body["denominator"], body["coverage_pct"]) == (0, 5, 0.0)
    assert body["recommendation"] == "stop_recommended" and body["needs_acknowledgement"]
    assert set(body["blocked_keys"]) == {"FR-003", "FR-004"}
    items = {i["subject_name"]: i for i in body["items"]}
    assert items["Stripe"]["status"] == "not_supported" and items["Stripe"]["requirement_ids"] == ["FR-003"]
    assert items["a relational database"]["alternative"] == "sqlite"
    assert items["a relational database"]["gap"]["kind"] == "unclear"
    assert {e["requirement_key"] for e in body["effects"]} >= {"FR-001", "NFR-001", "BR-001"}


def test_the_download_is_byte_exact_and_a_changed_record_is_refused(
    client: TestClient, isolated_settings: Settings
) -> None:
    project_id, run_id = make_run(client, isolated_settings, "power_apps")
    response = client.get(url(project_id, run_id, "/feasibility/report.md"), headers=bearer(client))
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/markdown")
    assert "= 0 / 5 = 0.0%" in response.text and "Power Apps" in response.text
    engine = create_sqlite_engine(isolated_settings.state_db_path)
    with session_factory(engine).begin() as session:  # negative control: tamper around the triggers
        session.execute(text("DROP TRIGGER feasibility_reports_no_update"))
        session.execute(
            text("UPDATE feasibility_reports SET payload = json_set(payload, '$.threshold_pct', 40)")
        )
    engine.dispose()
    refused = client.get(url(project_id, run_id, "/feasibility/report.md"), headers=bearer(client))
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "feasibility.checksum_mismatch"


def test_another_projects_report_is_not_found(client: TestClient, isolated_settings: Settings) -> None:
    project_id, run_id = make_run(client, isolated_settings, "angular")
    other = create_project(client, "Other")
    for tail in ("/feasibility", "/feasibility/report.md"):
        assert client.get(url(other, run_id, tail), headers=bearer(client)).status_code == 404
    _, no_report = make_run(client, isolated_settings, None)
    missing = client.get(url(project_id, no_report), headers=bearer(client))
    assert missing.status_code == 404


def _decide(client: TestClient, project_id: str, run_id: str, **body: Any) -> Any:
    return client.post(
        url(project_id, run_id, "/gates/feasibility_decision/decision"), headers=bearer(client), json=body
    )


def test_decision_v2_needs_the_acknowledgement_below_the_threshold(
    client: TestClient, isolated_settings: Settings
) -> None:
    project_id, run_id = make_run(client, isolated_settings, "angular")
    refused = _decide(client, project_id, run_id, decision="approved")
    assert (
        refused.status_code == 422
        and refused.json()["detail"]["code"] == "feasibility.acknowledgement_required"
    )
    accepted = _decide(client, project_id, run_id, decision="approved", acknowledge_low_coverage=True)
    assert accepted.status_code == 200 and accepted.json()["decision"] == "approved"
    actions = [r.action for r in audit_rows(isolated_settings)]
    assert "feasibility.low_coverage_acknowledged" in actions and "approval.recorded" in actions


def test_nothing_to_build_refuses_proceed_and_accepts_revise(
    client: TestClient, isolated_settings: Settings
) -> None:
    project_id, run_id = make_run(client, isolated_settings, "power_apps")
    refused = _decide(client, project_id, run_id, decision="approved", acknowledge_low_coverage=True)
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "feasibility.proceed_disabled"
    declined = _decide(client, project_id, run_id, decision="rejected", reason="revise")
    assert declined.status_code == 200
    reason = [r for r in audit_rows(isolated_settings) if r.action == "feasibility.decision_reason"]
    assert reason[0].details["reason"] == "revise"


def test_unknown_decision_fields_are_refused(client: TestClient, isolated_settings: Settings) -> None:
    project_id, run_id = make_run(client, isolated_settings, "angular")
    bad = _decide(client, project_id, run_id, decision="approved", substitution="react_vite")
    assert bad.status_code == 422
    bad_reason = _decide(client, project_id, run_id, decision="rejected", reason="later")
    assert bad_reason.status_code == 422


def test_reading_the_report_never_writes(client: TestClient, isolated_settings: Settings) -> None:
    project_id, run_id = make_run(client, isolated_settings, "none")
    before = len(audit_rows(isolated_settings))
    client.get(url(project_id, run_id), headers=bearer(client))
    assert len(audit_rows(isolated_settings)) == before
