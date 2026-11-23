"""Head Agent dispatcher (Part D, roadmap §4.3 rule 3): hands one task to its agent node.

The dispatcher starts the next attempt of a task and produces the ``DispatchRecord`` with the
attempt-ledger key ``run_id:node:task_id:attempt``. It refuses a task that is not waiting to run, an
agent that is not a build agent, and a task with no attempts left (F-10d). It changes nothing itself:
orchestration applies the returned status changes through the task state machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from devteam_core.contracts.orchestration import AGENT_NODE_FOR_ROLE, DispatchRecord, attempt_key
from devteam_core.contracts.statuses import BUILD_PLAN_ROLES, TaskStatus
from devteam_core.contracts.tasks import AgentTask


class DispatchError(ValueError):
    """The task cannot be dispatched."""


@dataclass(frozen=True, slots=True)
class Dispatch:
    task: AgentTask  # the task as it is once running
    record: DispatchRecord
    transitions: tuple[TaskStatus, ...]  # status changes to apply, in order


def dispatch(task: AgentTask, *, now: datetime) -> Dispatch:
    if task.agent_type not in BUILD_PLAN_ROLES:
        raise DispatchError(f"agent '{task.agent_type.value}' is not dispatched by the Head Agent")
    if task.status not in {TaskStatus.PENDING, TaskStatus.READY}:
        raise DispatchError(f"task {task.task_id} is '{task.status.value}', not waiting to run")
    if task.attempt_count >= task.max_attempts:
        raise DispatchError(f"task {task.task_id} has no attempts left (F-10d)")

    attempt = task.attempt_count + 1
    node = AGENT_NODE_FOR_ROLE[task.agent_type]
    running = task.model_copy(
        update={
            "status": TaskStatus.RUNNING,
            "attempt_count": attempt,
            "started_at": now,
            "completed_at": None,
        }
    )
    running = AgentTask.model_validate(running.model_dump())  # model_copy does not validate
    record = DispatchRecord(
        project_id=task.project_id,
        run_id=task.run_id,
        task_id=task.task_id,
        agent_type=task.agent_type,
        node=node,
        attempt=attempt,
        idempotency_key=attempt_key(task.run_id, node.value, task.task_id, attempt),
        input_artifact_refs=task.input_artifact_refs,
        permitted_paths=task.permitted_paths,
        done_criteria=task.done_criteria,
        dispatched_at=now,
    )
    steps = (
        (TaskStatus.READY, TaskStatus.RUNNING) if task.status is TaskStatus.PENDING else (TaskStatus.RUNNING,)
    )
    return Dispatch(task=running, record=record, transitions=steps)
