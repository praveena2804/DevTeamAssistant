"""Head Agent scheduler, dispatcher and failure router (D.2; roadmap §6.3, F-10, D-40)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from devteam_agents.head.dispatcher import DispatchError, dispatch
from devteam_agents.head.failure_router import (
    Failure,
    FailureSource,
    RouteAction,
    route_crash,
    route_failure,
)
from devteam_agents.head.scheduler import Action, SchedulerError, next_step
from devteam_agents.head.template_planner import repair_task, template_plan
from devteam_core.contracts.errors import ErrorCategory, is_auto_retryable
from devteam_core.contracts.orchestration import (
    AGENT_NODE_FOR_ROLE,
    ALLOWED_TRANSITIONS,
    NodeName,
    attempt_key,
)
from devteam_core.contracts.statuses import AgentRole, TaskStatus
from devteam_core.contracts.tasks import AgentTask
from tests.agents._support import KEYS, WRITE_SCOPES, context

T = TaskStatus
N = NodeName
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
NONE: frozenset[NodeName] = frozenset()
INTEGRATED = frozenset({N.INTEGRATION_BUILD})
BOTH = frozenset({N.INTEGRATION_BUILD, N.TEST_RUNNER})


def _plan() -> list[AgentTask]:
    ctx = context()
    return list(
        template_plan(
            project_id=ctx.project_id,
            run_id=ctx.run_id,
            spec_version_id=ctx.spec_version_id,
            in_scope_keys=KEYS,
            write_scopes=WRITE_SCOPES,
        )
    )


def _set(tasks: list[AgentTask], index: int, status: TaskStatus) -> None:
    tasks[index] = tasks[index].model_copy(update={"status": status})


def _complete(tasks: list[AgentTask], *indexes: int) -> None:
    for i in indexes:
        _set(tasks, i, T.COMPLETED)


def _allowed_after_schedule(node: NodeName) -> bool:
    return node in ALLOWED_TRANSITIONS[N.SCHEDULE]


# ------------------------------------------------------------------------------------- scheduler


def test_a_full_round_runs_in_workflow_order() -> None:
    tasks = _plan()
    done: set[NodeName] = set()
    visited: list[str] = []
    for _ in range(20):
        decision = next_step(tasks, frozenset(done))
        assert decision.action is Action.SKIP or _allowed_after_schedule(decision.node)
        if decision.action is Action.FINISHED:
            assert decision.node is N.ROUTE_AFTER_BUILD
            assert decision.all_completed
            break
        if decision.action is Action.DISPATCH:
            index = next(i for i, t in enumerate(tasks) if t.task_id == decision.task_id)
            assert decision.node is AGENT_NODE_FOR_ROLE[tasks[index].agent_type]
            visited.append(tasks[index].agent_type.value)
            _complete(tasks, index)
        else:
            assert decision.action is Action.RUN_SERVICE
            visited.append(decision.node.value)
            done.add(decision.node)
    assert visited == [
        "architect",
        "database_developer",
        "backend_developer",
        "frontend_developer",
        "integration_build",
        "tester",
        "test_runner",
    ]


def test_only_one_step_at_a_time() -> None:
    tasks = _plan()
    _set(tasks, 0, T.RUNNING)
    with pytest.raises(SchedulerError, match="one step runs at a time"):
        next_step(tasks, NONE)


def test_a_task_waits_for_its_dependencies() -> None:
    tasks = _plan()
    assert next_step(tasks, NONE).task_id == tasks[0].task_id
    _set(tasks, 0, T.READY)  # retried task is picked again, nothing later jumps ahead
    assert next_step(tasks, NONE).task_id == tasks[0].task_id


def test_tests_wait_for_the_integration_step() -> None:
    tasks = _plan()
    _complete(tasks, 0, 1, 2, 3)
    decision = next_step(tasks, NONE)
    assert (decision.action, decision.node) == (Action.RUN_SERVICE, N.INTEGRATION_BUILD)
    assert next_step(tasks, INTEGRATED).node is N.TESTER


@pytest.mark.parametrize("ended", [T.FAILED, T.BLOCKED, T.SKIPPED, T.CANCELLED, T.PARTIALLY_COMPLETED])
def test_dependents_of_an_unfinished_task_are_skipped_with_a_reason(ended: TaskStatus) -> None:
    tasks = _plan()
    _complete(tasks, 0)
    _set(tasks, 1, ended)
    decision = next_step(tasks, NONE)
    assert decision.action is Action.SKIP
    assert decision.skipped == (tasks[2].task_id,)
    assert "dependency" in decision.reason


def test_skips_cascade_then_the_round_finishes_incomplete() -> None:
    tasks = _plan()
    _set(tasks, 0, T.FAILED)
    for _ in range(10):
        decision = next_step(tasks, NONE)
        if decision.action is not Action.SKIP:
            break
        for tid in decision.skipped:
            index = next(i for i, t in enumerate(tasks) if t.task_id == tid)
            _set(tasks, index, T.SKIPPED)
    assert decision.action is Action.FINISHED
    assert decision.node is N.ROUTE_AFTER_BUILD
    assert not decision.all_completed
    assert [t.status for t in tasks] == [T.FAILED] + [T.SKIPPED] * 4


def test_a_task_that_can_never_become_ready_is_skipped_not_left_waiting() -> None:
    tasks = _plan()
    _complete(tasks, 0, 1, 2, 3)
    repair = repair_task(tasks[2], permitted_paths=("generated/backend/",), reason="build failed")
    tasks.append(repair.model_copy(update={"status": T.FAILED}))  # the repair failed: no integration
    decision = next_step(tasks, NONE)
    assert decision.action is Action.SKIP
    assert decision.skipped == (tasks[4].task_id,)  # the Tester task
    assert "never become ready" in decision.reason


def test_a_repair_task_reruns_integration_and_tests() -> None:
    tasks = _plan()
    _complete(tasks, 0, 1, 2, 3, 4)
    tasks.append(repair_task(tasks[2], permitted_paths=("generated/backend/",), reason="tests failed"))
    decision = next_step(tasks, NONE)  # orchestration cleared services_done when adding the repair
    assert (decision.action, decision.node) == (Action.DISPATCH, N.DEBUGGER_REVIEWER)
    _complete(tasks, 5)
    assert next_step(tasks, NONE).node is N.INTEGRATION_BUILD
    assert next_step(tasks, INTEGRATED).node is N.TEST_RUNNER
    final = next_step(tasks, BOTH)
    assert final.action is Action.FINISHED and final.all_completed


def test_an_empty_task_list_finishes_without_claiming_completion() -> None:
    decision = next_step([], BOTH)
    assert decision.action is Action.FINISHED
    assert decision.all_completed  # nothing to do and both services ran


def test_finished_is_not_complete_without_the_service_steps() -> None:
    tasks = _plan()
    _complete(tasks, 0, 1, 2, 3, 4)
    tasks_done: Sequence[AgentTask] = tasks
    assert next_step(tasks_done, NONE).node is N.INTEGRATION_BUILD
    assert next_step(tasks_done, INTEGRATED).node is N.TEST_RUNNER


# ------------------------------------------------------------------------------------ dispatcher


def test_dispatch_starts_the_next_attempt_with_the_ledger_key() -> None:
    task = _plan()[0]
    result = dispatch(task, now=NOW)
    assert result.transitions == (T.READY, T.RUNNING)
    assert result.task.status is T.RUNNING
    assert result.task.attempt_count == 1
    assert result.task.started_at == NOW
    record = result.record
    assert record.node is N.ARCHITECT
    assert record.attempt == 1
    assert record.idempotency_key == attempt_key(task.run_id, "architect", task.task_id, 1)
    assert record.permitted_paths == task.permitted_paths
    assert task.status is T.PENDING  # the input is not changed


def test_a_retry_is_the_second_attempt() -> None:
    task = _plan()[1].model_copy(update={"status": T.READY, "attempt_count": 1})
    result = dispatch(task, now=NOW)
    assert result.transitions == (T.RUNNING,)
    assert result.record.attempt == 2
    assert result.record.idempotency_key.endswith(":2")


@pytest.mark.parametrize("status", [T.RUNNING, T.COMPLETED, T.FAILED, T.BLOCKED, T.SKIPPED, T.CANCELLED])
def test_only_waiting_tasks_are_dispatched(status: TaskStatus) -> None:
    with pytest.raises(DispatchError, match="not waiting to run"):
        dispatch(_plan()[0].model_copy(update={"status": status}), now=NOW)


def test_no_dispatch_without_attempts_left() -> None:
    task = _plan()[0].model_copy(update={"status": T.READY, "attempt_count": 2, "max_attempts": 2})
    with pytest.raises(DispatchError, match="no attempts left"):
        dispatch(task, now=NOW)


def test_the_head_never_dispatches_discovery_or_delivery_agents() -> None:
    ctx = context()
    documenter = AgentTask(
        task_id="tsk_" + "0" * 25 + "1",
        project_id=ctx.project_id,
        run_id=ctx.run_id,
        agent_type=AgentRole.DOCUMENTER,
        operation_id="documenter.write_section",
        objective="Write the docs",
        done_criteria=("docs written",),
        spec_version_id=ctx.spec_version_id,
    )
    with pytest.raises(DispatchError, match="not dispatched by the Head"):
        dispatch(documenter, now=NOW)


# -------------------------------------------------------------------------------- failure router


def _failure(
    category: ErrorCategory,
    source: FailureSource = FailureSource.AGENT_TASK,
    *,
    attempts: int = 1,
    paths: tuple[str, ...] = (),
) -> Failure:
    task = _plan()[2].model_copy(update={"status": T.FAILED, "attempt_count": attempts})
    return Failure(task=task, source=source, category=category, code="x.y", paths=paths)


def _route(failure: Failure, allowance: int = 1) -> RouteAction:
    return route_failure(failure, reattempt_allowance=allowance, write_scopes=WRITE_SCOPES).action


@pytest.mark.parametrize("source", list(FailureSource))
@pytest.mark.parametrize("category", [ErrorCategory.POLICY_DENIED, ErrorCategory.BUDGET_EXHAUSTED])
def test_refusals_are_never_retried(category: ErrorCategory, source: FailureSource) -> None:
    """F-10c: whatever the source, attempts or paths, a refusal blocks."""
    failure = _failure(category, source, attempts=1, paths=("generated/backend/app.py",))
    route = route_failure(failure, reattempt_allowance=3, write_scopes=WRITE_SCOPES)
    assert route.action is RouteAction.BLOCK
    assert "F-10c" in route.reason
    assert not is_auto_retryable(category)


def test_resource_shortage_waits_and_fatal_fails() -> None:
    assert _route(_failure(ErrorCategory.RESOURCE_UNAVAILABLE)) is RouteAction.WAIT_FOR_RESOURCES
    assert _route(_failure(ErrorCategory.FATAL)) is RouteAction.FAIL


@pytest.mark.parametrize("category", [ErrorCategory.RETRYABLE, ErrorCategory.INVALID_OUTPUT])
@pytest.mark.parametrize("source", [FailureSource.AGENT_TASK, FailureSource.VERIFY_TASK])
def test_the_reattempt_bound_is_exact(category: ErrorCategory, source: FailureSource) -> None:
    """F-10d = 1 (budgets.yaml): the first failure is re-attempted once, the second is final."""
    first = route_failure(
        _failure(category, source, attempts=1), reattempt_allowance=1, write_scopes=WRITE_SCOPES
    )
    assert first.action is RouteAction.RETRY_TASK
    assert first.owner is AgentRole.BACKEND_DEVELOPER
    assert _route(_failure(category, source, attempts=2), allowance=1) is RouteAction.FAIL
    assert _route(_failure(category, source, attempts=1), allowance=0) is RouteAction.FAIL


def test_the_task_limit_caps_a_larger_allowance() -> None:
    failure = _failure(ErrorCategory.RETRYABLE, attempts=2)  # max_attempts is 2
    assert _route(failure, allowance=3) is RouteAction.FAIL


@pytest.mark.parametrize("source", [FailureSource.INTEGRATION_BUILD, FailureSource.TEST_RUNNER])
def test_build_and_test_failures_go_to_the_debugger_with_a_narrow_scope(source: FailureSource) -> None:
    failure = _failure(
        ErrorCategory.RETRYABLE, source, paths=("generated/backend/app.py", "generated/backend/db.py")
    )
    route = route_failure(failure, reattempt_allowance=1, write_scopes=WRITE_SCOPES)
    assert route.action is RouteAction.REPAIR
    assert route.owner is AgentRole.DEBUGGER_REVIEWER
    assert route.repair_paths == ("generated/backend/",)


@pytest.mark.parametrize(
    ("paths", "rule"),
    [
        ((), "no_location"),
        (("generated/tests/test_app.py",), "outside_repair_scope"),  # tests: not the Debugger's
        (("generated/docs/api.md",), "outside_repair_scope"),
    ],
)
def test_unlocated_or_out_of_scope_failures_need_classification(paths: tuple[str, ...], rule: str) -> None:
    failure = _failure(ErrorCategory.RETRYABLE, FailureSource.TEST_RUNNER, paths=paths)
    route = route_failure(failure, reattempt_allowance=1, write_scopes=WRITE_SCOPES)
    assert (route.action, route.rule) == (RouteAction.NEEDS_CLASSIFICATION, rule)
    assert route.repair_paths == ()


@pytest.mark.parametrize("path", ["../escape.py", "C:/Windows/system.ini", "generated\\backend\\a.py"])
def test_unsafe_reported_paths_block(path: str) -> None:
    failure = _failure(ErrorCategory.RETRYABLE, FailureSource.TEST_RUNNER, paths=(path,))
    route = route_failure(failure, reattempt_allowance=1, write_scopes=WRITE_SCOPES)
    assert (route.action, route.rule) == (RouteAction.BLOCK, "unsafe_path")


@pytest.mark.parametrize(
    ("crashes", "action"), [(1, RouteAction.RESUME), (3, RouteAction.RESUME), (4, RouteAction.BLOCK)]
)
def test_crashes_resume_until_more_than_the_cap(crashes: int, action: RouteAction) -> None:
    """D-40 (owner ruling): crashes are not failed attempts; block after more than 3 in a row."""
    route = route_crash(crashes, max_crash_resumes=3)
    assert route.action is action
