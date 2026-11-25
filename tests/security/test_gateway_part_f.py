"""Part F.1/F.2 LLM Gateway: routing, ModelCall records, recovery ladder, cancellation, RAM checks, circuit
breaker, unload-before-switch, rate limits, response cache (D-28) and embeddings.

The real ``OllamaProvider`` talks to a **respx-mocked** Ollama HTTP API (no model, no network), so every
HTTP path of the provider and every gateway branch is exercised deterministically. AC-F-01 is measured
directly: provider HTTP calls = ``ModelCall`` rows = ``model.called`` audit rows.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from sqlalchemy import select

from devteam_core.config.models import parse_model_catalog
from devteam_core.contracts.errors import ErrorCategory
from devteam_core.contracts.llm import (
    ChatMessage,
    ChatRole,
    EmbedRequest,
    ModelCall,
    ModelRequest,
    RecoveryStep,
    RouteRule,
    Trust,
)
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.ids import new_id
from devteam_harness.audit import DatabaseAuditSink
from devteam_harness.budgets.jobs import JobBudget, JobLimits
from devteam_harness.errors import PolicyDeniedError, ResourceUnavailableError
from devteam_harness.guardrails.input import InputGuard
from devteam_harness.llm_gateway import LLMGateway
from devteam_harness.llm_gateway.cache import EmbeddingCache, ResponseCache
from devteam_harness.llm_gateway.gateway import GatewayOptions
from devteam_harness.llm_gateway.limits import RateLimitConfig, RateLimits
from devteam_harness.llm_gateway.providers.ollama import OllamaProvider
from devteam_harness.llm_gateway.resilience import CircuitBreakers
from devteam_harness.llm_gateway.router import Router
from devteam_harness.prompts import sha256_text
from devteam_harness.resilience import RetryPolicy
from devteam_state.models import AuditEvent, ModelCallRow
from tests.fixtures.harness import Stack, make_stack

pytestmark = pytest.mark.security

BASE = "http://127.0.0.1:11434"
SYSTEM = "You are the backend developer agent. Answer in JSON."
CANARY_TEXT = "Quillfeather-7731 lending clause"
DIGESTS = {"code:3b": "b" * 64, "small:1b": "c" * 64, "emb:latest": "d" * 64}
CATALOG = """
schema_version: 1
default_slot: instruct
ram_margin_mb: 500
models:
  code:3b: {approved: true, benchmark_ref: b.md, peak_ram_mb: 2150, num_ctx: 4096, reduced_num_ctx: 2048,
            timeouts: {connect_s: 5, read_s: TOTAL, total_s: TOTAL}, slow_after_s: SLOW}
  small:1b: {approved: true, benchmark_ref: b.md, peak_ram_mb: 1200, num_ctx: 4096,
             timeouts: {connect_s: 5, read_s: 60, total_s: 60}}
  emb: {kind: embedding, approved: true, benchmark_ref: e.md, peak_ram_mb: 500, num_ctx: 2048,
        timeouts: {connect_s: 5, read_s: 30, total_s: 30}}
slots:
  instruct: {model: code:3b}
  coder: {model: code:3b}
  review: {model: null, inherit: coder}
  coder_large: {model: null, inherit: coder}
  fallback: {model: small:1b}
  embedding: {model: EMBED}
