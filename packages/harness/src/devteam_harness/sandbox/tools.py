"""Execute tools (HC-06 + HC-14; Part E plan §3.4): ``request_build``, ``request_test_run``,
``request_migration_check``.

An agent can only name a recipe from the tool's own allowlist and its validated arguments; it never
supplies a command. The tool copies the task's ``generated/`` tree (regular files only, no ``node_modules``,
no secrets files, size-capped) into a fresh container through :class:`SandboxService`. The central policy
grants these tools per role (``config/guardrails/policies.yaml``, ceilings in ``guardrails.config``); like
every build-stage tool they require an approved, frozen specification. **No agent declares these tools in
Part E** (agent specs are unchanged; D-47): they are reachable through the Tool Layer only.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from devteam_core.contracts.sandbox import TestCounts
from devteam_core.contracts.statuses import AgentRole
from devteam_core.contracts.tools import SideEffect
from devteam_harness.errors import PolicyDeniedError
from devteam_harness.guardrails.policy import Access
from devteam_harness.sandbox.errors import SandboxBlockedError
from devteam_harness.sandbox.service import Evidence, ExecutionOwner, SandboxService
from devteam_harness.tools.layer import ToolInvocation
from devteam_harness.tools.registry import ToolSpec

SKIPPED_DIRS = frozenset(
    {"node_modules", ".git", "__pycache__", ".venv", "dist", ".pytest_cache", ".devteam"}
)
MAX_FILES = 5_000
TOOL_TIMEOUT_S = 1_900.0  # lease wait (up to 900 s) + the longest wall clock (900 s on `standard`)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BuildArgs(_Strict):
    recipe: Literal["ruff_check", "bandit_scan", "static_validation", "tsc_check", "npm_build"]


class TestRunArgs(_Strict):
    __test__ = False  # not a pytest class
    recipe: Literal["pytest_api", "pytest_db", "vitest_run"]
    select: Literal["all", "api", "smoke"] = "all"


class MigrationArgs(_Strict):
    recipe: Literal["alembic_upgrade", "alembic_autogenerate"]
    engine: Literal["sqlite", "postgresql"] = "sqlite"
    message: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,40}$")


class ExecutionView(_Strict):
    """What the agent gets back: facts about one run. Never "verified"; tests are counts from the report."""

    execution_id: str
    recipe: str
    status: str
    exit_code: int | None
    duration_ms: int
    peak_memory_mb: float | None
    tests: TestCounts | None
    coverage_percent: float | None
    reason: str | None
    evidence: tuple[str, ...]


def collect_generated(workspace: Path, max_bytes: int) -> dict[str, bytes]:
    """Regular files under ``<workspace>/generated`` (relative to it), skipping links and build output."""
    root = workspace / "generated"
    files: dict[str, bytes] = {}
    total = 0
    if not root.is_dir():
        return files
    for current, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED_DIRS and not Path(current, d).is_symlink())
        for name in sorted(names):
            path = Path(current, name)
            if path.is_symlink() or not path.is_file():
                continue
            if name.startswith(".env") and name not in {".env", ".env.example"}:
                continue  # only the rendered placeholder file and the example
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            total += len(data)
            if total > max_bytes or len(files) >= MAX_FILES:
                raise PolicyDeniedError(
                    "sandbox.workspace_too_large", "the generated tree exceeds the copy-in cap"
                )
            files[relative] = data
    return files


def _handler(service: SandboxService) -> object:
    def run(inv: ToolInvocation) -> ExecutionView:
        args = inv.args
        recipe_args: dict[str, object] = {}
        if isinstance(args, TestRunArgs) and args.recipe == "pytest_api":
            recipe_args["select"] = args.select
        if isinstance(args, MigrationArgs):
            if args.engine == "postgresql":
                raise PolicyDeniedError(
                    "sandbox.postgresql_not_available", "PostgreSQL validation arrives in Part K (D-16)"
                )
            if args.recipe == "alembic_autogenerate":
                recipe_args["message"] = args.message or "generated_change"
        ctx = inv.ctx
        workspace = ctx.files.root
        outcome = service.execute(
            recipe=args.recipe,
            args=recipe_args,
            files=collect_generated(workspace, 512 * 1024 * 1024),
            owner=ExecutionOwner(project_id=ctx.task.project_id, run_id=ctx.task.run_id),
            principal_id=ctx.principal.id,
            evidence=Evidence(root=workspace, prefix=f"runs/{ctx.task.run_id}/sandbox"),
            cancel=_Cancel(inv),
        )
        execution = outcome.execution
        return ExecutionView(
            execution_id=execution.execution_id,
            recipe=execution.recipe,
            status=execution.status.value,
            exit_code=execution.exit_code,
            duration_ms=execution.duration_ms,
            peak_memory_mb=execution.peak_memory_mb,
            tests=execution.tests,
            coverage_percent=outcome.coverage_percent,
            reason=execution.reason,
            evidence=execution.outputs,
        )

    return run


class _Cancel:
    """Adapts the Tool Layer's cancel callback to the runner's ``is_set()`` check."""

    def __init__(self, inv: ToolInvocation) -> None:
        self._cancelled = inv.cancelled

    def is_set(self) -> bool:
        return self._cancelled()


_BUILD_ROLES = frozenset(
    {AgentRole.BACKEND_DEVELOPER, AgentRole.FRONTEND_DEVELOPER, AgentRole.DEBUGGER_REVIEWER}
)
_TEST_ROLES = frozenset({AgentRole.BACKEND_DEVELOPER, AgentRole.TESTER, AgentRole.DEBUGGER_REVIEWER})
_MIGRATION_ROLES = frozenset({AgentRole.DATABASE_DEVELOPER})


def _blocked(_inv: ToolInvocation) -> ExecutionView:
    raise SandboxBlockedError("sandbox.not_configured", "the sandbox is not configured in this process")


def blocked_tool_specs() -> list[ToolSpec]:
    """The same three tools, refusing every call as Blocked (no sandbox service wired in)."""
    return _specs(_blocked)


def sandbox_tool_specs(service: SandboxService) -> list[ToolSpec]:
    return _specs(_handler(service))


def _specs(handler: object) -> list[ToolSpec]:
    common = {
        "version": 1,
        "output_model": ExecutionView,
        "side_effect": SideEffect.EXECUTE,
        "access": Access.NONE,
        "path_arg": None,
        "timeout_s": TOOL_TIMEOUT_S,
        "handler": handler,
    }
    return [
        ToolSpec(name="request_build", input_model=BuildArgs, allowed_roles=_BUILD_ROLES, **common),  # type: ignore[arg-type]
        ToolSpec(name="request_test_run", input_model=TestRunArgs, allowed_roles=_TEST_ROLES, **common),  # type: ignore[arg-type]
        ToolSpec(
            name="request_migration_check",
            input_model=MigrationArgs,
            allowed_roles=_MIGRATION_ROLES,
            **common,  # type: ignore[arg-type]
        ),
    ]


__all__ = [
    "BuildArgs",
    "ExecutionView",
    "MigrationArgs",
    "TestRunArgs",
    "blocked_tool_specs",
    "collect_generated",
    "sandbox_tool_specs",
]
