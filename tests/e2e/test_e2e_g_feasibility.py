"""E2E-G (Part G): a real run reaches the Feasibility Gate and the decision is recorded and audited.

The API (in-process) starts the run; a real ``python -m devteam_worker`` process runs the real Part D BRD
Analyst through the real gateway and the local model, inside the loopback-only network jail. The real
feasibility node then evaluates the stored analysis. The Part D analyst extracts no technologies (Part H),
so the honest report says "No technology needs identified" (D-65, D-38: nothing is simulated here). The test
records the feasibility decision through the API, waits until the worker has consumed it, then cancels.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from devteam_core.config import Settings
from tests.conftest import audit_rows, bearer, create_project, upload
from tests.e2e.test_e2e_2_run import _worker_env
from tests.fixtures import brds
from tests.fixtures.harness import REPO_ROOT

pytestmark = [pytest.mark.llm, pytest.mark.slow]
DEADLINE_S = 1_200


def test_a_real_run_pauses_at_the_feasibility_gate_with_an_honest_report(  # noqa: PLR0915 - one scenario
    make_client: Callable[..., TestClient], isolated_settings: Settings, tmp_path: Path
) -> None:
    client = make_client(database={"busy_timeout_ms": 15000})
    project_id = create_project(client, "Library G")
    assert (
        upload(client, project_id, ("library.md", brds.brd_markdown()))["results"][0]["status"] == "extracted"
    )
    run = client.post(f"/api/projects/{project_id}/runs", json={}, headers=bearer(client)).json()
    base = f"/api/projects/{project_id}/runs/{run['id']}"
    net_log = tmp_path / "net.log"
    worker_log = tmp_path / "worker.log"
    with worker_log.open("wb") as handle:
        worker = subprocess.Popen(
            [sys.executable, "-m", "devteam_worker"], cwd=REPO_ROOT,
            env=_worker_env(isolated_settings, net_log),
            stdout=handle, stderr=subprocess.STDOUT,
        )  # fmt: skip
    try:
        deadline = time.monotonic() + DEADLINE_S
        detail: dict[str, object] = {}
        while time.monotonic() < deadline:
            if worker.poll() is not None:
                pytest.fail(
                    "worker exited: " + worker_log.read_text(encoding="utf-8", errors="replace")[-2000:]
                )
            detail = client.get(base, headers=bearer(client)).json()
            if detail["status"] == "waiting_for_human" or detail["status"] in {
                "blocked",
                "failed",
                "cancelled",
            }:
                break
            time.sleep(2)
        assert detail["status"] == "waiting_for_human" and detail["pending_gate"] == "feasibility_decision", (
            worker_log.read_text(encoding="utf-8", errors="replace")[-4000:]
        )

        report = client.get(f"{base}/feasibility", headers=bearer(client)).json()
        assert report["needs_identified"] is False and report["items"] == []  # honest: nothing extracted
        assert report["blocked_keys"] == [] and report["proceed_allowed"] is True
        assert report["recommendation"] in {"proceed", "not_applicable"}
        assert "react_vite" in {d["key"] for d in report["demoted"]}  # no verified kit in this installation
        download = client.get(f"{base}/feasibility/report.md", headers=bearer(client))
        assert download.status_code == 200 and "No technology needs identified" in download.text
        workspace_file = isolated_settings.workspaces_dir / project_id / "reports" / "FEASIBILITY_REPORT.md"
        assert (
            hashlib.sha256(workspace_file.read_bytes()).digest()
            == hashlib.sha256(download.text.encode("utf-8")).digest()
        )  # the workspace file, the record and the download are the same bytes
        gate = client.get(f"{base}/gate", headers=bearer(client)).json()
        assert gate["gate"] == "feasibility_decision" and "feasibility report" in gate["notes"][0]

        answer = client.post(
            f"{base}/gates/feasibility_decision/decision",
            json={"decision": "approved"},
            headers=bearer(client),
        )
        assert answer.status_code == 200, answer.text
        approval_id = answer.json()["approval_id"]
        while time.monotonic() < deadline:  # the worker consumes the decision and leaves the gate
            detail = client.get(base, headers=bearer(client)).json()
            if detail["pending_gate"] != "feasibility_decision":
                break
            time.sleep(2)
        assert detail["pending_gate"] != "feasibility_decision", detail
        client.post(f"{base}/cancel", headers=bearer(client))
    finally:
        if worker.poll() is None:
            for child in psutil.Process(worker.pid).children(recursive=True):
                child.kill()
            worker.kill()
            worker.wait(timeout=30)

    rows = audit_rows(isolated_settings)
    evaluated = [r for r in rows if r.action == "feasibility.evaluated"]
    assert len(evaluated) == 1 and evaluated[0].principal_id == "service:feasibility_gate"
    assert evaluated[0].details["report_id"] == report["report_id"]
    recorded = [r for r in rows if r.action == "approval.recorded" and r.target_id == approval_id]
    assert recorded and recorded[0].details["approval_type"] == "feasibility_decision"
    assert not net_log.exists() or net_log.read_text(encoding="utf-8") == ""  # no outbound connection
