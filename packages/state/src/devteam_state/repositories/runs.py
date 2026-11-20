"""Runs, agent tasks and the attempt ledger (HC-19, HS-12). Every query is scoped by ``project_id``.

Status fields are never written here except at creation; changes go through
:mod:`devteam_state.machines`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.statuses import RunStatus, Stage, TaskStatus
from devteam_core.contracts.tasks import AgentTask
from devteam_core.ids import new_id
from devteam_state.models import AgentTaskRow, Run, TaskAttempt


def create_run(
    session: Session, *, project_id: str, run_kind: str, stage: Stage, clock: Clock = system_clock
) -> Run:
    if run_kind not in {"initial", "change_request"}:
        raise ValueError("run_kind must be 'initial' or 'change_request'")
    now = clock.now()
    run = Run(
        id=new_id("run", clock),
        project_id=project_id,
        run_kind=run_kind,
        stage=stage.value,
        status=RunStatus.CREATED.value,
        created_at=now,
        updated_at=now,
    )
    session.add(run)
    session.flush()
    return run


def get_run(session: Session, project_id: str, run_id: str) -> Run | None:
    return session.scalars(select(Run).where(Run.project_id == project_id, Run.id == run_id)).first()


def add_task(session: Session, task: AgentTask, *, clock: Clock = system_clock) -> AgentTaskRow:
    """Persist a validated ``AgentTask`` contract. New tasks always start ``pending`` and unattempted."""
    if task.status is not TaskStatus.PENDING or task.attempt_count:
        raise ValueError("new tasks must be pending and unattempted")
    if get_run(session, task.project_id, task.run_id) is None:
        raise ValueError("the task's run does not exist in this project")
    now = clock.now()
    row = AgentTaskRow(
        id=task.task_id,
        project_id=task.project_id,
        run_id=task.run_id,
        agent_type=task.agent_type.value,
        operation_id=task.operation_id,
        status=TaskStatus.PENDING.value,
        attempt_count=0,
        max_attempts=task.max_attempts,
        payload=task.model_dump(mode="json"),
        created_at=now,
        updated_at=now,
    )
    session.add(row)
    session.flush()
    return row


def get_task(session: Session, project_id: str, task_id: str) -> AgentTaskRow | None:
    return session.scalars(
        select(AgentTaskRow).where(AgentTaskRow.project_id == project_id, AgentTaskRow.id == task_id)
    ).first()


def load_task_contract(row: AgentTaskRow) -> AgentTask:
    """Re-validate the stored contract with the current status and attempt count."""
    return AgentTask.model_validate({**row.payload, "status": row.status, "attempt_count": row.attempt_count})


def attempt_key(run_id: str, node: str, task_id: str, attempt: int) -> str:
    return f"{run_id}:{node}:{task_id}:{attempt}"


def start_attempt(
    session: Session, task: AgentTaskRow, *, node: str, clock: Clock = system_clock
) -> tuple[TaskAttempt, bool]:
    """Record the start of an attempt. Returns ``(attempt, already_completed)``.

    Idempotency (roadmap §6.7): if the same ``run:node:task:attempt`` already completed (for example the
    worker restarted after finishing it), the existing record is returned with ``True`` and the caller
    must not repeat side effects.
    """
    key = attempt_key(task.run_id, node, task.id, task.attempt_count)
    existing = session.scalars(select(TaskAttempt).where(TaskAttempt.key == key)).first()
    if existing is not None:
        return existing, existing.status == "completed"
    attempt = TaskAttempt(
        id=new_id("att", clock),
        project_id=task.project_id,
        run_id=task.run_id,
        task_id=task.id,
        node=node,
        attempt=task.attempt_count,
        key=key,
        status="started",
        started_at=clock.now(),
    )
    session.add(attempt)
    session.flush()
    return attempt, False


def finish_attempt(
    session: Session,
    attempt: TaskAttempt,
    *,
    ok: bool,
    error_code: str | None = None,
    clock: Clock = system_clock,
) -> TaskAttempt:
    if attempt.status != "started":
        raise ValueError(f"attempt {attempt.key} is already {attempt.status}")
    attempt.status = "completed" if ok else "failed"
    attempt.error_code = None if ok else (error_code or "error")
    attempt.finished_at = clock.now()
    session.flush()
    return attempt
