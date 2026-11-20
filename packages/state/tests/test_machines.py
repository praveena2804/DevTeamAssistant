"""C.3 run/task state machines and attempt ledger (HC-19, HS-12): illegal transitions rejected + audited."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.contracts.statuses import AgentRole, RunStatus, Stage, TaskStatus
from devteam_core.contracts.tasks import AgentTask
from devteam_core.ids import new_id
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.machines import (
    RUN_TRANSITIONS,
    TASK_TRANSITIONS,
    IllegalTransitionError,
    transition_run,
    transition_task,
)
from devteam_state.models import AuditEvent, Principal, Project
from devteam_state.repositories import audit, runs

pytestmark = pytest.mark.security
ACTOR = "service:executor"


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    db = tmp_path / "state.db"
    upgrade(db)
    engine = create_sqlite_engine(db)
    yield session_factory(engine)
    engine.dispose()


@pytest.fixture
def project_id(sessions: sessionmaker[Session]) -> str:
    now = datetime.now(UTC)
    pid, owner = new_id("prj"), new_id("prn")
    with sessions.begin() as s:
        s.add(Principal(id=owner, kind="human", display_name="Owner", created_at=now))
        s.add(
            Project(
                id=pid,
                name="P",
                name_key="p",
                description="",
                status="active",
                created_by=owner,
                created_at=now,
                updated_at=now,
            )
        )
    return pid


def _task(project_id: str, run_id: str, max_attempts: int = 2) -> AgentTask:
    return AgentTask(
        task_id=new_id("tsk"),
        project_id=project_id,
        run_id=run_id,
        agent_type=AgentRole.BACKEND_DEVELOPER,
        operation_id="backend_developer.implement",
        objective="Build it.",
        done_criteria=("done",),
        permitted_paths=("generated/backend/",),
        spec_version_id=new_id("spv"),
        max_attempts=max_attempts,
    )


def _actions(sessions: sessionmaker[Session], prefix: str) -> list[tuple[str, str]]:
    with sessions() as s:
        rows = s.scalars(
            select(AuditEvent).where(AuditEvent.action.like(f"{prefix}%")).order_by(AuditEvent.seq)
        )
        return [(r.action, r.outcome) for r in rows]


# -------------------------------------------------------------------------------------- tables


def test_terminal_states_have_no_exit() -> None:
    for status in (RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.BLOCKED):
        assert RUN_TRANSITIONS[status] == frozenset()
    for task_status in (
        TaskStatus.COMPLETED,
        TaskStatus.PARTIALLY_COMPLETED,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
    ):
        assert TASK_TRANSITIONS[task_status] == frozenset()
    assert set(RUN_TRANSITIONS) == set(RunStatus) and set(TASK_TRANSITIONS) == set(TaskStatus)


def test_tables_are_immutable() -> None:
    with pytest.raises(TypeError):
        RUN_TRANSITIONS[RunStatus.COMPLETED] = frozenset({RunStatus.RUNNING})  # type: ignore[index]


# ------------------------------------------------------------------------------------------ runs


def test_happy_run_lifecycle_is_audited(sessions: sessionmaker[Session], project_id: str) -> None:
    with sessions.begin() as s:
        run = runs.create_run(s, project_id=project_id, run_kind="initial", stage=Stage.ANALYSIS)
        for status in (
            RunStatus.RUNNING,
            RunStatus.WAITING_FOR_HUMAN,
            RunStatus.RUNNING,
            RunStatus.PAUSED,
            RunStatus.RUNNING,
            RunStatus.COMPLETED,
        ):
            transition_run(s, run, status, actor=ACTOR)
    assert _actions(sessions, "run.") == [("run.transitioned", "success")] * 6
    with sessions() as s:
        assert audit.verify_chain(s).ok


@pytest.mark.parametrize("current", list(RunStatus))
@pytest.mark.parametrize("requested", list(RunStatus))
def test_every_illegal_run_transition_is_rejected_and_audited(
    sessions: sessionmaker[Session], project_id: str, current: RunStatus, requested: RunStatus
) -> None:
    if requested in RUN_TRANSITIONS[current]:
        return  # legal pairs are covered by the lifecycle test
    with sessions.begin() as s:
        run = runs.create_run(s, project_id=project_id, run_kind="initial", stage=Stage.ANALYSIS)
        run.status = current.value  # direct setup of the starting state (test only)
        run_id = run.id
    with pytest.raises(IllegalTransitionError), sessions.begin() as s:
        loaded = runs.get_run(s, project_id, run_id)
        assert loaded is not None
        transition_run(s, loaded, requested, actor=ACTOR, denial_sessions=sessions)
    with sessions() as s:
        loaded = runs.get_run(s, project_id, run_id)
        assert loaded is not None and loaded.status == current.value  # unchanged
    assert _actions(sessions, "run.transition_denied") == [("run.transition_denied", "denied")]


def test_completed_run_cannot_be_reopened(sessions: sessionmaker[Session], project_id: str) -> None:
    with sessions.begin() as s:
        run = runs.create_run(s, project_id=project_id, run_kind="initial", stage=Stage.BUILD)
        transition_run(s, run, RunStatus.RUNNING, actor=ACTOR)
        transition_run(s, run, RunStatus.COMPLETED, actor=ACTOR)
    with pytest.raises(IllegalTransitionError, match="completed -> running"), sessions.begin() as s:
        transition_run(s, s.merge(run), RunStatus.RUNNING, actor=ACTOR, denial_sessions=sessions)


# ----------------------------------------------------------------------------------------- tasks


def _task_row(sessions: sessionmaker[Session], project_id: str, max_attempts: int = 2) -> tuple[str, str]:
    with sessions.begin() as s:
        run = runs.create_run(s, project_id=project_id, run_kind="initial", stage=Stage.BUILD)
        row = runs.add_task(s, _task(project_id, run.id, max_attempts))
        return run.id, row.id


def _move(sessions: sessionmaker[Session], project_id: str, task_id: str, *statuses: TaskStatus) -> None:
    with sessions.begin() as s:
        row = runs.get_task(s, project_id, task_id)
        assert row is not None
        for status in statuses:
            transition_task(s, row, status, actor=ACTOR, denial_sessions=sessions)


def test_task_lifecycle_counts_attempts(sessions: sessionmaker[Session], project_id: str) -> None:
    _, task_id = _task_row(sessions, project_id)
    _move(
        sessions,
        project_id,
        task_id,
        TaskStatus.READY,
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.READY,
        TaskStatus.RUNNING,
        TaskStatus.COMPLETED,
    )
    with sessions() as s:
        row = runs.get_task(s, project_id, task_id)
        assert row is not None and row.attempt_count == 2 and row.status == "completed"
        contract = runs.load_task_contract(row)
        assert contract.status is TaskStatus.COMPLETED and contract.attempt_count == 2


def test_retry_budget_is_enforced(sessions: sessionmaker[Session], project_id: str) -> None:
    _, task_id = _task_row(sessions, project_id, max_attempts=1)
    _move(sessions, project_id, task_id, TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.FAILED)
    with pytest.raises(IllegalTransitionError, match="no attempts left"):
        _move(sessions, project_id, task_id, TaskStatus.READY)
    assert ("task.transition_denied", "denied") in _actions(sessions, "task.")


@pytest.mark.parametrize(
    "path",
    [
        (TaskStatus.COMPLETED,),  # skipping straight to done
        (TaskStatus.RUNNING,),  # running without being ready
        (TaskStatus.READY, TaskStatus.RUNNING, TaskStatus.COMPLETED, TaskStatus.RUNNING),  # reopening
        (TaskStatus.READY, TaskStatus.SKIPPED, TaskStatus.READY),
    ],
)
def test_illegal_task_transitions_rejected(
    sessions: sessionmaker[Session], project_id: str, path: tuple[TaskStatus, ...]
) -> None:
    _, task_id = _task_row(sessions, project_id)
    with pytest.raises(IllegalTransitionError):
        _move(sessions, project_id, task_id, *path)
    with sessions() as s:
        row = runs.get_task(s, project_id, task_id)
        assert row is not None and row.status == "pending"  # the whole transaction rolled back


def test_tasks_are_isolated_by_project(sessions: sessionmaker[Session], project_id: str) -> None:
    _, task_id = _task_row(sessions, project_id)
    with sessions() as s:
        assert runs.get_task(s, new_id("prj"), task_id) is None
        assert runs.get_task(s, project_id, task_id) is not None


def test_new_tasks_must_be_pending_and_belong_to_an_existing_run(
    sessions: sessionmaker[Session], project_id: str
) -> None:
    with pytest.raises(ValueError, match="run"), sessions.begin() as s:
        runs.add_task(s, _task(project_id, new_id("run")))
    run_id, _ = _task_row(sessions, project_id)
    done = _task(project_id, run_id).model_copy(update={"status": TaskStatus.COMPLETED})
    with pytest.raises(ValueError, match="pending"), sessions.begin() as s:
        runs.add_task(s, done)


# ------------------------------------------------------------------------------- attempt ledger


def test_attempt_ledger_makes_side_effects_idempotent(
    sessions: sessionmaker[Session], project_id: str
) -> None:
    run_id, task_id = _task_row(sessions, project_id)
    _move(sessions, project_id, task_id, TaskStatus.READY, TaskStatus.RUNNING)
    with sessions.begin() as s:
        row = runs.get_task(s, project_id, task_id)
        assert row is not None
        attempt, done = runs.start_attempt(s, row, node="backend_developer")
        assert not done and attempt.key == f"{run_id}:backend_developer:{task_id}:1"
        runs.finish_attempt(s, attempt, ok=True)
    with sessions.begin() as s:  # e.g. the worker restarted and replays the node
        row = runs.get_task(s, project_id, task_id)
        assert row is not None
        again, done = runs.start_attempt(s, row, node="backend_developer")
        assert done and again.key == attempt.key
        with pytest.raises(ValueError, match="already completed"):
            runs.finish_attempt(s, again, ok=False)
