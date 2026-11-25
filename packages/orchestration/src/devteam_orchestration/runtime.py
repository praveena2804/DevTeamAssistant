"""The production harness for workflow runs (Part D; D-44 real local models only).

``build_runtime`` assembles every Part C component with the **Ollama** provider. There is no other
provider here and no switch to one: the fake provider is unreachable from orchestration (import-linter
contract "runs never use the fake model provider (D-38)").

It fails closed (``RuntimeConfigError``) when:

* cloud tracing is configured (``ensure_local_only``, D-30);
* a configuration file is missing or invalid (guardrails, budgets, approvals);
* the agent registry does not hold exactly the ten agents, or a spec disagrees with the central policy;
* any of the ten agents has no behaviour.

Whether Ollama is reachable and the configured models are installed is checked by :func:`check_models`
(the worker calls it at start). A run never substitutes anything when the model is missing: the agent
step reports *waiting for resources* or *blocked*.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from devteam_core.clock import Clock, system_clock
from devteam_core.config import Settings
from devteam_core.config.models import CHAT_SLOTS, load_model_catalog
from devteam_core.contracts import CONTRACT_MODELS
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.errors import ConfigError
from devteam_core.paths import find_repo_root
from devteam_harness.agent_sdk import AgentBehaviour
from devteam_harness.agent_sdk.registry import AgentRegistry
from devteam_harness.approvals import ApprovalService, load_approval_rules
from devteam_harness.audit import DatabaseAuditSink
from devteam_harness.budgets import BudgetManager, Budgets, load_budgets
from devteam_harness.budgets.resource_lease import ResourceLease
from devteam_harness.errors import HarnessError
from devteam_harness.executor import AgentExecutor, HarnessServices
from devteam_harness.guardrails import PolicyEngine, load_guardrails
from devteam_harness.guardrails.config import Guardrails
from devteam_harness.guardrails.input import InputGuard
from devteam_harness.llm_gateway import LLMGateway
from devteam_harness.llm_gateway.cache import EmbeddingCache, ResponseCache
from devteam_harness.llm_gateway.gateway import GatewayOptions
from devteam_harness.llm_gateway.limits import RateLimits, load_rate_limits
from devteam_harness.llm_gateway.providers.ollama import OllamaProvider
from devteam_harness.llm_gateway.resilience import CircuitBreakers
from devteam_harness.llm_gateway.router import Router
from devteam_harness.observability import Tracer
from devteam_harness.resilience import RetryPolicy
from devteam_harness.sandbox.service import SandboxService
from devteam_harness.secrets import secrets_manager_from_settings
from devteam_harness.tools import ToolLayer, build_default_registry
from devteam_harness.tools.files import ControlledFileService, FileLimits
from devteam_orchestration.prompts import build_prompt_registry
from devteam_orchestration.safety import ensure_local_only

PROVIDER_GRACE_S = 15  # the HTTP timeout outlives the gateway's F-9d timeout slightly


class RuntimeConfigError(ConfigError):
    code = "orchestration_runtime"


def _schema(name: str) -> dict[str, Any] | None:
    model = CONTRACT_MODELS.get(name)
    return model.model_json_schema(mode="validation") if model is not None else None


def _agents() -> tuple[AgentRegistry, dict[AgentRole, AgentBehaviour]]:
    registry = AgentRegistry()
    behaviours: dict[AgentRole, AgentBehaviour] = {}
    for role in AgentRole:
        package: Any = importlib.import_module(f"devteam_agents.{role.value}")
        registry.register(package.SPEC)
        behaviour = getattr(package, "BEHAVIOUR", None)
        if behaviour is None:
            raise RuntimeConfigError(f"agent {role.value} has no behaviour")
        behaviours[role] = behaviour
    return registry, behaviours


@dataclass(frozen=True)
class Runtime:
    settings: Settings
    sessions: sessionmaker[Session]
    executor: AgentExecutor
    registry: AgentRegistry
    approvals: ApprovalService
    tracer: Tracer
    sink: DatabaseAuditSink
    provider: OllamaProvider
    guardrails: Guardrails
    budgets: Budgets
    file_limits: FileLimits
    clock: Clock = system_clock
    write_scopes: MappingProxyType[AgentRole, tuple[str, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )
    sandbox: SandboxService | None = None  # Part E: reached through tools and verification jobs only
    router: Router | None = None  # Part F: model routing (config/models.yaml)
    gateway: LLMGateway | None = None  # Part F: the one door to models, also for service jobs (RAG)

    def workspace(self, project_id: str) -> Path:
        root = self.settings.workspaces_dir.resolve()
        path = (root / project_id).resolve()
        if path.parent != root:
            raise RuntimeConfigError("project workspace escapes the workspaces root")
        return path

    def files_for(self, project_id: str) -> ControlledFileService:
        return ControlledFileService(self.workspace(project_id), self.file_limits)

    def close(self) -> None:
        self.provider.close()


def build_runtime(
    settings: Settings,
    sessions: sessionmaker[Session],
    *,
    config_dir: Path | None = None,
    clock: Clock = system_clock,
) -> Runtime:
    ensure_local_only()
    config = config_dir or (find_repo_root() / "config")
    guardrails = load_guardrails(config)
    budgets = load_budgets(config)
    approvals = ApprovalService(load_approval_rules(config), sessions, clock=clock)
    sink = DatabaseAuditSink(sessions)
    tracer = Tracer(sessions)
    # Part F: model catalogue and router (config/models.yaml; MODEL_ROUTING.md); start-up rules 1-9.
    try:
        catalog = load_model_catalog(config, max_model_ram_mb=settings.resources.max_model_ram_mb)
    except ConfigError as exc:
        raise RuntimeConfigError(str(exc)) from None
    router = Router(catalog)
    timeout = min(
        settings.ollama.request_timeout_s,
        max(p.model_call_timeout_s for p in budgets.profiles.values()) + PROVIDER_GRACE_S,
    )
    slot_models: dict[ModelSlot, str] = {s: m for s in CHAT_SLOTS if (m := catalog.slot_model(s)) is not None}
    provider = OllamaProvider(
        settings.ollama.base_url,
        slot_models,
        timeout_s=timeout,
        keep_alive=settings.ollama.keep_alive,
        schemas=_schema,
    )
    # Part E: the sandbox (HC-14) and the cross-process LLM/sandbox lease (D-46) shared with the gateway.
    sandbox = SandboxService(
        settings, sessions, secrets_manager_from_settings(settings), models=provider, clock=clock
    )
    tools = build_default_registry(sandbox)
    tools.check_consistency(guardrails)
    tool_layer = ToolLayer(tools, PolicyEngine(guardrails), sink, sessions)
    lease = ResourceLease(
        sessions, ttl_s=settings.sandbox.lock_ttl_s, wait_s=settings.sandbox.lock_wait_s, clock=clock
    )
    retry = RetryPolicy(budgets.retries)
    registry, behaviours = _agents()
    try:
        registry.seal(guardrails)
        router.check_operations({spec.role: [op.operation_id for op in spec.operations] for spec in registry})
        rate_limits = RateLimits(load_rate_limits(config))
        prompts, skills = build_prompt_registry(config)  # Part F: files verified against prompts.yaml
        prompts.check_agents({spec.prompt_id: spec.prompt_version for spec in registry})
    except ConfigError as exc:
        raise RuntimeConfigError(str(exc)) from None
    llm = settings.llm
    gateway = LLMGateway(
        provider,
        InputGuard(guardrails.input),
        sink,
        tracer,
        retry,
        clock=clock,
        resource_lease=lease,
        router=router,
        sessions=sessions,
        rate_limits=rate_limits,
        breakers=CircuitBreakers(threshold=llm.breaker_threshold, cooldown_s=llm.breaker_cooldown_s),
        response_cache=ResponseCache(sessions, clock=clock),
        embedding_cache=EmbeddingCache(sessions, clock=clock),
        options=GatewayOptions(
            response_cache=llm.response_cache,
            cancel_wait_s=llm.cancel_wait_s,
            unload_verify_s=llm.unload_verify_s,
            digest_refresh_s=llm.digest_refresh_s,
            ram_margin_mb=catalog.ram_margin_mb,
        ),
    )
    services = HarnessServices(
        registry=registry,
        behaviours=MappingProxyType(behaviours),
        guardrails=guardrails,
        tool_layer=tool_layer,
        gateway=gateway,
        budgets=BudgetManager(budgets, sessions, sink),
        retry=retry,
        prompts=prompts,
        skills=skills,
        tracer=tracer,
        sink=sink,
        sessions=sessions,
        max_task_write_bytes=settings.files.max_task_write_bytes,
        clock=clock,
    )
    file_limits = FileLimits(
        max_write_bytes=settings.files.max_write_bytes,
        max_read_bytes=settings.files.max_read_bytes,
        max_list_entries=settings.files.max_list_entries,
    )
    return Runtime(
        settings=settings,
        sessions=sessions,
        executor=AgentExecutor(services),
        registry=registry,
        approvals=approvals,
        tracer=tracer,
        sink=sink,
        provider=provider,
        guardrails=guardrails,
        budgets=budgets,
        file_limits=file_limits,
        clock=clock,
        write_scopes=MappingProxyType({spec.role: tuple(spec.write_paths) for spec in registry}),
        sandbox=sandbox,
        router=router,
        gateway=gateway,
    )


@dataclass(frozen=True)
class ModelCheck:
    ok: bool
    version: str | None
    missing: tuple[str, ...]
    problem: str | None = None


def check_models(runtime: Runtime) -> ModelCheck:
    """Is the local Ollama reachable, and are the configured models installed? Never pulls a model."""
    try:
        version = runtime.provider.version()
        wanted = runtime.router.all_models() if runtime.router is not None else None
        missing = runtime.provider.missing_models(wanted)
    except HarnessError as exc:
        return ModelCheck(ok=False, version=None, missing=(), problem=exc.message)
    if missing:
        return ModelCheck(ok=False, version=version, missing=missing, problem="configured models missing")
    return ModelCheck(ok=True, version=version, missing=())
