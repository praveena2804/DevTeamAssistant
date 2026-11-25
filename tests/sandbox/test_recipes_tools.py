"""E.3: the ten recipes, the Migration Runner, the Test Runner base and the three execute tools, run on the
real engine against the starter-kit tree (no mocks). Includes the honesty checks: a failing test is
reported as failed, a test report cannot be faked into a pass, and refused requests run nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.config import Settings
from devteam_core.contracts.errors import ErrorCategory
from devteam_core.contracts.sandbox import SandboxExecutionStatus
from devteam_core.contracts.statuses import AgentRole, ModelSlot, Stage
from devteam_core.contracts.tasks import AgentTask
from devteam_core.contracts.tools import ToolCall, ToolOutcome
from devteam_core.ids import new_id
from devteam_harness.audit import DatabaseAuditSink
from devteam_harness.errors import PolicyDeniedError
from devteam_harness.guardrails import PolicyEngine, load_guardrails
from devteam_harness.iam.principals import issue_agent_principal
from devteam_harness.llm_gateway.providers.ollama import OllamaProvider
from devteam_harness.sandbox.service import Evidence, ExecutionOwner, SandboxService
from devteam_harness.secrets import SecretsManager
from devteam_harness.tools import ToolContext, ToolLayer, build_default_registry
from devteam_harness.tools.files import ControlledFileService, FileLimits
from devteam_services.integration_builder import template_files
from devteam_services.migration_runner import MigrationRunner
from devteam_services.test_runner import TestRunner
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.models import AuditEvent, Principal, Project
from devteam_state.repositories import sandbox as repo

pytestmark = [pytest.mark.docker, pytest.mark.security]

TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / "target_apps"
REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"


def tree(template_id: str) -> dict[str, bytes]:
    """The starter kit as the Integration/Build Service lays it out in generated/."""
    return template_files(template_id)


@pytest.fixture(scope="module")
def sessions(tmp_path_factory: pytest.TempPathFactory) -> Iterator[sessionmaker[Session]]:
    db = tmp_path_factory.mktemp("state") / "state.db"
    upgrade(db)
    engine = create_sqlite_engine(db)
    yield session_factory(engine)
    engine.dispose()


@pytest.fixture(scope="module")
def project_id(sessions: sessionmaker[Session]) -> str:
    now, owner, project = datetime.now(UTC), new_id("prn"), new_id("prj")
    with sessions.begin() as s:
        s.add(Principal(id=owner, kind="human", display_name="Owner", created_at=now))
        s.add(
            Project(
                id=project,
                name="P",
                name_key="p",
                description="",
                status="active",
                created_by=owner,
                created_at=now,
                updated_at=now,
            )
        )
    return project


@pytest.fixture(scope="module")
def service(
    live_settings: Settings,
    live_secrets: SecretsManager,
    sessions: sessionmaker[Session],
    wsl_keepalive: None,
) -> Iterator[SandboxService]:
    provider = OllamaProvider(
        live_settings.ollama.base_url,
        {ModelSlot.INSTRUCT: "qwen2.5:3b"},  # only used to unload models (Resource Guard)
        timeout_s=60,
    )
    yield SandboxService(live_settings, sessions, live_secrets, models=provider)
    provider.close()


def _run(
    service: SandboxService,
    recipe: str,
    files: dict[str, bytes],
    project_id: str,
    tmp: Path,
    args: dict[str, Any] | None = None,
) -> Any:
    return service.execute(
        recipe=recipe,
        args=args,
        files=files,
        owner=ExecutionOwner(project_id=project_id),
        principal_id="service:test_runner",
        evidence=Evidence(root=tmp, prefix="runs/r1/sandbox"),
    )


# -- every recipe on the starter-kit tree --------------------------------------------------------------


@pytest.mark.parametrize(
    ("recipe", "frontend", "tests"),
    [
        ("pytest_api", "html_fastapi_sqlite", 3),
        ("pytest_db", "html_fastapi_sqlite", 2),
        ("vitest_run", "react_fastapi_sqlite", 3),
        ("ruff_check", "html_fastapi_sqlite", None),
        ("bandit_scan", "html_fastapi_sqlite", None),
        ("static_validation", "html_fastapi_sqlite", None),
        ("tsc_check", "react_fastapi_sqlite", None),
        ("npm_build", "react_fastapi_sqlite", None),
        ("alembic_upgrade", "html_fastapi_sqlite", None),
    ],
)
def test_recipe_completes_offline_on_the_starter_kit(
    service: SandboxService,
    project_id: str,
    tmp_path: Path,
    sessions: sessionmaker[Session],
    recipe: str,
    frontend: str,
    tests: int | None,
) -> None:
    outcome = _run(service, recipe, tree(frontend), project_id, tmp_path)
    execution = outcome.execution
    stdout = (tmp_path / execution.outputs[0]).read_text(encoding="utf-8", errors="replace")
    stderr = (tmp_path / execution.outputs[1]).read_text(encoding="utf-8", errors="replace")
    assert execution.status is SandboxExecutionStatus.COMPLETED, (
        execution.reason,
        stdout[-2000:],
        stderr[-2000:],
    )
    if tests is not None:
        assert execution.tests is not None and execution.tests.passed == tests and execution.tests.clean
    else:
        assert execution.tests is None
    with sessions() as s:
        stored = repo.executions_for_project(s, project_id)
        assert any(e.execution_id == execution.execution_id for e in stored)
        audited = s.scalars(select(AuditEvent).where(AuditEvent.target_id == execution.execution_id)).one()
        assert audited.action == "sandbox.executed" and audited.outcome == "success"
        assert "stdout" not in json.dumps(audited.details)  # metadata only


def test_autogenerate_proposes_an_empty_revision_for_an_unchanged_schema(
    service: SandboxService, project_id: str, tmp_path: Path
) -> None:
    check = MigrationRunner(service).check(
        {"recipe": "alembic_autogenerate", "message": "no_change"},
        files=tree("html_fastapi_sqlite"),
        owner=ExecutionOwner(project_id=project_id),
        evidence=Evidence(tmp_path, "runs/r1/sandbox"),
    )
    assert check.passed and check.report["empty"] is True
    assert "proposed_revision.py" in check.outcome.outputs


def test_migration_roundtrip_detects_a_broken_downgrade(
    service: SandboxService, project_id: str, tmp_path: Path
) -> None:
    files = tree("html_fastapi_sqlite")
    broken = files["backend/alembic/versions/0001_baseline.py"].replace(
        b'op.drop_table("app_info")', b"pass  # forgot to drop the table"
    )
    files["backend/alembic/versions/0001_baseline.py"] = broken
    check = MigrationRunner(service).check(
        {"recipe": "alembic_upgrade"},
        files=files,
        owner=ExecutionOwner(project_id=project_id),
        evidence=Evidence(tmp_path, "runs/r1/sandbox"),
    )
    assert not check.passed
    assert check.outcome.execution.status is SandboxExecutionStatus.FAILED
    assert "tables left after downgrade" in check.report["reason"]


def test_postgresql_is_refused_until_part_k(service: SandboxService, project_id: str, tmp_path: Path) -> None:
    with pytest.raises(PolicyDeniedError) as info:
        MigrationRunner(service).check(
            {"recipe": "alembic_upgrade", "engine": "postgresql"},
            files={},
            owner=ExecutionOwner(project_id=project_id),
            evidence=Evidence(tmp_path, "x"),
        )
    assert info.value.code == "sandbox.postgresql_not_available"


# -- honesty: failing tests, fake reports ----------------------------------------------------------------


def test_a_failing_test_is_reported_as_failed(
    service: SandboxService, project_id: str, tmp_path: Path
) -> None:
    files = tree("html_fastapi_sqlite")
    files["tests/backend/test_broken.py"] = (
        b"import pytest\n\n@pytest.mark.api\ndef test_broken():\n    assert 1 == 2\n"
    )
    result = TestRunner(service).run(
        "pytest_api",
        files=files,
        owner=ExecutionOwner(project_id=project_id),
        evidence=Evidence(tmp_path, "runs/r1/sandbox"),
    )
    execution = result.outcome.execution
    assert execution.status is SandboxExecutionStatus.FAILED and execution.exit_code == 1
    assert execution.tests is not None and execution.tests.failed == 1 and execution.tests.passed == 3
    assert not result.passed


def test_no_tests_is_never_a_pass(service: SandboxService, project_id: str, tmp_path: Path) -> None:
    files = {k: v for k, v in tree("html_fastapi_sqlite").items() if not k.startswith("tests/backend/test_")}
    result = TestRunner(service).run(
        "pytest_api",
        files=files,
        owner=ExecutionOwner(project_id=project_id),
        evidence=Evidence(tmp_path, "runs/r1/sandbox"),
    )
    assert not result.passed  # pytest exits 5 ("no tests collected"); counts are zero


def test_a_forged_report_with_an_entity_declaration_is_rejected(
    service: SandboxService, project_id: str, tmp_path: Path
) -> None:
    forged = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
        b'<testsuite tests="99"><testcase name="t">&a;</testcase></testsuite>'
    )
    files = tree("html_fastapi_sqlite")
    files["tests/backend/conftest.py"] += (
        b"\n\ndef pytest_sessionfinish(session, exitstatus):\n"
        b"    import pathlib\n"
        b"    pathlib.Path('/work/.devteam/out/junit.xml').write_bytes(" + repr(forged).encode() + b")\n"
    )
    result = TestRunner(service).run(
        "pytest_api",
        files=files,
        owner=ExecutionOwner(project_id=project_id),
        evidence=Evidence(tmp_path, "runs/r1/sandbox"),
    )
    assert result.outcome.execution.tests is None
    assert "test report rejected" in (result.outcome.execution.reason or "")
    assert not result.passed


def test_requirement_markers_reach_the_report(
    service: SandboxService, project_id: str, tmp_path: Path
) -> None:
    files = tree("html_fastapi_sqlite")
    files["tests/backend/test_req.py"] = (
        b"import pytest\n\n@pytest.mark.api\n@pytest.mark.req('FR-001', 'BR-002')\n"
        b"def test_mapped():\n    assert True\n"
    )
    outcome = (
        TestRunner(service)
        .run(
            "pytest_api",
            files=files,
            owner=ExecutionOwner(project_id=project_id),
            evidence=Evidence(tmp_path, "runs/r1/sandbox"),
        )
        .outcome
    )
    assert outcome.report is not None
    mapped = [c for c in outcome.report.cases if c.name.endswith("test_mapped")]
    assert mapped and mapped[0].requirements == ("FR-001", "BR-002")


def test_a_malformed_requirement_marker_fails_the_run(
    service: SandboxService, project_id: str, tmp_path: Path
) -> None:
    files = tree("html_fastapi_sqlite")
    files["tests/backend/test_req.py"] = (
        b"import pytest\n\n@pytest.mark.req('REQ-1')\ndef test_x():\n    pass\n"
    )
    result = TestRunner(service).run(
        "pytest_api",
        files=files,
        owner=ExecutionOwner(project_id=project_id),
        evidence=Evidence(tmp_path, "runs/r1/sandbox"),
    )
    assert result.outcome.execution.status is SandboxExecutionStatus.FAILED and not result.passed


@pytest.mark.parametrize(
    ("recipe", "args"),
    [
        ("rm_rf", {}),
        ("pytest_api", {"select": "everything"}),
        ("pytest_api", {"cmd": "sh"}),
        ("alembic_autogenerate", {"message": "x; rm"}),
    ],
)
def test_unknown_recipes_and_bad_arguments_run_nothing(
    service: SandboxService,
    project_id: str,
    tmp_path: Path,
    sessions: sessionmaker[Session],
    recipe: str,
    args: dict[str, Any],
) -> None:
    with sessions() as s:
        before = len(repo.executions_for_project(s, project_id))
    with pytest.raises(PolicyDeniedError):
        _run(service, recipe, {}, project_id, tmp_path, args)
    with sessions() as s:
        assert len(repo.executions_for_project(s, project_id)) == before


# -- execute tools through the Tool Layer ----------------------------------------------------------------


def _ctx(
    project_id: str, role: AgentRole, root: Path, *, spec_approved: bool = True, stage: Stage = Stage.BUILD
) -> ToolContext:
    task = AgentTask(
        task_id=new_id("tsk"),
        project_id=project_id,
        run_id=new_id("run"),
        agent_type=role,
        operation_id=f"{role.value}.work",
        objective="Work.",
        done_criteria=("done",),
        permitted_paths=("generated/",),
        spec_version_id=new_id("spv"),
    )
    return ToolContext(
        principal=issue_agent_principal(task),
        task=task,
        stage=stage,
        spec_approved=spec_approved,
        spec_write_paths=("generated/",),
        files=ControlledFileService(
            root, FileLimits(max_write_bytes=65536, max_read_bytes=65536, max_list_entries=100)
        ),
        evidence_dir=root / "runs" / task.run_id / "diffs",
        max_task_write_bytes=1_000_000,
    )


def _layer(service: SandboxService, sessions: sessionmaker[Session]) -> ToolLayer:
    guardrails = load_guardrails(REPO_CONFIG)
    registry = build_default_registry(service)
    registry.check_consistency(guardrails)
    return ToolLayer(registry, PolicyEngine(guardrails), DatabaseAuditSink(sessions), sessions)


def _call(ctx: ToolContext, tool: str, args: dict[str, Any]) -> ToolCall:
    return ToolCall(
        call_id=new_id("tcl"),
        tool=tool,
        tool_version=1,
        principal=ctx.principal.id,
        task_id=ctx.task.task_id,
        args=args,
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspaces" / "prj"
    for name, data in tree("html_fastapi_sqlite").items():
        target = root / "generated" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (root / "generated" / "node_modules" / "evil").mkdir(parents=True)
    (root / "generated" / "node_modules" / "evil" / "x.js").write_bytes(b"never copied")
    (root / "generated" / ".env.local").write_bytes(b"NEVER_COPIED=1")
    return root


def test_tester_runs_tests_through_the_tool_layer(
    service: SandboxService, sessions: sessionmaker[Session], project_id: str, workspace: Path
) -> None:
    ctx = _ctx(project_id, AgentRole.TESTER, workspace)
    result = _layer(service, sessions).invoke(_call(ctx, "request_test_run", {"recipe": "pytest_api"}), ctx)
    assert result.outcome is ToolOutcome.OK, result.error
    value: dict[str, Any] = result.value  # type: ignore[assignment]
    assert value["status"] == "completed" and value["tests"]["passed"] == 3
    assert all(path.startswith(f"runs/{ctx.task.run_id}/sandbox/") for path in value["evidence"])


@pytest.mark.parametrize(
    ("role", "tool", "args"),
    [
        (AgentRole.TESTER, "request_build", {"recipe": "npm_build"}),  # not in the Tester's ceiling
        (AgentRole.FRONTEND_DEVELOPER, "request_test_run", {"recipe": "vitest_run"}),
        (AgentRole.HEAD, "request_build", {"recipe": "ruff_check"}),
        (AgentRole.ARCHITECT, "request_migration_check", {"recipe": "alembic_upgrade"}),
        (AgentRole.DOCUMENTER, "request_test_run", {"recipe": "pytest_api"}),
    ],
)
def test_roles_without_the_grant_are_refused(
    service: SandboxService,
    sessions: sessionmaker[Session],
    project_id: str,
    workspace: Path,
    role: AgentRole,
    tool: str,
    args: dict[str, Any],
) -> None:
    ctx = _ctx(project_id, role, workspace)
    result = _layer(service, sessions).invoke(_call(ctx, tool, args), ctx)
    assert result.outcome is ToolOutcome.DENIED


def test_no_execution_without_an_approved_spec(
    service: SandboxService, sessions: sessionmaker[Session], project_id: str, workspace: Path
) -> None:
    ctx = _ctx(project_id, AgentRole.TESTER, workspace, spec_approved=False)
    result = _layer(service, sessions).invoke(_call(ctx, "request_test_run", {"recipe": "pytest_api"}), ctx)
    assert result.outcome is ToolOutcome.DENIED and result.error is not None
    assert result.error.category is ErrorCategory.POLICY_DENIED


@pytest.mark.parametrize("args", [{"recipe": "pytest_api", "command": "sh -c id"}, {"recipe": "bash"}])
def test_commands_cannot_be_smuggled_in(
    service: SandboxService,
    sessions: sessionmaker[Session],
    project_id: str,
    workspace: Path,
    args: dict[str, Any],
) -> None:
    ctx = _ctx(project_id, AgentRole.TESTER, workspace)
    result = _layer(service, sessions).invoke(_call(ctx, "request_test_run", args), ctx)
    assert result.outcome is not ToolOutcome.OK


def test_postgresql_refused_through_the_tool(
    service: SandboxService, sessions: sessionmaker[Session], project_id: str, workspace: Path
) -> None:
    ctx = _ctx(project_id, AgentRole.DATABASE_DEVELOPER, workspace)
    result = _layer(service, sessions).invoke(
        _call(ctx, "request_migration_check", {"recipe": "alembic_upgrade", "engine": "postgresql"}), ctx
    )
    assert result.outcome is not ToolOutcome.OK and result.error is not None
    assert result.error.code == "sandbox.postgresql_not_available"


def test_unconfigured_sandbox_is_blocked_not_run(
    sessions: sessionmaker[Session], project_id: str, workspace: Path
) -> None:
    guardrails = load_guardrails(REPO_CONFIG)
    layer = ToolLayer(
        build_default_registry(), PolicyEngine(guardrails), DatabaseAuditSink(sessions), sessions
    )
    ctx = _ctx(project_id, AgentRole.TESTER, workspace)
    result = layer.invoke(_call(ctx, "request_test_run", {"recipe": "pytest_api"}), ctx)
    assert result.outcome is not ToolOutcome.OK and result.error is not None
    assert result.error.code == "sandbox.not_configured"


def test_copy_in_skips_node_modules_and_secret_env_files(workspace: Path) -> None:
    from devteam_harness.sandbox.tools import collect_generated  # noqa: PLC0415

    files = collect_generated(workspace, 10_000_000)
    assert "backend/app/main.py" in files
    assert not any(name.startswith("node_modules/") for name in files)
    assert ".env.local" not in files
