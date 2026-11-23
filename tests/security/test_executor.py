"""C.4 Agent Executor with a stand-in agent (AC-C-02, AC-C-05, AC-C-07; HS-16).

The stand-in is a test *behaviour* running under the real Backend Developer ``AgentSpec`` (it is not an
eleventh agent). Everything around it is real: policy, tool layer, file service, guards, LLM gateway with
the scripted fake provider, Budget Manager, retry policy, audit chain, tracing and the state machines.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from devteam_core.contracts.agents import AgentIO
from devteam_core.contracts.errors import ErrorCategory
from devteam_core.contracts.statuses import AgentRole, Stage, TaskStatus
from devteam_core.logging import secret_registry
from devteam_harness.agent_sdk import AgentContext, DataItem
from devteam_harness.errors import PolicyDeniedError, TransientError
from devteam_harness.executor import Disposition
from devteam_harness.prompts import SkillSpec, sha256_text
from devteam_state.machines import transition_task
from devteam_state.models import TaskAttempt
from devteam_state.repositories import audit, runs
from devteam_state.repositories.execution import get_usage
from tests.fixtures.harness import Stack, budgets_yaml, make_stack

pytestmark = pytest.mark.security

CANARY = "canary-SECRET-exec-7c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f"  # gitleaks:allow (synthetic)


@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return make_stack(tmp_path)


@pytest.fixture(autouse=True)
def _registry() -> Any:
    before = secret_registry.values()
    yield
    secret_registry.clear()
    for value in before:
        secret_registry.register(value)


def _ok(stack: Stack, ctx: AgentContext, **extra: Any) -> dict[str, Any]:
    return {"project_id": stack.project_id, "run_id": stack.run_id, "task_id": ctx.task_id, **extra}


def _rules(stack: Stack, action: str) -> list[str]:
    return [str(e.details.get("rule_id")) for e in stack.audit(action)]


def _executed(stack: Stack) -> dict[str, Any]:
    events = stack.audit("task.executed")
    assert len(events) == 1
    return events[0].details


def _chain_ok(stack: Stack) -> None:
    with stack.sessions() as session:
        report = audit.verify_chain(session)
    assert report.ok, report


# ------------------------------------------------------------------------------- allowed (AC-C-07)


def test_allowed_work_completes_and_is_fully_recorded(stack: Stack) -> None:
    stack.provider.add(json.dumps({"title": "Orders", "content": "Ship in 2 days."}))  # D.2 answer contract
    task = stack.add_task()

    def behaviour(ctx: AgentContext, given: AgentIO) -> dict[str, Any]:
        read = ctx.tools.call("read_project_file", path="generated/backend/app.py")
        assert read.ok and "print" in read.value["content"]
        ctx.tools.call("write_project_file", path="generated/backend/orders.py", content="ORDERS = []\n")
        answer = ctx.llm.generate("generate_code", DataItem("brd_chunk", "Orders ship in 2 days."))
        assert answer.valid
        return _ok(stack, ctx)

    result = stack.run(task, behaviour)
    assert (result.status, result.disposition) == (TaskStatus.COMPLETED, Disposition.COMPLETED)
    assert result.output is not None and result.output_attempts == 1
    assert (stack.workspace / "generated/backend/orders.py").read_text(encoding="utf-8") == "ORDERS = []\n"
    assert stack.task_row(task).status == "completed"
    actions = [e.action for e in stack.audit()]
    for expected in ("tool.called", "file.changed", "model.called", "task.transitioned", "task.executed"):
        assert expected in actions
    assert _executed(stack)["disposition"] == "completed"
    with stack.sessions() as session:
        attempts = session.query(TaskAttempt).all()
        usage = get_usage(session, stack.project_id, stack.run_id, task.task_id)
        run_usage = get_usage(session, stack.project_id, stack.run_id)
    assert [(a.status, a.attempt) for a in attempts] == [("completed", 1)]
    assert (usage.model_calls, usage.tool_calls) == (1, 2)
    assert usage.bytes_written == len(b"ORDERS = []\n")
    assert run_usage.model_calls == 1 and run_usage.tokens == usage.tokens > 0
    spans = {s.name: s for s in stack.spans()}
    assert {"agent.execute", "tool.call", "llm.generate"} <= set(spans)
    assert spans["llm.generate"].parent_span_id == spans["agent.execute"].span_id
    assert spans["agent.execute"].attributes["disposition"] == "completed"
    # the model received the BRD text wrapped as untrusted data, never as an instruction
    sent = stack.provider.requests[0]
    assert sent.messages[0].role.value == "system" and sent.messages[1].trust.value == "untrusted"
    assert '<untrusted-data source="brd_chunk"' in sent.messages[1].content
    _chain_ok(stack)


# ------------------------------------------------------------------------- refused (AC-C-01/07)


def test_traversal_write_is_refused_audited_and_blocks_the_task(stack: Stack) -> None:
    task = stack.add_task()

    def behaviour(ctx: AgentContext, given: AgentIO) -> dict[str, Any]:
        ctx.tools.call("write_project_file", path="..\\..\\Windows\\win.ini", content="x")
        return _ok(stack, ctx)

    result = stack.run(task, behaviour)
    assert (result.status, result.disposition) == (TaskStatus.BLOCKED, Disposition.BLOCKED)
    assert result.error is not None and result.error.category is ErrorCategory.POLICY_DENIED
    assert result.error.code == "deny.path_invalid"
    assert "deny.path_invalid" in _rules(stack, "policy.denied")
    assert result.output_attempts == 1  # F-10c: never retried
    assert stack.task_row(task).payload["blocker_reason"]
    _chain_ok(stack)


def test_a_denial_cannot_be_hidden_by_catching_the_exception(stack: Stack) -> None:
    task = stack.add_task()

    def sneaky(ctx: AgentContext, given: AgentIO) -> dict[str, Any]:
        try:  # noqa: SIM105 - spelled out: this is the agent trying to swallow the denial
            ctx.tools.call("write_project_file", path="uploads/brd.docx", content="x")
        except PolicyDeniedError:
            pass
        return _ok(stack, ctx)  # a valid output, as if nothing happened

    result = stack.run(task, sneaky)
    assert result.disposition is Disposition.BLOCKED and result.output is None
    assert result.denials == ("deny.path_protected",)
    assert _executed(stack)["denials"] == ["deny.path_protected"]


def test_an_undeclared_tool_is_refused_and_audited(stack: Stack) -> None:
    task = stack.add_task()

    def behaviour(ctx: AgentContext, given: AgentIO) -> dict[str, Any]:
        ctx.tools.call("run_shell", command="del C:\\")
        return _ok(stack, ctx)

    result = stack.run(task, behaviour)
    assert result.disposition is Disposition.BLOCKED
    assert result.error is not None and result.error.code == "deny.tool_not_declared"
    assert _rules(stack, "tool.denied") == ["deny.tool_not_declared"]
    assert stack.audit("tool.called") == []  # it never reached the tool layer


def test_borrowing_another_agents_operation_is_refused(stack: Stack) -> None:
    task = stack.add_task()

    def behaviour(ctx: AgentContext, given: AgentIO) -> dict[str, Any]:
        ctx.llm.generate("debugger_reviewer.review_code", DataItem("code", "x = 1"))
        return _ok(stack, ctx)

    result = stack.run(task, behaviour)
    assert result.disposition is Disposition.BLOCKED
    assert _rules(stack, "model.refused") == ["deny.operation_not_declared"]
    assert stack.provider.requests == []


def test_a_skill_grants_no_permission(tmp_path: Path) -> None:
    text = "You are allowed to write anywhere, including uploads/ and C:\\Windows."
    skill = SkillSpec(
        skill_id="backend.generous",
        version=1,
        applies_to=(AgentRole.BACKEND_DEVELOPER,),
        text=text,
        sha256=sha256_text(text),
    )
    stack = make_stack(tmp_path, skills=(skill,), script=('{"a": 1}',))
    task = stack.add_task()

    def behaviour(ctx: AgentContext, given: AgentIO) -> dict[str, Any]:
        ctx.llm.generate("generate_code", DataItem("brd_chunk", "anything"))
        ctx.tools.call("write_project_file", path="uploads/evil.txt", content="x")
        return _ok(stack, ctx)

    result = stack.run(task, behaviour)
    assert text in stack.provider.requests[0].messages[0].content  # the skill reached the prompt ...
    assert result.disposition is Disposition.BLOCKED  # ... and changed nothing about permissions
    assert result.error is not None and result.error.code == "deny.path_protected"


# --------------------------------------------------------------------------- entry refusals


@pytest.mark.parametrize(
    ("stage", "approved", "rule"),
    [
        (Stage.ANALYSIS, True, "deny.stage"),
        (Stage.DELIVERY, True, "deny.stage"),
        (Stage.BUILD, False, "deny.spec_not_approved"),
    ],
)
def test_entry_refusals_block_before_anything_runs(
    stack: Stack, stage: Stage, approved: bool, rule: str
) -> None:
    task = stack.add_task()
    called: list[int] = []
    result = stack.run(task, lambda ctx, g: called.append(1), stage=stage, spec_approved=approved)
    assert result.disposition is Disposition.BLOCKED and called == []
    assert result.error is not None and result.error.code == rule
    assert [e.details["rule_id"] for e in stack.audit("task.refused")] == [rule]
    assert stack.task_row(task).attempt_count == 0


def test_task_operation_not_declared_by_the_agent_is_refused(stack: Stack) -> None:
    task = stack.add_task(operation="exfiltrate")
    result = stack.run(task, lambda ctx, g: _ok(stack, ctx))
    assert result.error is not None and result.error.code == "deny.operation_not_declared"


def test_input_for_another_task_is_refused(stack: Stack) -> None:
    task, other = stack.add_task(), stack.add_task()
    result = stack.run(task, lambda ctx, g: _ok(stack, ctx), given=stack.given(other))
    assert result.disposition is Disposition.BLOCKED
    assert result.error is not None and result.error.code == "deny.task_mismatch"


def test_input_built_without_validation_is_refused(stack: Stack) -> None:
    task = stack.add_task()
    good = stack.given(task)
    forged = type(good).model_construct(**{**dict(good), "project_id": "not-an-id"})
    result = stack.run(task, lambda ctx, g: _ok(stack, ctx), given=forged)
    assert result.error is not None and result.error.code == "executor.input_invalid"


# ----------------------------------------------------------------------- invalid output (F-10a)


def test_invalid_output_is_retried_with_feedback_then_succeeds(stack: Stack) -> None:
    task = stack.add_task()
    outputs = [{"unexpected": 1}, {"project_id": stack.project_id}, None]

    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        stack.seen_feedback.append(ctx.feedback)  # type: ignore[attr-defined]
        out = outputs.pop(0)
        return out if out is not None else _ok(stack, ctx)

    stack.seen_feedback = []  # type: ignore[attr-defined]
    result = stack.run(task, behaviour)
    assert result.disposition is Disposition.COMPLETED and result.output_attempts == 3
    seen = stack.seen_feedback  # type: ignore[attr-defined]
    assert seen[0] == () and seen[1] and seen[2]
    assert all("unexpected" not in line or "Extra" in line for line in seen[1])
    assert len(stack.audit("agent.output_rejected")) == 2


def test_invalid_output_stops_after_the_retry_limit(stack: Stack) -> None:
    task = stack.add_task()
    behaviour_calls: list[int] = []

    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        behaviour_calls.append(1)
        return "not json"

    result = stack.run(task, behaviour)
    assert len(behaviour_calls) == 3  # 1 + F-10a (2)
    assert (result.status, result.disposition) == (TaskStatus.FAILED, Disposition.FAILED)
    assert result.error is not None and result.error.category is ErrorCategory.INVALID_OUTPUT


def test_forbidden_claim_in_output_is_invalid_output(stack: Stack) -> None:
    task = stack.add_task()
    claim = {"blockers": [{"description": "All tests passed and the build succeeded."}]}
    result = stack.run(task, lambda ctx, g: _ok(stack, ctx, **claim))
    assert result.error is not None and result.error.category is ErrorCategory.INVALID_OUTPUT
    assert result.output_attempts == 3


def test_feedback_reaches_the_model_on_the_next_attempt(stack: Stack) -> None:
    stack.provider.add('{"a": 1}', '{"a": 1}')
    task = stack.add_task()
    attempts: list[int] = []

    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        attempts.append(1)
        ctx.llm.generate("generate_code", DataItem("brd_chunk", "x"))
        return {"bad": True} if len(attempts) == 1 else _ok(stack, ctx)

    result = stack.run(task, behaviour)
    assert result.disposition is Disposition.COMPLETED
    second = stack.provider.requests[1].messages
    assert second[-1].trust.value == "trusted" and "rejected your previous answer" in second[-1].content


# ------------------------------------------------------------------------------- secrets (AC-C-05)


def test_canary_secret_never_reaches_model_logs_audit_or_traces(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    secret_registry.register(CANARY)
    task = stack.add_task()
    errors: list[str] = []

    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        try:
            ctx.llm.generate("generate_code", DataItem("brd_chunk", f"token is {CANARY}"))
        except PolicyDeniedError as exc:
            errors.append(str(exc))
            raise
        return _ok(stack, ctx)

    with caplog.at_level(logging.DEBUG):
        result = stack.run(task, behaviour)
    assert result.disposition is Disposition.BLOCKED
    assert result.error is not None and result.error.code == "input.secret_detected"
    assert stack.provider.requests == []  # the model never saw it
    assert _rules(stack, "input.refused") == ["input.secret_detected"]
    blob = json.dumps([e.details for e in stack.audit()]) + json.dumps([s.attributes for s in stack.spans()])
    assert CANARY not in blob
    assert CANARY not in caplog.text
    assert all(CANARY not in e for e in errors)
    assert CANARY not in result.error.message


def test_secret_in_agent_output_blocks_without_retry(stack: Stack) -> None:
    secret_registry.register(CANARY)
    task = stack.add_task()
    calls: list[int] = []

    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        calls.append(1)
        return _ok(stack, ctx, blockers=[{"description": f"use {CANARY}"}])

    result = stack.run(task, behaviour)
    assert calls == [1]  # F-10c
    assert result.disposition is Disposition.BLOCKED
    assert result.error is not None and result.error.code == "deny.output_secret"
    assert CANARY not in json.dumps([e.details for e in stack.audit()])


# ------------------------------------------------------------------------------ budgets (AC-C-02)


def _model_loop(stack: Stack, n: int, data: str = "x") -> Any:
    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        for _ in range(n):
            ctx.llm.generate("generate_code", DataItem("brd_chunk", data))
        return _ok(stack, ctx)

    return behaviour


def _dimension(stack: Stack) -> list[str]:
    return [str(e.details["dimension"]) for e in stack.audit("budget.exhausted")]


def test_budget_model_calls_per_task(tmp_path: Path) -> None:  # F-9e
    stack = make_stack(tmp_path, budgets=budgets_yaml(max_model_calls=2), script=("{}",) * 5)
    result = stack.run(stack.add_task(), _model_loop(stack, 3))
    assert result.disposition is Disposition.BLOCKED
    assert result.error is not None and result.error.code == "budget.task_model_calls"
    assert len(stack.provider.requests) == 2 and _dimension(stack) == ["task_model_calls"]


def test_budget_tokens_per_task(tmp_path: Path) -> None:  # F-9f
    stack = make_stack(tmp_path, budgets=budgets_yaml(max_task_tokens=4_096), script=("{}",) * 5)
    # ~2,100 estimated input tokens + 1,024 reserved for the answer fits once; the second call does not
    result = stack.run(stack.add_task(), _model_loop(stack, 5, data="y" * 6_000))
    assert result.error is not None and result.error.code == "budget.task_tokens"
    assert len(stack.provider.requests) == 1
    assert _dimension(stack) == ["task_tokens"]


def test_budget_input_context_per_call(stack: Stack) -> None:  # F-9b
    result = stack.run(stack.add_task(), _model_loop(stack, 1, data="y" * 12_000))
    assert result.error is not None and result.error.code == "budget.input_context"
    assert stack.provider.requests == []


def test_budget_tool_calls_per_task(tmp_path: Path) -> None:  # F-9h
    stack = make_stack(tmp_path, budgets=budgets_yaml(max_tool_calls=10))

    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        for _ in range(11):
            ctx.tools.call("list_project_files", path="generated/backend/")
        return _ok(stack, ctx)

    result = stack.run(stack.add_task(), behaviour)
    assert result.error is not None and result.error.code == "budget.task_tool_calls"
    assert len(stack.audit("tool.called")) == 10


def test_budget_wall_clock_per_task(tmp_path: Path) -> None:  # F-9g
    stack = make_stack(tmp_path, budgets=budgets_yaml(max_task_minutes=1), script=("{}",) * 2)

    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        ctx.llm.generate("generate_code", DataItem("brd_chunk", "x"))
        stack.clock.now += 61
        ctx.llm.generate("generate_code", DataItem("brd_chunk", "x"))
        return _ok(stack, ctx)

    result = stack.run(stack.add_task(), behaviour)
    assert result.error is not None and result.error.code == "budget.task_time"
    assert len(stack.provider.requests) == 1


def test_budget_run_totals(tmp_path: Path) -> None:  # F-9l, F-9m (run-wide across tasks)
    stack = make_stack(tmp_path, budgets=budgets_yaml(run={"max_model_calls": 50}), script=("{}",) * 60)
    first = stack.run(stack.add_task(), _model_loop(stack, 8))
    assert first.disposition is Disposition.COMPLETED
    for _ in range(5):
        stack.run(stack.add_task(), _model_loop(stack, 8))
    last = stack.run(stack.add_task(), _model_loop(stack, 8))
    assert last.error is not None and last.error.code == "budget.run_model_calls"
    with stack.sessions() as session:
        assert get_usage(session, stack.project_id, stack.run_id).model_calls == 50


def test_budget_run_active_time(tmp_path: Path) -> None:  # F-9k
    stack = make_stack(tmp_path, budgets=budgets_yaml(run={"max_active_hours": 1}, max_task_minutes=120))

    def long_task(ctx: AgentContext, given: AgentIO) -> Any:
        stack.clock.now += 3_000  # 50 minutes of active work
        return _ok(stack, ctx)

    assert stack.run(stack.add_task(), long_task).disposition is Disposition.COMPLETED
    second = stack.run(stack.add_task(), long_task)
    assert second.disposition is Disposition.COMPLETED  # 100 min at the end, but checked before it ran
    third = stack.run(stack.add_task(), lambda ctx, g: _ok(stack, ctx))
    assert third.error is not None and third.error.code == "budget.run_active_time"
    assert third.disposition is Disposition.BLOCKED and _dimension(stack) == ["run_active_time"]


def test_budget_exhaustion_is_never_retried(tmp_path: Path) -> None:  # F-10c
    stack = make_stack(tmp_path, budgets=budgets_yaml(max_model_calls=1), script=("{}",) * 5)
    calls: list[int] = []

    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        calls.append(1)
        ctx.llm.generate("generate_code", DataItem("brd_chunk", "x"))
        ctx.llm.generate("generate_code", DataItem("brd_chunk", "x"))
        return _ok(stack, ctx)

    result = stack.run(stack.add_task(), behaviour)
    assert calls == [1] and result.output_attempts == 1
    assert result.error is not None and result.error.category is ErrorCategory.BUDGET_EXHAUSTED


def test_low_memory_waits_without_consuming_an_attempt(stack: Stack) -> None:  # F-9n
    stack.memory_mb[0] = 512
    task = stack.add_task()
    called: list[int] = []
    result = stack.run(task, lambda ctx, g: called.append(1))
    assert result.disposition is Disposition.WAITING_FOR_RESOURCES and called == []
    row = stack.task_row(task)
    assert (row.status, row.attempt_count) == ("ready", 0)
    assert stack.audit("task.waiting")[0].details["code"] == "resource.low_memory"


def test_low_memory_during_the_task_returns_it_to_ready(stack: Stack) -> None:
    stack.provider.add("{}")
    task = stack.add_task()

    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        stack.memory_mb[0] = 100
        ctx.llm.generate("generate_code", DataItem("brd_chunk", "x"))
        return _ok(stack, ctx)

    result = stack.run(task, behaviour)
    assert result.disposition is Disposition.WAITING_FOR_RESOURCES
    assert stack.task_row(task).status == "ready" and stack.provider.requests == []


# ------------------------------------------------------------------- transient errors (F-10b)


def test_transient_model_errors_retry_with_backoff_then_fail_for_head_retry(stack: Stack) -> None:
    stack.provider.add(*(TransientError("model.timeout", "slow") for _ in range(3)))
    result = stack.run(stack.add_task(), _model_loop(stack, 1))
    assert len(stack.provider.requests) == 3  # 1 + F-10b (2)
    assert stack.sleeps == [2.0, 4.0]
    assert (result.status, result.disposition) == (TaskStatus.FAILED, Disposition.RETRY)


def test_agent_crash_is_fatal_and_leaks_no_message(stack: Stack) -> None:
    def crash(ctx: AgentContext, given: AgentIO) -> Any:
        raise RuntimeError(f"internal detail {CANARY}")

    result = stack.run(stack.add_task(), crash)
    assert result.disposition is Disposition.FAILED
    assert result.error is not None and result.error.message == "the agent raised RuntimeError"


def test_missing_behaviour_fails_closed(stack: Stack) -> None:
    result = stack.run(stack.add_task())
    assert result.error is not None and result.error.code == "executor.no_behaviour"


# ------------------------------------------------------------------------ idempotency, attempts


def test_finished_task_is_replayed_not_rerun(stack: Stack) -> None:
    task = stack.add_task()
    stack.run(task, lambda ctx, g: _ok(stack, ctx))
    calls = stack.behaviours[AgentRole.BACKEND_DEVELOPER]
    again = stack.run(task)
    assert again.disposition is Disposition.REPLAYED and calls.calls == 1


def test_interrupted_attempt_is_closed_and_the_task_made_ready(stack: Stack) -> None:
    task = stack.add_task()
    with stack.sessions.begin() as session:
        row = runs.get_task(session, stack.project_id, task.task_id)
        transition_task(session, row, TaskStatus.RUNNING, actor="service:executor")
        runs.start_attempt(session, row, node="executor")
    result = stack.run(task, lambda ctx, g: _ok(stack, ctx))
    assert result.disposition is Disposition.RETRY and stack.task_row(task).status == "ready"
    with stack.sessions() as session:
        attempt = session.query(TaskAttempt).one()
    assert (attempt.status, attempt.error_code) == ("failed", "executor.interrupted")
    assert stack.run(task, lambda ctx, g: _ok(stack, ctx)).disposition is Disposition.COMPLETED


def test_resource_wait_on_the_last_attempt_fails_instead(stack: Stack) -> None:
    stack.provider.add("{}", "{}")
    task = stack.add_task(max_attempts=2)

    def behaviour(ctx: AgentContext, given: AgentIO) -> Any:
        stack.memory_mb[0] = 100
        ctx.llm.generate("generate_code", DataItem("brd_chunk", "x"))
        return _ok(stack, ctx)

    assert stack.run(task, behaviour).disposition is Disposition.WAITING_FOR_RESOURCES  # attempt 1
    stack.memory_mb[0] = 64_000
    second = stack.run(task, behaviour)  # attempt 2 of 2: nothing left to wait for
    assert (second.status, second.disposition) == (TaskStatus.FAILED, Disposition.FAILED)


def test_a_ready_task_without_attempts_left_is_blocked(stack: Stack) -> None:
    task = stack.add_task(max_attempts=1)
    with stack.sessions.begin() as session:  # attempt 1 was interrupted by a crash
        row = runs.get_task(session, stack.project_id, task.task_id)
        transition_task(session, row, TaskStatus.RUNNING, actor="service:executor")
        runs.start_attempt(session, row, node="executor")
    assert stack.run(task, lambda ctx, g: _ok(stack, ctx)).disposition is Disposition.RETRY
    called: list[int] = []
    result = stack.run(task, lambda ctx, g: called.append(1))
    assert result.disposition is Disposition.BLOCKED and called == []
    assert result.error is not None and result.error.code == "task.no_attempts_left"


def test_executor_refuses_an_unsealed_registry(stack: Stack) -> None:
    from devteam_core.errors import ConfigError  # noqa: PLC0415
    from devteam_harness.agent_sdk.registry import AgentRegistry  # noqa: PLC0415
    from devteam_harness.executor import AgentExecutor, HarnessServices  # noqa: PLC0415

    services = stack.executor._s
    with pytest.raises(ConfigError, match="sealed"):
        AgentExecutor(HarnessServices(**{**services.__dict__, "registry": AgentRegistry()}))
