"""A complete C.4 harness stack for tests: real SQLite state DB (migrated), real config files, the ten real
AgentSpecs, real policy engine / tool layer / guards / budget manager / approvals / tracer, and the scripted
fake model provider. Only the agent *behaviour* is a test stand-in (it runs under a real agent's spec;
it is not an eleventh agent).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.contracts.agents import AGENT_IO, AgentIO, BuildTaskInput
from devteam_core.contracts.statuses import AgentRole, Stage, TaskStatus
from devteam_core.contracts.tasks import AgentTask
from devteam_core.ids import new_id
from devteam_harness.agent_sdk import AgentContext
from devteam_harness.agent_sdk.registry import AgentRegistry
from devteam_harness.agent_sdk.spec import AgentSpec
from devteam_harness.audit import DatabaseAuditSink
from devteam_harness.budgets import BudgetManager, Budgets, load_budgets, parse_budgets
from devteam_harness.executor import AgentExecutor, ExecutionResult, HarnessServices, TaskRun
from devteam_harness.guardrails import PolicyEngine, load_guardrails
from devteam_harness.guardrails.config import Guardrails
from devteam_harness.llm_gateway import LLMGateway
from devteam_harness.llm_gateway.providers.fake import FakeProvider, Step
from devteam_harness.observability import Tracer
from devteam_harness.prompts import InMemoryPromptRegistry, InMemorySkillRegistry, PromptTemplate, SkillSpec
from devteam_harness.resilience import RetryPolicy
from devteam_harness.tools import ToolLayer, build_default_registry
from devteam_harness.tools.files import ControlledFileService, FileLimits
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.machines import transition_task
from devteam_state.models import AuditEvent, Principal, Project, TraceSpanRow
from devteam_state.repositories import runs

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_CONFIG = REPO_ROOT / "config"
AGENT_PACKAGES = (
    "brd_analyst",
    "requirements_finalizer",
    "head",
    "architect",
    "database_developer",
    "backend_developer",
    "frontend_developer",
    "tester",
    "debugger_reviewer",
    "documenter",
)
LIMITS = FileLimits(max_write_bytes=64 * 1024, max_read_bytes=64 * 1024, max_list_entries=100)


def real_specs() -> list[AgentSpec]:
    return [importlib.import_module(f"devteam_agents.{name}.spec").SPEC for name in AGENT_PACKAGES]


def sealed_registry(guardrails: Guardrails | None = None) -> AgentRegistry:
    registry = AgentRegistry()
    for spec in real_specs():
        registry.register(spec)
    return registry.seal(guardrails)


class Behaviour:
    """A stand-in behaviour: wraps a function ``(ctx, given) -> raw output``."""

    def __init__(self, fn: Callable[[AgentContext, AgentIO], Any]) -> None:
        self.fn = fn
        self.calls = 0

    def run(self, ctx: AgentContext, given: AgentIO) -> Any:
        self.calls += 1
        return self.fn(ctx, given)


class Monotonic:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@dataclass
class Stack:
    sessions: sessionmaker[Session]
    project_id: str
    owner_id: str
    run_id: str
    spec_version_id: str
    workspace: Path
    files: ControlledFileService
    provider: FakeProvider
    guardrails: Guardrails
    budgets: BudgetManager
    tracer: Tracer
    executor: AgentExecutor
    behaviours: dict[AgentRole, Any]
    skills: list[SkillSpec]
    memory_mb: list[int] = field(default_factory=lambda: [64_000])
    clock: Monotonic = field(default_factory=Monotonic)
    sleeps: list[float] = field(default_factory=list)

    # ------------------------------------------------------------------------------------- tasks

    def add_task(
        self,
        role: AgentRole = AgentRole.BACKEND_DEVELOPER,
        *,
        operation: str = "generate_code",
        permitted: tuple[str, ...] = ("generated/backend/",),
        max_attempts: int = 2,
        budget_profile: str = "default",
        ready: bool = True,
        run_id: str | None = None,
    ) -> AgentTask:
        task = AgentTask(
            task_id=new_id("tsk"),
            project_id=self.project_id,
            run_id=run_id or self.run_id,
            agent_type=role,
            operation_id=f"{role.value}.{operation}",
            objective="Implement the order endpoint.",
            done_criteria=("endpoint exists",),
            permitted_paths=permitted,
            spec_version_id=self.spec_version_id,
            max_attempts=max_attempts,
            budget_profile=budget_profile,
        )
        with self.sessions.begin() as session:
            row = runs.add_task(session, task)
            if ready:
                transition_task(session, row, TaskStatus.READY, actor="service:executor")
        return task

    def given(self, task: AgentTask) -> AgentIO:
        cls = AGENT_IO[task.agent_type][0]
        assert issubclass(cls, BuildTaskInput)
        return cls(
            project_id=task.project_id, run_id=task.run_id, task=task, spec_version_id=self.spec_version_id
        )

    def run(
        self,
        task: AgentTask,
        fn: Callable[[AgentContext, AgentIO], Any] | None = None,
        *,
        stage: Stage = Stage.BUILD,
        spec_approved: bool = True,
        given: AgentIO | None = None,
    ) -> ExecutionResult:
        if fn is not None:
            self.behaviours[task.agent_type] = Behaviour(fn)
        return self.executor.execute(
            TaskRun(
                project_id=self.project_id,
                task_id=task.task_id,
                stage=stage,
                given=given if given is not None else self.given(task),
                files=self.files,
                spec_approved=spec_approved,
            )
        )

    def output(self, task: AgentTask, **extra: Any) -> dict[str, Any]:
        return {"project_id": task.project_id, "run_id": task.run_id, "task_id": task.task_id, **extra}

    # ----------------------------------------------------------------------------------- inspect

    def audit(self, action: str | None = None) -> list[AuditEvent]:
        with self.sessions() as session:
            query = select(AuditEvent).order_by(AuditEvent.seq)
            if action is not None:
                query = query.where(AuditEvent.action == action)
            return list(session.scalars(query))

    def spans(self) -> list[TraceSpanRow]:
        with self.sessions() as session:
            return list(session.scalars(select(TraceSpanRow).order_by(TraceSpanRow.start_ts)))

    def task_row(self, task: AgentTask) -> Any:
        with self.sessions() as session:
            return runs.get_task(session, self.project_id, task.task_id)


def make_stack(
    tmp_path: Path,
    *,
    budgets: Budgets | str | None = None,
    script: tuple[Step, ...] = (),
    provider_delay_s: float = 0.0,
    skills: tuple[SkillSpec, ...] = (),
) -> Stack:
    db = tmp_path / "state.db"
    upgrade(db)
    engine = create_sqlite_engine(db)
    sessions = session_factory(engine)
    now = datetime.now(UTC)
    owner, project = new_id("prn"), new_id("prj")
    with sessions.begin() as session:
        session.add(Principal(id=owner, kind="human", display_name="Owner", created_at=now))
        session.add(
            Project(
                id=project,
                name="Shop",
                name_key="shop",
                description="",
                status="active",
                created_by=owner,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        run = runs.create_run(session, project_id=project, run_kind="initial", stage=Stage.BUILD)
        run_id = run.id
    workspace = tmp_path / "workspaces" / project
    (workspace / "generated" / "backend").mkdir(parents=True)
    (workspace / "generated" / "backend" / "app.py").write_bytes(b"print('hi')\n")

    guardrails = load_guardrails(REPO_CONFIG)
    if isinstance(budgets, str):
        loaded = parse_budgets(budgets.encode("utf-8"))
    else:
        loaded = budgets or load_budgets(REPO_CONFIG)
    sink = DatabaseAuditSink(sessions)
    tool_registry = build_default_registry()
    tool_registry.check_consistency(guardrails)
    tool_layer = ToolLayer(tool_registry, PolicyEngine(guardrails), sink, sessions)
    tracer = Tracer(sessions)
    provider = FakeProvider(script, delay_s=provider_delay_s)
    retry = RetryPolicy(loaded.retries, jitter=lambda: 0.0)
    holder: dict[str, Any] = {}

    def sleep(seconds: float) -> None:
        holder["stack"].sleeps.append(seconds)

    gateway = LLMGateway(provider, _input_guard(guardrails), sink, tracer, retry, sleep=sleep)
    memory = [64_000]
    clock = Monotonic()
    manager = BudgetManager(loaded, sessions, sink, memory_probe=lambda: memory[0], monotonic=clock)
    prompts = InMemoryPromptRegistry()
    for name in AGENT_PACKAGES:
        prompts.register(
            PromptTemplate.create(f"{name}.system", 1, f"You are the {name} agent. Answer in JSON.")
        )
    skill_list = list(skills)
    behaviours: dict[AgentRole, Any] = {}
    services = HarnessServices(
        registry=sealed_registry(guardrails),
        behaviours=behaviours,
        guardrails=guardrails,
        tool_layer=tool_layer,
        gateway=gateway,
        budgets=manager,
        retry=retry,
        prompts=prompts,
        skills=InMemorySkillRegistry(tuple(skill_list)),
        tracer=tracer,
        sink=sink,
        sessions=sessions,
        max_task_write_bytes=1_000_000,
    )
    stack = Stack(
        sessions=sessions,
        project_id=project,
        owner_id=owner,
        run_id=run_id,
        spec_version_id=new_id("spv"),
        workspace=workspace,
        files=ControlledFileService(workspace, LIMITS),
        provider=provider,
        guardrails=guardrails,
        budgets=manager,
        tracer=tracer,
        executor=AgentExecutor(services),
        behaviours=behaviours,
        skills=skill_list,
        memory_mb=memory,
        clock=clock,
    )
    holder["stack"] = stack
    return stack


def _input_guard(guardrails: Guardrails) -> Any:
    from devteam_harness.guardrails.input import InputGuard  # noqa: PLC0415

    return InputGuard(guardrails.input)


def budgets_yaml(
    *, run: dict[str, int] | None = None, retries: dict[str, float] | None = None, **profile: int
) -> str:
    """The repository budgets with selected ``default`` profile, run or retry values replaced."""
    loaded = load_budgets(REPO_CONFIG)
    base = loaded.profiles["default"].model_dump()
    base.update(profile)
    run_values = {**loaded.run.model_dump(), **(run or {})}
    retry_values = {**loaded.retries.model_dump(), **(retries or {})}

    def flow(values: dict[str, Any]) -> str:
        return "{" + ", ".join(f"{k}: {v}" for k, v in values.items()) + "}"

    lines = "\n".join(f"    {k}: {v}" for k, v in base.items())
    return (
        "version: 1\nprofiles:\n  default:\n" + lines + "\n"
        f"run: {flow(run_values)}\n"
        f"resources: {flow(loaded.resources.model_dump())}\n"
        f"retries: {flow(retry_values)}\n"
    )
