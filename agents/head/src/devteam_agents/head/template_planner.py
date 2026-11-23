"""Head Agent template planner (Part D): a fixed, deterministic build plan. No model is used.

The template is the same for every project: Architect, then Database, Backend and Frontend
Developers, then the Tester. Every in-scope requirement key is carried by every stage, so coverage
is complete by construction. More than 200 keys (the per-task limit) are split into several tasks
per stage; each stage depends on every task of the stage before it.

The Debugger & Code Reviewer is not in the template: it receives repair tasks from the failure
router (:func:`repair_task`). Part J adds the model planner; its output goes through the same
validator.

Each task may write only inside its agent's write scope, which the caller passes in as data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from devteam_core.contracts.statuses import AgentRole
from devteam_core.contracts.tasks import AgentTask
from devteam_core.ids import new_id

KEYS_PER_TASK = 200  # AgentTask.requirement_ids limit


@dataclass(frozen=True, slots=True)
class TemplateStage:
    role: AgentRole
    operation_id: str
    objective: str
    done_criterion: str


TEMPLATE: tuple[TemplateStage, ...] = (
    TemplateStage(
        AgentRole.ARCHITECT,
        "architect.design",
        "Design the architecture and the API contract for the approved specification",
        "architecture documents and API contract written inside the architecture scope",
    ),
    TemplateStage(
        AgentRole.DATABASE_DEVELOPER,
        "database_developer.generate_code",
        "Generate the database models and migrations for the approved specification",
        "database files written inside the database scope",
    ),
    TemplateStage(
        AgentRole.BACKEND_DEVELOPER,
        "backend_developer.generate_code",
        "Generate the backend API for the approved specification",
        "backend files written inside the backend scope",
    ),
    TemplateStage(
        AgentRole.FRONTEND_DEVELOPER,
        "frontend_developer.generate_code",
        "Generate the user interface for the approved specification",
        "frontend files written inside the frontend scope",
    ),
    TemplateStage(
        AgentRole.TESTER,
        "tester.generate_tests",
        "Generate tests that trace to the approved requirements",
        "test files written inside the test scope",
    ),
)

IdFactory = Callable[[str], str]


def _chunks(keys: tuple[str, ...]) -> list[tuple[str, ...]]:
    if not keys:
        return [()]
    return [keys[i : i + KEYS_PER_TASK] for i in range(0, len(keys), KEYS_PER_TASK)]


def template_plan(
    *,
    project_id: str,
    run_id: str,
    spec_version_id: str,
    in_scope_keys: Iterable[str],
    write_scopes: Mapping[AgentRole, tuple[str, ...]],
    make_id: IdFactory = new_id,
) -> tuple[AgentTask, ...]:
    """The template plan's tasks, in dependency order. Validate them with ``validate_plan``."""
    keys = tuple(sorted(set(in_scope_keys)))
    tasks: list[AgentTask] = []
    previous: tuple[str, ...] = ()
    for stage in TEMPLATE:
        current: list[str] = []
        for chunk in _chunks(keys):
            task = AgentTask(
                task_id=make_id("tsk"),
                project_id=project_id,
                run_id=run_id,
                agent_type=stage.role,
                operation_id=stage.operation_id,
                objective=stage.objective,
                requirement_ids=chunk,
                depends_on=previous,
                permitted_paths=write_scopes.get(stage.role, ()),
                done_criteria=(stage.done_criterion,),
                spec_version_id=spec_version_id,
            )
            tasks.append(task)
            current.append(task.task_id)
        previous = tuple(current)
    return tuple(tasks)


def repair_task(
    failed: AgentTask,
    *,
    permitted_paths: tuple[str, ...],
    reason: str,
    make_id: IdFactory = new_id,
) -> AgentTask:
    """A Debugger & Code Reviewer task to repair a failure in ``permitted_paths`` (from the router)."""
    return AgentTask(
        task_id=make_id("tsk"),
        project_id=failed.project_id,
        run_id=failed.run_id,
        agent_type=AgentRole.DEBUGGER_REVIEWER,
        operation_id="debugger_reviewer.repair",
        objective=f"Repair the failure reported for task {failed.task_id}: {reason}"[:2_000],
        requirement_ids=failed.requirement_ids,
        permitted_paths=permitted_paths,
        done_criteria=("the reported failure no longer occurs when the checks are run again",),
        spec_version_id=failed.spec_version_id,
    )
