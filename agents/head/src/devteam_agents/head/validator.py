"""Head Agent plan validator (Part D, AC-D-04, Spec §23 #10). Deterministic: no I/O, no model.

A candidate plan comes from the template planner now and from the model planner in Part J, so it is
treated as **untrusted data**. The validator checks every task and the plan as a whole and reports
*every* problem with a code (``PlanIssueCode``); it does not stop at the first. Only a candidate with
no issue at all becomes a ``TaskPlan``.

Checks (each has its own code):

* empty plan, too many tasks, duplicate task IDs;
* unknown agent, or an agent that cannot be scheduled in a build plan (agents 4-9 only);
* task for another project or run; task for another spec version;
* a permitted path or expected artefact that is not a safe workspace-relative path (``..``, drives,
  ...) or lies outside the agent's write scope (including another agent's area);
* a task that is not new (status other than pending, or already attempted);
* a requirement key that is not in the approved spec's scope; an in-scope key no task covers;
* a dependency on an unknown task; a dependency cycle (the cycle is named);
* anything else the ``AgentTask`` contract rejects (field names and error types only, never values).

The write scopes of the other agents are passed in as data, so the Head never imports another agent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from devteam_core.contracts._base import check_relative_path, path_in_scope
from devteam_core.contracts.orchestration import PlanIssue, PlanIssueCode, PlanValidationReport
from devteam_core.contracts.statuses import BUILD_PLAN_ROLES, AgentRole, TaskStatus
from devteam_core.contracts.tasks import MAX_PLAN_TASKS, AgentTask, TaskPlan
from devteam_core.ids import is_valid_id

C = PlanIssueCode
_SHOW = 80  # characters of an untrusted value shown in a detail (repr, so control characters are escaped)


@dataclass(frozen=True, slots=True)
class PlanContext:
    """What a plan must match: the run, the approved spec version and the agents' write scopes."""

    plan_id: str
    project_id: str
    run_id: str
    spec_version_id: str
    in_scope_keys: frozenset[str]
    write_scopes: Mapping[AgentRole, tuple[str, ...]]
    source: Literal["template", "model"] = "template"


@dataclass(frozen=True, slots=True)
class ValidatedPlan:
    report: PlanValidationReport
    plan: TaskPlan | None  # set only when the report is ok

    @property
    def ok(self) -> bool:
        return self.plan is not None


def _show(value: object) -> str:
    text = repr(value)
    return text if len(text) <= _SHOW else text[: _SHOW - 3] + "..."


def _strs(value: object) -> tuple[str, ...] | None:
    """The value as a tuple of strings, or None if it is malformed (left to the schema check)."""
    if isinstance(value, list | tuple) and all(isinstance(v, str) for v in value):
        return tuple(value)
    return None


class _Collector:
    def __init__(self) -> None:
        self.issues: list[PlanIssue] = []
        self.tasks_with_issues: set[str] = set()

    def add(self, code: PlanIssueCode, detail: str, task_id: str | None = None) -> None:
        tid = task_id if isinstance(task_id, str) and is_valid_id(task_id, "tsk") else None
        if tid is not None:
            self.tasks_with_issues.add(tid)
        self.issues.append(PlanIssue(code=code, task_id=tid, detail=detail[:500]))


def validate_plan(candidate: Sequence[AgentTask | Mapping[str, Any]], context: PlanContext) -> ValidatedPlan:
    """Check a candidate plan. Returns the report and, only if it is clean, the ``TaskPlan``."""
    found = _Collector()
    raws = _normalise(candidate, found)

    if not raws and not found.issues:
        found.add(C.EMPTY_PLAN, "the plan has no tasks")
    elif len(raws) > MAX_PLAN_TASKS:
        found.add(C.TOO_MANY_TASKS, f"{len(raws)} tasks; at most {MAX_PLAN_TASKS} are allowed")
    else:
        ids = _check_ids(raws, found)
        for raw in raws:
            _check_task(raw, context, found)
        _check_dependencies(raws, ids, found)
        _check_coverage(raws, context, found)
        validated = _check_schema(raws, found)
        if not found.issues:
            return _accept(validated, context, found)
    return _result(context, found, None)


