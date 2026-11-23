"""D.4 run-control API (LG-08): start, read, control, gates, events, trace, history, metrics.

The API only writes jobs and flags (D-41); these tests check the API side. Where a run must be waiting
at a gate, the test puts it there the way the worker does (status and pending gate), because the worker
is not running inside these tests.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from devteam_core.config import Settings
from devteam_core.contracts.statuses import RunStatus
from devteam_state import create_sqlite_engine, session_factory
from devteam_state.models import Run, RunJob
from tests.conftest import audit_rows, bearer, create_project, upload
from tests.fixtures import brds

pytestmark = pytest.mark.integration


def _db(settings: Settings) -> Any:
    engine = create_sqlite_engine(settings.state_db_path)
    return engine, session_factory(engine)


def _jobs(settings: Settings, run_id: str) -> list[tuple[str, str, dict[str, Any]]]:
    engine, sessions = _db(settings)
    try:
        with sessions() as s:
            return [
                (j.kind, j.status, dict(j.payload))
                for j in s.scalars(select(RunJob).where(RunJob.run_id == run_id))
            ]
    finally:
        engine.dispose()


def _set_run(settings: Settings, run_id: str, **values: Any) -> None:
    engine, sessions = _db(settings)
    try:
        with sessions.begin() as s:
            run = s.get(Run, run_id)
            assert run is not None
            for key, value in values.items():
                setattr(run, key, value)
    finally:
        engine.dispose()


def _project_with_brd(client: TestClient) -> str:
    project_id = create_project(client, "Library")
    result = upload(client, project_id, ("library.md", brds.brd_markdown()))
    assert result["results"][0]["status"] == "extracted"
    return project_id


def _start(client: TestClient, project_id: str) -> dict[str, Any]:
    response = client.post(f"/api/projects/{project_id}/runs", json={}, headers=bearer(client))
    assert response.status_code == 201, response.text
    return dict(response.json())


def test_a_run_needs_a_document_and_only_one_runs_at_a_time(
    client: TestClient, isolated_settings: Settings
) -> None:
    empty = create_project(client, "Empty")
    refused = client.post(f"/api/projects/{empty}/runs", json={}, headers=bearer(client))
    assert (refused.status_code, refused.json()["detail"]["code"]) == (409, "run.no_document")

    project_id = _project_with_brd(client)
    run = _start(client, project_id)
    assert run["status"] == "created" and run["mode"] == "stub" and run["verified"] is False
    assert "nothing built, tested or verified" in run["label"]
    assert _jobs(isolated_settings, run["id"]) == [("start", "queued", {})]
    second = client.post(f"/api/projects/{project_id}/runs", json={}, headers=bearer(client))
    assert (second.status_code, second.json()["detail"]["code"]) == (409, "run.active")
    change = client.post(
        f"/api/projects/{project_id}/runs", json={"run_kind": "change_request"}, headers=bearer(client)
    )
    assert change.json()["detail"]["code"] == "run.change_unavailable"
    actions = [r.action for r in audit_rows(isolated_settings)]
    assert "run.started" in actions and "run.start" in actions


def test_read_list_and_cancel_a_waiting_run(client: TestClient, isolated_settings: Settings) -> None:
    project_id = _project_with_brd(client)
    run = _start(client, project_id)
    base = f"/api/projects/{project_id}/runs/{run['id']}"
    detail = client.get(base, headers=bearer(client)).json()
    assert detail["total_tasks"] == 0 and detail["last_event_seq"] == 0
    assert [
        r["id"] for r in client.get(f"/api/projects/{project_id}/runs", headers=bearer(client)).json()
    ] == [run["id"]]

    paused = client.post(f"{base}/pause", headers=bearer(client))
    assert (paused.status_code, paused.json()["detail"]["code"]) == (409, "run.not_running")
    cancelled = client.post(f"{base}/cancel", headers=bearer(client))
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    again = client.post(f"{base}/cancel", headers=bearer(client))
    assert again.json()["detail"]["code"] == "run.finished"
    history = client.get(f"{base}/history", headers=bearer(client)).json()
    assert {"run.started", "run.transitioned"} <= {h["action"] for h in history}


def test_a_running_run_is_paused_or_cancelled_cooperatively(
    client: TestClient, isolated_settings: Settings
) -> None:
    project_id = _project_with_brd(client)
    run = _start(client, project_id)
    _set_run(isolated_settings, run["id"], status=RunStatus.RUNNING.value)
    base = f"/api/projects/{project_id}/runs/{run['id']}"
    assert client.post(f"{base}/pause", headers=bearer(client)).json()["control_request"] == "pause"
    assert client.post(f"{base}/cancel", headers=bearer(client)).json()["control_request"] == "cancel"
    assert client.get(base, headers=bearer(client)).json()["status"] == "running"  # the worker applies it
    _set_run(isolated_settings, run["id"], status=RunStatus.PAUSED.value, control_request="none")
    resumed = client.post(f"{base}/resume", headers=bearer(client))
    assert resumed.status_code == 200
    assert ("resume", "queued", {}) in _jobs(isolated_settings, run["id"])


def test_gate_decisions_go_through_the_approval_service(
    client: TestClient, isolated_settings: Settings
) -> None:
    project_id = _project_with_brd(client)
    run = _start(client, project_id)
    base = f"/api/projects/{project_id}/runs/{run['id']}"
    assert client.get(f"{base}/gate", headers=bearer(client)).json()["detail"]["code"] == "gate.not_pending"
    _set_run(
        isolated_settings,
        run["id"],
        status=RunStatus.WAITING_FOR_HUMAN.value,
        pending_gate="feasibility_decision",
    )

    gate = client.get(f"{base}/gate", headers=bearer(client)).json()
    assert gate["gate"] == "feasibility_decision" and gate["approval_type"] == "feasibility_decision"
    assert any("not evaluated" in note for note in gate["notes"])

    wrong = client.post(
        f"{base}/gates/project_approval/decision", json={"decision": "approved"}, headers=bearer(client)
    )
    assert wrong.json()["detail"]["code"] == "gate.not_pending"
    unknown = client.post(
        f"{base}/gates/self_destruct/decision", json={"decision": "approved"}, headers=bearer(client)
    )
    assert unknown.status_code == 404
    forged_field = client.post(
        f"{base}/gates/feasibility_decision/decision",
        json={"decision": "approved", "approval_id": "apr_x"},
        headers=bearer(client),
    )
    assert forged_field.status_code == 422  # the client cannot send an approval ID

    decided = client.post(
        f"{base}/gates/feasibility_decision/decision", json={"decision": "approved"}, headers=bearer(client)
    )
    assert decided.status_code == 200, decided.text
    approval_id = decided.json()["approval_id"]
    assert (
        "gate_decision",
        "queued",
        {"run_id": run["id"], "gate": "feasibility_decision", "approval_id": approval_id},
    ) in _jobs(isolated_settings, run["id"])
    duplicate = client.post(
        f"{base}/gates/feasibility_decision/decision", json={"decision": "approved"}, headers=bearer(client)
    )
    assert (duplicate.status_code, duplicate.json()["detail"]["code"]) == (403, "approval.duplicate")
    actions = [(r.action, r.outcome) for r in audit_rows(isolated_settings)]
    assert ("approval.recorded", "success") in actions and ("approval.denied", "denied") in actions


def test_project_approval_needs_a_draft_spec(client: TestClient, isolated_settings: Settings) -> None:
    project_id = _project_with_brd(client)
    run = _start(client, project_id)
    _set_run(
        isolated_settings,
        run["id"],
        status=RunStatus.WAITING_FOR_HUMAN.value,
        pending_gate="project_approval",
    )
    response = client.post(
        f"/api/projects/{project_id}/runs/{run['id']}/gates/project_approval/decision",
        json={"decision": "approved"},
        headers=bearer(client),
    )
    assert response.json()["detail"]["code"] == "gate.no_spec"


def test_events_stream_trace_and_metrics(client: TestClient, isolated_settings: Settings) -> None:
    project_id = _project_with_brd(client)
    run = _start(client, project_id)
    base = f"/api/projects/{project_id}/runs/{run['id']}"
    client.post(f"{base}/cancel", headers=bearer(client))
    assert client.get(f"{base}/events?after=0", headers=bearer(client)).json() == []
    with client.stream("GET", f"{base}/events/stream", headers=bearer(client)) as stream:
        assert stream.headers["content-type"].startswith("text/event-stream")
        assert stream.headers["cache-control"] == "no-cache"
        body = "".join(stream.iter_text())
    assert "event: end" in body
    trace = client.get(f"{base}/trace", headers=bearer(client)).json()
    assert trace["spans"] == []
    metrics = client.get(f"{base}/metrics", headers=bearer(client)).json()
    assert metrics["model_calls"] == 0 and metrics["nodes"] == []
    assert client.get(f"{base}/tasks", headers=bearer(client)).json() == []


def test_runs_are_project_scoped(client: TestClient) -> None:
    a = _project_with_brd(client)
    b = create_project(client, "Other")
    run = _start(client, a)
    for path in ("", "/tasks", "/events", "/trace", "/history", "/metrics", "/gate"):
        assert (
            client.get(f"/api/projects/{b}/runs/{run['id']}{path}", headers=bearer(client)).status_code == 404
        )
    assert (
        client.post(f"/api/projects/{b}/runs/{run['id']}/cancel", headers=bearer(client)).status_code == 404
    )
    assert client.get(f"/api/projects/{a}/runs/not-a-run", headers=bearer(client)).status_code == 404


def test_a_refused_decision_is_a_403_and_is_audited(client: TestClient, isolated_settings: Settings) -> None:
    """The Approval Service refuses a publish approval that is not bound to a frozen spec (HC-13)."""
    project_id = _project_with_brd(client)
    run = _start(client, project_id)
    _set_run(
        isolated_settings,
        run["id"],
        status=RunStatus.WAITING_FOR_HUMAN.value,
        pending_gate="publish_approval",
    )
    response = client.post(
        f"/api/projects/{project_id}/runs/{run['id']}/gates/publish_approval/decision",
        json={"decision": "approved"},
        headers=bearer(client),
    )
    assert (response.status_code, response.json()["detail"]["code"]) == (403, "approval.unbound")
    assert [j for j in _jobs(isolated_settings, run["id"]) if j[0] == "gate_decision"] == []  # nothing queued
    rows = audit_rows(isolated_settings)
    assert ("approval.denied", "denied") in [(r.action, r.outcome) for r in rows]
    assert rows[-1].action == "run.gate_decision" and rows[-1].outcome == "denied"
