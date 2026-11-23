"""The controlled tool execution layer (HC-06, HS-10): the only path from an agent to a tool.

``ToolLayer.invoke`` runs, in order, and stops at the first failure:

1. **registry**: unknown tool or version → denied ``deny.unregistered_tool``;
2. **identity**: the call's principal and task must be the context's (issued by the executor) →
   ``deny.task_mismatch``;
3. **tool roles**: the tool must allow the agent's role → ``deny.tool_role``;
4. **arguments**: validated against the tool's input schema → error ``tool.invalid_arguments``;
5. **policy engine** (role x tool x path x stage, task scope, AgentSpec scope, frozen files) →
   denied with the engine's rule;
6. **approval requirement** → denied ``deny.approval_required`` (no approval-gated tools exist yet);
7. **per-task write budget** (F-9j) → error ``budget_exhausted``;
8. **handler under a time limit**: on timeout the call fails with ``tool.timeout`` and the handler's
   cancellation flag is set, so a write is abandoned before it is committed;
9. **filesystem safety** (Controlled File Service) → denied ``deny.path_unsafe``.

Every outcome is audited before the result is returned: denials as ``policy.denied`` /
``tool.denied``, executions as ``tool.called`` with a bounded, redacted argument summary (file contents
are never recorded), and writes additionally as ``file.changed`` with checksums and a diff reference.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.errors import ErrorCategory, ErrorRef
from devteam_core.contracts.events import FileChanged, ToolCallRecorded
from devteam_core.contracts.statuses import Stage
from devteam_core.contracts.tasks import AgentTask
from devteam_core.contracts.tools import ToolCall, ToolOutcome, ToolResult
from devteam_core.errors import ConfigError, DevTeamError
from devteam_core.ids import new_id
from devteam_core.logging import redact_text, secret_registry
from devteam_harness.guardrails.policy import Access, AuditSink, PolicyEngine, PolicyRequest
from devteam_harness.iam.principals import AgentPrincipal
from devteam_harness.tools.files import ControlledFileService, FileLimitError, FileSafetyError
from devteam_harness.tools.registry import ToolRegistry, ToolSpec

_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="devteam-tool")
_SUMMARY_CHARS = 120


class ToolNotFoundError(DevTeamError):
    code = "tool.not_found"


class ToolUnavailableError(DevTeamError):
    code = "tool.not_available"


class ToolContext(BaseModel):
    """Per-task context built by the executor (C.4). Agents never construct or see it."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    principal: AgentPrincipal
    task: AgentTask
    stage: Stage
    spec_approved: bool
    spec_write_paths: tuple[str, ...]
    frozen_paths: frozenset[str] = Field(default_factory=frozenset)
    test_maintenance_approved: bool = False
    files: ControlledFileService
    evidence_dir: Path
    max_task_write_bytes: int = Field(ge=1)
    bytes_written: int = 0

    @model_validator(mode="after")
    def _evidence_inside_workspace(self) -> ToolContext:
        try:
            self.evidence_dir.resolve().relative_to(self.files.root)
        except ValueError:
            raise ConfigError("the evidence directory must be inside the project workspace") from None
        return self


@dataclass(frozen=True)
class ToolInvocation:
    args: Any
    ctx: ToolContext
    sessions: sessionmaker[Session]
    cancelled: Callable[[], bool]


def _summary(spec: ToolSpec, args: BaseModel) -> dict[str, str]:
    registered = secret_registry.values()
    summary: dict[str, str] = {}
    for name, value in list(args.model_dump(mode="json").items())[:20]:
        if name in spec.summary_exclude:
            size = len(value) if isinstance(value, str | list | dict) else 0
            summary[name[:40]] = f"<{size} chars>"
        elif isinstance(value, str):
            summary[name[:40]] = redact_text(value[:_SUMMARY_CHARS], registered)[:200]
        else:
            summary[name[:40]] = redact_text(str(value)[:60], registered)
    return summary


def _safe(text: str | None, limit: int = _SUMMARY_CHARS) -> str | None:
    return None if text is None else redact_text(text[:limit], secret_registry.values())


