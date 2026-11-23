"""Head Agent plan validator and template planner (D.2; AC-D-04, Spec §23 #10).

Every rejection reason has its own test asserting its specific ``PlanIssueCode``. Each test starts from a
template plan that is proven valid (the negative control) and plants exactly one defect.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from devteam_agents.head.template_planner import KEYS_PER_TASK, TEMPLATE, repair_task, template_plan
from devteam_agents.head.validator import (
    PlanContext,
    find_cycle,
    validate_added_task,
    validate_plan,
)
from devteam_core.contracts.orchestration import PlanIssueCode
from devteam_core.contracts.statuses import BUILD_PLAN_ROLES, AgentRole, TaskStatus
from devteam_core.contracts.tasks import MAX_PLAN_TASKS
from devteam_core.ids import new_id
from tests.agents._support import KEYS, WRITE_SCOPES, context, id_factory

C = PlanIssueCode


def _plan(ctx: PlanContext, keys: frozenset[str] | None = None) -> list[dict[str, Any]]:
    tasks = template_plan(
        project_id=ctx.project_id,
        run_id=ctx.run_id,
        spec_version_id=ctx.spec_version_id,
        in_scope_keys=KEYS if keys is None else keys,
        write_scopes=WRITE_SCOPES,
        make_id=id_factory(),
    )
    return [t.model_dump(mode="python") for t in tasks]


def _codes(candidate: list[Any], ctx: PlanContext) -> list[PlanIssueCode]:
    result = validate_plan(candidate, ctx)
    assert not result.ok
    assert result.plan is None
    assert not result.report.ok
    return [issue.code for issue in result.report.issues]


# ------------------------------------------------------------------------------- the clean plan


def test_the_template_plan_validates() -> None:
    ctx = context()
    result = validate_plan(_plan(ctx), ctx)
    assert result.ok, result.report.issues
    assert result.report.ok and result.report.issues == ()
    assert result.report.source == "template"
    assert result.plan is not None
    plan = result.plan
    assert (plan.plan_id, plan.project_id, plan.run_id, plan.spec_version_id) == (
        ctx.plan_id,
        ctx.project_id,
        ctx.run_id,
        ctx.spec_version_id,
    )
    assert [t.agent_type for t in plan.tasks] == [stage.role for stage in TEMPLATE]
    assert plan.uncovered(KEYS) == frozenset()


def test_the_template_follows_the_build_order_and_stays_in_scope() -> None:
    ctx = context()
    tasks = validate_plan(_plan(ctx), ctx).plan
    assert tasks is not None
    order = [t.agent_type for t in tasks.tasks]
    assert order == [
        AgentRole.ARCHITECT,
        AgentRole.DATABASE_DEVELOPER,
        AgentRole.BACKEND_DEVELOPER,
        AgentRole.FRONTEND_DEVELOPER,
        AgentRole.TESTER,
    ]
    for previous, task in zip(tasks.tasks, tasks.tasks[1:], strict=False):
        assert task.depends_on == (previous.task_id,)
    for task in tasks.tasks:
        assert task.permitted_paths == WRITE_SCOPES[task.agent_type]
        assert task.operation_id.startswith(f"{task.agent_type.value}.")
        assert task.status is TaskStatus.PENDING


def test_more_keys_than_one_task_holds_are_split_and_still_covered() -> None:
    keys = frozenset(f"FR-{n:03d}" for n in range(1, KEYS_PER_TASK + 51))
    ctx = context(in_scope_keys=keys)
    result = validate_plan(_plan(ctx, keys), ctx)
    assert result.ok, result.report.issues
    assert result.plan is not None
    assert len(result.plan.tasks) == 2 * len(TEMPLATE)
    architects = [t for t in result.plan.tasks if t.agent_type is AgentRole.ARCHITECT]
    databases = [t for t in result.plan.tasks if t.agent_type is AgentRole.DATABASE_DEVELOPER]
    assert all(set(d.depends_on) == {a.task_id for a in architects} for d in databases)


def test_a_plan_with_no_in_scope_keys_still_has_every_stage() -> None:
    ctx = context(in_scope_keys=frozenset())
    result = validate_plan(_plan(ctx, frozenset()), ctx)
    assert result.ok
    assert result.plan is not None
    assert len(result.plan.tasks) == len(TEMPLATE)


@settings(max_examples=40, deadline=None)
@given(
    keys=st.frozensets(
        st.builds(
            lambda prefix, n: f"{prefix}-{n:03d}",
            st.sampled_from(["FR", "NFR", "BR"]),
            st.integers(min_value=1, max_value=999),
        ),
        max_size=450,
    )
)
def test_every_template_plan_validates(keys: frozenset[str]) -> None:
    """Property (Part D plan §7): whatever the scope, the template planner's plan is accepted."""
    ctx = context(in_scope_keys=keys)
    result = validate_plan(_plan(ctx, keys), ctx)
    assert result.ok, result.report.issues


