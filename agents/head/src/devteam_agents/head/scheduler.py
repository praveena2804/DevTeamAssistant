"""Head Agent scheduler (Part D, roadmap §6.3): picks the next step. Deterministic and sequential.

One step runs at a time (Spec §7.2). The order follows the build workflow (roadmap §4.3):

1. code-generation tasks (Architect, Database, Backend, Frontend Developers, and Debugger repair
   tasks), each once all its dependencies are **completed**, in plan order;
2. the Integration/Build service, once every generation task is completed;
3. Tester tasks, only after the integration step;
4. the Test Runner service, once every Tester task is completed;
5. ``route_after_build`` once nothing is left to do.

A task whose dependency ended without completing (failed, blocked, skipped, cancelled, partially
completed) is **skipped** with the reason. A task that can never become ready (for example, Tester
tasks when a repair task failed, so integration cannot run) is skipped too, never left waiting.

``services_done`` lists the service steps already run in the current round. Orchestration clears it
when the failure router adds a repair task, so integration and tests run again after a repair.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from devteam_core.contracts.orchestration import AGENT_NODE_FOR_ROLE, NodeName
from devteam_core.contracts.statuses import AgentRole, TaskStatus
from devteam_core.contracts.tasks import AgentTask

T = TaskStatus

GENERATION_ROLES = frozenset(
    {
        AgentRole.ARCHITECT,
        AgentRole.DATABASE_DEVELOPER,
        AgentRole.BACKEND_DEVELOPER,
        AgentRole.FRONTEND_DEVELOPER,
        AgentRole.DEBUGGER_REVIEWER,
    }
)
TEST_ROLES = frozenset({AgentRole.TESTER})
_OPEN = frozenset({T.PENDING, T.READY})
_ENDED_WITHOUT_COMPLETING = frozenset({T.FAILED, T.BLOCKED, T.SKIPPED, T.CANCELLED, T.PARTIALLY_COMPLETED})


class Action(StrEnum):
    DISPATCH = "dispatch"
    RUN_SERVICE = "run_service"
    SKIP = "skip"
    FINISHED = "finished"


@dataclass(frozen=True, slots=True)
class ScheduleDecision:
    action: Action
    node: NodeName  # where the graph goes next (always allowed after ``schedule``, except SKIP)
    reason: str
    task_id: str | None = None
    skipped: tuple[str, ...] = ()
    all_completed: bool = False


class SchedulerError(RuntimeError):
    """The task list is inconsistent (for example, a task is still running)."""


def next_step(  # noqa: PLR0911 - one return per scheduling decision
    tasks: Sequence[AgentTask], services_done: frozenset[NodeName]
) -> ScheduleDecision:
    running = [t.task_id for t in tasks if t.status is T.RUNNING]
    if running:
        raise SchedulerError(f"task {running[0]} is still running; one step runs at a time")
    status = {t.task_id: t.status for t in tasks}

    skipped = [
        t.task_id
        for t in tasks
        if t.status in _OPEN and any(status.get(d) in _ENDED_WITHOUT_COMPLETING for d in t.depends_on)
    ]
    if skipped:
        return ScheduleDecision(
            Action.SKIP, NodeName.SCHEDULE, "a dependency ended without completing", skipped=tuple(skipped)
        )

    def ready(task: AgentTask) -> bool:
        return task.status in _OPEN and all(status.get(d) is T.COMPLETED for d in task.depends_on)

    generation = [t for t in tasks if t.agent_type in GENERATION_ROLES]
    testing = [t for t in tasks if t.agent_type in TEST_ROLES]

    for task in generation:
        if ready(task):
            return _dispatch(task)
    generated = all(t.status is T.COMPLETED for t in generation)
    if generated and NodeName.INTEGRATION_BUILD not in services_done:
        return ScheduleDecision(
            Action.RUN_SERVICE, NodeName.INTEGRATION_BUILD, "every generation task is completed"
        )
    if NodeName.INTEGRATION_BUILD in services_done:
        for task in testing:
            if ready(task):
                return _dispatch(task)
        if all(t.status is T.COMPLETED for t in testing) and NodeName.TEST_RUNNER not in services_done:
            return ScheduleDecision(Action.RUN_SERVICE, NodeName.TEST_RUNNER, "every test task is completed")

    still_open = tuple(t.task_id for t in tasks if t.status in _OPEN)
    if still_open:
        return ScheduleDecision(
            Action.SKIP,
            NodeName.SCHEDULE,
            "can never become ready: an earlier step did not complete",
            skipped=still_open,
        )
    complete = (
        all(t.status is T.COMPLETED for t in tasks)
        and {
            NodeName.INTEGRATION_BUILD,
            NodeName.TEST_RUNNER,
        }
        <= services_done
    )
    return ScheduleDecision(
        Action.FINISHED,
        NodeName.ROUTE_AFTER_BUILD,
        "every task and service step is done" if complete else "no task can run any more",
        all_completed=complete,
    )


def _dispatch(task: AgentTask) -> ScheduleDecision:
    return ScheduleDecision(
        Action.DISPATCH,
        AGENT_NODE_FOR_ROLE[task.agent_type],
        "dependencies completed",
        task_id=task.task_id,
    )
