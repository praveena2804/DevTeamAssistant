"""Run and task state machines (HC-19, HS-12; roadmap §6.4, Spec §20.2).

Run and task statuses change **only** through :func:`transition_run` / :func:`transition_task`. Every
transition is checked against the static tables below; an illegal one raises :class:`IllegalTransition`
and is audited as ``run.transition_denied`` / ``task.transition_denied`` (in its own transaction, so the
denial is recorded even though the caller's work is rolled back). Legal transitions are audited as
``run.transitioned`` / ``task.transitioned`` in the caller's transaction.

Agents never call these functions: the executor, scheduler and gate services do (C.4, D).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType

from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.statuses import RunStatus, TaskStatus
from devteam_state.models import AgentTaskRow, Run
from devteam_state.repositories import audit

R, T = RunStatus, TaskStatus

RUN_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = MappingProxyType(
    {
        R.CREATED: frozenset({R.RUNNING, R.CANCELLED}),
        R.RUNNING: frozenset(
            {
                R.PAUSED,
                R.WAITING_FOR_HUMAN,
                R.WAITING_FOR_RESOURCES,
                R.COMPLETED,
                R.CANCELLED,
                R.FAILED,
                R.BLOCKED,
            }
        ),
        R.PAUSED: frozenset({R.RUNNING, R.CANCELLED}),
        R.WAITING_FOR_HUMAN: frozenset({R.RUNNING, R.CANCELLED}),
        R.WAITING_FOR_RESOURCES: frozenset({R.RUNNING, R.CANCELLED, R.BLOCKED}),
        R.COMPLETED: frozenset(),
        R.CANCELLED: frozenset(),
        R.FAILED: frozenset(),
        R.BLOCKED: frozenset(),
    }
)

TASK_TRANSITIONS: Mapping[TaskStatus, frozenset[TaskStatus]] = MappingProxyType(
    {
        T.PENDING: frozenset({T.READY, T.BLOCKED, T.SKIPPED, T.CANCELLED}),
        T.READY: frozenset({T.RUNNING, T.BLOCKED, T.SKIPPED, T.CANCELLED}),
        T.RUNNING: frozenset(
            {T.COMPLETED, T.PARTIALLY_COMPLETED, T.FAILED, T.BLOCKED, T.CANCELLED, T.READY}  # READY = retry
        ),
        T.FAILED: frozenset({T.READY}),  # re-attempt, only while attempts remain (F-10d)
        T.BLOCKED: frozenset({T.READY, T.CANCELLED}),
        T.COMPLETED: frozenset(),
        T.PARTIALLY_COMPLETED: frozenset(),
        T.SKIPPED: frozenset(),
        T.CANCELLED: frozenset(),
    }
)


class IllegalTransitionError(RuntimeError):
    def __init__(self, kind: str, entity_id: str, current: str, requested: str, reason: str) -> None:
        super().__init__(f"illegal {kind} transition {current} -> {requested} for {entity_id}: {reason}")
        self.kind = kind
        self.entity_id = entity_id
        self.current = current
        self.requested = requested
        self.reason = reason


IllegalTransition = IllegalTransitionError


def _record_when_released(
    session: Session, denial_sessions: sessionmaker[Session], write: Callable[[Session], None]
) -> None:
    """Write the denial in its own transaction once the caller's transaction has ended.

    SQLite allows one writer: if the caller's transaction already holds the write lock, a second
    connection cannot write until it is released. The record is therefore written right after the
    caller's commit or rollback, so it survives the rollback that normally follows a denial.
    """
    fired = False

    def hook(_session: Session) -> None:
        nonlocal fired
        if fired:
            return
        fired = True  # commit and rollback hooks are both registered; only the first one writes
        with denial_sessions.begin() as own:
            write(own)

    if not session.in_transaction():
        with denial_sessions.begin() as own:
            write(own)
        return
    event.listen(session, "after_commit", hook, once=True)
    event.listen(session, "after_rollback", hook, once=True)


def _deny(
    session: Session,
    denial_sessions: sessionmaker[Session] | None,
    *,
    kind: str,
    entity_id: str,
    project_id: str,
    actor: str,
    current: str,
    requested: str,
    reason: str,
) -> IllegalTransitionError:
    if denial_sessions is not None:

        def write(own: Session) -> None:
            audit.append(
                own,
                action=f"{kind}.transition_denied",
                outcome="denied",
                principal_id=actor,
                project_id=project_id,
                target_type=kind,
                target_id=entity_id,
                details={"from": current, "to": requested, "reason": reason},
            )

        _record_when_released(session, denial_sessions, write)
    return IllegalTransitionError(kind, entity_id, current, requested, reason)


def transition_run(
    session: Session,
    run: Run,
    to: RunStatus,
    *,
    actor: str,
    reason: str | None = None,
    denial_sessions: sessionmaker[Session] | None = None,
    clock: Clock = system_clock,
) -> Run:
    current = RunStatus(run.status)
    if to not in RUN_TRANSITIONS[current]:
        raise _deny(
            session,
            denial_sessions,
            kind="run",
            entity_id=run.id,
            project_id=run.project_id,
            actor=actor,
            current=current.value,
            requested=to.value,
            reason="not an allowed run transition",
        )
    run.status = to.value
    run.updated_at = clock.now()
    audit.append(
        session,
        action="run.transitioned",
        outcome="success",
        principal_id=actor,
        project_id=run.project_id,
        target_type="run",
        target_id=run.id,
        details={"from": current.value, "to": to.value, "reason": reason},
    )
    session.flush()
    return run


def transition_task(
    session: Session,
    task: AgentTaskRow,
    to: TaskStatus,
    *,
    actor: str,
    reason: str | None = None,
    denial_sessions: sessionmaker[Session] | None = None,
    clock: Clock = system_clock,
) -> AgentTaskRow:
    current = TaskStatus(task.status)
    problem: str | None = None
    if to not in TASK_TRANSITIONS[current]:
        problem = "not an allowed task transition"
    elif current is TaskStatus.FAILED and to is TaskStatus.READY and task.attempt_count >= task.max_attempts:
        problem = f"no attempts left ({task.attempt_count}/{task.max_attempts})"
    if problem is not None:
        raise _deny(
            session,
            denial_sessions,
            kind="task",
            entity_id=task.id,
            project_id=task.project_id,
            actor=actor,
            current=current.value,
            requested=to.value,
            reason=problem,
        )
    if to is TaskStatus.RUNNING:
        task.attempt_count += 1
    task.status = to.value
    task.updated_at = clock.now()
    audit.append(
        session,
        action="task.transitioned",
        outcome="success",
        principal_id=actor,
        project_id=task.project_id,
        target_type="task",
        target_id=task.id,
        details={"from": current.value, "to": to.value, "reason": reason, "attempt": task.attempt_count},
    )
    session.flush()
    return task
