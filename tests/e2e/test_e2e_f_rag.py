"""Part F end to end (AC-F-01, AC-F-02, AC-F-03; real local Ollama, never in CI):

upload a BRD through the API → the API queues an index job (it never embeds) → a real
``python -m devteam_worker --once`` process inside the loopback-only network jail indexes it with the real
embedding model (``nomic-embed-text``) into a real in-process ChromaDB → the API reports the index complete →
a project-scoped search returns the BRD's own chunk references. Every embedding is a gateway call recorded
as a ``ModelCall``; no connection leaves the machine.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from devteam_core.config import Settings
from tests.conftest import bearer, create_project, upload
from tests.fixtures import brds
from tests.fixtures.harness import REPO_ROOT

pytestmark = [pytest.mark.llm, pytest.mark.slow]
JAIL = REPO_ROOT / "tests" / "fixtures" / "loopback_only"


def _env(settings: Settings, net_log: Path) -> dict[str, str]:
    variables = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("DEVTEAM_", "LANGSMITH_", "LANGCHAIN_", "CHROMA_", "OTEL_EXPORTER_"))
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


def test_upload_index_and_search_end_to_end(
    make_client: Callable[..., TestClient], isolated_settings: Settings, tmp_path: Path
) -> None:
    client = make_client(database={"busy_timeout_ms": 15000})
    project_id = create_project(client, "Library")
    assert (
        upload(client, project_id, ("library.md", brds.brd_markdown()))["results"][0]["status"] == "extracted"
    )
    base = f"/api/projects/{project_id}/knowledge/index"
    queued = client.get(base, headers=bearer(client)).json()
    assert queued["latest_job"]["status"] == "queued" and not queued["index_complete"]

    net_log = tmp_path / "net.log"
    worker_log = tmp_path / "worker.log"
    with worker_log.open("wb") as handle:
        proc = subprocess.run(
            [sys.executable, "-m", "devteam_worker", "--once"],
            cwd=REPO_ROOT, env=_env(isolated_settings, net_log), stdout=handle, stderr=subprocess.STDOUT,
            timeout=1_200, check=False,
        )  # fmt: skip
    log_text = worker_log.read_text(encoding="utf-8", errors="replace")
    assert proc.returncode == 0, log_text[-3000:]
    assert (
        not net_log.exists() or net_log.read_text(encoding="utf-8") == ""
    )  # AC-F-02: nothing left the machine

    status = client.get(base, headers=bearer(client)).json()
    assert status["latest_job"]["status"] == "done", status
    assert status["index_complete"] and status["embedding_model"] == "nomic-embed-text"
    with sqlite3.connect(isolated_settings.state_db_path) as conn:
        calls = conn.execute("SELECT kind, service, outcome, model_tag FROM model_calls").fetchall()
        chunks = conn.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE project_id = ?", (project_id,)
        ).fetchone()[0]
    assert calls and {c[:3] for c in calls} == {("embed", "rag_ingestor", "valid")}  # AC-F-01
    assert {c[3] for c in calls} == {"nomic-embed-text"} and chunks > 0

    # search through the same code path the agent tool uses (worker side), on the persisted index
    from devteam_harness.rag.store import ChromaVectorStore  # noqa: PLC0415
    from devteam_orchestration.runtime import build_runtime  # noqa: PLC0415
    from devteam_state import create_sqlite_engine, session_factory  # noqa: PLC0415

    engine = create_sqlite_engine(isolated_settings.state_db_path)
    runtime = build_runtime(
        isolated_settings,
        session_factory(engine),
        rag_store=ChromaVectorStore(isolated_settings.data_dir / "chroma"),
    )
    try:
        assert runtime.retriever is not None
        outcome = runtime.retriever.search(project_id, "How many books may a member borrow?", limit=3)
    finally:
        runtime.close()
        engine.dispose()
    assert outcome.hits and not outcome.index_incomplete and outcome.dropped == 0
    with sqlite3.connect(isolated_settings.state_db_path) as conn:
        query = "SELECT id, reference FROM document_chunks WHERE project_id = ?"
        own = {r[0]: r[1] for r in conn.execute(query, (project_id,))}
    assert all(h.chunk_id in own and h.reference == own[h.chunk_id] for h in outcome.hits)  # AC-F-03
    assert any("five" in h.text.lower() for h in outcome.hits), [h.text[:80] for h in outcome.hits]
