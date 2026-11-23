"""E2E-2 (AC-D-01): a full Part D run through the API and a real worker process with the local model.

* The API (in-process ``TestClient``) starts the run and records the three gate decisions; it never runs
  the graph. A separate ``python -m devteam_worker`` process executes it with the real Ollama provider.
* The worker runs inside a loopback-only network jail: any non-local connection is refused and recorded;
  the run must make none.
* A canary sentence in the BRD must not appear in the checkpoints, run events, the SSE stream, trace spans,
  the audit log or the worker's log (state is references only; events and spans are metadata only).
* Progress events arrive over SSE in order with no gaps; the final report says "not verified".
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from devteam_core.config import Settings
from tests.conftest import bearer, create_project, upload
from tests.fixtures import brds
from tests.fixtures.harness import REPO_ROOT

pytestmark = [pytest.mark.llm, pytest.mark.slow]
CANARY = "Quillfeather-7731 lending clause"
JAIL = REPO_ROOT / "tests" / "fixtures" / "loopback_only"
DEADLINE_S = 1_800


def _worker_env(settings: Settings, net_log: Path) -> dict[str, str]:
    variables = {
        k: v for k, v in os.environ.items() if not k.startswith(("DEVTEAM_", "LANGSMITH_", "LANGCHAIN_"))
    }
    variables.update(
        {
            "DEVTEAM_ROOT": str(settings.root),
            "DEVTEAM_PROFILE": "test",
            "DEVTEAM_DATABASE__BUSY_TIMEOUT_MS": "15000",
            "E2E_NET_JAIL_LOG": str(net_log),
            "PYTHONPATH": os.pathsep.join([str(JAIL), os.environ.get("PYTHONPATH", "")]),
        }
    )
    return variables


def _brd_with_canary() -> bytes:
    return brds.brd_markdown() + f"\n\n## Notes\n\nThe {CANARY} applies to rare books only.\n".encode()


def test_full_run_through_the_api_and_a_real_worker(  # noqa: PLR0915 - one end-to-end scenario
    make_client: Callable[..., TestClient], isolated_settings: Settings, tmp_path: Path
) -> None:
    client = make_client(database={"busy_timeout_ms": 15000})
    project_id = create_project(client, "Library")
    assert (
        upload(client, project_id, ("library.md", _brd_with_canary()))["results"][0]["status"] == "extracted"
    )
    run = client.post(f"/api/projects/{project_id}/runs", json={}, headers=bearer(client)).json()
    base = f"/api/projects/{project_id}/runs/{run['id']}"

    net_log = tmp_path / "net.log"
    worker_log = tmp_path / "worker.log"
    env = _worker_env(isolated_settings, net_log)
    probe = subprocess.run(
        [sys.executable, "-c", "import socket; socket.create_connection(('192.0.2.1', 80), timeout=2)"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    assert probe.returncode != 0 and "blocked" in probe.stderr  # negative control: the jail is active
    net_log.unlink()

    with worker_log.open("wb") as handle:
        worker = subprocess.Popen(
            [sys.executable, "-m", "devteam_worker"],
            cwd=REPO_ROOT,
            env=env,
            stdout=handle,
            stderr=subprocess.STDOUT,
        )
    decided: list[str] = []
    try:
        deadline = time.monotonic() + DEADLINE_S
        status = ""
        while time.monotonic() < deadline:
            if worker.poll() is not None:  # fail fast: the worker must keep running
                pytest.fail(
                    "worker exited: " + worker_log.read_text(encoding="utf-8", errors="replace")[-2000:]
                )
            detail = client.get(base, headers=bearer(client)).json()
            status = detail["status"]
            if status in {"completed", "blocked", "failed", "cancelled"}:
                break
            if status == "waiting_for_human" and detail["pending_gate"] and len(decided) < 3:
                gate = detail["pending_gate"]
                if not decided or decided[-1] != f"{gate}@{detail['updated_at']}":
                    info = client.get(f"{base}/gate", headers=bearer(client)).json()
                    if gate == "project_approval":
                        assert info["requirements"], info  # the person sees what they approve
                    answer = client.post(
                        f"{base}/gates/{gate}/decision", json={"decision": "approved"}, headers=bearer(client)
                    )
                    assert answer.status_code == 200, answer.text
                    decided.append(f"{gate}@{detail['updated_at']}")
            time.sleep(2)
        log_text = worker_log.read_text(encoding="utf-8", errors="replace")
        assert status == "completed", log_text[-4000:]
    finally:
        if worker.poll() is None:
            for child in psutil.Process(worker.pid).children(recursive=True):
                child.kill()
            worker.kill()
            worker.wait(timeout=30)

    assert [d.split("@")[0] for d in decided] == [
        "feasibility_decision",
        "project_approval",
        "publish_approval",
    ]
    assert not net_log.exists() or net_log.read_text(encoding="utf-8") == ""  # no outbound connection

    tasks = client.get(f"{base}/tasks", headers=bearer(client)).json()
    assert {t["status"] for t in tasks} == {"completed"} and len(tasks) == 9
    assert all(t["harness_verified"] is False for t in tasks)

    with client.stream("GET", f"{base}/events/stream", headers=bearer(client)) as stream:
        body = "".join(stream.iter_text())
    ids = [int(line[4:]) for line in body.splitlines() if line.startswith("id: ")]
    assert ids == list(range(1, len(ids) + 1))  # in order, no gaps
    assert "event: run.summary" in body and '"verified":false' in body
    assert "event: end" in body

    trace = client.get(f"{base}/trace", headers=bearer(client)).json()
    names = {s["name"] for s in trace["spans"]}
    assert {"graph.run", "graph.node.brd_analyst", "agent.execute", "llm.generate"} <= names
    metrics = client.get(f"{base}/metrics", headers=bearer(client)).json()
    assert metrics["model_calls"] >= 9 and metrics["output_tokens"] > 0

    # Canary: never in checkpoints, events, spans or audit (metadata only), the SSE stream or the worker log
    data = isolated_settings.data_dir
    needle = CANARY.encode("utf-8")
    for db_name, query in (
        ("checkpoints.db", "SELECT checkpoint FROM checkpoints"),
        ("checkpoints.db", "SELECT metadata FROM checkpoints"),
        ("checkpoints.db", "SELECT value FROM writes"),
        ("state.db", "SELECT payload FROM run_events"),
        ("state.db", "SELECT attributes || name FROM trace_spans"),
        ("state.db", "SELECT details FROM audit_events"),
    ):
        with sqlite3.connect(data / db_name) as conn:
            conn.text_factory = bytes  # search the stored bytes (checkpoints are binary msgpack)
            rows = [bytes(r[0]) if r[0] is not None else b"" for r in conn.execute(query)]
        assert rows, (db_name, query)
        assert not any(needle in r for r in rows), (db_name, query)
    assert CANARY not in body
    assert CANARY not in log_text
    with sqlite3.connect(data / "state.db") as conn:  # positive control: the canary did reach the BRD chunks
        assert any(CANARY in str(r[0]) for r in conn.execute("SELECT text FROM document_chunks"))
