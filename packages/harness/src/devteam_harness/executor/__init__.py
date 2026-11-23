"""Agent Executor (HS-16; roadmap §5.2 lifecycle): the only way an agent runs.

For one task, in this order:

1. **load** the task from SQLite (project-scoped) and its agent's sealed ``AgentSpec``; **issue** the
   agent principal ``agent:<role>:<task_id>`` (agents never create their own identity);
2. **entry checks**, refused before anything runs (task → ``blocked``, audited ``task.refused``): the
   task's operation is declared by the agent, the stage is one of the agent's stages, build/delivery work
   names an approved frozen spec, and the input is exactly the agent's input contract for this task;
3. **idempotency** (attempt ledger): a task that already finished is not run again (``replayed``); an
   attempt interrupted by a crash is closed as failed and the task returns to ``ready``;
4. **budget** (F-9) and free memory (F-9n) before starting. Low memory does not consume an attempt;
   the task stays ``ready`` and the run waits for resources;
5. ``ready → running`` (attempt counted), then the agent runs inside a trace span with an
   ``AgentContext``. Model calls go through the LLM Gateway, tool calls through the Tool Layer;
6. the **output guard** checks the result (size, secrets, schema, cross-checks, forbidden claims).
   Invalid output is retried within F-10a with the violations fed back; nothing else is retried here;
7. any policy denial during the attempt makes the attempt ``policy_denied``, **even if the agent caught
   the exception**;
8. **finish** in one transaction: attempt ledger, task status, ``task.executed`` audit event. Usage
   counters are persisted as they accrue; the active time is added at the end.

Outcome mapping (category → task status / disposition):

=========================  =================  ==========================================
success                    ``completed``      ``completed``
``invalid_output``         ``failed``         ``failed`` (after F-10a retries)
``retryable``              ``failed``         ``retry`` (the Head may re-attempt, F-10d)
``fatal``                  ``failed``         ``failed``
``policy_denied``          ``blocked``        ``blocked`` (never retried, F-10c)
``budget_exhausted``       ``blocked``        ``blocked`` (never retried, F-10c)
``resource_unavailable``   ``ready``          ``waiting_for_resources``
=========================  =================  ==========================================
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts import CONTRACT_MODELS
from devteam_core.contracts.agents import AgentIO
from devteam_core.contracts.errors import ErrorCategory, ErrorRef
from devteam_core.contracts.llm import ChatMessage, ChatRole, ModelParams, ModelRequest, Trust
from devteam_core.contracts.statuses import AgentRole, Stage, TaskStatus
from devteam_core.contracts.tasks import AgentTask
from devteam_core.contracts.tools import ToolCall, ToolOutcome
from devteam_core.errors import ConfigError
from devteam_core.ids import new_id
from devteam_core.logging import get_logger, redact_text, secret_registry
from devteam_harness.agent_sdk import AgentBehaviour
from devteam_harness.agent_sdk.context import (
    AgentContext,
    DataItem,
    LLMAccess,
    ModelAnswer,
    ToolAccess,
    ToolAnswer,
)
from devteam_harness.agent_sdk.registry import AgentRegistry
from devteam_harness.agent_sdk.spec import AgentSpec
from devteam_harness.budgets.manager import BudgetManager, TaskBudget
from devteam_harness.errors import (
    BudgetExhaustedError,
    FatalError,
    HarnessError,
    InvalidOutputError,
    PolicyDeniedError,
)
from devteam_harness.guardrails.config import Guardrails
from devteam_harness.guardrails.input import InputGuard, InputRejectedError
from devteam_harness.guardrails.output import OutputGuard, OutputRejectedError
from devteam_harness.guardrails.policy import SPEC_APPROVAL_STAGES, AuditSink
from devteam_harness.iam.principals import AgentPrincipal, issue_agent_principal
from devteam_harness.llm_gateway import LLMGateway
from devteam_harness.observability.tracing import Tracer
from devteam_harness.prompts import PromptRegistry, SkillRegistry, sha256_text
from devteam_harness.resilience import RetryPolicy
from devteam_harness.tools.files import ControlledFileService
from devteam_harness.tools.layer import ToolContext, ToolLayer
from devteam_state.machines import transition_task
from devteam_state.models import TaskAttempt
from devteam_state.repositories import audit, runs
from devteam_state.repositories.audit import Outcome

EXECUTOR = "service:executor"
NODE = "executor"
_CODE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$")
_log = get_logger("devteam_harness.executor")


class Disposition(StrEnum):
    COMPLETED = "completed"
    RETRY = "retry"
    FAILED = "failed"
    BLOCKED = "blocked"
    WAITING_FOR_RESOURCES = "waiting_for_resources"
    REPLAYED = "replayed"


_OUTCOMES: Mapping[ErrorCategory, tuple[TaskStatus, Disposition]] = {
    ErrorCategory.INVALID_OUTPUT: (TaskStatus.FAILED, Disposition.FAILED),
    ErrorCategory.RETRYABLE: (TaskStatus.FAILED, Disposition.RETRY),
    ErrorCategory.FATAL: (TaskStatus.FAILED, Disposition.FAILED),
    ErrorCategory.POLICY_DENIED: (TaskStatus.BLOCKED, Disposition.BLOCKED),
    ErrorCategory.BUDGET_EXHAUSTED: (TaskStatus.BLOCKED, Disposition.BLOCKED),
    ErrorCategory.RESOURCE_UNAVAILABLE: (TaskStatus.READY, Disposition.WAITING_FOR_RESOURCES),
}


@dataclass(frozen=True)
class TaskRun:
    """What the caller (the graph, Part D) asks for. ``spec_approved`` comes from the build-entry guard."""

    project_id: str
    task_id: str
    stage: Stage
    given: AgentIO
    files: ControlledFileService
    spec_approved: bool = False
    frozen_paths: frozenset[str] = field(default_factory=frozenset)
    test_maintenance_approved: bool = False


@dataclass(frozen=True)
class ExecutionResult:
    task_id: str
    status: TaskStatus
    disposition: Disposition
    output: AgentIO | None = None
    error: ErrorRef | None = None
    output_attempts: int = 0
    denials: tuple[str, ...] = ()


@dataclass(frozen=True)
class HarnessServices:
    registry: AgentRegistry
    behaviours: Mapping[AgentRole, AgentBehaviour]
    guardrails: Guardrails
    tool_layer: ToolLayer
    gateway: LLMGateway
    budgets: BudgetManager
    retry: RetryPolicy
    prompts: PromptRegistry
    skills: SkillRegistry
    tracer: Tracer
    sink: AuditSink
    sessions: sessionmaker[Session]
    max_task_write_bytes: int
    clock: Clock = system_clock


def _short(text: str, limit: int = 300) -> str:
    return redact_text(text, secret_registry.values())[:limit] or "error"


def _code(value: str | None, fallback: str) -> str:
    return value if value and len(value) <= 64 and _CODE.fullmatch(value) else fallback


class _Attempt:
    """Per-attempt record of denials seen by the access objects (kept even if the agent swallows them)."""

    def __init__(self) -> None:
        self.denials: list[str] = []


class AgentExecutor:
    def __init__(self, services: HarnessServices) -> None:
        if not services.registry.sealed:
            raise ConfigError("the agent registry must be sealed (exactly ten agents) before execution")
        self._s = services
        self._input_guard = InputGuard(services.guardrails.input)
        self._output_guard = OutputGuard(services.guardrails.output)

    # ---------------------------------------------------------------------------------------- audit

    def _audit(
        self, action: str, outcome: Outcome, task: AgentTask, principal_id: str, **details: Any
    ) -> str:
        return self._s.sink.record(
            action=action,
            outcome=outcome,
            principal_id=principal_id,
            project_id=task.project_id,
            details={"task_id": task.task_id, "run_id": task.run_id, **details},
        )

    def _error(self, exc: HarnessError, task: AgentTask) -> ErrorRef:
        rule = getattr(exc, "rule_id", None)
        return ErrorRef(
            category=exc.category,
            code=_code(rule or exc.code, "error"),
            message=_short(exc.message),
            task_id=task.task_id,
            occurred_at=self._s.clock.now(),
        )

    # --------------------------------------------------------------------------------------- execute

    def execute(self, run: TaskRun) -> ExecutionResult:  # noqa: PLR0911 - one return per lifecycle exit
        with self._s.sessions() as session:
            row = runs.get_task(session, run.project_id, run.task_id)
            if row is None:
                raise FatalError("executor.unknown_task", "no such task in this project")
            task = runs.load_task_contract(row)
            status = TaskStatus(row.status)
        spec = self._s.registry.get(task.agent_type)
        principal = issue_agent_principal(task)

        if status in {TaskStatus.COMPLETED, TaskStatus.PARTIALLY_COMPLETED}:
            self._audit("task.replayed", "success", task, EXECUTOR, status=status.value)
            return ExecutionResult(task.task_id, status, Disposition.REPLAYED)
        if status is TaskStatus.RUNNING:
            return self._recover_interrupted(task)

        refusal = self._entry_problem(task, spec, run)
        if refusal is not None:
            self._audit(
                "task.refused",
                "denied",
                task,
                EXECUTOR,
                code=refusal.code,
                rule_id=getattr(refusal, "rule_id", None),
            )
            return self._finish(
                task, None, TaskStatus.BLOCKED, Disposition.BLOCKED, self._error(refusal, task), 0, ()
            )

        budget = self._s.budgets.open_task(task)
        try:
            budget.check_start()
        except BudgetExhaustedError as exc:
            return self._finish(
                task, None, TaskStatus.BLOCKED, Disposition.BLOCKED, self._error(exc, task), 0, ()
            )
        free = self._s.budgets.free_memory_mb()
        needed = self._s.budgets.budgets.resources.min_free_ram_mb
        if free < needed:
            self._audit(
                "task.waiting",
                "denied",
                task,
                EXECUTOR,
                code="resource.low_memory",
                free_mb=free,
                needed_mb=needed,
            )
            error = ErrorRef(
                category=ErrorCategory.RESOURCE_UNAVAILABLE,
                code="resource.low_memory",
                message=f"{free} MB free; at least {needed} MB needed (F-9n)",
                task_id=task.task_id,
                occurred_at=self._s.clock.now(),
            )
            return ExecutionResult(
                task.task_id, TaskStatus(status), Disposition.WAITING_FOR_RESOURCES, error=error
            )

        with self._s.sessions.begin() as session:
            row = runs.get_task(session, task.project_id, task.task_id)
            assert row is not None  # noqa: S101 - loaded above in the same project
            if row.attempt_count >= row.max_attempts:
                attempts_left = False
            else:
                attempts_left = True
                transition_task(
                    session, row, TaskStatus.RUNNING, actor=EXECUTOR, denial_sessions=self._s.sessions
                )
                attempt, _ = runs.start_attempt(session, row, node=NODE)
                attempt_id = attempt.id
        if not attempts_left:
            spent = PolicyDeniedError("task.no_attempts_left", "the task has no attempts left (F-10d)")
            return self._finish(
                task, None, TaskStatus.BLOCKED, Disposition.BLOCKED, self._error(spent, task), 0, ()
            )

        with self._s.tracer.span(
            "agent.execute",
            project_id=task.project_id,
            run_id=task.run_id,
            task_id=task.task_id,
            stage=run.stage.value,
            **{"agent.role": task.agent_type.value, "operation_id": task.operation_id},
        ) as span:
            output, failure, attempts, denials = self._run_agent(task, spec, principal, run, budget)
            budget.close()
            if failure is None:
                target, disposition = TaskStatus.COMPLETED, Disposition.COMPLETED
            else:
                target, disposition = _OUTCOMES[failure.category]
                span.set("error.category", failure.category.value)
                span.set("error.code", failure.code)
            span.set("disposition", disposition.value)
            span.set("attempt", attempts)
            return self._finish(task, attempt_id, target, disposition, failure, attempts, denials, output)

    # ----------------------------------------------------------------------------------- lifecycle

    def _entry_problem(self, task: AgentTask, spec: AgentSpec, run: TaskRun) -> HarnessError | None:  # noqa: PLR0911
        if task.operation_id not in spec.operation_ids:
            return PolicyDeniedError(
                "executor.operation",
                "the task's operation is not declared by its agent",
                rule_id="deny.operation_not_declared",
            )
        role_policy = self._s.guardrails.policy.roles.get(task.agent_type)
        if role_policy is None or run.stage not in role_policy.stages:
            return PolicyDeniedError(
                "executor.stage",
                f"{task.agent_type.value} does not work in the {run.stage.value} stage",
                rule_id="deny.stage",
            )
        if run.stage in SPEC_APPROVAL_STAGES and (not run.spec_approved or task.spec_version_id is None):
            return PolicyDeniedError(
                "executor.spec",
                "this stage needs an approved, frozen spec version",
                rule_id="deny.spec_not_approved",
            )
        if type(run.given) is not spec.input_model:
            return FatalError(
                "executor.input_contract", f"{task.agent_type.value} must receive {spec.input_model.__name__}"
            )
        try:  # re-validate: a model built with model_construct() would skip the contract's checks
            given = spec.input_model.model_validate_json(run.given.model_dump_json())
        except ValidationError:
            return FatalError("executor.input_invalid", "the input does not satisfy its contract")
        if (given.project_id, given.run_id) != (task.project_id, task.run_id):
            return PolicyDeniedError(
                "executor.input_scope",
                "the input belongs to another project or run",
                rule_id="deny.task_mismatch",
            )
        embedded = getattr(given, "task", None)
        if embedded is not None and getattr(embedded, "task_id", None) != task.task_id:
            return PolicyDeniedError(
                "executor.input_task", "the input is for another task", rule_id="deny.task_mismatch"
            )
        return None

    def _recover_interrupted(self, task: AgentTask) -> ExecutionResult:
        """A task found ``running`` was interrupted (crash/restart): close the attempt, make it ready."""
        with self._s.sessions.begin() as session:
            row = runs.get_task(session, task.project_id, task.task_id)
            assert row is not None  # noqa: S101
            attempt, done = runs.start_attempt(session, row, node=NODE)
            if done:  # finished but not transitioned: cannot happen (same transaction); refuse to guess
                raise FatalError("executor.inconsistent", "a completed attempt on a running task")
            runs.finish_attempt(session, attempt, ok=False, error_code="executor.interrupted")
            transition_task(
                session,
                row,
                TaskStatus.READY,
                actor=EXECUTOR,
                reason="interrupted attempt",
                denial_sessions=self._s.sessions,
            )
            audit.append(
                session,
                action="task.recovered",
                outcome="success",
                principal_id=EXECUTOR,
                project_id=task.project_id,
                target_type="task",
                target_id=task.task_id,
                details={"attempt": attempt.attempt, "run_id": task.run_id},
            )
        return ExecutionResult(task.task_id, TaskStatus.READY, Disposition.RETRY)

    def _run_agent(
        self, task: AgentTask, spec: AgentSpec, principal: AgentPrincipal, run: TaskRun, budget: TaskBudget
    ) -> tuple[AgentIO | None, ErrorRef | None, int, tuple[str, ...]]:
        behaviour = self._s.behaviours.get(task.agent_type)
        if behaviour is None:
            exc = FatalError(
                "executor.no_behaviour", f"no behaviour is installed for {task.agent_type.value}"
            )
            return None, self._error(exc, task), 0, ()
        tool_ctx = ToolContext(
            principal=principal,
            task=task,
            stage=run.stage,
            spec_approved=run.spec_approved,
            spec_write_paths=spec.write_paths,
            frozen_paths=run.frozen_paths,
            test_maintenance_approved=run.test_maintenance_approved,
            files=run.files,
            evidence_dir=run.files.root / "runs" / task.run_id / "diffs",
            max_task_write_bytes=self._s.max_task_write_bytes,
            bytes_written=budget.task_usage.bytes_written,
        )
        feedback: tuple[str, ...] = ()
        retries = 0
        all_denials: list[str] = []
        while True:
            attempt = _Attempt()
            ctx = AgentContext(
                role=task.agent_type,
                task_id=task.task_id,
                input=run.given,
                llm=LLMAccess(
                    self._generate_fn(task, spec, principal, budget, attempt, feedback, retries + 1)
                ),
                tools=ToolAccess(self._tool_fn(task, spec, principal, budget, attempt, tool_ctx)),
                log=_log.bind(task_id=task.task_id, run_id=task.run_id, agent=task.agent_type.value),
                feedback=feedback,
            )
            failure: HarnessError | None = None
            output: AgentIO | None = None
            try:
                raw = behaviour.run(ctx, run.given)
                if not attempt.denials:
                    output = self._output_guard.check(task.agent_type, run.given, raw)
            except OutputRejectedError as exc:
                failure = (
                    PolicyDeniedError(exc.code, "the output contains a secret", rule_id="deny.output_secret")
                    if exc.category is ErrorCategory.POLICY_DENIED
                    else InvalidOutputError(exc.code, f"{len(exc.violations)} violation(s)", exc.violations)
                )
            except HarnessError as exc:
                failure = exc
            except Exception as exc:  # an agent bug: fatal, type name only (no message content)
                failure = FatalError("agent.crashed", f"the agent raised {type(exc).__name__}")
            all_denials.extend(attempt.denials)
            if attempt.denials and not isinstance(failure, PolicyDeniedError | BudgetExhaustedError):
                failure = PolicyDeniedError(
                    "executor.denied",
                    "a denied action occurred during the attempt",
                    rule_id=attempt.denials[0],
                )
                output = None
            if failure is None and output is not None:
                return output, None, retries + 1, tuple(all_denials)
            assert failure is not None  # noqa: S101 - output is None only on failure
            if isinstance(failure, InvalidOutputError):
                self._audit(
                    "agent.output_rejected",
                    "error",
                    task,
                    principal.id,
                    code=failure.code,
                    attempt=retries + 1,
                    violations=len(failure.violations),
                )
                decision = self._s.retry.decide(failure.category, retries)
                if decision.retry:
                    retries += 1
                    feedback = failure.violations or (failure.message,)
                    continue
            return None, self._error(failure, task), retries + 1, tuple(all_denials)

    def _finish(  # noqa: PLR0917 - private helper; every argument is required context
        self,
        task: AgentTask,
        attempt_id: str | None,
        target: TaskStatus,
        disposition: Disposition,
        error: ErrorRef | None,
        attempts: int,
        denials: tuple[str, ...],
        output: AgentIO | None = None,
    ) -> ExecutionResult:
        with self._s.sessions.begin() as session:
            row = runs.get_task(session, task.project_id, task.task_id)
            assert row is not None  # noqa: S101
            if attempt_id is not None:
                attempt = session.get(TaskAttempt, attempt_id)
                assert attempt is not None  # noqa: S101
                runs.finish_attempt(
                    session, attempt, ok=error is None, error_code=error.code if error else None
                )
            if target is TaskStatus.READY and row.attempt_count >= row.max_attempts:
                target, disposition = TaskStatus.FAILED, Disposition.FAILED  # no attempt left to wait for
            if TaskStatus(row.status) is not target:
                if target is TaskStatus.BLOCKED:
                    row.payload = {**row.payload, "blocker_reason": error.message if error else "blocked"}
                transition_task(
                    session,
                    row,
                    target,
                    actor=EXECUTOR,
                    reason=error.code if error else None,
                    denial_sessions=self._s.sessions,
                )
            audit.append(
                session,
                action="task.executed",
                outcome="success"
                if error is None
                else ("denied" if target is TaskStatus.BLOCKED else "error"),
                principal_id=EXECUTOR,
                project_id=task.project_id,
                target_type="task",
                target_id=task.task_id,
                details={
                    "run_id": task.run_id,
                    "agent": task.agent_type.value,
                    "operation_id": task.operation_id,
                    "disposition": disposition.value,
                    "output_attempts": attempts,
                    "error_category": error.category.value if error else None,
                    "error_code": error.code if error else None,
                    "denials": list(denials[:20]),
                },
            )
        return ExecutionResult(task.task_id, target, disposition, output, error, attempts, denials)

    # ------------------------------------------------------------------------------ access objects

    def _generate_fn(  # noqa: PLR0917
        self,
        task: AgentTask,
        spec: AgentSpec,
        principal: AgentPrincipal,
        budget: TaskBudget,
        attempt: _Attempt,
        feedback: tuple[str, ...],
        attempt_no: int,
    ) -> Any:
        def generate(operation: str, items: tuple[DataItem, ...]) -> ModelAnswer:
            op_id = operation if "." in operation else f"{task.agent_type.value}.{operation}"
            op = spec.operation(op_id)
            if op is None:
                attempt.denials.append("deny.operation_not_declared")
                self._audit(
                    "model.refused",
                    "denied",
                    task,
                    principal.id,
                    rule_id="deny.operation_not_declared",
                    operation=_short(op_id, 100),
                )
                raise PolicyDeniedError(
                    "model.operation",
                    "the operation is not declared by this agent",
                    rule_id="deny.operation_not_declared",
                )
            prompt = self._s.prompts.get(spec.prompt_id, spec.prompt_version)
            skills = self._s.skills.skills_for(task.agent_type, op_id)
            system_text = prompt.text + "".join(
                f"\n\n[skill {s.skill_id} v{s.version}]\n{s.text}" for s in skills
            )
            messages = [ChatMessage(role=ChatRole.SYSTEM, content=system_text, trust=Trust.TRUSTED)]
            for item in items:
                try:
                    message, screening = self._input_guard.wrap_untrusted(item.content, item.label)
                except InputRejectedError as exc:
                    attempt.denials.append(exc.code)
                    self._audit(
                        "input.refused",
                        "denied",
                        task,
                        principal.id,
                        rule_id=exc.code,
                        label=_short(item.label, 40),
                    )
                    raise PolicyDeniedError(exc.code, exc.message, rule_id=exc.code) from None
                if screening.flagged:
                    self._audit(
                        "input.flagged",
                        "success",
                        task,
                        principal.id,
                        label=item.label,
                        patterns=list(screening.flags),
                    )
                messages.append(message)
            if feedback:
                notes = "\n".join(f"- {line}" for line in feedback)
                messages.append(
                    ChatMessage(
                        role=ChatRole.USER,
                        trust=Trust.TRUSTED,
                        content=f"The platform rejected your previous answer:\n{notes}\n"
                        "Return a corrected answer.",
                    )
                )
            request = ModelRequest(
                request_id=new_id("mrq", self._s.clock),
                role=task.agent_type,
                operation_id=op_id,
                slot=op.slot or spec.default_slot,
                task_id=task.task_id,
                run_id=task.run_id,
                messages=tuple(messages),
                output_schema=op.output_schema,
                params=ModelParams(
                    max_output_tokens=budget.profile.max_output_tokens,
                    context_window=budget.profile.context_window,
                ),
                prompt_id=spec.prompt_id,
                prompt_version=spec.prompt_version,
                prompt_hash=sha256_text(system_text),
            )
            model = CONTRACT_MODELS.get(op.output_schema) if op.output_schema else None
            try:
                response = self._s.gateway.generate(
                    request,
                    budget=budget,
                    principal_id=principal.id,
                    output_model=model,
                    attempt=min(attempt_no, 4),
                )
            except PolicyDeniedError as exc:
                attempt.denials.append(exc.rule_id or exc.code)
                raise
            return ModelAnswer(
                text=response.output_text,
                parsed=response.parsed,
                valid=response.validation.valid,
                errors=response.validation.errors,
            )

        return generate

    def _tool_fn(  # noqa: PLR0917
        self,
        task: AgentTask,
        spec: AgentSpec,
        principal: AgentPrincipal,
        budget: TaskBudget,
        attempt: _Attempt,
        tool_ctx: ToolContext,
    ) -> Any:
        def call_tool(tool: str, version: int, args: dict[str, Any]) -> ToolAnswer:
            if tool not in spec.tools:
                attempt.denials.append("deny.tool_not_declared")
                self._audit(
                    "tool.denied",
                    "denied",
                    task,
                    principal.id,
                    rule_id="deny.tool_not_declared",
                    tool=_short(str(tool), 64),
                )
                raise PolicyDeniedError(
                    "tool.denied", "the agent does not declare this tool", rule_id="deny.tool_not_declared"
                )
            budget.check_tool_call()
            try:
                call = ToolCall(
                    call_id=new_id("tcl", self._s.clock),
                    tool=tool,
                    tool_version=version,
                    principal=principal.id,
                    task_id=task.task_id,
                    args=args,
                )
            except ValidationError:
                return ToolAnswer(
                    ok=False, error_code="tool.invalid_call", message="the tool call is malformed"
                )
            before = tool_ctx.bytes_written
            with self._s.tracer.span(
                "tool.call",
                project_id=task.project_id,
                task_id=task.task_id,
                **{"tool.name": tool, "tool.version": version},
            ) as span:
                result = self._s.tool_layer.invoke(call, tool_ctx)
                span.set("outcome", result.outcome.value)
                if result.policy_rule_id:
                    span.set("policy.rule_id", result.policy_rule_id)
            budget.record_tool_call(max(0, tool_ctx.bytes_written - before))
            if result.outcome is ToolOutcome.DENIED:
                rule = result.policy_rule_id or "deny.default"
                attempt.denials.append(rule)
                raise PolicyDeniedError(
                    "tool.denied", result.error.message if result.error else "denied", rule_id=rule
                )
            if result.error is not None and result.error.category is ErrorCategory.BUDGET_EXHAUSTED:
                raise BudgetExhaustedError(
                    "task_write_bytes", self._s.max_task_write_bytes, tool_ctx.bytes_written
                )
            if result.outcome is ToolOutcome.OK:
                return ToolAnswer(ok=True, value=result.value)
            return ToolAnswer(
                ok=False,
                error_code=result.error.code if result.error else "tool.error",
                message=result.error.message if result.error else None,
            )

        return call_tool


__all__ = ["AgentExecutor", "Disposition", "ExecutionResult", "HarnessServices", "TaskRun"]
