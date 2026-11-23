"""E2E-1 / Spec §23 scenario #1 (extraction part) / AC-B-05, against the real uvicorn server.

Upload a complete supported BRD; extraction preserves source references.
"""

import os
import secrets
import socket
import subprocess
import sys
import time
from collections.abc import Iterator

import httpx
import pytest

from devteam_core.config import Settings
from tests.conftest import UI_ORIGIN
from tests.fixtures import brds

pytestmark = pytest.mark.integration

E2E_TOKEN = secrets.token_urlsafe(32)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def server(isolated_settings: Settings) -> Iterator[str]:
    port = _free_port()
    env = {
        **os.environ,
        "DEVTEAM_ROOT": str(isolated_settings.root),
        "DEVTEAM_PROFILE": "test",
        "DEVTEAM_API__PORT": str(port),
        "DTTEST_SECRET__API_TOKEN": E2E_TOKEN,  # test-profile memory keyring seed (D-18; no file)
    }
    log = (isolated_settings.root / "server.log").open("w", encoding="utf-8")  # a pipe would fill and block
    proc = subprocess.Popen(
        [sys.executable, "-m", "devteam_api"], env=env, stdout=log, stderr=subprocess.STDOUT, text=True
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if httpx.get(f"{base}/health", timeout=2).status_code == 200:
                    break
            except httpx.TransportError:
                assert proc.poll() is None, (isolated_settings.root / "server.log").read_text(
                    encoding="utf-8"
                )
                time.sleep(0.3)
        yield base
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
        log.close()


def test_scenario_1_upload_complete_brd_and_navigate_references(
    server: str, isolated_settings: Settings
) -> None:
    token = E2E_TOKEN
    assert not isolated_settings.api_token_path.exists()  # D-18: no plaintext key file
    with httpx.Client(base_url=server, timeout=60) as anonymous:
        assert anonymous.post("/api/projects", json={"name": "x"}).status_code == 401  # demo step 6

    with httpx.Client(base_url=server, timeout=60, headers={"Origin": UI_ORIGIN}) as browser:
        # Sign in like the UI does: token → HttpOnly cookie.
        assert browser.post("/api/session", json={"token": token}).status_code == 200
        project = browser.post("/api/projects", json={"name": "Library Demo"}).json()
        result = browser.post(
            f"/api/projects/{project['id']}/documents",
            files=[
                ("files", ("sample_brd.docx", brds.brd_docx())),
                ("files", ("sample_brd.pdf", brds.brd_pdf())),
                ("files", ("corrupt.docx", brds.corrupt_docx())),
            ],
        ).json()
        assert [(r["filename"], r["status"]) for r in result["results"]] == [
            ("sample_brd.docx", "extracted"),
            ("sample_brd.pdf", "extracted"),
            ("corrupt.docx", "rejected"),
        ]
        assert result["results"][2]["message"].startswith("This Word file is damaged")

        for item in result["results"][:2]:
            doc_id = item["document_id"]
            detail = browser.get(f"/api/projects/{project['id']}/documents/{doc_id}").json()
            outline = [entry["title"] for entry in detail["outline"]]
            assert all(heading in outline for heading in brds.EXPECTED_HEADINGS), outline
            chunks = browser.get(
                f"/api/projects/{project['id']}/documents/{doc_id}/chunks", params={"limit": 200}
            ).json()["items"]
            by_section = {c["section_path"].split(" > ")[-1]: c for c in chunks}
            fr = by_section["3 Functional Requirements"]
            assert "FR-002 The librarian shall register a loan" in fr["text"]
            assert fr["reference"].startswith(item["filename"])
            assert "§ 3 Functional Requirements" in fr["reference"]
            if item["filename"].endswith(".pdf"):
                assert fr["page_start"] == 2 and "p.2" in fr["reference"]
                assert by_section["5 Acceptance Criteria"]["page_start"] == 3
            else:
                assert fr["page_start"] is None  # DOCX: section + paragraph references

        actions = [
            (e["action"], e["outcome"])
            for e in browser.get(f"/api/projects/{project['id']}/audit").json()["items"]
        ]
        assert actions.count(("document.upload", "success")) == 2
        assert actions.count(("document.upload", "error")) == 1
        assert ("document.upload_batch", "success") in actions
        assert ("project.create", "success") in actions
