"""Part C demonstration (plan §8): ``uv run python scripts/demo_part_c.py``.

Everything happens in a new temporary folder; your real data, keys and settings are not touched, and no
AI model is used (the fake provider answers). A stand-in behaviour runs under the real Backend Developer
declaration (it is not an eleventh agent) and tries allowed and forbidden things. The script prints, in
plain words, what was allowed, refused and logged, then checks the activity log, edits one entry in the
temporary copy, and shows that the check finds it.
"""

from __future__ import annotations

import importlib
import json
import logging
import sqlite3
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from devteam_core.contracts.agents import BackendDeveloperInput
from devteam_core.contracts.statuses import AgentRole, Stage, TaskStatus
from devteam_core.contracts.tasks import AgentTask
from devteam_core.ids import new_id
from devteam_harness.agent_sdk import AgentContext, DataItem
from devteam_harness.agent_sdk.registry import AgentRegistry
from devteam_harness.audit import DatabaseAuditSink
from devteam_harness.budgets import BudgetManager, load_budgets
from devteam_harness.errors import PolicyDeniedError
from devteam_harness.executor import AgentExecutor, ExecutionResult, HarnessServices, TaskRun
from devteam_harness.guardrails import PolicyEngine, load_guardrails
from devteam_harness.guardrails.input import InputGuard
from devteam_harness.llm_gateway import LLMGateway
from devteam_harness.llm_gateway.providers.fake import FakeProvider
from devteam_harness.observability import Tracer
from devteam_harness.prompts import InMemoryPromptRegistry, InMemorySkillRegistry, PromptTemplate
from devteam_harness.resilience import RetryPolicy
from devteam_harness.tools import ToolLayer, build_default_registry
from devteam_harness.tools.files import ControlledFileService, FileLimits
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.machines import transition_task
from devteam_state.models import AuditEvent, Principal, Project
from devteam_state.repositories import runs
from devteam_state.repositories.audit import verify_chain

