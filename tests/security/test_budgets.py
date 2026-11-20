"""C.4 budgets (HC-05, F-9) and retry policy (HC-12, F-10): configuration ceilings and exact behaviour."""

from __future__ import annotations

from pathlib import Path

import pytest

from devteam_core.contracts.errors import ErrorCategory
from devteam_core.contracts.llm import ModelUsage
from devteam_core.errors import ConfigError
from devteam_harness.budgets import load_budgets, parse_budgets
from devteam_harness.errors import BudgetExhaustedError, ResourceUnavailableError
from devteam_harness.resilience import RetryPolicy
from devteam_state.repositories.execution import get_usage
from tests.fixtures.harness import REPO_CONFIG, budgets_yaml, make_stack

pytestmark = pytest.mark.security


# ------------------------------------------------------------------------------------ config


def test_repository_budgets_are_the_approved_values() -> None:
    b = load_budgets(REPO_CONFIG)
    p = b.profiles["default"]
    assert (p.max_output_tokens, p.max_input_tokens, p.context_window) == (1024, 3072, 4096)  # F-9a/b/c
    assert p.model_call_timeout_s == 180  # F-9d, owner ruling AP-008
    assert (p.max_model_calls, p.max_task_tokens, p.max_task_minutes, p.max_tool_calls) == (8, 32768, 30, 200)
    assert (b.run.max_active_hours, b.run.max_tokens, b.run.max_model_calls) == (12, 1_500_000, 600)
    assert b.resources.min_free_ram_mb == 1024  # F-9n
    assert (b.retries.invalid_output, b.retries.transient, b.retries.task_reattempts) == (2, 2, 1)
    assert len(b.digest) == 64


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ({"max_output_tokens": 4096}, "max_output_tokens"),  # above F-9a max
        ({"model_call_timeout_s": 5}, "model_call_timeout_s"),  # below F-9d min
        ({"max_model_calls": 0}, "max_model_calls"),
        ({"max_tool_calls": 5000}, "max_tool_calls"),
        ({"max_input_tokens": 3500}, "context_window"),  # F-9b + F-9a > F-9c
    ],
)
def test_values_outside_the_approved_ranges_are_rejected(change: dict[str, int], where: str) -> None:
    with pytest.raises(ConfigError, match=r"budgets\.yaml is invalid") as exc:
        parse_budgets(budgets_yaml(**change).encode())
    assert where in str(exc.value)


def test_policy_denial_retries_cannot_be_configured() -> None:  # F-10c is fixed in code
    text = budgets_yaml().replace("retries: {", "retries: {policy_denied: 1, ")
    with pytest.raises(ConfigError, match="policy_denied"):
        parse_budgets(text.encode())


def test_unknown_keys_missing_default_and_oversized_profiles_are_rejected() -> None:
    with pytest.raises(ConfigError):
        parse_budgets(budgets_yaml().replace("version: 1", "version: 1\nunlimited: true").encode())
    with pytest.raises(ConfigError, match="default"):
        parse_budgets(budgets_yaml().replace("  default:", "  heavy:").encode())
    with pytest.raises(ConfigError, match="more tokens per task"):
        parse_budgets(budgets_yaml(max_task_tokens=200_000, run={"max_tokens": 100_000}).encode())
    with pytest.raises(ConfigError, match="not valid YAML"):
        parse_budgets(b"version: [1")


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing"):
        load_budgets(tmp_path)


def test_unknown_budget_profile_fails_closed() -> None:
    with pytest.raises(ConfigError, match="unknown budget profile"):
        load_budgets(REPO_CONFIG).profile("unlimited")


# ----------------------------------------------------------------------------- task budget


def test_every_limit_is_checked_before_the_action(tmp_path: Path) -> None:
    stack = make_stack(tmp_path, budgets=budgets_yaml(max_model_calls=2, max_tool_calls=10))
    budget = stack.budgets.open_task(stack.add_task())
    with pytest.raises(BudgetExhaustedError) as out:  # F-9a
        budget.check_model_call(10, 2048)
    assert out.value.dimension == "output_tokens"
    with pytest.raises(BudgetExhaustedError) as ctx:  # F-9c
        budget.check_context_window(8192)
    assert ctx.value.dimension == "context_window"
    budget.check_model_call(100, 1024)
    budget.record_model_call(ModelUsage(input_tokens=100, output_tokens=50))
    budget.record_model_call(ModelUsage(input_tokens=100, output_tokens=50))
    with pytest.raises(BudgetExhaustedError) as calls:
        budget.check_model_call(100, 1024)
    assert (calls.value.dimension, calls.value.limit, calls.value.used) == ("task_model_calls", 2, 3)
    for _ in range(10):
        budget.check_tool_call()
        budget.record_tool_call()
    with pytest.raises(BudgetExhaustedError, match="task_tool_calls"):
        budget.check_tool_call()
    dims = [e.details["dimension"] for e in stack.audit("budget.exhausted")]
    assert dims == ["output_tokens", "context_window", "task_model_calls", "task_tool_calls"]


