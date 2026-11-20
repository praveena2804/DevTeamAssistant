"""C.3: ``GET /api/audit/verify`` and ``scripts/verify_audit.py`` against a real (tampered) database."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from devteam_core.config import Settings
from tests.conftest import bearer, create_project
from tests.scripts.test_scripts import load_script

pytestmark = [pytest.mark.integration, pytest.mark.security]


def test_verify_requires_sign_in(client: TestClient) -> None:
    assert client.get("/api/audit/verify").status_code == 401


def test_verify_reports_intact_then_the_tampered_entry(
    client: TestClient,
    isolated_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_project(client, "Alpha")
    create_project(client, "Beta")
    intact = client.get("/api/audit/verify", headers=bearer(client)).json()
    assert intact["ok"] is True and intact["broken_at"] is None and intact["checked"] >= 3

    con = sqlite3.connect(isolated_settings.state_db_path)
    con.execute("DROP TRIGGER audit_events_no_update")
    con.execute("UPDATE audit_events SET outcome = 'denied' WHERE seq = 2")
    con.commit()
    con.close()

    broken = client.get("/api/audit/verify", headers=bearer(client)).json()
    assert broken == {
        "ok": False,
        "checked": 1,
        "pre_chain_rows": 0,
        "broken_at": 2,
        "reason": "the entry was altered",
    }

    monkeypatch.setenv("DEVTEAM_ROOT", str(isolated_settings.root))
    monkeypatch.setenv("DEVTEAM_PROFILE", "test")
    script: Any = load_script("verify_audit")
    assert script.main() == 1
    assert "BROKEN at entry 2" in capsys.readouterr().out
