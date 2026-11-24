"""The centralized LLM Gateway (HC-02, HS-15): the only door to a model. No LiteLLM (D-35).

``generate`` runs, in order:

1. **input guard** (:meth:`InputGuard.check_request`): a registered secret or credential shape in any
   message, or an untrusted message not wrapped by the guard, is refused (``policy_denied``) and audited
   as ``model.refused``; the provider is never called;
2. **budget** (F-9a/b/c/e/f/g/k/l/m/n) checked before every attempt, including retries;
3. **one call at a time** (Spec §7.2): a process-wide lock is held for the whole provider call. If a call
   times out (F-9d) the lock is released only when that call really ends, so calls never overlap;
4. **transient retries** (F-10b) with backoff, outside the lock;
5. **structured output validation** against the operation's output contract (if any);
6. **usage** recorded in the budget, **audit** ``model.called`` with metadata only (no prompt or output
   text), and a trace span with allowlisted attributes.
"""

from __future__ import annotations

import concurrent.futures
import json
import threading
import time
from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.errors import ErrorCategory
from devteam_core.contracts.events import ModelCallRecorded
from devteam_core.contracts.llm import ModelRequest, ModelResponse, ModelUsage, OutputValidation
from devteam_core.ids import new_id
from devteam_core.logging import redact_text, secret_registry
from devteam_harness.budgets.manager import TaskBudget, estimate_tokens
from devteam_harness.budgets.resource_lease import ResourceLease
from devteam_harness.errors import HarnessError, PolicyDeniedError, TransientError
from devteam_harness.guardrails.input import InputGuard, InputRejectedError
from devteam_harness.guardrails.policy import AuditSink
from devteam_harness.llm_gateway.providers import ModelProvider, ProviderReply
from devteam_harness.observability.tracing import Tracer
from devteam_harness.resilience import RetryPolicy
from devteam_state.repositories.audit import Outcome

MODEL_LOCK = threading.Lock()  # Spec §7.2: one model call at a time in this process, across gateways
_CALLS = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="devteam-llm")
MAX_VALIDATION_ERRORS = 10


def _validate(text: str, model: type[BaseModel] | None) -> tuple[OutputValidation, dict[str, object] | None]:
    if model is None:
        return OutputValidation(valid=True), None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return OutputValidation(valid=False, errors=(f"not valid JSON (line {exc.lineno})",)), None
    if not isinstance(data, dict):
        return OutputValidation(valid=False, errors=("expected a JSON object",)), None
    try:
        model.model_validate(data)
    except ValidationError as exc:
        errors = tuple(
            redact_text(
                f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}", secret_registry.values()
            )[:300]
            for e in exc.errors(include_input=False, include_url=False)[:MAX_VALIDATION_ERRORS]
        )
        return OutputValidation(valid=False, errors=errors), None
    return OutputValidation(valid=True), data


