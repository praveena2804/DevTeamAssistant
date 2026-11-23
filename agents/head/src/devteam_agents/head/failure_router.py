"""Head Agent failure router (Part D, roadmap §6.3): a fixed rules table. No model is used.

The first matching rule decides. Order matters: a refusal is never retried, whatever else is true.

========================  ===========================================  =========================
Rule                      When                                         Action
========================  ===========================================  =========================
``policy_denied``         error category ``policy_denied``             block (never retried, F-10c)
``budget_exhausted``      error category ``budget_exhausted``          block (never retried, F-10c)
``resource_unavailable``  error category ``resource_unavailable``      wait for resources
``fatal``                 error category ``fatal``                     fail the task
``unsafe_path``           a reported path is not workspace-relative    block
``no_location``           build/test failure with no failing path      needs classification
``outside_repair_scope``  a failing path the Debugger may not edit     needs classification
``repair``                build/test failure inside the repair scope   repair task for the Debugger
``reattempt``             agent task failed, re-attempts left (F-10d)  retry the task
``no_attempts_left``      agent task failed, none left                 fail the task
========================  ===========================================  =========================

"Needs classification" is where Part J adds a model classification (validated against the agent
list). Until then orchestration blocks the task with the router's reason. The repair-loop limit is
Part N's; this router does not count repairs.

Crash-interrupted attempts are not failures (D-40): :func:`route_crash` resumes the task, and blocks
it after more than ``max_crash_resumes`` consecutive crashes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from devteam_core.contracts._base import check_relative_path, path_in_scope
from devteam_core.contracts.errors import ErrorCategory
from devteam_core.contracts.statuses import AgentRole
from devteam_core.contracts.tasks import AgentTask

E = ErrorCategory


class FailureSource(StrEnum):
    AGENT_TASK = "agent_task"  # the executor reported the task failed
    VERIFY_TASK = "verify_task"  # post-task verification rejected the output
    INTEGRATION_BUILD = "integration_build"
    TEST_RUNNER = "test_runner"


class RouteAction(StrEnum):
    RETRY_TASK = "retry_task"
    REPAIR = "repair"
    BLOCK = "block"
    WAIT_FOR_RESOURCES = "wait_for_resources"
    FAIL = "fail"
    NEEDS_CLASSIFICATION = "needs_classification"
    RESUME = "resume"  # crash recovery only


@dataclass(frozen=True, slots=True)
class Failure:
    task: AgentTask  # the task the failure is attributed to
    source: FailureSource
    category: ErrorCategory
    code: str
    paths: tuple[str, ...] = ()  # failing files, workspace-relative (untrusted: from tool output)


@dataclass(frozen=True, slots=True)
class Route:
    action: RouteAction
    rule: str
    reason: str
    owner: AgentRole | None = None
    repair_paths: tuple[str, ...] = ()


_REFUSALS = {
    E.POLICY_DENIED: ("policy_denied", "refused by policy; refusals are never retried (F-10c)"),
    E.BUDGET_EXHAUSTED: ("budget_exhausted", "budget exhausted; never retried automatically (F-10c)"),
}
_BUILD_SOURCES = frozenset({FailureSource.INTEGRATION_BUILD, FailureSource.TEST_RUNNER})


def route_failure(
    failure: Failure, *, reattempt_allowance: int, write_scopes: Mapping[AgentRole, tuple[str, ...]]
) -> Route:
    """Decide what happens after ``failure``. ``reattempt_allowance`` is F-10d (``budgets.yaml``)."""
    category = failure.category
    if category in _REFUSALS:
        rule, reason = _REFUSALS[category]
        return Route(RouteAction.BLOCK, rule, reason)
    if category is E.RESOURCE_UNAVAILABLE:
        return Route(
            RouteAction.WAIT_FOR_RESOURCES, "resource_unavailable", "waiting for memory or the model"
        )
    if category is E.FATAL:
        return Route(RouteAction.FAIL, "fatal", f"fatal error '{failure.code}'")

    for path in failure.paths:
        try:
            check_relative_path(path, allow_directory=False)
        except ValueError:
            return Route(
                RouteAction.BLOCK, "unsafe_path", "a reported failing path is not workspace-relative"
            )

    if failure.source in _BUILD_SOURCES:
        return _route_build_failure(failure, write_scopes.get(AgentRole.DEBUGGER_REVIEWER, ()))
    return _route_task_failure(failure.task, reattempt_allowance)


def _route_build_failure(failure: Failure, repair_scopes: tuple[str, ...]) -> Route:
    if not failure.paths:
        return Route(
            RouteAction.NEEDS_CLASSIFICATION, "no_location", "the failure names no file; owner unknown"
        )
    outside = [p for p in failure.paths if not any(path_in_scope(p, s) for s in repair_scopes)]
    if outside:
        return Route(
            RouteAction.NEEDS_CLASSIFICATION,
            "outside_repair_scope",
            f"{len(outside)} failing file(s) lie outside the Debugger's repair scope",
        )
    scopes = tuple(s for s in repair_scopes if any(path_in_scope(p, s) for p in failure.paths))
    return Route(
        RouteAction.REPAIR,
        "repair",
        f"{failure.source.value} failed ('{failure.code}') in {len(failure.paths)} file(s)",
        owner=AgentRole.DEBUGGER_REVIEWER,
        repair_paths=scopes,
    )


def _route_task_failure(task: AgentTask, reattempt_allowance: int) -> Route:
    allowance = max(0, min(reattempt_allowance, task.max_attempts - 1))
    reattempts_used = max(0, task.attempt_count - 1)
    if reattempts_used < allowance and task.attempt_count < task.max_attempts:
        return Route(
            RouteAction.RETRY_TASK,
            "reattempt",
            f"re-attempt {reattempts_used + 1} of {allowance} (F-10d)",
            owner=task.agent_type,
        )
    return Route(RouteAction.FAIL, "no_attempts_left", f"no re-attempts left ({allowance} allowed, F-10d)")


def route_crash(consecutive_crashes: int, *, max_crash_resumes: int) -> Route:
    """D-40: a crash is not a failed attempt. Block only after more than ``max_crash_resumes``."""
    if consecutive_crashes > max_crash_resumes:
        return Route(
            RouteAction.BLOCK,
            "repeated_crashes",
            f"{consecutive_crashes} consecutive crashes (more than {max_crash_resumes})",
        )
    return Route(RouteAction.RESUME, "crash_resume", f"resume after crash {consecutive_crashes}")
