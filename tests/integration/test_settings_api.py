"""Part F.5/F.6 API (D-55, D-57): read-only settings, knowledge-index status and queue, model-call records.

The API never contacts Ollama or the vector store: the model inventory is a table the worker writes, and a
rebuild request only queues a job for the worker.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from devteam_core.config import Settings
from devteam_core.contracts.llm import ModelCall, RouteRule
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.ids import new_id
from devteam_state import create_sqlite_engine, session_factory
from devteam_state.repositories import model_calls as model_calls_repo
from tests.conftest import audit_rows, bearer, create_project, upload
from tests.fixtures import brds

pytestmark = pytest.mark.integration


def test_models_settings_come_from_config_and_the_worker_inventory(
    client: TestClient, isolated_settings: Settings
) -> None:
    body = client.get("/api/settings/models", headers=bearer(client)).json()
    tags = {m["tag"]: m for m in body["models"]}
    assert {"qwen2.5:3b", "qwen2.5-coder:3b", "qwen2.5:1.5b", "nomic-embed-text"} <= set(tags)
    assert all(m["installed"] is None for m in body["models"])  # the worker has not checked yet
    slots = {s["slot"]: s for s in body["slots"]}
    assert (
        slots["review"]["model"] == "qwen2.5-coder:3b" and slots["embedding"]["model"] == "nomic-embed-text"
    )
    assert slots["coder"]["fallbacks"] == ["qwen2.5:1.5b"] and body["response_cache"] is False
    engine = create_sqlite_engine(isolated_settings.state_db_path)
    with session_factory(engine).begin() as session:
        model_calls_repo.replace_inventory(
            session, {"qwen2.5:3b": "a" * 64}, ("qwen2.5:3b", "qwen2.5:1.5b"), server_version="0.40.18",
            checked_at=datetime.now(UTC),
        )  # fmt: skip
    engine.dispose()
    after = {m["tag"]: m for m in client.get("/api/settings/models", headers=bearer(client)).json()["models"]}
    assert after["qwen2.5:3b"]["installed"] is True and after["qwen2.5:3b"]["digest"] == "a" * 12
    assert after["qwen2.5:1.5b"]["installed"] is False


def test_profile_limits_and_eval_status(client: TestClient) -> None:
    profile = client.get("/api/settings/profile", headers=bearer(client)).json()
    assert profile["max_model_ram_mb"] > 0 and profile["ram_margin_mb"] == 512
    limits = client.get("/api/settings/limits", headers=bearer(client)).json()
    assert limits["rate_limits"]["agents"]["default"]["calls_per_minute"] == 12
    assert limits["rag"]["top_k"] == 6 and "build_memory_tokens" in limits["context"]
    evals = {e["prompt_id"]: e for e in client.get("/api/settings/evals", headers=bearer(client)).json()}
    assert len(evals) == 10 and evals["brd_analyst.system"]["report"] == "matches"
    assert evals["head.system"]["report"] == "none" and not evals["head.system"]["eval_required"]


def test_an_upload_queues_an_index_job_and_a_rebuild_is_queued_once(
    client: TestClient, isolated_settings: Settings
) -> None:
    project_id = create_project(client, "Library")
    before = client.get(f"/api/projects/{project_id}/knowledge/index", headers=bearer(client)).json()
    assert before["latest_job"] is None and before["collection"] is None
    upload(client, project_id, ("library.md", brds.brd_markdown()))
    status = client.get(f"/api/projects/{project_id}/knowledge/index", headers=bearer(client)).json()
    assert status["latest_job"]["status"] == "queued" and status["latest_job"]["kind"] == "index"
    assert (
        status["documents_extracted"] == 1
        and status["documents_indexed"] == 0
        and not status["index_complete"]
    )
    first = client.post(f"/api/projects/{project_id}/knowledge/index", headers=bearer(client))
    assert first.status_code == 202
    assert first.json()["job_id"] == status["latest_job"]["job_id"]  # one active job per project
    rows = [r for r in audit_rows(isolated_settings) if r.action == "knowledge.rebuild"]
    assert [r.outcome for r in rows] == ["success"]


def test_model_calls_of_a_run_are_metadata_only(client: TestClient, isolated_settings: Settings) -> None:
    from devteam_state.repositories import runs as runs_repo  # noqa: PLC0415

    project_id = create_project(client, "Shop")
    engine = create_sqlite_engine(isolated_settings.state_db_path)
    sessions = session_factory(engine)
    with sessions.begin() as session:
        from devteam_core.contracts.statuses import Stage  # noqa: PLC0415

        run_id = runs_repo.create_run(
            session, project_id=project_id, run_kind="initial", stage=Stage.BUILD
        ).id
        model_calls_repo.record_model_call(
            session,
            ModelCall(
                call_id=new_id("mcl"), project_id=project_id, run_id=run_id, task_id=new_id("tsk"),
                agent=AgentRole.HEAD, kind="generate", operation_id="head.plan", slot=ModelSlot.INSTRUCT,
                model_tag="qwen2.5:3b", model_digest="a" * 64, route_rule=RouteRule.SLOT, input_hash="b" * 64,
                input_tokens=10, output_tokens=5, duration_ms=100, outcome="valid",
            ),
            created_at=datetime.now(UTC),
        )  # fmt: skip
    engine.dispose()
    calls = client.get(f"/api/projects/{project_id}/runs/{run_id}/model-calls", headers=bearer(client)).json()
    assert [(c["agent"], c["model_tag"], c["route_rule"], c["model_digest"]) for c in calls] == [
        ("head", "qwen2.5:3b", "slot", "a" * 12)
    ]
    assert not {"text", "content", "messages", "output_text"} & set(calls[0])
    missing = client.get(f"/api/projects/{project_id}/runs/run_01JD3X5Q0000000000000000AA/model-calls",
                         headers=bearer(client))  # fmt: skip
    assert missing.status_code == 404