class LLMGateway:
    def __init__(
        self,
        provider: ModelProvider,
        input_guard: InputGuard,
        sink: AuditSink,
        tracer: Tracer,
        retry_policy: RetryPolicy,
        *,
        clock: Clock = system_clock,
        sleep: Callable[[float], None] = time.sleep,
        resource_lease: ResourceLease | None = None,
    ) -> None:
        self._provider = provider
        self._guard = input_guard
        self._sink = sink
        self._tracer = tracer
        self._retry = retry_policy
        self._clock = clock
        self._sleep = sleep
        # Part E (D-46): the cross-process LLM/sandbox lease; a sandbox run in any process makes calls wait.
        self._lease = resource_lease

    def _audit(  # noqa: PLR0917 - private helper; every argument is required context
        self,
        request: ModelRequest,
        principal_id: str,
        project_id: str | None,
        outcome: Outcome,
        reply: ProviderReply | None,
        duration_ms: int,
        attempt: int,
        valid: bool,
        error_code: str | None = None,
    ) -> str:
        details: dict[str, object]
        if reply is not None:
            event = ModelCallRecorded(
                event_id=new_id("evt", self._clock),
                ts=self._clock.now(),
                project_id=project_id,
                run_id=request.run_id,
                task_id=request.task_id,
                actor=principal_id,
                provider=self._provider.name,
                model_id=reply.model_id,
                operation_id=request.operation_id,
                prompt_id=request.prompt_id,
                prompt_version=request.prompt_version,
                prompt_hash=request.prompt_hash,
                input_tokens=reply.input_tokens,
                output_tokens=reply.output_tokens,
                duration_ms=duration_ms,
                output_valid=valid,
                attempt=attempt,
            )
            details = event.model_dump(mode="json", exclude={"schema_version", "ts", "event_id"})
        else:
            details = {
                "operation_id": request.operation_id,
                "task_id": request.task_id,
                "run_id": request.run_id,
                "provider": self._provider.name.value,
                "duration_ms": duration_ms,
                "attempt": attempt,
            }
        details["request_id"] = request.request_id
        if error_code is not None:
            details["error_code"] = error_code
        action = "model.refused" if outcome == "denied" else "model.called"
        return self._sink.record(
            action=action, outcome=outcome, principal_id=principal_id, project_id=project_id, details=details
        )

    def _call_once(self, request: ModelRequest, timeout_s: float) -> ProviderReply:
        # D-46: never a model call while a sandbox run holds the machine (ResourceUnavailableError → wait).
        lease = self._lease.acquire("llm") if self._lease is not None else None
        if not MODEL_LOCK.acquire(timeout=timeout_s):
            if lease is not None:
                lease.release()
            raise TransientError("model.busy", "another model call is still running")
        try:
            future = _CALLS.submit(self._provider.generate, request)
        except BaseException:
            MODEL_LOCK.release()
            if lease is not None:
                lease.release()
            raise

        def _done(_future: object) -> None:  # released only when the call really ends
            MODEL_LOCK.release()
            if lease is not None:
                lease.release()

        future.add_done_callback(_done)
        try:
            return future.result(timeout=timeout_s)
        except concurrent.futures.TimeoutError:
            raise TransientError("model.timeout", f"the model did not answer within {timeout_s:g}s") from None

    def generate(
        self,
        request: ModelRequest,
        *,
        budget: TaskBudget,
        principal_id: str,
        output_model: type[BaseModel] | None = None,
        attempt: int = 1,
    ) -> ModelResponse:
        project_id = budget.task.project_id
        if request.task_id != budget.task.task_id or request.run_id != budget.task.run_id:
            raise PolicyDeniedError("model.task_mismatch", "the request does not belong to the budgeted task")
        try:
            self._guard.check_request(request)
        except InputRejectedError as exc:
            self._audit(request, principal_id, project_id, "denied", None, 0, attempt, False, exc.code)
            raise PolicyDeniedError(exc.code, exc.message) from None

        input_tokens = estimate_tokens("".join(m.content for m in request.messages))
        budget.check_context_window(request.params.context_window)
        retries = 0
        with self._tracer.span(
            "llm.generate",
            project_id=project_id,
            task_id=request.task_id or "",
            operation_id=request.operation_id,
            attempt=attempt,
            **{
                "model.provider": self._provider.name.value,
                "model.slot": request.slot.value,
                "prompt.id": request.prompt_id,
                "prompt.version": request.prompt_version,
            },
        ) as span:
            while True:
                budget.check_model_call(input_tokens, request.params.max_output_tokens)
                started = time.monotonic()
                try:
                    reply = self._call_once(request, budget.model_call_timeout_s)
                except HarnessError as exc:
                    duration = int((time.monotonic() - started) * 1000)
                    # a failed call still used a model slot: count it, with the input it was sent
                    budget.record_model_call(ModelUsage(input_tokens=input_tokens, output_tokens=0))
                    self._audit(
                        request, principal_id, project_id, "error", None, duration, attempt, False, exc.code
                    )
                    decision = self._retry.decide(exc.category, retries)
                    if exc.category is ErrorCategory.RETRYABLE and decision.retry:
                        retries += 1
                        span.set("retries", retries)
                        self._sleep(decision.delay_s)
                        continue
                    span.set("error.code", exc.code)
                    raise
                except Exception as exc:  # provider bug: fatal, never retried, type only
                    budget.record_model_call(ModelUsage(input_tokens=input_tokens, output_tokens=0))
                    self._audit(
                        request, principal_id, project_id, "error", None, 0, attempt, False, "model.failed"
                    )
                    raise HarnessError(
                        "model.failed", f"the provider failed ({type(exc).__name__})"
                    ) from None
                break
            duration = int((time.monotonic() - started) * 1000)
            usage = ModelUsage(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
            budget.record_model_call(usage)
            validation, parsed = _validate(reply.text, output_model)
            self._audit(
                request, principal_id, project_id, "success", reply, duration, attempt, validation.valid
            )
            span.set("tokens.input", usage.input_tokens)
            span.set("tokens.output", usage.output_tokens)
            span.set("model.id", reply.model_id)
            span.set("outcome", "valid" if validation.valid else "invalid")
        return ModelResponse(
            request_id=request.request_id,
            provider=self._provider.name,
            model_id=reply.model_id,
            output_text=reply.text,
            parsed=parsed,
            usage=usage,
            duration_ms=duration,
            attempt=attempt,
            validation=validation,
        )


__all__ = ["MODEL_LOCK", "LLMGateway"]
