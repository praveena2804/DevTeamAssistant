"""E2E-3 (AC-E-01, AC-E-03): API queues starter-kit verifications → a real worker process → integration
service → sandbox (real engine) → results visible through the API, under the ``low_memory`` profile.

* The API (in-process ``TestClient``) only queues requests; it never contacts Docker.
* A separate ``python -m devteam_worker --once`` process, started with ``DEVTEAM_PROFILE=low_memory`` and the
  real Windows Credential Manager, claims and runs both verifications, then exits.
* Every container ran with the ``low_memory`` limits, and the measured peak memory stays inside them.
* The client-key passphrase never appears in the worker's log or in any API response (checked as booleans,
  so a failure can never print it).
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from devteam_core.config import Settings, load_settings
from devteam_harness.sandbox.tls import PASSPHRASE_REF
from devteam_harness.secrets import SecretsManager
from tests.conftest import bearer
from tests.fixtures.harness import REPO_ROOT

pytestmark = [pytest.mark.docker, pytest.mark.slow]
KITS = ("html_fastapi_sqlite", "react_fastapi_sqlite")
WORKER_TIMEOUT_S = 2_400


def _prepare_root(settings: Settings, live: Settings) -> None:
    """The throw-away root gets copies of what a real installation has: kits, image lock, TLS material."""
    shutil.copytree(REPO_ROOT / "templates", settings.root / "templates")
    lock = settings.sandbox_image_lock_path
    lock.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(live.sandbox_image_lock_path, lock)
    settings.sandbox_tls_dir.mkdir(parents=True)
    for item in live.sandbox_tls_dir.iterdir():
        shutil.copy2(item, settings.sandbox_tls_dir / item.name)


def _worker_env(settings: Settings) -> dict[str, str]:
    variables = {k: v for k, v in os.environ.items() if not k.startswith(("DEVTEAM_", "LANGSMITH_"))}
    variables.update(
        {
            "DEVTEAM_ROOT": str(settings.root),
            "DEVTEAM_PROFILE": "low_memory",  # AC-E-03: the 16 GB laptop profile
            "DEVTEAM_DATABASE__BUSY_TIMEOUT_MS": "15000",
        }
    )
    return variables


def test_verifications_queued_through_the_api_run_in_a_real_worker(
    make_client: Callable[..., TestClient],
    isolated_settings: Settings,
    live_settings: Settings,
    live_secrets: SecretsManager,
    wsl_keepalive: None,
    tmp_path: Path,
) -> None:
    _prepare_root(isolated_settings, live_settings)
    client = make_client()
    headers = bearer(client)
    before = client.get("/api/sandbox/status", headers=headers).json()
    assert before["reachable"] is None  # the API never contacted the engine itself

    queued: dict[str, str] = {}
    for kit in KITS:
        response = client.post(f"/api/sandbox/templates/{kit}/verifications", headers=headers)
        assert response.status_code == 201, response.text
        queued[kit] = response.json()["verification_id"]

    worker_log = tmp_path / "worker.log"
    with worker_log.open("wb") as handle:
        proc = subprocess.run(
            [sys.executable, "-m", "devteam_worker", "--once"],
            cwd=REPO_ROOT,
            env=_worker_env(isolated_settings),
            stdout=handle,
            stderr=subprocess.STDOUT,
            timeout=WORKER_TIMEOUT_S,
            check=False,
        )
    log_text = worker_log.read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, log_text[-4000:]

    low_memory = load_settings(root=isolated_settings.root, profile="low_memory", environ={})
    ceiling = low_memory.sandbox.limits.memory_mb
    assert ceiling == 1536
    bodies: list[str] = []
    for kit, verification_id in queued.items():
        response = client.get(f"/api/sandbox/verifications/{verification_id}", headers=headers)
        bodies.append(response.text)
        result = response.json()
        detail = [(r["recipe"], r["status"], r["reason"]) for r in result["recipes"]]
        assert result["status"] == "passed", (kit, detail, log_text[-3000:])
        assert result["recipes"] and all(r["status"] == "completed" and r["exit_code"] == 0
                                         for r in result["recipes"]), detail  # fmt: skip
        assert result["peak_memory_mb"] is not None and 0 < result["peak_memory_mb"] <= ceiling
        assert all(r["peak_memory_mb"] is None or r["peak_memory_mb"] <= ceiling for r in result["recipes"])
        tests = {r["recipe"]: r["tests"] for r in result["recipes"] if r["tests"] is not None}
        assert tests["pytest_api"]["passed"] == 3 and tests["pytest_api"]["failed"] == 0
        assert tests["pytest_db"]["passed"] == 2 and tests["pytest_db"]["failed"] == 0
        if kit == "react_fastapi_sqlite":
            assert tests["vitest_run"]["passed"] == 3
        print(
            f"\nE2E-3 {kit}: {result['duration_ms'] / 1000:.1f}s, peak {result['peak_memory_mb']} MB "
            f"(limit {ceiling} MB)"
        )

    kits = {k["template_id"]: k for k in client.get("/api/sandbox/templates", headers=headers).json()}
    assert all(kits[kit]["latest"]["status"] == "passed" for kit in KITS)
    status = client.get("/api/sandbox/status", headers=headers).json()
    assert status["configured"] is True and status["reachable"] is True  # written by the worker
    bodies.append(str(status))

    # every container ran with the low_memory limits (from the execution records)
    with sqlite3.connect(isolated_settings.state_db_path) as conn:
        memory = [
            r[0]
            for r in conn.execute(
                "SELECT json_extract(payload, '$.limits.memory_mb') FROM sandbox_executions"
            )
        ]
        actions = {row[0] for row in conn.execute("SELECT action FROM audit_events")}
    assert memory and set(memory) == {ceiling}
    assert {"sandbox.verification_request", "sandbox.executed"} <= actions

    secret = live_secrets.require(PASSPHRASE_REF).reveal()
    leaked = [secret in log_text, *(secret in body for body in bodies)]
    del secret
    assert not any(leaked)  # booleans only: a failure can never print the value