ROOT = Path(__file__).resolve().parents[1]
AGENTS = (
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


class _Stand_in:  # noqa: N801 - reads as a label in the output
    def __init__(self, fn: Callable[[AgentContext, Any], Any]) -> None:
        self.fn = fn

    def run(self, ctx: AgentContext, given: Any) -> Any:
        return self.fn(ctx, given)


def _say(step: str, outcome: str, detail: str = "") -> None:
    print(f"  {step:<58} -> {outcome}{f'  ({detail})' if detail else ''}")


def main() -> int:  # noqa: PLR0915 - a linear demonstration
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))
    with tempfile.TemporaryDirectory(prefix="devteam-demo-") as tmp:
        base = Path(tmp)
        db = base / "state.db"
        upgrade(db)
        engine = create_sqlite_engine(db)
        sessions = session_factory(engine)
        now = datetime.now(UTC)
        owner, project = new_id("prn"), new_id("prj")
        with sessions.begin() as s:
            s.add(Principal(id=owner, kind="human", display_name="Demo", created_at=now))
            s.add(
                Project(
                    id=project,
                    name="Demo",
                    name_key="demo",
                    description="",
                    status="active",
                    created_by=owner,
                    created_at=now,
                    updated_at=now,
                )
            )
            s.flush()
            run_id = runs.create_run(s, project_id=project, run_kind="initial", stage=Stage.BUILD).id
        workspace = base / "workspaces" / project
        (workspace / "generated" / "backend").mkdir(parents=True)
        (workspace / "generated" / "backend" / "app.py").write_text("print('hello')\n", encoding="utf-8")

        config = ROOT / "config"
        guardrails = load_guardrails(config)
        budgets = load_budgets(config)
        sink = DatabaseAuditSink(sessions)
        registry = AgentRegistry()
        for name in AGENTS:
            registry.register(importlib.import_module(f"devteam_agents.{name}.spec").SPEC)
        registry.seal(guardrails)
        tools = build_default_registry()
        tools.check_consistency(guardrails)
        tracer = Tracer(sessions)
        retry = RetryPolicy(budgets.retries, jitter=lambda: 0.0)
        provider = FakeProvider(['{"files": []}'])
        prompts = InMemoryPromptRegistry()
        for name in AGENTS:
            prompts.register(PromptTemplate.create(f"{name}.system", 1, f"You are the {name} agent."))
        behaviours: dict[AgentRole, Any] = {}
        executor = AgentExecutor(
            HarnessServices(
                registry=registry,
                behaviours=behaviours,
                guardrails=guardrails,
                tool_layer=ToolLayer(tools, PolicyEngine(guardrails), sink, sessions),
                gateway=LLMGateway(
                    provider, InputGuard(guardrails.input), sink, tracer, retry, sleep=lambda s: None
                ),
                budgets=BudgetManager(budgets, sessions, sink),
                retry=retry,
                prompts=prompts,
                skills=InMemorySkillRegistry(),
                tracer=tracer,
                sink=sink,
                sessions=sessions,
                max_task_write_bytes=10 * 1024 * 1024,
            )
        )
        files = ControlledFileService(workspace, FileLimits(1024 * 1024, 1024 * 1024, 2000))
        spec_version = new_id("spv")

        def run(fn: Callable[[AgentContext, Any], Any], max_attempts: int = 2) -> ExecutionResult:
            task = AgentTask(
                task_id=new_id("tsk"),
                project_id=project,
                run_id=run_id,
                agent_type=AgentRole.BACKEND_DEVELOPER,
                operation_id="backend_developer.generate_code",
                objective="Demonstration task.",
                done_criteria=("shown",),
                permitted_paths=("generated/backend/",),
                spec_version_id=spec_version,
                max_attempts=max_attempts,
            )
            with sessions.begin() as s:
                transition_task(s, runs.add_task(s, task), TaskStatus.READY, actor="service:executor")
            behaviours[AgentRole.BACKEND_DEVELOPER] = _Stand_in(fn)
            given = BackendDeveloperInput(
                project_id=project, run_id=run_id, task=task, spec_version_id=spec_version
            )
            return executor.execute(
                TaskRun(
                    project_id=project,
                    task_id=task.task_id,
                    stage=Stage.BUILD,
                    given=given,
                    files=files,
                    spec_approved=True,
                )
            )

        def ok(ctx: AgentContext) -> dict[str, Any]:
            return {"project_id": project, "run_id": run_id, "task_id": ctx.task_id}

        print("\nDevTeam AI - Part C demonstration (temporary folder, fake AI, your data untouched)\n")

        def allowed(ctx: AgentContext, given: Any) -> Any:
            ctx.tools.call("read_project_file", path="generated/backend/app.py")
            ctx.llm.generate("generate_code", DataItem("brd_chunk", "Orders ship in two days."))
            return ok(ctx)

        r = run(allowed)
        _say(
            "1. Read an allowed file and ask the (fake) AI",
            "ALLOWED" if r.error is None else "?",
            r.status.value,
        )

        def escape(ctx: AgentContext, given: Any) -> Any:
            ctx.tools.call("write_project_file", path="..\\..\\Windows\\win.ini", content="x")
            return ok(ctx)

        r = run(escape)
        _say("2. Write to ..\\..\\Windows\\win.ini", "REFUSED, LOGGED", r.error.code if r.error else "?")

        def no_tool(ctx: AgentContext, given: Any) -> Any:
            try:  # noqa: SIM105 - spelled out: the stand-in tries to swallow the refusal
                ctx.tools.call("run_shell", command="del C:\\")
            except PolicyDeniedError:
                pass  # trying to hide the refusal does not help
            return ok(ctx)

        r = run(no_tool)
        _say(
            "3. Use a tool it does not have (a shell), and hide it",
            "REFUSED, LOGGED",
            r.error.code if r.error else "?",
        )

        def bad_answers(ctx: AgentContext, given: Any) -> Any:
            return "this is not the required form"

        r = run(bad_answers)
        _say(
            "4. Keep giving answers in the wrong form",
            "STOPPED",
            f"after {r.output_attempts} attempts: {r.disposition.value}",
        )

        with sessions() as s:
            report = verify_chain(s)
            denials = s.query(AuditEvent).filter(AuditEvent.outcome == "denied").count()
            target = s.query(AuditEvent).filter(AuditEvent.action == "policy.denied").first()
        _say(
            "5. Check the activity log",
            "INTACT" if report.ok else "BROKEN",
            f"{report.checked} entries, {denials} refusals recorded",
        )

        tracer.shutdown()
        engine.dispose()
        if target is None or target.seq is None:
            return 1
        con = sqlite3.connect(db)  # someone edits the database file directly
        try:
            con.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
            details = dict(target.details)
            details["rule_id"] = "allow.everything"
            con.execute("UPDATE audit_events SET details = ? WHERE id = ?", (json.dumps(details), target.id))
            con.commit()
        finally:
            con.close()
        engine = create_sqlite_engine(db)
        with session_factory(engine)() as s:
            report = verify_chain(s)
        engine.dispose()
        _say(
            f"6. Edit log entry {target.seq} in the temporary copy, check again",
            "BROKEN" if not report.ok else "NOT DETECTED",
            f"at entry {report.broken_at}",
        )
        print("\nThe temporary folder is now deleted.\n")
        return 0 if (not report.ok and report.broken_at == target.seq) else 1


if __name__ == "__main__":
    raise SystemExit(main())
