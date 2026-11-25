"""The centralized LLM Gateway (HC-02, HS-15/HS-20/HS-21): the only door to a model. No LiteLLM (D-35).

``generate`` runs, in order:

1. **scope and input guard**: the request must belong to the budgeted task (or, for a platform service,
   carry no task); a registered secret or credential shape in any message, or an untrusted message not
   wrapped by the guard, is refused (``policy_denied``) and audited as ``model.refused``;
2. **rate limit** per agent role or service (Part F, HC-18): waits within a bound, otherwise
   ``rate.limited`` (the task waits for resources);
3. **routing** (Part F, GW-04): the router picks the model (operation > agent > slot > global) and the
   recovery rungs (reduced context, then smaller models) used only after *resource* failures;
4. **response cache** (D-28, off by default): a hit is re-validated against the contract;
5. **per rung**: circuit breaker, per-model free-RAM check (peak + margin), budget check, then the call
   under the cross-process lease (D-46) and the in-process lock (Spec §7.2). Inside the lock, any other
   loaded model is unloaded first (verified via ``/api/ps``). One effective timeout = the smallest of the
   model's configured total, the budget's call timeout and the provider timeout. A call that times out is
   **cancelled** and its model unloaded before the next rung, so a hung call never blocks the ladder;
6. **transient retries** (F-10b) stay on the same rung; **resource failures** (OOM, load failure, timeout,
   RAM, open breaker) step down; **invalid output** is never a recovery trigger (the executor's feedback
   retry handles it); every rung exhausted → ``model.exhausted`` (the task waits for resources and the run
   checkpoints: Spec scenario #22);
7. **validation**, **usage**, **audit** ``model.called`` (metadata only) and a **``ModelCall`` record**
   (no prompt, input or answer text; AC-F-01), plus a trace span with allowlisted attributes.

``embed`` is the same door for embeddings (RAG, Part F): platform services only (never agents), the
embedding slot only, an embedding cache per project, and the same lease, lock, timeout and records.

Without a router (Part C unit tests) the gateway behaves as before: the provider's slot default, timeouts
retried, no recovery ladder.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import hashlib
import json
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.errors import ErrorCategory
from devteam_core.contracts.events import ModelCallRecorded
from devteam_core.contracts.llm import (
    EmbedRequest,
    EmbedResponse,
    ModelCall,
    ModelParams,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    OutputValidation,
    RecoveryStep,
    RouteRule,
)
from devteam_core.contracts.statuses import ModelSlot
from devteam_core.ids import new_id
from devteam_core.logging import redact_text, secret_registry
from devteam_harness.budgets.manager import free_ram_mb
from devteam_harness.budgets.resource_lease import ResourceLease
from devteam_harness.errors import HarnessError, PolicyDeniedError, ResourceUnavailableError, TransientError
from devteam_harness.guardrails.input import InputGuard, InputRejectedError
from devteam_harness.guardrails.policy import AuditSink
from devteam_harness.llm_gateway.cache import EmbeddingCache, ResponseCache, response_key
from devteam_harness.llm_gateway.limits import RateLimits
from devteam_harness.llm_gateway.providers import CallTimeouts, LocalModelServer, ModelProvider, ProviderReply
from devteam_harness.llm_gateway.resilience import RECOVERABLE, CircuitBreakers, TokenCalibrator
from devteam_harness.llm_gateway.router import ModelChoice, Router
from devteam_harness.observability.tracing import Tracer
from devteam_harness.resilience import RetryPolicy
from devteam_state.repositories.audit import Outcome
from devteam_state.repositories.model_calls import record_model_call

MODEL_LOCK = threading.Lock()  # Spec §7.2: one model call at a time in this process, across gateways
_CALLS = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="devteam-llm")
MAX_VALIDATION_ERRORS = 10
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
T = TypeVar("T")


class CallBudget(Protocol):
    """What the gateway needs from a budget: ``TaskBudget`` (agents) or ``JobBudget`` (services)."""

    @property
    def project_id(self) -> str | None: ...

    @property
    def run_id(self) -> str | None: ...

    @property
    def task_id(self) -> str | None: ...

    @property
    def model_call_timeout_s(self) -> int: ...

    def check_context_window(self, requested: int) -> None: ...

    def check_model_call(self, input_tokens: int, max_output_tokens: int) -> None: ...

    def record_model_call(self, usage: ModelUsage) -> None: ...


@dataclass(frozen=True, slots=True)
class GatewayOptions:
    response_cache: bool = False  # D-28
    cancel_wait_s: float = 15.0
    unload_verify_s: float = 10.0
    digest_refresh_s: float = 300.0
    ram_margin_mb: int = 512


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


def input_hash(request: ModelRequest) -> str:
    """SHA-256 over the messages and the answer contract name (recorded instead of the text)."""
    material = json.dumps(
        [[m.role.value, m.trust.value, m.label, m.content] for m in request.messages]
        + [request.output_schema],
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _texts_hash(texts: tuple[str, ...]) -> str:
    return hashlib.sha256("\0".join(texts).encode("utf-8")).hexdigest()


def _same_model(a: str, b: str) -> bool:
    a, b = a.lower(), b.lower()
    return a in {b, f"{b}:latest"} or b == f"{a}:latest"


class _StepDown(Exception):  # noqa: N818 - internal control flow, never escapes the gateway
    def __init__(self, error: HarnessError) -> None:
        super().__init__(error.code)
        self.error = error


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
        router: Router | None = None,
        sessions: sessionmaker[Session] | None = None,
        rate_limits: RateLimits | None = None,
        breakers: CircuitBreakers | None = None,
        response_cache: ResponseCache | None = None,
        embedding_cache: EmbeddingCache | None = None,
        options: GatewayOptions | None = None,
        free_ram: Callable[[], int] = free_ram_mb,
        monotonic: Callable[[], float] = time.monotonic,
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
        self._router = router
        self._sessions = sessions
        self._rate = rate_limits
        self._breakers = breakers or CircuitBreakers()
        self._response_cache = response_cache
        self._embedding_cache = embedding_cache
        self._options = options or GatewayOptions()
        self._free_ram = free_ram
        self._monotonic = monotonic
        self._calibrator = TokenCalibrator()
        self._digests: dict[str, str] = {}
        self._digests_at = -math.inf
        self._digest_lock = threading.Lock()

    @property
    def router(self) -> Router | None:
        return self._router

    # ------------------------------------------------------------------------------- inventory

    def _server(self) -> LocalModelServer | None:
        return self._provider if isinstance(self._provider, LocalModelServer) else None

    def model_digest(self, tag: str) -> str | None:
        """The installed digest of ``tag`` (from the provider's inventory; refreshed periodically)."""
        server = self._server()
        if server is None:
            return None
        with self._digest_lock:
            stale = self._monotonic() - self._digests_at > self._options.digest_refresh_s
            if stale or tag.lower() not in self._digests:
                # keep what we had if unreachable; the call itself will report the server
                with contextlib.suppress(HarnessError):
                    self._digests = server.list_models()
                self._digests_at = self._monotonic()
            digest = self._digests.get(tag.lower()) or self._digests.get(f"{tag.lower()}:latest")
        return digest if digest and _HEX64.fullmatch(digest) else None

    def _prepare_model(self, tag: str) -> bool:
        """Inside the lock: unload any *other* loaded model (one model in memory, MODEL_ROUTING §4) and
        report whether ``tag`` must be loaded from cold."""
        server = self._server()
        if server is None:
            return False
        loaded = server.loaded_models()
        others = [m for m in loaded if not _same_model(m, tag)]
        for model in others:
            server.unload(model)
        if others:
            self._wait_unloaded(server, others)
        return not any(_same_model(m, tag) for m in loaded)

    def _wait_unloaded(self, server: LocalModelServer, models: list[str]) -> None:
        deadline = self._monotonic() + self._options.unload_verify_s
        while self._monotonic() < deadline:
            loaded = server.loaded_models()
            if not any(_same_model(m, x) for m in loaded for x in models):
                return
            self._sleep(0.25)

    # --------------------------------------------------------------------------------- records

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
        extra: dict[str, object] | None = None,
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
        if extra:
            details.update(extra)
        action = "model.refused" if outcome == "denied" else "model.called"
        return self._sink.record(
            action=action, outcome=outcome, principal_id=principal_id, project_id=project_id, details=details
        )

    def _record(self, call: dict[str, Any]) -> None:
        if self._sessions is None:
            return
        model_call = ModelCall.model_validate({"call_id": new_id("mcl", self._clock), **call})
        with self._sessions.begin() as session:
            record_model_call(session, model_call, created_at=self._clock.now())

    # ------------------------------------------------------------------------------- one call

    def _call_once(self, work: Callable[[], T], timeout_s: float) -> T:
        # D-46: never a model call while a sandbox run holds the machine (ResourceUnavailableError → wait).
        lease = self._lease.acquire("llm") if self._lease is not None else None
        if not MODEL_LOCK.acquire(timeout=timeout_s):
            if lease is not None:
                lease.release()
            raise TransientError("model.busy", "another model call is still running")
        try:
            future = _CALLS.submit(work)
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
            self._cancel(future)
            raise TransientError("model.timeout", f"the model did not answer within {timeout_s:g}s") from None

    def _cancel(self, future: concurrent.futures.Future[Any]) -> None:
        """Abort a timed-out call so the next rung never waits behind it (Part F plan §5 F.2)."""
        cancel = getattr(self._provider, "cancel_inflight", None)
        if not callable(cancel):
            return  # cannot cancel: the lock stays held until the call really ends (Spec §7.2)
        cancel()
        with contextlib.suppress(Exception):  # the cancelled call is expected to fail
            future.result(timeout=self._options.cancel_wait_s)

    # --------------------------------------------------------------------------------- generate

    def generate(  # noqa: PLR0912, PLR0915 - the documented pipeline, one step per block
        self,
        request: ModelRequest,
        *,
        budget: CallBudget,
        principal_id: str,
        output_model: type[BaseModel] | None = None,
        attempt: int = 1,
    ) -> ModelResponse:
        project_id = budget.project_id
        if request.task_id != budget.task_id or request.run_id != budget.run_id:
            raise PolicyDeniedError("model.task_mismatch", "the request does not belong to the budgeted task")
        service = principal_id.removeprefix("service:") if principal_id.startswith("service:") else None
        if service is None and budget.task_id is None:
            raise PolicyDeniedError("model.no_task", "an agent's model call must belong to a task")
        try:
            self._guard.check_request(request)
        except InputRejectedError as exc:
            self._audit(request, principal_id, project_id, "denied", None, 0, attempt, False, exc.code)
            raise PolicyDeniedError(exc.code, exc.message) from None

        caller = {"agent": None, "service": service} if service else {"agent": request.role, "service": None}
        base_record: dict[str, Any] = {
            "project_id": project_id,
            "run_id": request.run_id,
            "task_id": request.task_id,
            **caller,
            "kind": "generate",
            "operation_id": request.operation_id,
            "prompt_id": request.prompt_id,
            "prompt_version": request.prompt_version,
            "prompt_hash": request.prompt_hash,
            "input_hash": input_hash(request),
            "schema_name": request.output_schema,
        }
        if self._rate is not None:
            self._rate.acquire("service" if service else "agent", service or request.role.value)
        budget.check_context_window(request.params.context_window)

        choice: ModelChoice | None = None
        rungs: tuple[ModelChoice | None, ...] = (None,)
        if self._router is not None:
            choice = self._router.resolve(request.role, request.operation_id, request.slot)
            rungs = (choice, *self._router.recovery(choice))

        retries_total = 0
        with self._tracer.span(
            "llm.generate",
            project_id=project_id or "",
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
            if choice is not None:
                span.set("model.route_rule", choice.rule.value)
                cached = self._from_cache(
                    request, choice, output_model, base_record, principal_id, attempt, span
                )
                if cached is not None:
                    return cached
            last_error: HarnessError | None = None
            used: ModelChoice | None = None
            reply: ProviderReply | None = None
            duration = 0
            cold = False
            for rung in rungs:
                if rung is not None:
                    blocked = self._rung_blocked(rung, request.params.max_output_tokens)
                    if blocked is not None:
                        last_error = blocked
                        continue
                call_request = request
                if rung is not None and rung.num_ctx is not None:
                    params = ModelParams.model_validate(
                        {**request.params.model_dump(), "context_window": rung.num_ctx}
                    )
                    call_request = request.model_copy(update={"params": params})
                try:
                    reply, duration, cold, retries = self._attempt(
                        call_request, rung, budget, principal_id, attempt, base_record
                    )
                    retries_total += retries
                except _StepDown as stepped:
                    assert rung is not None  # noqa: S101 - only routed rungs step down
                    self._breakers.failure(rung.tag)
                    last_error = stepped.error
                    span.set("model.recovery_trigger", stepped.error.code)
                    continue
                used = rung
                break
            if reply is None:
                span.set("error.code", "model.exhausted")
                code = last_error.code if last_error else "none"
                raise ResourceUnavailableError(
                    "model.exhausted",
                    f"no local model could answer after every recovery step (last: {code}); "
                    "the task waits for resources",
                )

            usage = ModelUsage(input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
            budget.record_model_call(usage)
            validation, parsed = _validate(reply.text, output_model)
            tag = used.tag if used is not None else reply.model_id
            digest = self.model_digest(tag) if used is not None else None
            slow = bool(used and used.entry.slow_after_s and duration / 1000 > used.entry.slow_after_s)
            if used is not None:
                if slow:
                    self._breakers.failure(used.tag)
                else:
                    self._breakers.success(used.tag)
                chars = sum(len(m.content) for m in request.messages)
                self._calibrator.observe(used.tag, chars, reply.input_tokens, cold=cold)
            step = used.step if used is not None else RecoveryStep.NONE
            rule = used.rule if used is not None else RouteRule.SLOT
            self._audit(
                request,
                principal_id,
                project_id,
                "success",
                reply,
                duration,
                attempt,
                validation.valid,
                extra={
                    "model_tag": tag,
                    "route_rule": rule.value,
                    "recovery_step": step.value,
                    "cache_hit": False,
                    "slow": slow,
                },
            )
            self._record(
                {
                    **base_record,
                    "slot": (used.slot if used else request.slot).value,
                    "model_tag": tag,
                    "model_digest": digest,
                    "route_rule": rule.value,
                    "recovery_step": step.value,
                    "cache_hit": False,
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "duration_ms": duration,
                    "free_ram_mb": self._free_ram(),
                    "outcome": "valid" if validation.valid else "invalid",
                    "attempt": attempt,
                }
            )
            if (
                validation.valid
                and used is not None
                and digest
                and project_id
                and self._options.response_cache
                and self._response_cache is not None
            ):
                self._response_cache.put(
                    self._cache_key(request, digest, project_id),
                    project_id=project_id,
                    model_digest=digest,
                    prompt_hash=request.prompt_hash,
                    text=reply.text,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                )
            span.set("tokens.input", usage.input_tokens)
            span.set("tokens.output", usage.output_tokens)
            span.set("model.id", tag)
            span.set("model.recovery_step", step.value)
            span.set("model.slow", slow)
            if retries_total:
                span.set("retries", retries_total)
            span.set("outcome", "valid" if validation.valid else "invalid")
        return ModelResponse(
            request_id=request.request_id,
            provider=self._provider.name,
            model_id=tag,
            model_digest=digest,
            route_rule=rule if used is not None else None,
            recovery_step=step,
            output_text=reply.text,
            parsed=parsed,
            usage=usage,
            duration_ms=duration,
            attempt=attempt,
            validation=validation,
        )

    def _rung_blocked(self, rung: ModelChoice, max_output_tokens: int) -> HarnessError | None:
        if rung.num_ctx is not None and max_output_tokens >= rung.num_ctx:
            return ResourceUnavailableError(
                "model.context_too_small", "the reduced context cannot fit the answer"
            )
        if not self._breakers.allows(rung.tag):
            return ResourceUnavailableError(
                "model.circuit_open", f"model '{rung.tag}' is resting after failures"
            )
        peak = rung.entry.peak_ram_mb
        if peak is not None:
            free = self._free_ram()
            if free < peak + self._options.ram_margin_mb:
                return ResourceUnavailableError(
                    "model.ram", f"{free} MB free; '{rung.tag}' needs {peak + self._options.ram_margin_mb} MB"
                )
        return None

    def _effective_timeout(
        self, rung: ModelChoice | None, budget: CallBudget
    ) -> tuple[float, CallTimeouts | None]:
        if rung is None:
            return float(budget.model_call_timeout_s), None
        total = min(rung.entry.timeouts.total_s, float(budget.model_call_timeout_s))
        return total, CallTimeouts(
            connect_s=rung.entry.timeouts.connect_s, read_s=min(rung.entry.timeouts.read_s, total)
        )

    def _attempt(  # noqa: PLR0917 - one rung's attempt with its retries
        self,
        request: ModelRequest,
        rung: ModelChoice | None,
        budget: CallBudget,
        principal_id: str,
        attempt: int,
        base_record: dict[str, Any],
    ) -> tuple[ProviderReply, int, bool, int]:
        text = "".join(m.content for m in request.messages)
        tag = rung.tag if rung is not None else None
        estimate = self._calibrator.estimate(tag, text) if tag else self._calibrator.estimate("", text)
        timeout_s, timeouts = self._effective_timeout(rung, budget)
        retries = 0
        while True:
            budget.check_model_call(estimate, request.params.max_output_tokens)
            started = self._monotonic()

            def work() -> tuple[ProviderReply, bool]:
                cold = self._prepare_model(tag) if tag else False
                if tag is None:
                    return self._provider.generate(request), cold
                return self._provider.generate(request, model=tag, timeouts=timeouts), cold

            try:
                reply, cold = self._call_once(work, timeout_s)
            except HarnessError as exc:
                duration = int((self._monotonic() - started) * 1000)
                # a failed call still used a model slot: count it, with the input it was sent
                budget.record_model_call(ModelUsage(input_tokens=estimate, output_tokens=0))
                self._audit(
                    request, principal_id, budget.project_id, "error", None, duration, attempt, False,
                    exc.code,
                    extra={"model_tag": tag, "recovery_step": (rung.step.value if rung else "none")},
                )  # fmt: skip
                if rung is not None:
                    self._record(
                        {
                            **base_record, "slot": rung.slot.value, "model_tag": rung.tag,
                            "model_digest": self.model_digest(rung.tag), "route_rule": rung.rule.value,
                            "recovery_step": rung.step.value, "input_tokens": estimate, "output_tokens": 0,
                            "duration_ms": duration, "free_ram_mb": self._free_ram(), "outcome": "error",
                            "error_code": exc.code, "attempt": attempt,
                        }
                    )  # fmt: skip
                    if exc.code in RECOVERABLE:
                        raise _StepDown(exc) from None
                decision = self._retry.decide(exc.category, retries)
                if exc.category is ErrorCategory.RETRYABLE and decision.retry:
                    retries += 1
                    self._sleep(decision.delay_s)
                    continue
                raise
            except Exception as exc:  # provider bug: fatal, never retried, type only
                budget.record_model_call(ModelUsage(input_tokens=estimate, output_tokens=0))
                self._audit(
                    request, principal_id, budget.project_id, "error", None, 0, attempt, False, "model.failed"
                )
                raise HarnessError("model.failed", f"the provider failed ({type(exc).__name__})") from None
            duration = int((self._monotonic() - started) * 1000)
            return reply, duration, cold, retries

    # ----------------------------------------------------------------------------- response cache

    def _cache_key(self, request: ModelRequest, digest: str, project_id: str) -> str:
        return response_key(
            project_id=project_id,
            model_digest=digest,
            prompt_hash=request.prompt_hash,
            input_hash=input_hash(request),
            params=request.params.model_dump(),
            schema_name=request.output_schema,
        )

    def _from_cache(  # noqa: PLR0917 - private helper
        self,
        request: ModelRequest,
        choice: ModelChoice,
        output_model: type[BaseModel] | None,
        base_record: dict[str, Any],
        principal_id: str,
        attempt: int,
        span: Any,
    ) -> ModelResponse | None:
        project_id = base_record["project_id"]
        if not (self._options.response_cache and self._response_cache is not None and project_id):
            return None
        digest = self.model_digest(choice.tag)
        if digest is None:
            return None
        cached = self._response_cache.get(self._cache_key(request, digest, project_id), project_id=project_id)
        if cached is None:
            return None
        validation, parsed = _validate(cached.text, output_model)
        if not validation.valid:  # re-validated on every hit (D-28); an invalid entry is a miss
            return None
        reply = ProviderReply(cached.text, cached.input_tokens, cached.output_tokens, choice.tag)
        self._audit(
            request, principal_id, project_id, "success", reply, 0, attempt, True,
            extra={"model_tag": choice.tag, "route_rule": choice.rule.value, "recovery_step": "none",
                   "cache_hit": True},
        )  # fmt: skip
        self._record(
            {
                **base_record, "slot": choice.slot.value, "model_tag": choice.tag, "model_digest": digest,
                "route_rule": choice.rule.value, "recovery_step": "none", "cache_hit": True,
                "input_tokens": cached.input_tokens, "output_tokens": cached.output_tokens, "duration_ms": 0,
                "free_ram_mb": None, "outcome": "valid", "attempt": attempt,
            }
        )  # fmt: skip
        span.set("model.cache_hit", True)
        span.set("model.id", choice.tag)
        span.set("outcome", "valid")
        return ModelResponse(
            request_id=request.request_id,
            provider=self._provider.name,
            model_id=choice.tag,
            model_digest=digest,
            route_rule=choice.rule,
            cache_hit=True,
            output_text=cached.text,
            parsed=parsed,
            usage=ModelUsage(input_tokens=cached.input_tokens, output_tokens=cached.output_tokens),
            duration_ms=0,
            attempt=attempt,
            validation=validation,
        )

    # ------------------------------------------------------------------------------------ embed

    def embed(self, request: EmbedRequest, *, budget: CallBudget) -> EmbedResponse:  # noqa: PLR0915
        """Embeddings for a platform service (RAG). Same door, lease, lock, timeout and records."""
        server = self._server()
        if self._router is None or server is None:
            raise ResourceUnavailableError(
                "rag.embedding_not_configured", "embeddings need the local model server"
            )
        if budget.project_id != request.project_id or budget.task_id is not None:
            raise PolicyDeniedError("model.task_mismatch", "an embedding request belongs to a service job")
        service = request.principal_id.removeprefix("service:")
        operation = request.purpose
        choice = self._router.embedding()
        if self._rate is not None:
            self._rate.acquire("service", service)
        digest = self.model_digest(choice.tag)
        unique = tuple(dict.fromkeys(request.texts))
        vectors: dict[str, tuple[float, ...]] = {}
        if self._embedding_cache is not None and digest:
            vectors.update(self._embedding_cache.get_many(request.project_id, digest, unique))
        missing = tuple(t for t in unique if t not in vectors)
        base = {
            "project_id": request.project_id, "run_id": None, "task_id": None, "agent": None,
            "service": service, "kind": "embed", "operation_id": operation, "slot": ModelSlot.EMBEDDING.value,
            "model_tag": choice.tag, "model_digest": digest, "route_rule": RouteRule.SLOT.value,
            "recovery_step": "none", "input_hash": _texts_hash(request.texts),
        }  # fmt: skip
        details = {"operation_id": operation, "provider": self._provider.name.value, "model_tag": choice.tag,
                   "request_id": request.request_id, "inputs": len(request.texts)}  # fmt: skip
        with self._tracer.span(
            "llm.embed",
            project_id=request.project_id,
            operation_id=operation,
            **{"model.provider": self._provider.name.value, "model.slot": ModelSlot.EMBEDDING.value},
        ) as span:
            span.set("model.id", choice.tag)
            if not missing:
                self._sink.record(
                    action="model.called", outcome="success", principal_id=request.principal_id,
                    project_id=request.project_id, details={**details, "cache_hit": True, "duration_ms": 0},
                )  # fmt: skip
                self._record({**base, "cache_hit": True, "input_tokens": 0, "output_tokens": 0,
                              "duration_ms": 0, "outcome": "valid", "attempt": 1})  # fmt: skip
                span.set("model.cache_hit", True)
                return self._embed_response(request, choice.tag, digest, vectors, 0, 0)
            blocked = self._rung_blocked(choice, 0)
            if blocked is not None:
                raise blocked
            estimate = sum(self._calibrator.estimate(choice.tag, t) for t in missing)
            timeout_s, timeouts = self._effective_timeout(choice, budget)
            retries = 0
            while True:
                budget.check_model_call(estimate, 0)
                started = self._monotonic()

                def work() -> tuple[Any, bool]:
                    cold = self._prepare_model(choice.tag)
                    return server.embed(choice.tag, missing, timeouts=timeouts), cold

                try:
                    reply, _cold = self._call_once(work, timeout_s)
                except HarnessError as exc:
                    duration = int((self._monotonic() - started) * 1000)
                    budget.record_model_call(ModelUsage(input_tokens=estimate, output_tokens=0))
                    self._sink.record(
                        action="model.called", outcome="error", principal_id=request.principal_id,
                        project_id=request.project_id,
                        details={**details, "error_code": exc.code, "duration_ms": duration,
                                 "cache_hit": False},
                    )  # fmt: skip
                    self._record({**base, "cache_hit": False, "input_tokens": estimate, "output_tokens": 0,
                                  "duration_ms": duration, "outcome": "error", "error_code": exc.code,
                                  "attempt": retries + 1})  # fmt: skip
                    if exc.code in RECOVERABLE:
                        self._breakers.failure(choice.tag)
                        raise ResourceUnavailableError(
                            exc.code, "the embedding model is not available right now; the job waits"
                        ) from None
                    decision = self._retry.decide(exc.category, retries)
                    if exc.category is ErrorCategory.RETRYABLE and decision.retry:
                        retries += 1
                        self._sleep(decision.delay_s)
                        continue
                    raise
                break
            duration = int((self._monotonic() - started) * 1000)
            budget.record_model_call(ModelUsage(input_tokens=reply.input_tokens, output_tokens=0))
            self._breakers.success(choice.tag)
            fresh = dict(zip(missing, reply.vectors, strict=True))
            vectors.update(fresh)
            if self._embedding_cache is not None and digest:
                self._embedding_cache.put_many(request.project_id, digest, fresh)
            self._sink.record(
                action="model.called", outcome="success", principal_id=request.principal_id,
                project_id=request.project_id,
                details={**details, "cache_hit": False, "duration_ms": duration,
                         "input_tokens": reply.input_tokens,
                         "embedded": len(missing)},
            )  # fmt: skip
            self._record({**base, "cache_hit": False, "input_tokens": reply.input_tokens, "output_tokens": 0,
                          "duration_ms": duration, "free_ram_mb": self._free_ram(), "outcome": "valid",
                          "attempt": retries + 1})  # fmt: skip
            span.set("tokens.input", reply.input_tokens)
            span.set("outcome", "valid")
            return self._embed_response(request, choice.tag, digest, vectors, reply.input_tokens, duration)

    def _embed_response(  # noqa: PLR0917 - private helper
        self,
        request: EmbedRequest,
        tag: str,
        digest: str | None,
        vectors: dict[str, tuple[float, ...]],
        tokens: int,
        duration: int,
    ) -> EmbedResponse:
        return EmbedResponse(
            request_id=request.request_id,
            model_id=tag,
            model_digest=digest,
            vectors=tuple(vectors[t] for t in request.texts),
            input_tokens=tokens,
            duration_ms=duration,
        )


__all__ = ["MODEL_LOCK", "CallBudget", "GatewayOptions", "LLMGateway", "input_hash"]
