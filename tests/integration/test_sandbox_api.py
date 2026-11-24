"""Part E sandbox API (plan §3.9; D-49): status, starter kits, queued verification requests.

The API never contacts Docker: these tests run without an engine. Status comes from the worker's status
file; a verification request only queues a row (the worker runs it, see tests/sandbox/test_e2e_3_sandbox.py).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from devteam_core.config import Settings
from tests.conftest import audit_rows, bearer

pytestmark = pytest.mark.integration


def test_status_before_the_worker_checked(client: TestClient) -> None:
    body = client.get("/api/sandbox/status", headers=bearer(client)).json()
    assert body["reachable"] is None and body["lock_holder"] is None
    assert {image["name"] for image in body["images"]} <= {"python_api", "node_web"}


def test_status_reflects_the_worker_status_file(client: TestClient, isolated_settings: Settings) -> None:
    path = isolated_settings.data_dir / "sandbox_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "configured": True,
                "reachable": False,
                "reason": "sandbox.docker_unavailable: x",
                "checked_at": "2026-11-23T22:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    body = client.get("/api/sandbox/status", headers=bearer(client)).json()
    assert body["reachable"] is False and body["reason"].startswith("sandbox.docker_unavailable")


def test_starter_kits_are_listed(client: TestClient) -> None:
    kits = client.get("/api/sandbox/templates", headers=bearer(client)).json()
    assert {kit["template_id"] for kit in kits} == {"html_fastapi_sqlite", "react_fastapi_sqlite"}
    assert all(kit["latest"] is None for kit in kits)
    react = next(kit for kit in kits if kit["template_id"] == "react_fastapi_sqlite")
    assert react["self_test"][-1] == "npm_build"


def test_verification_is_queued_once_and_audited(client: TestClient, isolated_settings: Settings) -> None:
    url = "/api/sandbox/templates/html_fastapi_sqlite/verifications"
    first = client.post(url, headers=bearer(client))
    assert first.status_code == 201 and first.json()["status"] == "queued"
    verification_id = first.json()["verification_id"]
    assert client.post(url, headers=bearer(client)).status_code == 409  # one active request per kit
    read = client.get(f"/api/sandbox/verifications/{verification_id}", headers=bearer(client)).json()
    assert read["status"] == "queued" and read["recipes"] == []
    kits = client.get("/api/sandbox/templates", headers=bearer(client)).json()
    html = next(kit for kit in kits if kit["template_id"] == "html_fastapi_sqlite")
    assert html["latest"]["verification_id"] == verification_id
    rows = [row for row in audit_rows(isolated_settings) if row.action == "sandbox.verification_request"]
    assert [row.outcome for row in rows] == ["success", "error"]  # the refused duplicate is audited too
    assert rows[0].target_id == "html_fastapi_sqlite"


@pytest.mark.parametrize(
    ("method", "url", "status"),
    [
        ("POST", "/api/sandbox/templates/angular_dotnet/verifications", 404),
        ("POST", "/api/sandbox/templates/..%2F..%2Fetc/verifications", 404),
        ("GET", "/api/sandbox/verifications/tvf_not-an-id", 404),
        ("GET", "/api/sandbox/verifications/tvf_01JD3X5Q0000000000000000AA", 404),
    ],
)
def test_unknown_kits_and_verifications(client: TestClient, method: str, url: str, status: int) -> None:
    assert client.request(method, url, headers=bearer(client)).status_code == status


def test_sign_in_required(client: TestClient) -> None:
    assert client.get("/api/sandbox/status").status_code == 401
    assert client.post("/api/sandbox/templates/html_fastapi_sqlite/verifications").status_code == 401