def validate_added_task(
    task: AgentTask, *, known_task_ids: frozenset[str], context: PlanContext
) -> tuple[PlanIssue, ...]:
    """Issues with one task added to a running plan (a repair task from the failure router).

    The same per-task rules apply (agent, run, spec version, scope, new status, requirement keys);
    its dependencies must name tasks already in the plan, and its ID must be new.
    """
    found = _Collector()
    raw = task.model_dump(mode="python")
    if task.task_id in known_task_ids:
        found.add(C.DUPLICATE_TASK_ID, "task ID is already used in this plan", task.task_id)
    _check_task(raw, context, found)
    for dep in task.depends_on:
        if dep not in known_task_ids:
            found.add(C.MISSING_DEPENDENCY, f"depends on unknown task {_show(dep)}", task.task_id)
    return tuple(found.issues)


# --------------------------------------------------------------------------------------- the checks


def _normalise(candidate: Sequence[object], found: _Collector) -> list[dict[str, Any]]:
    raws: list[dict[str, Any]] = []
    for index, item in enumerate(candidate):
        if isinstance(item, AgentTask):
            raws.append(item.model_dump(mode="python"))
        elif isinstance(item, Mapping):
            raws.append(dict(item))
        else:
            found.add(C.SCHEMA_INVALID, f"item {index} is not a task object")
    return raws


def _check_ids(raws: Sequence[Mapping[str, Any]], found: _Collector) -> set[str]:
    seen: set[str] = set()
    for raw in raws:
        tid = raw.get("task_id")
        if not isinstance(tid, str):
            continue
        if tid in seen:
            found.add(C.DUPLICATE_TASK_ID, f"task ID {_show(tid)} is used more than once", tid)
        seen.add(tid)
    return seen


def _check_task(raw: Mapping[str, Any], context: PlanContext, found: _Collector) -> None:
    tid = raw.get("task_id")
    task_id = tid if isinstance(tid, str) else None

    role_value = raw.get("agent_type")
    try:
        role = AgentRole(role_value) if isinstance(role_value, str) else None
    except ValueError:
        role = None
    if role is None:
        found.add(C.UNKNOWN_AGENT, f"agent {_show(role_value)} is not one of the ten agents", task_id)
    elif role not in BUILD_PLAN_ROLES:
        found.add(C.ROLE_NOT_PLANNABLE, f"agent '{role.value}' cannot be scheduled in a build plan", task_id)

    if (raw.get("project_id"), raw.get("run_id")) != (context.project_id, context.run_id):
        found.add(C.FOREIGN_TASK, "the task belongs to another project or run", task_id)
    if raw.get("spec_version_id") != context.spec_version_id:
        found.add(C.MIXED_SPEC_VERSION, "the task is not based on the approved spec version", task_id)

    status = raw.get("status", TaskStatus.PENDING)
    if status != TaskStatus.PENDING or raw.get("attempt_count", 0) != 0:
        found.add(C.TASK_NOT_NEW, "a new plan may contain only pending, unattempted tasks", task_id)

    if role is not None and role in BUILD_PLAN_ROLES:
        scopes = context.write_scopes.get(role, ())
        _check_paths(raw.get("permitted_paths", ()), role, scopes, task_id, found, directory=True)
        _check_paths(raw.get("expected_artifacts", ()), role, scopes, task_id, found, directory=False)

    keys = _strs(raw.get("requirement_ids", ()))
    for key in sorted(set(keys or ()) - context.in_scope_keys):
        found.add(C.UNKNOWN_REQUIREMENT, f"{_show(key)} is not in the approved spec's scope", task_id)


def _check_paths(
    value: object,
    role: AgentRole,
    scopes: tuple[str, ...],
    task_id: str | None,
    found: _Collector,
    *,
    directory: bool,
) -> None:
    paths = _strs(value)
    if paths is None:
        return  # malformed: reported by the schema check
    for path in paths:
        try:
            check_relative_path(path, allow_directory=directory)
        except ValueError:
            found.add(C.PATH_OUTSIDE_SCOPE, f"{_show(path)} is not a safe workspace-relative path", task_id)
            continue
        if not any(path_in_scope(path, scope) for scope in scopes):
            found.add(C.PATH_OUTSIDE_SCOPE, f"{_show(path)} is outside the {role.value} write scope", task_id)