# ------------------------------------------------------------------- one planted defect per test


def test_empty_plan() -> None:
    assert _codes([], context()) == [C.EMPTY_PLAN]


def test_too_many_tasks() -> None:
    ctx = context()
    one = _plan(ctx)[0]
    assert _codes([one] * (MAX_PLAN_TASKS + 1), ctx) == [C.TOO_MANY_TASKS]


def test_duplicate_task_ids() -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[2]["task_id"] = plan[1]["task_id"]
    plan[2]["depends_on"] = ()
    assert C.DUPLICATE_TASK_ID in _codes(plan, ctx)


def test_unknown_agent() -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[1]["agent_type"] = "deployment_wizard"  # an eleventh agent does not exist
    codes = _codes(plan, ctx)
    assert codes == [C.UNKNOWN_AGENT]


@pytest.mark.parametrize(
    "role",
    [AgentRole.BRD_ANALYST, AgentRole.REQUIREMENTS_FINALIZER, AgentRole.HEAD, AgentRole.DOCUMENTER],
)
def test_roles_outside_the_build_plan_are_refused(role: AgentRole) -> None:
    assert role not in BUILD_PLAN_ROLES
    ctx = context()
    plan = _plan(ctx)
    plan[1]["agent_type"] = role
    plan[1]["operation_id"] = f"{role.value}.anything"
    assert C.ROLE_NOT_PLANNABLE in _codes(plan, ctx)


def test_missing_dependency() -> None:
    ctx = context()
    plan = _plan(ctx)
    ghost = new_id("tsk")
    plan[3]["depends_on"] = (ghost,)
    result = validate_plan(plan, ctx)
    issues = [i for i in result.report.issues if i.code is C.MISSING_DEPENDENCY]
    assert len(issues) == 1
    assert issues[0].task_id == plan[3]["task_id"]
    assert ghost in issues[0].detail


def test_cycle_is_rejected_and_named() -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[0]["depends_on"] = (plan[2]["task_id"],)  # architect -> backend -> database -> architect
    result = validate_plan(plan, ctx)
    cycles = [i for i in result.report.issues if i.code is C.CYCLE]
    assert len(cycles) == 1
    for task in plan[:3]:
        assert task["task_id"] in cycles[0].detail
    assert [i.code for i in result.report.issues] == [C.CYCLE]


def test_self_dependency_is_reported_once_as_a_cycle() -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[1]["depends_on"] = (plan[1]["task_id"],)
    assert _codes(plan, ctx) == [C.CYCLE]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("permitted_paths", ("generated/frontend/",)),  # another agent's scope
        ("permitted_paths", ("generated/",)),  # wider than the agent's scope
        ("permitted_paths", ("generated/../../secrets/",)),  # climbing out
        ("permitted_paths", ("C:/Windows/",)),  # a drive
        ("permitted_paths", ("tests/",)),  # outside generated/
        ("expected_artifacts", ("generated/frontend/app.tsx",)),
        ("expected_artifacts", ("../outside.py",)),
    ],
)
def test_paths_outside_the_agents_write_scope(field: str, value: tuple[str, ...]) -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[2][field] = value  # the Backend Developer's task
    codes = _codes(plan, ctx)
    assert codes == [C.PATH_OUTSIDE_SCOPE]


def test_uncovered_requirement() -> None:
    ctx = context()
    plan = _plan(ctx, KEYS - {"FR-002"})
    result = validate_plan(plan, ctx)
    assert [i.code for i in result.report.issues] == [C.UNCOVERED_REQUIREMENT]
    assert "FR-002" in result.report.issues[0].detail


def test_unknown_requirement() -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[0]["requirement_ids"] = (*plan[0]["requirement_ids"], "FR-999")
    assert _codes(plan, ctx) == [C.UNKNOWN_REQUIREMENT]


def test_mixed_spec_version() -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[4]["spec_version_id"] = new_id("spv")
    assert _codes(plan, ctx) == [C.MIXED_SPEC_VERSION]


@pytest.mark.parametrize("field", ["project_id", "run_id"])
def test_task_from_another_project_or_run(field: str) -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[1][field] = new_id("prj" if field == "project_id" else "run")
    assert _codes(plan, ctx) == [C.FOREIGN_TASK]


