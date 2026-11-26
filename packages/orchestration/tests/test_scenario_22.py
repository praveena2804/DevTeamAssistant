"""Spec scenario #22 / AC-F-04: "Simulate model out-of-memory; system checkpoints and pauses without a paid
API call."

A real runtime and graph run against a respx-mocked local Ollama whose every answer is an out-of-memory
error. The gateway walks the recovery ladder (reduced context, then the smaller model), then the task waits
for resources: the run is ``waiting_for_resources`` with its checkpoint saved and a ``resume`` job queued,
nothing was invented, and no request left for any host other than the local Ollama. A resume re-enters the
same step from the checkpoint.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from sqlalchemy import select, text

from devteam_core.contracts.statuses import RunStatus
from devteam_orchestration.execution import advance
from devteam_state.models import AgentTaskRow, ModelCallRow, Run
from tests.fixtures.orchestration import Env, assume_free_ram, make_env

BASE = "http://127.0.0.1:11434"
TAGS = {"models": [{"name": n, "digest": d * 64} for n, d in
                   (("qwen2.5:3b", "a"), ("qwen2.5-coder:3b", "b"), ("qwen2.5:1.5b", "c"))]}  # fmt: skip


@pytest.fixture
def env(tmp_path: Path) -> Iterator[tuple[Env, respx.MockRouter]]:
    with respx.mock(assert_all_mocked=True, assert_all_called=False) as mock:
        mock.get(f"{BASE}/api/tags").mock(return_value=httpx.Response(200, json=TAGS))
        mock.get(f"{BASE}/api/version").mock(return_value=httpx.Response(200, json={"version": "0.40.16"}))
        mock.get(f"{BASE}/api/ps").mock(return_value=httpx.Response(200, json={"models": []}))
        mock.post(f"{BASE}/api/generate").mock(return_value=httpx.Response(200, json={"done": True}))
        mock.post(f"{BASE}/api/chat").mock(
            return_value=httpx.Response(500, json={"error": "model requires more system memory (2.3 GiB)"})
        )
        built = make_env(tmp_path, ollama_url=BASE, with_graph=True)
        assert built.rt.gateway is not None
        assume_free_ram(built)  # the OOM is simulated by the model server, not by this host
        try:
            yield built, mock
        finally:
            built.close()


def _status(env: Env) -> str:
    with env.sessions() as session:
        run = session.get(Run, env.run_id)
        assert run is not None
        return run.status


def test_simulated_oom_checkpoints_and_pauses_without_any_external_call(
    env: tuple[Env, respx.MockRouter],
) -> None:
    built, mock = env
    result = advance(built.rt, built.graph, project_id=built.project_id, run_id=built.run_id, kind="start")
    assert result.result == "waiting_for_resources"
    assert _status(built) == RunStatus.WAITING_FOR_RESOURCES.value

    chat = [c for c in mock.calls if c.request.url.path == "/api/chat"]
    tried = [
        (json.loads(c.request.content)["model"], json.loads(c.request.content)["options"]["num_ctx"])
        for c in chat
    ]
    assert tried[:3] == [("qwen2.5:3b", 4096), ("qwen2.5:3b", 2048), ("qwen2.5:1.5b", 4096)]  # the ladder
    assert {c.request.url.host for c in mock.calls} == {"127.0.0.1"}  # nothing left this machine

    with built.sessions() as session:
        tasks = list(session.scalars(select(AgentTaskRow).where(AgentTaskRow.run_id == built.run_id)))
        assert [(t.agent_type, t.status) for t in tasks] == [("brd_analyst", "ready")]  # nothing invented
        jobs = [tuple(j) for j in session.execute(text("SELECT kind, status FROM run_jobs")).all()]
        assert jobs == [("resume", "queued")]
        calls = list(session.scalars(select(ModelCallRow)))
        assert calls and all(c.outcome == "error" for c in calls)
        assert {c.error_code for c in calls} == {"model.oom"}
    with sqlite3.connect(built.settings.checkpoints_db_path) as conn:
        saved = conn.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE thread_id = ?", (built.run_id,)
        ).fetchone()[0]
    assert saved > 0  # the run checkpointed before pausing


def test_a_resume_re_enters_the_same_step_from_the_checkpoint(env: tuple[Env, respx.MockRouter]) -> None:
    built, mock = env
    advance(built.rt, built.graph, project_id=built.project_id, run_id=built.run_id, kind="start")
    before = len([c for c in mock.calls if c.request.url.path == "/api/chat"])
    result = advance(built.rt, built.graph, project_id=built.project_id, run_id=built.run_id, kind="resume")
    assert result.result == "waiting_for_resources"  # still out of memory: waits again, invents nothing
    after = len([c for c in mock.calls if c.request.url.path == "/api/chat"])
    assert after > before  # the analyst step was tried again from the checkpoint
    with built.sessions() as session:
        tasks = list(session.scalars(select(AgentTaskRow).where(AgentTaskRow.run_id == built.run_id)))
    assert [t.agent_type for t in tasks] == ["brd_analyst"]  # no later step ran
