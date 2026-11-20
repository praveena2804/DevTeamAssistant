"""``AgentTask`` (Spec §6.4 + F-11) and ``TaskPlan`` structural validation.

A task is a **handoff**: it tells one agent what to do, what it may read and where it may write.
The contract makes a malicious or malformed handoff unrepresentable:

- the operation must belong to the task's own agent (no borrowing another agent's operation);
- write scopes are workspace-relative and cannot climb out (``..``, drives, UNC, streams);
- build-stage tasks must name the approved, frozen spec version (F-11b);
- attempts are bounded (F-10d: at most 1 + 3 attempts);
- a task can carry only a task status; it has no way to express a project status such as Verified.

``TaskPlan`` adds the plan-level checks the Head Agent's plan validator relies on: known agents,
existing dependencies, no cycles, one project/run/spec version, and requirement coverage.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Self

from pydantic import Field, StringConstraints, model_validator

from devteam_core.contracts._base import (
    AwareDatetime,
    Contract,
    PathScope,
    PlanId,
    ProjectId,
    RecordRef,
    RelPath,
    RunId,
    ShortText,
    SpecVersionId,
    TaskId,
    TraceKey,
    path_in_scope,
)
from devteam_core.contracts.statuses import BUILD_PLAN_ROLES, SPEC_BOUND_ROLES, AgentRole, TaskStatus

MAX_TASK_ATTEMPTS = 4  # 1 attempt + F-10d maximum of 3 re-attempts
MAX_PLAN_TASKS = 500
_LIST = 200

OperationId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z_]{1,40}\.[a-z][a-z0-9_]{1,60}$")]
BudgetProfileName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,40}$")]


class AgentTask(Contract):
    task_id: TaskId
    project_id: ProjectId
    run_id: RunId  # F-11a
    agent_type: AgentRole
    operation_id: OperationId  # F-11c (D-37)
    objective: ShortText
    requirement_ids: tuple[TraceKey, ...] = Field(default=(), max_length=_LIST)
    input_artifact_refs: tuple[RecordRef, ...] = Field(default=(), max_length=_LIST)
    depends_on: tuple[TaskId, ...] = Field(default=(), max_length=_LIST)
    permitted_paths: tuple[PathScope, ...] = Field(default=(), max_length=50)
    expected_artifacts: tuple[RelPath, ...] = Field(default=(), max_length=_LIST)
    done_criteria: tuple[ShortText, ...] = Field(min_length=1, max_length=50)
    status: TaskStatus = TaskStatus.PENDING
    attempt_count: int = Field(default=0, ge=0, le=MAX_TASK_ATTEMPTS)
    max_attempts: int = Field(default=2, ge=1, le=MAX_TASK_ATTEMPTS)
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    result_ref: RecordRef | None = None
    blocker_reason: ShortText | None = None
    spec_version_id: SpecVersionId | None = None  # F-11b
    budget_profile: BudgetProfileName = "default"  # F-11d

    @model_validator(mode="after")
    def _handoff_rules(self) -> Self:
        if not self.operation_id.startswith(f"{self.agent_type.value}."):
            raise ValueError(f"operation '{self.operation_id}' does not belong to agent '{self.agent_type}'")
        if self.task_id in self.depends_on:
            raise ValueError("a task cannot depend on itself")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise ValueError("duplicate dependencies")
        if self.attempt_count > self.max_attempts:
            raise ValueError("attempt_count exceeds max_attempts")
        if self.agent_type in SPEC_BOUND_ROLES and self.spec_version_id is None:
            raise ValueError(f"{self.agent_type.value} tasks must name the approved spec version")
        for artifact in self.expected_artifacts:
            if not any(path_in_scope(artifact, scope) for scope in self.permitted_paths):
                raise ValueError(f"expected artifact '{artifact}' lies outside the task's permitted paths")
        if self.completed_at is not None and (self.started_at is None or self.completed_at < self.started_at):
            raise ValueError("completed_at requires started_at and must not precede it")
        if self.status is TaskStatus.BLOCKED and self.blocker_reason is None:
            raise ValueError("a blocked task must state the blocker")
        return self


class TaskPlan(Contract):
    plan_id: PlanId
    project_id: ProjectId
    run_id: RunId
    spec_version_id: SpecVersionId
    tasks: tuple[AgentTask, ...] = Field(min_length=1, max_length=MAX_PLAN_TASKS)

    @model_validator(mode="after")
    def _plan_rules(self) -> Self:
        ids = [t.task_id for t in self.tasks]
        if len(set(ids)) != len(ids):
            raise ValueError("task IDs must be unique within a plan")
        known = set(ids)
        for task in self.tasks:
            if task.agent_type not in BUILD_PLAN_ROLES:
                raise ValueError(f"agent '{task.agent_type.value}' cannot be scheduled in a build plan")
            if (task.project_id, task.run_id, task.spec_version_id) != (
                self.project_id,
                self.run_id,
                self.spec_version_id,
            ):
                raise ValueError(f"task {task.task_id} belongs to another project, run or spec version")
            missing = [d for d in task.depends_on if d not in known]
            if missing:
                raise ValueError(f"task {task.task_id} depends on unknown task(s): {', '.join(missing)}")
            if task.status is not TaskStatus.PENDING or task.attempt_count:
                raise ValueError("a new plan may contain only pending, unattempted tasks")
        self.topological_order()  # raises on cycles
        return self

    def topological_order(self) -> tuple[TaskId, ...]:
        """Dependency order (Kahn's algorithm; ties broken by plan order). Raises on a cycle."""
        remaining = {t.task_id: set(t.depends_on) for t in self.tasks}
        order: list[TaskId] = []
        while remaining:
            ready = [tid for tid, deps in remaining.items() if not deps]
            if not ready:
                raise ValueError("the plan contains a dependency cycle: " + ", ".join(sorted(remaining)))
            for tid in ready:
                order.append(tid)
                del remaining[tid]
            for deps in remaining.values():
                deps.difference_update(ready)
        return tuple(order)

    def uncovered(self, in_scope: Iterable[str]) -> frozenset[str]:
        """In-scope requirement/rule keys that no task covers (Spec §6.4 success criterion)."""
        covered = {key for task in self.tasks for key in task.requirement_ids}
        return frozenset(set(in_scope) - covered)
