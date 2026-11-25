"""D.4 worker (LG-07, D-41): lease, recovery, control flags, graceful stop, fail-closed start.

These tests need no model: the provider points at a closed loopback port, so the first agent step
waits for resources (honestly) instead of producing anything. The real crash-and-restart test with a
model is ``test_worker_kill.py`` (``llm``).
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from devteam_core.contracts.statuses import AgentRole, RunStatus, Stage, TaskStatus
from devteam_core.contracts.tasks import AgentTask
from devteam_orchestration import commands
from devteam_orchestration.steps import recover_crash, task_row_status
from devteam_state.models import AuditEvent, Run, RunJob, TaskAttempt, WorkerLease
from devteam_state.repositories import orchestration as orch
from devteam_state.repositories import runs
from devteam_worker.worker import Worker
from tests.fixtures.harness import REPO_ROOT
from tests.fixtures.orchestration import Env, closed_port, make_env

R = RunStatus


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    built = make_env(tmp_path, ollama_url=f"http://127.0.0.1:{closed_port()}", with_graph=True)
    yield built
    built.close()


def _queue_start(env: Env) -> None:
    with env.sessions.begin() as session:
        orch.enqueue_job(session, project_id=env.project_id, run_id=env.run_id, kind="start")


def _run(env: Env) -> Run:
    with env.sessions() as session:
        run = session.get(Run, env.run_id)
        assert run is not None
        return run


def _jobs(env: Env) -> list[tuple[str, str, str | None]]:
    with env.sessions() as session:
        return [
            (j.kind, j.status, j.error_code)
            for j in session.scalars(select(RunJob).order_by(RunJob.created_at))
        ]


# ------------------------------------------------------------------------------------------ lease


def test_a_second_worker_refuses_while_the_first_holds_the_lease(env: Env) -> None:
    first, second = Worker(env.rt, env.graph, owner="w1"), Worker(env.rt, env.graph, owner="w2")
    assert first.start()
    try:
        assert not second.start()
    finally:
        first.close()
    assert second.start()  # released: the next worker may start
    second.close()


def test_a_stale_lease_is_taken_over_and_audited(env: Env) -> None:
    first = Worker(env.rt, env.graph, owner="dead")
    assert first.start()
    first._stop.set()
    with env.sessions.begin() as session:
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        lease.heartbeat_at = lease.heartbeat_at - timedelta(
            seconds=env.settings.orchestration.lease_ttl_s + 5
        )
    second = Worker(env.rt, env.graph, owner="alive")
    assert second.start()
    second.close()
    with env.sessions() as session:
        started = [
            e.details
            for e in session.scalars(select(AuditEvent).where(AuditEvent.action == "worker.started"))
        ]
    assert started[-1]["took_over_from"] == "dead"


# ---------------------------------------------------------------------------------------- jobs


def test_a_start_job_runs_until_the_model_is_needed_then_waits(env: Env) -> None:
    _queue_start(env)
    worker = Worker(env.rt, env.graph, owner="w")
    assert worker.start()
    try:
        outcome = worker.run_once()
        assert outcome is not None and outcome.result.result == "waiting_for_resources"
        assert worker.run_once() is None  # the resume job is delayed (resource_wait_s)
    finally:
        worker.close()
    assert _run(env).status == R.WAITING_FOR_RESOURCES.value
    assert _jobs(env) == [("start", "done", None), ("resume", "queued", None)]


def test_recovery_requeues_work_a_dead_worker_left_once(env: Env) -> None:
    with env.sessions.begin() as session:
        job = orch.enqueue_job(session, project_id=env.project_id, run_id=env.run_id, kind="start")
        orch.claim_next_job(session)  # "running", as a dead worker left it
        run = session.get(Run, env.run_id)
        assert run is not None
        run.status = R.RUNNING.value
        job_id = job.id
    worker = Worker(env.rt, env.graph, owner="w")
    assert worker.start()  # start() recovers
    worker.close()
    jobs = _jobs(env)
    assert jobs[0] == ("start", "failed", "worker.crashed")
    assert jobs[1:] == [("resume", "queued", None)]
    second = Worker(env.rt, env.graph, owner="w2")
    assert second.start()
    second.close()
    assert len(_jobs(env)) == 2  # no duplicate resume job
    assert job_id


def test_pause_then_resume_continues_from_the_checkpoint(env: Env) -> None:
    _queue_start(env)
    with env.sessions.begin() as session:
        orch.set_control_request(session, env.project_id, env.run_id, "pause")
    worker = Worker(env.rt, env.graph, owner="w")
    assert worker.start()
    try:
        outcome = worker.run_once()
        assert outcome is not None and outcome.result.result == "paused"
        run = _run(env)
        assert (run.status, run.control_request) == (R.PAUSED.value, "none")
        values = env.graph.get_state({"configurable": {"thread_id": env.run_id}}).values
        assert values["last_node"] == "route_entry"  # stopped after one node; checkpoint kept
        with env.sessions.begin() as session:
            commands.request_resume(session, project_id=env.project_id, run_id=env.run_id, actor="prn_x")
        outcome = worker.run_once()
        assert outcome is not None and outcome.result.result == "waiting_for_resources"
        values = env.graph.get_state({"configurable": {"thread_id": env.run_id}}).values
        assert values["last_node"] == "intake"  # continued, did not restart from the entry
    finally:
        worker.close()


def test_cancel_keeps_the_last_checkpoint(env: Env) -> None:
    _queue_start(env)
    with env.sessions.begin() as session:
        orch.set_control_request(session, env.project_id, env.run_id, "pause")
    worker = Worker(env.rt, env.graph, owner="w")
    assert worker.start()
    try:
        assert worker.run_once() is not None  # paused after the first node: a checkpoint exists
        with env.sessions.begin() as session:
            orch.set_control_request(session, env.project_id, env.run_id, "cancel")
            orch.enqueue_job(session, project_id=env.project_id, run_id=env.run_id, kind="resume")
        outcome = worker.run_once()
        assert outcome is not None and outcome.result.result == "cancelled"
    finally:
        worker.close()
    assert _run(env).status == R.CANCELLED.value
    values = env.graph.get_state({"configurable": {"thread_id": env.run_id}}).values
    assert values["last_node"] == "route_entry"  # the final checkpoint is kept


def test_a_cancel_requested_before_anything_ran_leaves_no_checkpoint(env: Env) -> None:
    _queue_start(env)
    with env.sessions.begin() as session:
        orch.set_control_request(session, env.project_id, env.run_id, "cancel")
    worker = Worker(env.rt, env.graph, owner="w")
    assert worker.start()
    try:
        outcome = worker.run_once()
        assert outcome is not None and outcome.result.result == "cancelled"
    finally:
        worker.close()
    assert _run(env).status == R.CANCELLED.value
    assert env.graph.get_state({"configurable": {"thread_id": env.run_id}}).values == {}


def test_a_stopping_worker_leaves_the_run_resumable(env: Env) -> None:
    _queue_start(env)
    worker = Worker(env.rt, env.graph, owner="w")
    assert worker.start()
    worker.stop()
    outcome = worker.run_once()
    worker.close()
    assert outcome is not None and outcome.result.result == "stopped"
    assert _run(env).status == R.RUNNING.value
    assert _jobs(env) == [("start", "failed", "worker.stopped"), ("resume", "queued", None)]


# ---------------------------------------------------------------------------------- D-40 crashes


def _running_task(env: Env, crashes: int) -> str:
    task = AgentTask(
        task_id="tsk_" + "0" * 25 + "9",
        project_id=env.project_id,
        run_id=env.run_id,
        agent_type=AgentRole.BRD_ANALYST,
        operation_id="brd_analyst.analyse_section",
        objective="Analyse",
        done_criteria=("done",),
    )
    with env.sessions.begin() as session:
        row = runs.add_task(session, task)
        row.status = TaskStatus.RUNNING.value
        for n in range(1, crashes + 1):
            row.attempt_count = n
            attempt, _ = runs.start_attempt(session, row, node="executor")
            if n < crashes:
                attempt.status = "interrupted"
    return task.task_id


def test_a_crash_is_not_a_failed_attempt(env: Env) -> None:
    task_id = _running_task(env, crashes=1)
    recover_crash(env.rt, env.project_id, task_id)
    assert task_row_status(env.rt, env.project_id, task_id) is TaskStatus.READY
    with env.sessions() as session:
        row = runs.get_task(session, env.project_id, task_id)
        assert row is not None and row.max_attempts == 3  # the crashed attempt was given back
        statuses = [
            a.status for a in session.scalars(select(TaskAttempt).where(TaskAttempt.task_id == task_id))
        ]
    assert statuses == ["interrupted"]


@pytest.mark.parametrize(("crashes", "expected"), [(3, TaskStatus.READY), (4, TaskStatus.BLOCKED)])
def test_more_than_three_consecutive_crashes_block_the_task(
    env: Env, crashes: int, expected: TaskStatus
) -> None:
    """D-40 (owner ruling): block after more than 3 consecutive crashes."""
    task_id = _running_task(env, crashes=crashes)
    recover_crash(env.rt, env.project_id, task_id)
    assert task_row_status(env.rt, env.project_id, task_id) is expected


# ------------------------------------------------------------------------------- fail-closed start


def _worker_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DEVTEAM_", "LANGSMITH_", "LANGCHAIN_"))}
    env.update(
        {
            "DEVTEAM_PROFILE": "test",
            "DEVTEAM_PATHS__DATA_DIR": str(tmp_path / "data"),
            "DEVTEAM_PATHS__WORKSPACES_DIR": str(tmp_path / "ws"),
            "DEVTEAM_LOGGING__FILE_ENABLED": "false",
            "DEVTEAM_OLLAMA__BASE_URL": f"http://127.0.0.1:{closed_port()}",
            **extra,
        }
    )
    return env


def _worker(tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "devteam_worker", "--once"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=REPO_ROOT,
        env=_worker_env(tmp_path, **extra),
        timeout=120,
    )


def test_the_worker_refuses_an_unmigrated_database(tmp_path: Path) -> None:
    result = _worker(tmp_path)
    assert result.returncode == 2, result.stderr
    assert "not at the current migration" in result.stderr


def test_the_worker_refuses_cloud_tracing(tmp_path: Path) -> None:
    from devteam_state import upgrade  # noqa: PLC0415

    upgrade(tmp_path / "data" / "state.db")
    result = _worker(tmp_path, LANGSMITH_TRACING="true")
    assert result.returncode == 2, result.stderr
    assert "LANGSMITH_TRACING" in result.stderr
    assert "true" not in result.stderr.split("LANGSMITH_TRACING", 1)[1][:20]  # names, never values


def test_the_worker_starts_without_ollama_and_exits_when_idle(tmp_path: Path) -> None:
    from devteam_state import upgrade  # noqa: PLC0415

    upgrade(tmp_path / "data" / "state.db")
    result = _worker(tmp_path)
    assert result.returncode == 0, result.stderr
    assert Stage.INTAKE  # the import of the contracts works in the worker process too


def test_a_restart_right_after_a_crash_waits_for_the_dead_lease(env: Env) -> None:
    dead = Worker(env.rt, env.graph, owner="dead")
    assert dead.start()
    dead._stop.set()
    with env.sessions.begin() as session:  # the lease goes stale in about two seconds
        lease = session.get(WorkerLease, 1)
        assert lease is not None
        lease.heartbeat_at = lease.heartbeat_at - timedelta(
            seconds=env.settings.orchestration.lease_ttl_s - 2
        )
    restarted = Worker(env.rt, env.graph, owner="restarted")
    assert not restarted.start()  # without waiting: still looks alive
    assert restarted.start(wait_s=env.settings.orchestration.lease_ttl_s)
    restarted.close()


def test_waiting_does_not_let_a_second_worker_take_a_live_lease(env: Env) -> None:
    live = Worker(env.rt, env.graph, owner="live")
    assert live.start()
    try:
        second = Worker(env.rt, env.graph, owner="second")
        assert not second.start(wait_s=3)
    finally:
        live.close()


# ------------------------------------------------------------------------- sandbox housekeeping (Part E)


def test_the_worker_starts_when_the_root_has_no_starter_kits(
    env: Env, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (found by E2E-2 in E.5): ``DEVTEAM_ROOT`` without ``templates/`` crashed the worker."""
    bare = tmp_path / "bare_root"
    (bare / "config").mkdir(parents=True)
    (bare / "config" / "defaults.yaml").write_text("", encoding="utf-8")
    (bare / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setenv("DEVTEAM_ROOT", str(bare))
    worker = Worker(env.rt, env.graph, owner="no-kits")
    assert worker.start()
    try:
        status = worker.refresh_sandbox(sweep=True)
    finally:
        worker.close()
    assert status["stale_marked"] == 0


def test_a_failing_stale_refresh_never_stops_the_worker(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    import devteam_worker.worker as worker_module  # noqa: PLC0415

    def broken(*_args: object) -> int:
        raise OSError("simulated")

    monkeypatch.setattr(worker_module, "refresh_stale", broken)
    worker = Worker(env.rt, env.graph, owner="broken-refresh")
    assert worker.start()  # housekeeping failed, the run worker still runs
    try:
        status = worker.refresh_sandbox(sweep=True)
    finally:
        worker.close()
    assert status["stale_marked"] is None


class _FakeSandbox:
    def __init__(self, swept: tuple[int, int] | None = (2, 1)) -> None:
        self.swept = swept

    def engine_status(self) -> dict[str, object]:
        return {"reachable": True, "version": "29.1.3", "operating_system": "Ubuntu 24.04.4 LTS"}

    def sweep(self) -> tuple[int, int] | None:
        return self.swept


def _with_sandbox(env: Env, sandbox: Any) -> Worker:
    import dataclasses  # noqa: PLC0415

    return Worker(dataclasses.replace(env.rt, sandbox=sandbox), env.graph, owner="sandbox-worker")


def test_startup_sweep_removes_leftovers_and_is_audited(env: Env) -> None:
    import json  # noqa: PLC0415

    worker = _with_sandbox(env, _FakeSandbox())
    assert worker.start()  # start() runs refresh_sandbox(sweep=True)
    worker.close()
    status = json.loads((env.settings.data_dir / "sandbox_status.json").read_text(encoding="utf-8"))
    assert status["reachable"] is True and status["swept"] == [2, 1]
    with env.sessions() as session:
        swept = [
            e.details for e in session.scalars(select(AuditEvent).where(AuditEvent.action == "sandbox.swept"))
        ]
    assert swept == [{"containers": 2, "volumes": 1}]


def _queue_verification(env: Env, kit: str = "html_fastapi_sqlite") -> str:
    from devteam_services import templates as kit_service  # noqa: PLC0415

    return kit_service.request_verification(env.sessions, kit, requested_by="prn_test").verification_id


def test_verification_jobs_run_after_runs_and_an_error_is_recorded_blocked(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import devteam_worker.worker as worker_module  # noqa: PLC0415
    from devteam_core.contracts.sandbox import TemplateVerificationStatus  # noqa: PLC0415
    from devteam_state.repositories import sandbox as sandbox_repo  # noqa: PLC0415

    worker = _with_sandbox(env, _FakeSandbox(swept=None))
    assert worker.run_verification_once() is False  # nothing queued
    assert Worker(env.rt, env.graph).run_verification_once() is False  # no sandbox configured: nothing runs

    first = _queue_verification(env)
    seen: list[str] = []

    def fake_run(verification: object, *_a: object, **_kw: object) -> object:
        seen.append(verification.verification_id)  # type: ignore[attr-defined]
        return verification

    monkeypatch.setattr(worker_module, "run_verification", fake_run)
    assert worker.run_verification_once() is True and seen == [first]

    second = _queue_verification(env, "react_fastapi_sqlite")  # the first is still active (one per kit)

    def crash(*_a: object, **_kw: object) -> object:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(worker_module, "run_verification", crash)
    assert worker.run_verification_once() is True
    with env.sessions() as session:
        stored = sandbox_repo.get_verification(session, second)
    assert stored is not None and stored.status is TemplateVerificationStatus.BLOCKED
    assert stored.reason == "the verification stopped with an error (RuntimeError)"


def test_run_forever_serves_verifications_and_refreshes_status_when_idle(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    import devteam_worker.worker as worker_module  # noqa: PLC0415

    worker = _with_sandbox(env, _FakeSandbox())
    calls: list[str] = []

    def verify() -> bool:
        calls.append("verify")
        return False

    monkeypatch.setattr(worker, "run_verification_once", verify)

    def refresh(*, sweep: bool = False) -> dict[str, object]:
        calls.append("refresh")
        worker.stop()
        return {}

    monkeypatch.setattr(worker, "refresh_sandbox", refresh)
    monkeypatch.setattr(worker_module, "SANDBOX_STATUS_INTERVAL_S", 0.0)
    worker.run_forever()
    assert calls == ["verify", "refresh"]


def test_an_index_job_without_a_vector_store_ends_blocked_and_audited(env: Env) -> None:
    from datetime import UTC, datetime  # noqa: PLC0415

    from devteam_core.ids import new_id  # noqa: PLC0415
    from devteam_state.repositories import rag as rag_repo  # noqa: PLC0415

    with env.sessions.begin() as session:
        job_id = rag_repo.queue_index_job(
            session, job_id=new_id("rij"), project_id=env.project_id, kind="index",
            requested_by=env.owner.id, now=datetime.now(UTC),
        )  # fmt: skip
    worker = Worker(env.rt, env.graph, owner="indexer")
    assert env.rt.ingestor is None  # no vector store configured (KI-045)
    assert worker.run_index_job_once() is True
    assert worker.run_index_job_once() is False  # nothing left
    with env.sessions() as session:
        job = rag_repo.latest_job(session, env.project_id)
        assert job is not None and job.id == job_id
        assert (job.status, job.error_code) == ("blocked", "rag.store_unavailable")
        events = [
            e.details for e in session.scalars(select(AuditEvent).where(AuditEvent.action == "rag.indexed"))
        ]
    assert events == [
        {"job_id": job_id, "kind": "index", "status": "blocked", "error_code": "rag.store_unavailable"}
    ]


def test_the_model_inventory_is_not_written_when_ollama_is_unreachable(env: Env) -> None:
    from devteam_state.repositories import model_calls as model_calls_repo  # noqa: PLC0415

    worker = Worker(env.rt, env.graph, owner="inventory")
    assert worker.refresh_models() is False  # the env's Ollama URL is a closed port
    with env.sessions() as session:
        assert model_calls_repo.inventory(session) == []