fallback_ladders: {coder: [small:1b], instruct: [small:1b]}
agents: {}
operations: {}
"""


@dataclass
class Rig:
    stack: Stack
    gateway: LLMGateway
    provider: OllamaProvider
    mock: respx.MockRouter
    chat: respx.Route
    embed: respx.Route
    unload: respx.Route
    loaded: list[str]

    def calls(self) -> list[ModelCall]:
        with self.stack.sessions() as session:
            rows = session.scalars(select(ModelCallRow).order_by(ModelCallRow.created_at))
            from devteam_state.repositories.model_calls import _to_contract  # noqa: PLC0415

            return [_to_contract(r) for r in rows]

    def audits(self) -> list[AuditEvent]:
        return [e for e in self.stack.audit() if e.action == "model.called"]

    def bodies(self) -> list[dict[str, Any]]:
        return [json.loads(c.request.content) for c in self.chat.calls]


def _ok(text: str = '{"files": []}') -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "x",
            "message": {"content": text},
            "done": True,
            "prompt_eval_count": 40,
            "eval_count": 5,
        },
    )


def _oom() -> httpx.Response:
    return httpx.Response(
        500, json={"error": "model requires more system memory (2.1 GiB) than is available"}
    )


@pytest.fixture
def make_rig(tmp_path: Path) -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []
    with respx.mock(base_url=BASE, assert_all_called=False) as mock:

        def factory(
            *,
            free_ram: int = 64_000,
            total_s: float = 100,
            slow_s: float = 90,
            embedding: str = "emb",
            response_cache: bool = False,
            breaker_threshold: int = 3,
            rate: RateLimitConfig | None = None,
        ) -> Rig:
            stack = make_stack(tmp_path / f"r{len(rigs)}")
            text = (
                CATALOG.replace("TOTAL", str(total_s))
                .replace("SLOW", str(slow_s))
                .replace("EMBED", embedding)
            )
            router = Router(parse_model_catalog(text))
            loaded: list[str] = []
            mock.get("/api/tags").mock(
                return_value=httpx.Response(
                    200, json={"models": [{"name": n, "digest": d} for n, d in DIGESTS.items()]}
                )
            )
            mock.get("/api/ps").mock(
                side_effect=lambda _r: httpx.Response(200, json={"models": [{"name": m} for m in loaded]})
            )

            def _unload(request: httpx.Request) -> httpx.Response:
                model = json.loads(request.content)["model"]
                if model in loaded:
                    loaded.remove(model)
                return httpx.Response(200, json={"done": True})

            unload = mock.post("/api/generate").mock(side_effect=_unload)
            chat = mock.post("/api/chat")
            embed = mock.post("/api/embed")
            provider = OllamaProvider(
                BASE, {ModelSlot.INSTRUCT: "code:3b"}, timeout_s=60, keep_alive="0s",
                schemas=lambda _name: {"type": "object"},
            )  # fmt: skip
            gateway = LLMGateway(
                provider,
                InputGuard(stack.guardrails.input),
                DatabaseAuditSink(stack.sessions),
                stack.tracer,
                RetryPolicy(stack.budgets.budgets.retries, jitter=lambda: 0.0),
                sleep=lambda _s: None,
                router=router,
                sessions=stack.sessions,
                rate_limits=RateLimits(rate, sleep=lambda _s: None) if rate else None,
                breakers=CircuitBreakers(threshold=breaker_threshold),
                response_cache=ResponseCache(stack.sessions),
                embedding_cache=EmbeddingCache(stack.sessions),
                options=GatewayOptions(response_cache=response_cache, unload_verify_s=0.2, cancel_wait_s=5),
                free_ram=lambda: free_ram,
            )
            rig = Rig(stack, gateway, provider, mock, chat, embed, unload, loaded)
            rigs.append(rig)
            return rig

        yield factory
    for rig in rigs:
        rig.provider.close()


def _request(task: Any, *, content: str = CANARY_TEXT, schema: str | None = None) -> ModelRequest:
    return ModelRequest(
        request_id=new_id("mrq"),
        role=AgentRole.BACKEND_DEVELOPER,
        operation_id="backend_developer.generate_code",
        slot=ModelSlot.CODER,
        task_id=task.task_id,
        run_id=task.run_id,
        messages=(
            ChatMessage(role=ChatRole.SYSTEM, content=SYSTEM, trust=Trust.TRUSTED),
            ChatMessage(role=ChatRole.USER, content=content, trust=Trust.TRUSTED),
        ),
        output_schema=schema,
        prompt_id="backend_developer.system",
        prompt_version=1,
        prompt_hash=sha256_text(SYSTEM),
    )


def _generate(rig: Rig, request: ModelRequest | None = None) -> Any:
    task = rig.stack.add_task()
    budget = rig.stack.budgets.open_task(task)
    request = request or _request(task)
    if request.task_id != task.task_id:
        request = request.model_copy(update={"task_id": task.task_id, "run_id": task.run_id})
    return rig.gateway.generate(
        request, budget=budget, principal_id=f"agent:backend_developer:{task.task_id}"
    )


# ------------------------------------------------------------------------------- F.1 routing


def test_routes_by_slot_and_records_every_call_without_content(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.chat.mock(return_value=_ok())
    response = _generate(rig)
    assert rig.bodies()[0]["model"] == "code:3b" and rig.bodies()[0]["options"]["num_ctx"] == 4096
    assert response.route_rule is RouteRule.SLOT and response.model_digest == "b" * 64
    [call] = rig.calls()
    assert (call.model_tag, call.model_digest, call.route_rule, call.outcome) == (
        "code:3b", "b" * 64, RouteRule.SLOT, "valid"
    )  # fmt: skip
    assert call.agent is AgentRole.BACKEND_DEVELOPER and call.prompt_hash == sha256_text(SYSTEM)
    # AC-F-01: provider HTTP calls = ModelCall rows = audit rows
    assert rig.chat.call_count == len(rig.calls()) == len(rig.audits()) == 1
    # no content anywhere in records, audit or spans
    with rig.stack.sessions() as session:
        stored = json.dumps([list(r) for r in session.execute(select(ModelCallRow.__table__))], default=str)
    blob = (
        stored
        + json.dumps([a.details for a in rig.audits()])
        + json.dumps([s.attributes for s in rig.stack.spans()])
    )
    assert CANARY_TEXT not in blob and SYSTEM not in blob and '"files"' not in blob


def test_model_call_contract_has_no_content_field() -> None:
    forbidden = {"text", "content", "messages", "output", "output_text", "prompt", "answer", "input"}
    assert forbidden.isdisjoint(ModelCall.model_fields), set(ModelCall.model_fields) & forbidden


def test_unloads_another_loaded_model_before_switching(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.loaded.append("small:1b")
    rig.chat.mock(return_value=_ok())
    _generate(rig)
    assert [json.loads(c.request.content)["model"] for c in rig.unload.calls] == ["small:1b"]
    assert rig.unload.calls[0].request.url.path == "/api/generate"


# ------------------------------------------------------------------------------- F.2 resilience


def test_oom_steps_down_reduced_context_then_smaller_model(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.chat.mock(side_effect=[_oom(), _oom(), _ok()])
    response = _generate(rig)
    assert [(b["model"], b["options"]["num_ctx"]) for b in rig.bodies()] == [
        ("code:3b", 4096), ("code:3b", 2048), ("small:1b", 4096)
    ]  # fmt: skip
    assert response.recovery_step is RecoveryStep.SMALLER_MODEL and response.model_id == "small:1b"
    calls = rig.calls()
    assert [(c.outcome, c.error_code, c.recovery_step) for c in calls] == [
        ("error", "model.oom", RecoveryStep.NONE),
        ("error", "model.oom", RecoveryStep.REDUCED_CONTEXT),
        ("valid", None, RecoveryStep.SMALLER_MODEL),
    ]
    assert rig.chat.call_count == len(calls) == len(rig.audits())  # AC-F-01 holds for failures too


def test_exhausted_ladder_waits_for_resources_with_no_other_endpoint(make_rig: Callable[..., Rig]) -> None:
    """Spec scenario #22 at gateway level: OOM everywhere → waiting for resources, no external call."""
    rig = make_rig()
    rig.chat.mock(side_effect=lambda _r: _oom())
    with pytest.raises(ResourceUnavailableError) as info:
        _generate(rig)
    assert info.value.code == "model.exhausted"
    assert info.value.category is ErrorCategory.RESOURCE_UNAVAILABLE
    assert rig.chat.call_count == 3
    hosts = {c.request.url.host for c in rig.mock.calls}
    assert hosts == {"127.0.0.1"}  # every request went to the local Ollama only