@pytest.mark.parametrize(
    "change", [{"status": TaskStatus.COMPLETED}, {"status": TaskStatus.RUNNING}, {"attempt_count": 1}]
)
def test_a_new_plan_holds_only_new_tasks(change: dict[str, Any]) -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[1].update(change)
    assert C.TASK_NOT_NEW in _codes(plan, ctx)


def test_other_contract_violations_name_fields_never_values() -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[1]["operation_id"] = "frontend_developer.generate_code"  # borrowed from another agent
    plan[2]["claims_verified"] = "SECRET-CANARY-VALUE"  # an unknown field carrying a value
    del plan[3]["done_criteria"]
    result = validate_plan(plan, ctx)
    issues = result.report.issues
    assert [i.code for i in issues] == [C.SCHEMA_INVALID] * 3
    details = " ".join(i.detail for i in issues)
    assert "claims_verified" in details
    assert "done_criteria" in details
    assert "SECRET-CANARY-VALUE" not in details


def test_items_that_are_not_tasks() -> None:
    ctx = context(in_scope_keys=frozenset())  # with keys in scope they would also be uncovered
    assert _codes(["not a task", 42], ctx) == [C.SCHEMA_INVALID, C.SCHEMA_INVALID]


def test_untrusted_values_are_shown_escaped_and_shortened() -> None:
    ctx = context()
    plan = _plan(ctx)
    plan[1]["agent_type"] = "evil\n" + "x" * 500
    detail = validate_plan(plan, ctx).report.issues[0].detail
    assert "\n" not in detail
    assert len(detail) < 200


def test_every_problem_is_reported_not_just_the_first() -> None:
    ctx = context()
    plan = _plan(ctx, KEYS - {"BR-001"})
    plan[0]["permitted_paths"] = ("generated/frontend/",)
    plan[1]["agent_type"] = "eleventh_agent"
    plan[3]["depends_on"] = (new_id("tsk"),)
    plan[4]["spec_version_id"] = new_id("spv")
    codes = set(_codes(plan, ctx))
    assert codes == {
        C.PATH_OUTSIDE_SCOPE,
        C.UNKNOWN_AGENT,
        C.MISSING_DEPENDENCY,
        C.MIXED_SPEC_VERSION,
        C.UNCOVERED_REQUIREMENT,
    }


def test_model_plans_are_labelled_as_such() -> None:
    ctx = context(source="model")
    assert validate_plan(_plan(ctx), ctx).report.source == "model"


def test_agent_task_objects_are_accepted_as_candidates() -> None:
    ctx = context()
    tasks = template_plan(
        project_id=ctx.project_id,
        run_id=ctx.run_id,
        spec_version_id=ctx.spec_version_id,
        in_scope_keys=KEYS,
        write_scopes=WRITE_SCOPES,
    )
    assert validate_plan(tasks, ctx).ok


# ---------------------------------------------------------------------------- cycles and repairs


def test_find_cycle() -> None:
    assert find_cycle({}) == []
    assert find_cycle({"a": {"b"}, "b": {"c"}, "c": set()}) == []
    assert find_cycle({"a": {"a"}}) == ["a", "a"]
    cycle = find_cycle({"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}})
    assert cycle[0] == cycle[-1] and set(cycle) == {"a", "b", "c"}
    long_chain = {f"n{i:05d}": {f"n{i + 1:05d}"} for i in range(5_000)}
    assert find_cycle(long_chain) == []  # no recursion limit


def test_repair_tasks_are_checked_like_plan_tasks() -> None:
    ctx = context()
    plan = validate_plan(_plan(ctx), ctx).plan
    assert plan is not None
    known = frozenset(t.task_id for t in plan.tasks)
    backend = plan.tasks[2]

    good = repair_task(backend, permitted_paths=("generated/backend/",), reason="tests failed")
    assert good.agent_type is AgentRole.DEBUGGER_REVIEWER
    assert good.requirement_ids == backend.requirement_ids
    assert validate_added_task(good, known_task_ids=known, context=ctx) == ()

    tests_dir = repair_task(backend, permitted_paths=("generated/tests/",), reason="x")
    assert [i.code for i in validate_added_task(tests_dir, known_task_ids=known, context=ctx)] == [
        C.PATH_OUTSIDE_SCOPE
    ]
    reused = good.model_copy(update={"task_id": backend.task_id})
    assert [i.code for i in validate_added_task(reused, known_task_ids=known, context=ctx)] == [
        C.DUPLICATE_TASK_ID
    ]
    dangling = good.model_copy(update={"depends_on": (new_id("tsk"),)})
    assert [i.code for i in validate_added_task(dangling, known_task_ids=known, context=ctx)] == [
        C.MISSING_DEPENDENCY
    ]