def test_usage_survives_a_restart(tmp_path: Path) -> None:
    stack = make_stack(tmp_path, budgets=budgets_yaml(max_model_calls=2))
    task = stack.add_task()
    first = stack.budgets.open_task(task)
    first.record_model_call(ModelUsage(input_tokens=10, output_tokens=5))
    first.record_model_call(ModelUsage(input_tokens=10, output_tokens=5))
    reopened = stack.budgets.open_task(task)  # e.g. after the worker restarted
    assert reopened.task_usage.model_calls == 2
    with pytest.raises(BudgetExhaustedError):
        reopened.check_start()


def test_low_memory_is_resource_unavailable_not_a_budget(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    budget = stack.budgets.open_task(stack.add_task())
    stack.memory_mb[0] = 1023
    with pytest.raises(ResourceUnavailableError) as exc:
        budget.check_model_call(10, 1024)
    assert exc.value.category is ErrorCategory.RESOURCE_UNAVAILABLE
    stack.memory_mb[0] = 1024
    budget.check_model_call(10, 1024)


def test_active_time_is_added_once(tmp_path: Path) -> None:
    stack = make_stack(tmp_path)
    task = stack.add_task()
    budget = stack.budgets.open_task(task)
    stack.clock.now += 12.5
    budget.close()
    budget.close()
    with stack.sessions() as session:
        assert get_usage(session, stack.project_id, stack.run_id, task.task_id).active_ms == 12_500
        assert get_usage(session, stack.project_id, stack.run_id).active_ms == 12_500


# ------------------------------------------------------------------------------- retry policy


def _policy(jitter: float = 0.0, **retries: float) -> RetryPolicy:
    limits = load_budgets(REPO_CONFIG).retries.model_copy(update=retries)
    return RetryPolicy(limits, jitter=lambda: jitter)


def test_retry_counts_are_exact() -> None:
    policy = _policy()
    for category, allowed in (
        (ErrorCategory.INVALID_OUTPUT, 2),
        (ErrorCategory.RETRYABLE, 2),
        (ErrorCategory.POLICY_DENIED, 0),
        (ErrorCategory.BUDGET_EXHAUSTED, 0),
        (ErrorCategory.RESOURCE_UNAVAILABLE, 0),
        (ErrorCategory.FATAL, 0),
    ):
        decisions = [policy.decide(category, n).retry for n in range(5)]
        assert decisions == [True] * allowed + [False] * (5 - allowed), category


def test_backoff_doubles_with_jitter_and_is_capped() -> None:
    assert [_policy().backoff_s(n) for n in (1, 2, 3, 4)] == [2.0, 4.0, 8.0, 16.0]
    assert _policy(jitter=1.0).backoff_s(1) == 2.5  # at most +25%
    assert _policy().backoff_s(10) == 30.0  # never more than 30 s
    assert _policy(jitter=7.0).backoff_s(1) == 2.5  # a misbehaving jitter source is clamped
    assert _policy().decide(ErrorCategory.INVALID_OUTPUT, 0).delay_s == 0.0
    assert _policy().decide(ErrorCategory.RETRYABLE, 1).delay_s == 4.0
    with pytest.raises(ValueError, match="start at 1"):
        _policy().backoff_s(0)


def test_run_token_limit_counts_every_task_of_the_run(tmp_path: Path) -> None:  # F-9l
    stack = make_stack(tmp_path, budgets=budgets_yaml(run={"max_tokens": 100_000}))
    first = stack.budgets.open_task(stack.add_task())
    first.record_model_call(ModelUsage(input_tokens=99_000, output_tokens=0))
    second = stack.budgets.open_task(stack.add_task())  # a different task in the same run
    assert second.task_usage.tokens == 0 and second.run_usage.tokens == 99_000
    with pytest.raises(BudgetExhaustedError) as exc:
        second.check_model_call(100, 1024)
    assert (exc.value.dimension, exc.value.limit) == ("run_tokens", 100_000)
    second.check_model_call(100, 128)  # what still fits is allowed