def _check_dependencies(raws: Sequence[Mapping[str, Any]], ids: set[str], found: _Collector) -> None:
    graph: dict[str, set[str]] = {}
    for raw in raws:
        tid = raw.get("task_id")
        deps = _strs(raw.get("depends_on", ()))
        if not isinstance(tid, str) or deps is None:
            continue
        for dep in deps:
            if dep not in ids:
                found.add(C.MISSING_DEPENDENCY, f"depends on unknown task {_show(dep)}", tid)
        graph.setdefault(tid, set()).update(d for d in deps if d in ids)
    cycle = find_cycle(graph)
    if cycle:
        found.add(C.CYCLE, "dependency cycle: " + " -> ".join(cycle), cycle[0])


def find_cycle(graph: Mapping[str, set[str]]) -> list[str]:
    """One dependency cycle as a path that returns to its start (``[a, b, a]``), or ``[]``.

    Iterative depth-first search, so a long dependency chain cannot exhaust the call stack.
    """
    state: dict[str, int] = {}  # 1 = on the current path, 2 = finished
    for start in sorted(graph):
        if state.get(start):
            continue
        path: list[str] = [start]
        pending: list[list[str]] = [sorted(graph.get(start, ()))]
        state[start] = 1
        while path:
            if not pending[-1]:
                state[path.pop()] = 2
                pending.pop()
                continue
            nxt = pending[-1].pop(0)
            if state.get(nxt) == 1:
                return [*path[path.index(nxt) :], nxt]
            if not state.get(nxt):
                state[nxt] = 1
                path.append(nxt)
                pending.append(sorted(graph.get(nxt, ())))
    return []


def _check_coverage(raws: Sequence[Mapping[str, Any]], context: PlanContext, found: _Collector) -> None:
    covered = {key for raw in raws for key in (_strs(raw.get("requirement_ids", ())) or ())}
    for key in sorted(context.in_scope_keys - covered):
        found.add(C.UNCOVERED_REQUIREMENT, f"{key} is not covered by any task")


def _check_schema(raws: Sequence[Mapping[str, Any]], found: _Collector) -> list[AgentTask]:
    """Full ``AgentTask`` validation for tasks with no specific issue yet (no duplicate reports)."""
    validated: list[AgentTask] = []
    for raw in raws:
        tid = raw.get("task_id")
        try:
            validated.append(AgentTask.model_validate(raw))
        except ValueError:  # pydantic's ValidationError; its text may echo values, so it is not shown
            if isinstance(tid, str) and tid in found.tasks_with_issues:
                continue
            found.add(C.SCHEMA_INVALID, _schema_detail(raw), tid if isinstance(tid, str) else None)
    return validated


def _schema_detail(raw: Mapping[str, Any]) -> str:
    """Name unknown and missing fields (never values: the candidate is untrusted)."""
    fields = AgentTask.model_fields
    unknown = sorted(str(k)[:40] for k in raw if k not in fields)[:5]
    missing = sorted(name for name, f in fields.items() if f.is_required() and name not in raw)
    parts = ["the task does not match the AgentTask contract"]
    if unknown:
        parts.append("unknown fields: " + ", ".join(unknown))
    if missing:
        parts.append("missing fields: " + ", ".join(missing))
    return "; ".join(parts)


def _accept(tasks: list[AgentTask], context: PlanContext, found: _Collector) -> ValidatedPlan:
    try:
        plan = TaskPlan(
            plan_id=context.plan_id,
            project_id=context.project_id,
            run_id=context.run_id,
            spec_version_id=context.spec_version_id,
            tasks=tuple(tasks),
        )
    except ValueError:  # defence in depth: the contract disagrees with the checks above
        found.add(C.SCHEMA_INVALID, "the plan was rejected by the TaskPlan contract")
        return _result(context, found, None)
    return _result(context, found, plan)


def _result(context: PlanContext, found: _Collector, plan: TaskPlan | None) -> ValidatedPlan:
    report = PlanValidationReport(
        plan_id=context.plan_id,
        run_id=context.run_id,
        ok=not found.issues,
        issues=tuple(found.issues[:1_000]),
        source=context.source,
    )
    return ValidatedPlan(report=report, plan=plan)