class ToolLayer:
    def __init__(
        self,
        registry: ToolRegistry,
        engine: PolicyEngine,
        sink: AuditSink,
        sessions: sessionmaker[Session],
        *,
        clock: Clock = system_clock,
    ) -> None:
        self._registry = registry
        self._engine = engine
        self._sink = sink
        self._sessions = sessions
        self._clock = clock

    # ---------------------------------------------------------------------------------------- results

    def _error(self, category: ErrorCategory, code: str, message: str, task_id: str | None) -> ErrorRef:
        return ErrorRef(
            category=category,
            code=code,
            message=_safe(message, 300) or code,
            task_id=task_id,
            occurred_at=self._clock.now(),
        )

    def _deny(self, call: ToolCall, ctx: ToolContext, rule: str, reason: str, **extra: Any) -> ToolResult:
        action = "policy.denied" if extra.pop("from_engine", False) else "tool.denied"
        audit_id = self._sink.record(
            action=action,
            outcome="denied",
            principal_id=_safe(call.principal, 80),
            project_id=ctx.task.project_id,
            details={
                "rule_id": rule,
                "tool": call.tool[:64],
                "tool_version": call.tool_version,
                "call_id": call.call_id,
                "task_id": ctx.task.task_id,
                "run_id": ctx.task.run_id,
                "stage": ctx.stage.value,
                "reason": reason[:200],
                **{k: _safe(v) if isinstance(v, str) else v for k, v in extra.items()},
                "policy_digest": self._engine.digest,
            },
        )
        return ToolResult(
            call_id=call.call_id,
            outcome=ToolOutcome.DENIED,
            error=self._error(ErrorCategory.POLICY_DENIED, "tool.denied", reason, ctx.task.task_id),
            policy_rule_id=rule,
            audit_event_id=audit_id,
        )

    def _record_call(  # noqa: PLR0917 - private helper; every argument is required context
        self,
        call: ToolCall,
        ctx: ToolContext,
        spec: ToolSpec | None,
        summary: dict[str, str],
        outcome: ToolOutcome,
        started: float,
        error: ErrorRef | None = None,
    ) -> str:
        event = ToolCallRecorded(
            event_id=new_id("evt", self._clock),
            ts=self._clock.now(),
            project_id=ctx.task.project_id,
            run_id=ctx.task.run_id,
            task_id=ctx.task.task_id,
            actor=ctx.principal.id,
            tool=call.tool,
            tool_version=call.tool_version,
            outcome=outcome,
            args_summary=summary if spec is None or spec.audit == "full" else {},
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        details = event.model_dump(mode="json", exclude={"schema_version", "ts", "event_id"})
        details["call_id"] = call.call_id
        if error is not None:
            details["error_code"] = error.code
            details["error_category"] = error.category.value
        return self._sink.record(
            action="tool.called",
            outcome="success" if outcome is ToolOutcome.OK else "error",
            principal_id=ctx.principal.id,
            project_id=ctx.task.project_id,
            details=details,
        )

    def _fail(  # noqa: PLR0917 - private helper; every argument is required context
        self,
        call: ToolCall,
        ctx: ToolContext,
        spec: ToolSpec | None,
        summary: dict[str, str],
        started: float,
        category: ErrorCategory,
        code: str,
        message: str,
    ) -> ToolResult:
        error = self._error(category, code, message, ctx.task.task_id)
        audit_id = self._record_call(call, ctx, spec, summary, ToolOutcome.ERROR, started, error)
        return ToolResult(
            call_id=call.call_id, outcome=ToolOutcome.ERROR, error=error, audit_event_id=audit_id
        )

    # ----------------------------------------------------------------------------------------- invoke

    def invoke(self, call: ToolCall, ctx: ToolContext) -> ToolResult:  # noqa: PLR0911, PLR0912 - one return per stage
        started = time.monotonic()
        spec = self._registry.get(call.tool, call.tool_version)
        if spec is None:
            return self._deny(call, ctx, "deny.unregistered_tool", "no such tool or tool version")
        if call.principal != ctx.principal.id or call.task_id != ctx.task.task_id:
            return self._deny(call, ctx, "deny.task_mismatch", "the call does not belong to this task")
        if ctx.principal.role not in spec.allowed_roles:
            return self._deny(call, ctx, "deny.tool_role", f"{spec.name} is not available to this agent")
        try:
            args = spec.input_model.model_validate(call.args)
        except ValidationError as exc:
            messages = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
                for e in exc.errors(include_input=False)[:5]
            )
            return self._fail(
                call, ctx, spec, {}, started, ErrorCategory.INVALID_OUTPUT, "tool.invalid_arguments", messages
            )
        summary = _summary(spec, args)
        path = getattr(args, spec.path_arg) if spec.path_arg else None
        decision = self._engine.decide(
            PolicyRequest(
                principal=ctx.principal,
                tool=spec.name,
                access=spec.access,
                path=path,
                stage=ctx.stage,
                task=ctx.task,
                spec_approved=ctx.spec_approved,
                spec_write_paths=ctx.spec_write_paths,
                frozen_paths=ctx.frozen_paths,
                test_maintenance_approved=ctx.test_maintenance_approved,
            )
        )
        if not decision.allowed:
            return self._deny(
                call,
                ctx,
                decision.rule_id,
                decision.reason,
                from_engine=True,
                path=path,
                access=spec.access.value,
            )
        if spec.requires_approval:
            return self._deny(call, ctx, "deny.approval_required", "this tool needs a human approval")
        size = len(str(getattr(args, "content", "")).encode("utf-8")) if spec.access is Access.WRITE else 0
        if ctx.bytes_written + size > ctx.max_task_write_bytes:
            return self._fail(
                call,
                ctx,
                spec,
                summary,
                started,
                ErrorCategory.BUDGET_EXHAUSTED,
                "budget.task_write_bytes",
                "the task's write budget (F-9j) is used up",
            )
        cancel = threading.Event()
        invocation = ToolInvocation(args=args, ctx=ctx, sessions=self._sessions, cancelled=cancel.is_set)
        future = _EXECUTOR.submit(spec.handler, invocation)
        try:
            output = future.result(timeout=spec.timeout_s)
        except concurrent.futures.TimeoutError:
            cancel.set()
            return self._fail(
                call,
                ctx,
                spec,
                summary,
                started,
                ErrorCategory.RETRYABLE,
                "tool.timeout",
                f"{spec.name} exceeded its time limit of {spec.timeout_s}s",
            )
        except FileSafetyError as exc:
            return self._deny(call, ctx, "deny.path_unsafe", exc.message, path=path)
        except FileLimitError as exc:
            return self._fail(
                call,
                ctx,
                spec,
                summary,
                started,
                ErrorCategory.INVALID_OUTPUT,
                "tool.file_limit",
                exc.message,
            )
        except ToolNotFoundError as exc:
            return self._fail(
                call, ctx, spec, summary, started, ErrorCategory.INVALID_OUTPUT, "tool.not_found", exc.message
            )
        except ToolUnavailableError as exc:
            return self._fail(
                call,
                ctx,
                spec,
                summary,
                started,
                ErrorCategory.RESOURCE_UNAVAILABLE,
                "tool.not_available",
                exc.message,
            )
        except Exception as exc:
            return self._fail(
                call, ctx, spec, summary, started, ErrorCategory.FATAL, "tool.failed", type(exc).__name__
            )
        ctx.bytes_written += size
        audit_id = self._record_call(call, ctx, spec, summary, ToolOutcome.OK, started)
        change = getattr(output, "_change", None)
        if change is not None:
            self._record_change(call, ctx, change)
        return ToolResult(
            call_id=call.call_id,
            outcome=ToolOutcome.OK,
            value=output.model_dump(mode="json"),
            audit_event_id=audit_id,
        )

    def _record_change(self, call: ToolCall, ctx: ToolContext, change: Any) -> None:
        ctx.evidence_dir.mkdir(parents=True, exist_ok=True)
        diff_file = ctx.evidence_dir / f"{call.call_id}.diff"
        diff_file.write_text(
            redact_text(change.diff, secret_registry.values()), encoding="utf-8", newline="\n"
        )
        diff_ref = diff_file.resolve().relative_to(ctx.files.root).as_posix()
        event = FileChanged(
            event_id=new_id("evt", self._clock),
            ts=self._clock.now(),
            project_id=ctx.task.project_id,
            run_id=ctx.task.run_id,
            task_id=ctx.task.task_id,
            actor=ctx.principal.id,
            path=change.path,
            before_sha256=change.before_sha256,
            after_sha256=change.after_sha256,
            bytes_written=change.bytes_written,
            diff_ref=diff_ref,
        )
        details = event.model_dump(mode="json", exclude={"schema_version", "ts", "event_id"})
        details["call_id"] = call.call_id
        self._sink.record(
            action="file.changed",
            outcome="success",
            principal_id=ctx.principal.id,
            project_id=ctx.task.project_id,
            details=details,
        )