def test_invalid_output_never_steps_down(make_rig: Callable[..., Rig]) -> None:
    from devteam_core.contracts.drafts import DocumentDraft  # noqa: PLC0415

    rig = make_rig()
    rig.chat.mock(return_value=_ok("not json"))
    task = rig.stack.add_task()
    request = _request(task, schema="DocumentDraft")
    response = rig.gateway.generate(
        request,
        budget=rig.stack.budgets.open_task(task),
        principal_id=f"agent:backend_developer:{task.task_id}",
        output_model=DocumentDraft,
    )
    assert not response.validation.valid and response.recovery_step is RecoveryStep.NONE
    assert rig.chat.call_count == 1 and rig.calls()[0].outcome == "invalid"


def test_a_timed_out_call_is_cancelled_and_the_next_rung_runs(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(total_s=0.5, slow_s=0.4)
    cancelled: list[bool] = []
    original = rig.provider.cancel_inflight

    def spy() -> None:
        cancelled.append(True)
        original()

    rig.provider.cancel_inflight = spy  # type: ignore[method-assign]

    seen: list[int] = []

    def slow_then_ok(_request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) == 1:
            time.sleep(1.5)
        return _ok()

    rig.chat.mock(side_effect=slow_then_ok)
    response = _generate(rig)
    assert cancelled == [True]
    assert response.recovery_step is RecoveryStep.REDUCED_CONTEXT
    assert [c.error_code for c in rig.calls()] == ["model.timeout", None]


def test_a_model_that_does_not_fit_in_memory_is_never_called(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(free_ram=2_000)  # code:3b needs 2150+500; small:1b needs 1200+500
    rig.chat.mock(return_value=_ok())
    response = _generate(rig)
    assert [b["model"] for b in rig.bodies()] == ["small:1b"] and response.model_id == "small:1b"


def test_too_little_memory_for_every_model_waits(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(free_ram=500)
    rig.chat.mock(return_value=_ok())
    with pytest.raises(ResourceUnavailableError, match=r"model\.ram"):
        _generate(rig)
    assert rig.chat.call_count == 0


def test_circuit_breaker_skips_a_failing_model(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(breaker_threshold=1)
    rig.chat.mock(side_effect=[_oom(), _ok(), _ok()])
    first = _generate(rig)  # code:3b OOM → breaker open → reduced rung skipped → small:1b
    second = _generate(rig)  # code:3b skipped without a call
    assert first.model_id == second.model_id == "small:1b"
    assert [b["model"] for b in rig.bodies()] == ["code:3b", "small:1b", "small:1b"]


def test_rate_limit_waits_then_refuses(make_rig: Callable[..., Rig]) -> None:
    config = RateLimitConfig.model_validate(
        {
            "max_wait_s": 0,
            "agents": {"default": {"calls_per_minute": 1, "burst": 1}},
            "services": {"default": {"calls_per_minute": 1, "burst": 1}},
        }
    )
    rig = make_rig(rate=config)
    rig.chat.mock(return_value=_ok())
    _generate(rig)
    with pytest.raises(ResourceUnavailableError) as info:
        _generate(rig)
    assert info.value.code == "rate.limited"
    assert rig.chat.call_count == 1  # never dropped silently, never a recovery trigger


# ------------------------------------------------------------------------------- D-28 cache


def test_response_cache_is_off_by_default(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.chat.mock(return_value=_ok())
    task = rig.stack.add_task()
    request = _request(task)
    for _ in range(2):
        rig.gateway.generate(
            request,
            budget=rig.stack.budgets.open_task(task),
            principal_id=f"agent:backend_developer:{task.task_id}",
        )
    assert rig.chat.call_count == 2


def test_cache_hit_is_revalidated_project_scoped_and_recorded(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(response_cache=True)
    rig.chat.mock(return_value=_ok())
    task = rig.stack.add_task()
    request = _request(task)
    principal = f"agent:backend_developer:{task.task_id}"
    rig.gateway.generate(request, budget=rig.stack.budgets.open_task(task), principal_id=principal)
    hit = rig.gateway.generate(request, budget=rig.stack.budgets.open_task(task), principal_id=principal)
    assert hit.cache_hit and hit.validation.valid and rig.chat.call_count == 1
    assert [c.cache_hit for c in rig.calls()] == [False, True]
    # another project never sees this entry
    other = make_rig(response_cache=True)  # another project (own database and project ID)
    other.chat.mock(return_value=_ok())  # respx shares one route per pattern: re-arm it
    _generate(other, request)
    assert [c.cache_hit for c in other.calls()] == [False]


def test_an_answer_containing_a_secret_is_never_cached(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(response_cache=True)
    token = "ghp_" + "a1b2c3d4e5" * 4  # gitleaks:allow (synthetic token pattern)
    rig.chat.mock(return_value=_ok(json.dumps({"files": [], "note": token})))
    task = rig.stack.add_task()
    request = _request(task)
    principal = f"agent:backend_developer:{task.task_id}"
    for _ in range(2):
        rig.gateway.generate(request, budget=rig.stack.budgets.open_task(task), principal_id=principal)
    assert rig.chat.call_count == 2


# ------------------------------------------------------------------------------- embeddings


def _job(project: str, **kw: Any) -> JobBudget:
    return JobBudget(
        JobLimits(
            max_model_calls=10, max_tokens=100_000, max_minutes=10, model_call_timeout_s=30, min_free_ram_mb=0
        ),
        project_id=project,
        **kw,
    )


def _embed_request(project: str, *texts: str) -> EmbedRequest:
    return EmbedRequest(
        request_id=new_id("mrq"), principal_id="service:rag_ingestor", purpose="rag.index",
        project_id=project, texts=texts,
    )  # fmt: skip


def test_embeddings_go_through_the_gateway_and_are_cached(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.embed.mock(
        side_effect=lambda r: httpx.Response(
            200,
            json={
                "model": "emb",
                "embeddings": [[0.1, 0.2, 0.3] for _ in json.loads(r.content)["input"]],
                "prompt_eval_count": 7,
            },
        )
    )
    project = rig.stack.project_id
    first = rig.gateway.embed(_embed_request(project, "alpha", "beta"), budget=_job(project))
    again = rig.gateway.embed(_embed_request(project, "beta", "alpha"), budget=_job(project))
    assert len(first.vectors) == 2
    assert again.vectors[0] == pytest.approx(first.vectors[1]) and again.vectors[1] == pytest.approx(
        first.vectors[0]
    )
    assert rig.embed.call_count == 1  # second request fully served by the per-project embedding cache
    calls = rig.calls()
    assert [(c.kind, c.service, c.cache_hit) for c in calls] == [
        ("embed", "rag_ingestor", False),
        ("embed", "rag_ingestor", True),
    ]
    assert all(c.agent is None and c.slot is ModelSlot.EMBEDDING for c in calls)
    assert len(rig.audits()) == 2


def test_embedding_requests_are_for_services_only() -> None:
    with pytest.raises(ValueError, match="principal_id"):
        EmbedRequest(
            request_id=new_id("mrq"), principal_id="agent:brd_analyst:tsk_01JD3X5Q0000000000000000AA",
            purpose="rag.index", project_id=new_id("prj"), texts=("x",),
        )  # fmt: skip


def test_an_embedding_job_budget_must_match_the_project(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    with pytest.raises(PolicyDeniedError):
        rig.gateway.embed(_embed_request(rig.stack.project_id, "x"), budget=_job(new_id("prj")))
    assert rig.embed.call_count == 0


def test_no_embedding_model_fails_closed(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig(embedding="null")
    with pytest.raises(ResourceUnavailableError) as info:
        rig.gateway.embed(_embed_request(rig.stack.project_id, "x"), budget=_job(rig.stack.project_id))
    assert info.value.code == "rag.embedding_not_configured" and rig.embed.call_count == 0


def test_an_agent_call_without_a_task_is_refused(make_rig: Callable[..., Rig]) -> None:
    rig = make_rig()
    rig.chat.mock(return_value=_ok())
    request = _request(rig.stack.add_task()).model_copy(update={"task_id": None, "run_id": None})
    with pytest.raises(PolicyDeniedError, match="task"):
        rig.gateway.generate(
            request, budget=_job(rig.stack.project_id), principal_id="agent:backend_developer:x"
        )
    assert rig.chat.call_count == 0
