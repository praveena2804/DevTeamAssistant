"""AC-D-02 / Spec §23 #23: kill the worker mid-build, restart it, finish with nothing done twice.

A real worker process runs a real run with the local model. The test acts as the API (decisions go
through the same command interface). The kill point is chosen from the database, not from timing: the
worker is killed while the Database Developer's task is running (its model call in progress).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil
import pytest
from sqlalchemy import func, select

from devteam_core.contracts.records import ApprovalDecision
from devteam_core.contracts.statuses import GateName, RunStatus
from devteam_orchestration import commands
from devteam_state.models import (
    AgentTaskRow,
    AuditEvent,
    Run,
    RunEventRow,
    SpecVersionRow,
    TaskAttempt,
    TaskPlanRow,
)
from devteam_state.repositories import audit
from devteam_state.repositories import orchestration as orch
from tests.fixtures.harness import REPO_ROOT
from tests.fixtures.orchestration import Env, make_env

pytestmark = [pytest.mark.llm, pytest.mark.slow]
DEADLINE_S = 1_800


def _spawn(env: Env, log: Path) -> subprocess.Popen[bytes]:
    variables = {
        k: v for k, v in os.environ.items() if not k.startswith(("DEVTEAM_", "LANGSMITH_", "LANGCHAIN_"))
    }
    variables.update(
        {
            "DEVTEAM_PATHS__DATA_DIR": str(env.settings.data_dir),
            "DEVTEAM_PATHS__WORKSPACES_DIR": str(env.settings.workspaces_dir),
            "DEVTEAM_LOGGING__FILE_ENABLED": "false",
            "DEVTEAM_ORCHESTRATION__HEARTBEAT_INTERVAL_S": "2",
            "DEVTEAM_ORCHESTRATION__LEASE_TTL_S": "6",
            "DEVTEAM_ORCHESTRATION__POLL_INTERVAL_S": "1",
        }
    )
    handle = log.open("ab")
    return subprocess.Popen(
        [sys.executable, "-m", "devteam_worker"],
        cwd=REPO_ROOT,
        env=variables,
        stdout=handle,
        stderr=subprocess.STDOUT,
    )


def _kill(process: subprocess.Popen[bytes]) -> None:
    parent = psutil.Process(process.pid)
    for child in parent.children(recursive=True):
        child.kill()
    parent.kill()
    process.wait(timeout=30)


def _wait(what: str, check: Callable[[], Any]) -> Any:
    deadline = time.monotonic() + DEADLINE_S
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(1)
    raise AssertionError(f"timed out waiting for {what}")


def _run(env: Env) -> Run:
    with env.sessions() as session:
        run = session.get(Run, env.run_id)
        assert run is not None
        return run


def _decide_pending(env: Env, decided: set[str]) -> bool:
    """Approve the pending gate once (as the API would); a gate already decided is left to the worker."""
    run = _run(env)
    if run.status != RunStatus.WAITING_FOR_HUMAN.value or run.pending_gate is None:
        return False
    if run.pending_gate in decided:
        return False
    commands.submit_gate_decision(
        env.sessions,
        env.rt.approvals,
        env.owner,
        project_id=env.project_id,
        run_id=env.run_id,
        gate=GateName(run.pending_gate),
        decision=ApprovalDecision.APPROVED,
    )
    decided.add(run.pending_gate)
    return True


def _task(env: Env, agent: str) -> AgentTaskRow | None:
    with env.sessions() as session:
        return session.scalar(
            select(AgentTaskRow).where(AgentTaskRow.run_id == env.run_id, AgentTaskRow.agent_type == agent)
        )


def test_kill_and_restart_resumes_without_repeating_work(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    log = tmp_path / "worker.log"
    with env.sessions.begin() as session:
        orch.enqueue_job(session, project_id=env.project_id, run_id=env.run_id, kind="start")
    worker = _spawn(env, log)
    decided: set[str] = set()
    try:

        def db_dev_running() -> bool:
            _decide_pending(env, decided)
            task = _task(env, "database_developer")
            return task is not None and task.status == "running"

        _wait("the Database Developer to be running", db_dev_running)
        _kill(worker)  # hard kill in the middle of a model call
        assert _run(env).status == RunStatus.RUNNING.value  # nothing finished the run

        worker = _spawn(env, log)  # takes over the stale lease, recovers the run (D-40)

        def finished() -> bool:
            _decide_pending(env, decided)
            return _run(env).status in {
                s.value for s in (RunStatus.COMPLETED, RunStatus.BLOCKED, RunStatus.FAILED)
            }

        _wait("the run to finish", finished)
    finally:
        if worker.poll() is None:
            _kill(worker)
    run = _run(env)
    assert run.status == RunStatus.COMPLETED.value, log.read_text(encoding="utf-8", errors="replace")[-3000:]

    with env.sessions() as session:
        tasks = list(session.scalars(select(AgentTaskRow).where(AgentTaskRow.run_id == env.run_id)))
        by_agent = {t.agent_type: t for t in tasks}
        assert len(tasks) == len(by_agent) == 9  # one task per agent step: none was created twice
        assert {t.status for t in tasks} == {"completed"}
        db_dev = by_agent["database_developer"]
        attempts = [
            a.status
            for a in session.scalars(
                select(TaskAttempt).where(TaskAttempt.task_id == db_dev.id).order_by(TaskAttempt.attempt)
            )
        ]
        assert attempts == ["interrupted", "completed"]  # D-40: the crash is recorded, not counted as failed
        assert db_dev.max_attempts == 3  # the crashed attempt was given back
        for agent, task in by_agent.items():
            if agent != "database_developer":
                assert task.attempt_count == 1, agent  # finished work was not repeated
        assert (
            session.scalar(
                select(func.count()).select_from(TaskPlanRow).where(TaskPlanRow.run_id == env.run_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count()).select_from(SpecVersionRow).where(SpecVersionRow.run_id == env.run_id)
            )
            == 1
        )
        summaries = session.scalar(
            select(func.count())
            .select_from(RunEventRow)
            .where(RunEventRow.run_id == env.run_id, RunEventRow.kind == "run.summary")
        )
        assert summaries == 1
        actions = [e.action for e in session.scalars(select(AuditEvent))]
        assert actions.count("run.recovered") >= 1
        assert actions.count("task.crash_recovered") == 1
        assert actions.count("spec.frozen") == 1
        assert actions.count("plan.accepted") == 1
        assert audit.verify_chain(session).ok
    env.close()
