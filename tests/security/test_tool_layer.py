"""C.3 controlled tool execution (HC-06, HS-10; AC-C-01, AC-C-07 groundwork).

Every agent tool call goes through ``ToolLayer.invoke``: registry → identity binding → argument schema →
policy engine → approval requirement → handler under a time limit → audit (+ file-change evidence).
Unauthorised tools, paths, roles and stages are rejected **and** audited; the audit chain stays intact.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from devteam_core.contracts.errors import ErrorCategory
from devteam_core.contracts.statuses import AgentRole, Stage
from devteam_core.contracts.tasks import AgentTask
from devteam_core.contracts.tools import SideEffect, ToolCall, ToolOutcome, ToolResult, agent_principal
from devteam_core.errors import ConfigError
from devteam_core.ids import new_id
from devteam_harness.audit import DatabaseAuditSink
from devteam_harness.guardrails import Access, PolicyEngine, load_guardrails
from devteam_harness.iam.principals import issue_agent_principal
from devteam_harness.tools import ToolContext, ToolLayer, ToolRegistry, ToolSpec, build_default_registry
from devteam_harness.tools.files import ControlledFileService, FileLimits
from devteam_state import create_sqlite_engine, session_factory, upgrade
from devteam_state.models import AuditEvent, DocumentChunk, Principal, Project, SourceDocument
from devteam_state.repositories import audit

pytestmark = pytest.mark.security

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"
CANARY = "canary-SECRET-tool-4f1e2d3c4b5a69788796a5b4c3d2e1f0"  # gitleaks:allow (synthetic)
LIMITS = FileLimits(max_write_bytes=64 * 1024, max_read_bytes=64 * 1024, max_list_entries=100)


@pytest.fixture
def sessions(tmp_path: Path) -> Iterator[sessionmaker[Session]]:
    db = tmp_path / "state.db"
    upgrade(db)
    engine = create_sqlite_engine(db)
    yield session_factory(engine)
    engine.dispose()


@pytest.fixture
def project(sessions: sessionmaker[Session]) -> dict[str, str]:
    now = datetime.now(UTC)
    ids = {
        "project": new_id("prj"),
        "other": new_id("prj"),
        "owner": new_id("prn"),
        "doc": new_id("doc"),
        "foreign_doc": new_id("doc"),
    }
    with sessions.begin() as s:
        s.add(Principal(id=ids["owner"], kind="human", display_name="Owner", created_at=now))
        for key, name in (("project", "A"), ("other", "B")):
            s.add(
                Project(
                    id=ids[key],
                    name=name,
                    name_key=name.lower(),
                    description="",
                    status="active",
                    created_by=ids["owner"],
                    created_at=now,
                    updated_at=now,
                )
            )
        for doc_key, proj_key in (("doc", "project"), ("foreign_doc", "other")):
            s.add(
                SourceDocument(
                    id=ids[doc_key],
                    project_id=ids[proj_key],
                    role="primary_brd",
                    original_filename="brd.docx",
                    stored_name=f"{ids[doc_key]}.docx",
                    media_type="x",
                    size_bytes=1,
                    sha256="0" * 64,
                    status="extracted",
                    page_count=1,
                    chunk_count=1,
                    char_count=10,
                    warnings=[],
                    outline=[],
                    extractor="docx",
                    extractor_version="1",
                    uploaded_by=ids["owner"],
                    created_at=now,
                )
            )
            s.flush()
            s.add(
                DocumentChunk(
                    id="chk_" + ("a" if doc_key == "doc" else "b") * 26,
                    project_id=ids[proj_key],
                    document_id=ids[doc_key],
                    ordinal=0,
                    kind="text",
                    text=f"Orders ship in 2 days ({doc_key}).",
                    section_path="3 Orders",
                    page_start=1,
                    page_end=1,
                    paragraph_start=1,
                    paragraph_end=1,
                    reference="brd.docx · p.1 · § 3 Orders · ¶1",
                    content_sha256="0" * 64,
                )
            )
    return ids


@pytest.fixture
def root(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspaces" / "prj"
    (workspace / "generated" / "backend").mkdir(parents=True)
    (workspace / "uploads").mkdir()
    (workspace / "generated" / "backend" / "app.py").write_bytes(b"print('hi')\n")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    return workspace


@pytest.fixture
def layer(sessions: sessionmaker[Session]) -> ToolLayer:
    guardrails = load_guardrails(REPO_CONFIG)
    registry = build_default_registry()
    registry.check_consistency(guardrails)
    return ToolLayer(registry, PolicyEngine(guardrails), DatabaseAuditSink(sessions), sessions)


def _task(project_id: str, role: AgentRole = AgentRole.BACKEND_DEVELOPER, **kw: Any) -> AgentTask:
    spec = None if role in {AgentRole.BRD_ANALYST, AgentRole.REQUIREMENTS_FINALIZER} else new_id("spv")
    return AgentTask(
        task_id=new_id("tsk"),
        project_id=project_id,
        run_id=new_id("run"),
        agent_type=role,
        operation_id=f"{role.value}.work",
        objective="Work.",
        done_criteria=("done",),
        permitted_paths=kw.pop("permitted_paths", ("generated/",)),
        spec_version_id=spec,
        **kw,
    )


def _ctx(task: AgentTask, root: Path, stage: Stage = Stage.BUILD, **kw: Any) -> ToolContext:
    return ToolContext(
        principal=issue_agent_principal(task),
        task=task,
        stage=stage,
        spec_approved=True,
        spec_write_paths=("generated/",),
        files=ControlledFileService(root, LIMITS),
        evidence_dir=root / "runs" / task.run_id / "diffs",
        max_task_write_bytes=kw.pop("max_task_write_bytes", 1_000_000),
        **kw,
    )


def _call(ctx: ToolContext, tool: str, args: dict[str, Any], **kw: Any) -> ToolCall:
    return ToolCall(
        call_id=new_id("tcl"),
        tool=tool,
        tool_version=kw.pop("version", 1),
        principal=kw.pop("principal", ctx.principal.id),
        task_id=kw.pop("task_id", ctx.task.task_id),
        args=args,
    )


def _audit(sessions: sessionmaker[Session], event_id: str) -> AuditEvent:
    with sessions() as s:
        return s.scalars(select(AuditEvent).where(AuditEvent.id == event_id)).one()


def _all_audit_text(sessions: sessionmaker[Session]) -> str:
    with sessions() as s:
        return json.dumps([[r.action, r.principal_id, r.details] for r in s.scalars(select(AuditEvent))])


def _chain_ok(sessions: sessionmaker[Session]) -> bool:
    with sessions() as s:
        return audit.verify_chain(s).ok


# --------------------------------------------------------------------------------- allowed calls


def test_allowed_read_is_executed_and_audited(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    ctx = _ctx(_task(project["project"]), root)
    result = layer.invoke(_call(ctx, "read_project_file", {"path": "generated/backend/app.py"}), ctx)
    assert result.outcome is ToolOutcome.OK and result.value["content"] == "print('hi')\n"  # type: ignore[index]
    row = _audit(sessions, result.audit_event_id)
    assert (row.action, row.outcome, row.principal_id) == ("tool.called", "success", ctx.principal.id)
    assert (
        row.details["tool"] == "read_project_file"
        and row.details["args_summary"]["path"] == "generated/backend/app.py"
    )
    assert _chain_ok(sessions)


def test_allowed_write_records_checksums_and_diff_evidence(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    ctx = _ctx(_task(project["project"]), root)
    result = layer.invoke(
        _call(
            ctx, "write_project_file", {"path": "generated/backend/app.py", "content": f"KEY = '{CANARY}'\n"}
        ),
        ctx,
    )
    assert result.outcome is ToolOutcome.OK
    assert (root / "generated/backend/app.py").read_text(encoding="utf-8") == f"KEY = '{CANARY}'\n"
    with sessions() as s:
        change = s.scalars(select(AuditEvent).where(AuditEvent.action == "file.changed")).one()
    assert change.details["path"] == "generated/backend/app.py"
    assert change.details["before_sha256"] and change.details["after_sha256"]
    diff_file = root / change.details["diff_ref"]
    assert diff_file.is_file() and "+KEY" in diff_file.read_text(encoding="utf-8")
    assert diff_file.resolve().is_relative_to((root / "runs").resolve())
    assert CANARY not in _all_audit_text(sessions)  # file content never goes into the audit trail
    assert _audit(sessions, result.audit_event_id).details["args_summary"]["content"].startswith("<")
    assert _chain_ok(sessions)


def test_extracted_document_reader_is_project_isolated(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    task = _task(project["project"], AgentRole.BRD_ANALYST)
    ctx = _ctx(task, root, stage=Stage.ANALYSIS)
    own = layer.invoke(_call(ctx, "read_extracted_document", {"document_id": project["doc"]}), ctx)
    assert own.outcome is ToolOutcome.OK
    chunk = own.value["chunks"][0]  # type: ignore[index]
    assert chunk["reference"].startswith("brd.docx") and "(doc)" in chunk["text"]
    foreign = layer.invoke(
        _call(ctx, "read_extracted_document", {"document_id": project["foreign_doc"]}), ctx
    )
    assert foreign.outcome is ToolOutcome.ERROR and foreign.error is not None
    assert foreign.error.code == "tool.not_found" and "(foreign_doc)" not in json.dumps(
        foreign.model_dump(mode="json")
    )


def test_knowledge_search_is_an_interface_until_part_f(
    layer: ToolLayer, project: dict[str, str], root: Path
) -> None:
    ctx = _ctx(_task(project["project"], AgentRole.BRD_ANALYST), root, stage=Stage.ANALYSIS)
    result = layer.invoke(_call(ctx, "search_project_knowledge", {"query": "orders"}), ctx)
    assert result.outcome is ToolOutcome.ERROR and result.error is not None
    assert (
        result.error.category is ErrorCategory.RESOURCE_UNAVAILABLE
        and result.error.code == "tool.not_available"
    )


# ------------------------------------------------------------------------------ rejected + audited


def _assert_denied(result: ToolResult, rule: str, sessions: sessionmaker[Session]) -> AuditEvent:
    assert result.outcome is ToolOutcome.DENIED, result
    assert result.policy_rule_id == rule
    assert result.error is not None and result.error.category is ErrorCategory.POLICY_DENIED
    row = _audit(sessions, result.audit_event_id)
    assert row.outcome == "denied" and row.details["rule_id"] == rule
    assert _chain_ok(sessions)
    return row


def test_path_traversal_write_is_rejected_and_audited(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    """Spec §23 scenario #24 (tool-layer part): traversal rejected and audited."""
    ctx = _ctx(_task(project["project"]), root)
    result = layer.invoke(
        _call(ctx, "write_project_file", {"path": "../../outside.txt", "content": "pwned"}), ctx
    )
    _assert_denied(result, "deny.path_invalid", sessions)
    assert (root.parent.parent / "outside.txt").read_text(encoding="utf-8") == "outside"


def test_unregistered_tool_is_rejected_and_audited(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    """Spec §23 scenario #24: an unauthorised tool is rejected and audited."""
    ctx = _ctx(_task(project["project"]), root)
    row = _assert_denied(
        layer.invoke(_call(ctx, "run_shell", {"command": "rm -rf /"}), ctx),
        "deny.unregistered_tool",
        sessions,
    )
    assert row.details["tool"] == "run_shell"


def test_wrong_tool_version_is_rejected(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    ctx = _ctx(_task(project["project"]), root)
    result = layer.invoke(
        _call(ctx, "read_project_file", {"path": "generated/backend/app.py"}, version=2), ctx
    )
    _assert_denied(result, "deny.unregistered_tool", sessions)


def test_call_for_another_task_is_rejected(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    """Malicious handoff: a call carrying another task's identity is refused."""
    ctx = _ctx(_task(project["project"]), root)
    other = new_id("tsk")
    call = _call(
        ctx,
        "read_project_file",
        {"path": "generated/backend/app.py"},
        principal=agent_principal(AgentRole.ARCHITECT, other),
        task_id=other,
    )
    _assert_denied(layer.invoke(call, ctx), "deny.task_mismatch", sessions)


def test_role_and_stage_violations_are_rejected(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    head = _ctx(_task(project["project"], AgentRole.HEAD), root)
    _assert_denied(
        layer.invoke(
            _call(head, "write_project_file", {"path": "generated/backend/x.py", "content": "x"}), head
        ),
        "deny.tool_role",
        sessions,
    )
    early = _ctx(_task(project["project"]), root, stage=Stage.ANALYSIS)
    _assert_denied(
        layer.invoke(_call(early, "read_project_file", {"path": "generated/backend/app.py"}), early),
        "deny.stage",
        sessions,
    )
    unapproved = _ctx(_task(project["project"]), root)
    unapproved = unapproved.model_copy(update={"spec_approved": False})
    _assert_denied(
        layer.invoke(
            _call(unapproved, "read_project_file", {"path": "generated/backend/app.py"}), unapproved
        ),
        "deny.spec_not_approved",
        sessions,
    )


def test_protected_area_write_is_rejected(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    ctx = _ctx(_task(project["project"]), root)
    result = layer.invoke(_call(ctx, "write_project_file", {"path": "uploads/brd.pdf", "content": "x"}), ctx)
    assert result.outcome is ToolOutcome.DENIED and not (root / "uploads/brd.pdf").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions exist only on Windows")
def test_junction_inside_an_allowed_scope_is_rejected_by_the_file_service(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    import _winapi  # noqa: PLC0415

    _winapi.CreateJunction(str(root.parent.parent), str(root / "generated" / "backend" / "escape"))
    ctx = _ctx(_task(project["project"]), root)
    result = layer.invoke(
        _call(
            ctx, "write_project_file", {"path": "generated/backend/escape/outside.txt", "content": "pwned"}
        ),
        ctx,
    )
    _assert_denied(result, "deny.path_unsafe", sessions)
    assert (root.parent.parent / "outside.txt").read_text(encoding="utf-8") == "outside"


@pytest.mark.parametrize(
    "args",
    [{}, {"path": 5}, {"path": "generated/backend/app.py", "follow_links": True}, {"path": "x" * 2000}],
)
def test_malformed_arguments_are_invalid_and_audited(
    layer: ToolLayer,
    project: dict[str, str],
    root: Path,
    sessions: sessionmaker[Session],
    args: dict[str, Any],
) -> None:
    ctx = _ctx(_task(project["project"]), root)
    result = layer.invoke(_call(ctx, "read_project_file", args), ctx)
    assert result.outcome is ToolOutcome.ERROR and result.error is not None
    assert (
        result.error.category is ErrorCategory.INVALID_OUTPUT
        and result.error.code == "tool.invalid_arguments"
    )
    assert _audit(sessions, result.audit_event_id).outcome == "error"


def test_secrets_in_arguments_never_reach_the_audit(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    from devteam_core.logging import secret_registry  # noqa: PLC0415

    secret_registry.register(CANARY)
    try:
        ctx = _ctx(_task(project["project"]), root)
        layer.invoke(_call(ctx, "read_project_file", {"path": f"generated/{CANARY}.py"}), ctx)
        layer.invoke(_call(ctx, "write_project_file", {"path": f"../{CANARY}", "content": CANARY}), ctx)
        assert CANARY not in _all_audit_text(sessions)
    finally:
        secret_registry.unregister(CANARY)


# --------------------------------------------------------------------------------- budgets, timeouts


def test_per_task_write_budget_stops_writes(
    layer: ToolLayer, project: dict[str, str], root: Path, sessions: sessionmaker[Session]
) -> None:
    ctx = _ctx(_task(project["project"]), root, max_task_write_bytes=10)
    ok = layer.invoke(
        _call(ctx, "write_project_file", {"path": "generated/backend/a.py", "content": "x" * 8}), ctx
    )
    assert ok.outcome is ToolOutcome.OK
    over = layer.invoke(
        _call(ctx, "write_project_file", {"path": "generated/backend/b.py", "content": "x" * 8}), ctx
    )
    assert over.outcome is ToolOutcome.ERROR and over.error is not None
    assert over.error.category is ErrorCategory.BUDGET_EXHAUSTED
    assert not (root / "generated/backend/b.py").exists()


class _Args(BaseModel):
    path: str


class _Out(BaseModel):
    done: bool


def _slow_registry(seconds: float) -> ToolRegistry:
    def slow_write(inv: Any) -> _Out:
        time.sleep(seconds)
        inv.ctx.files.write_text(inv.args.path, "late", cancelled=inv.cancelled)
        return _Out(done=True)

    registry = build_default_registry()
    registry.register(
        ToolSpec(
            name="slow_write",
            version=1,
            input_model=_Args,
            output_model=_Out,
            side_effect=SideEffect.WRITE_WORKSPACE,
            access=Access.WRITE,
            path_arg="path",
            allowed_roles=frozenset({AgentRole.BACKEND_DEVELOPER}),
            timeout_s=0.2,
            handler=slow_write,
        )
    )
    return registry


def test_tool_time_limit_cancels_the_write(
    sessions: sessionmaker[Session], project: dict[str, str], root: Path
) -> None:
    guardrails = load_guardrails(REPO_CONFIG)
    from dataclasses import replace  # noqa: PLC0415
    from types import MappingProxyType  # noqa: PLC0415

    from devteam_harness.guardrails.config import ToolGrant  # noqa: PLC0415

    role = AgentRole.BACKEND_DEVELOPER
    policy_role = guardrails.policy.roles[role]
    tools = MappingProxyType(
        {**policy_role.tools, "slow_write": ToolGrant(read=(), write=("generated/backend/",))}
    )
    roles = MappingProxyType({**guardrails.policy.roles, role: replace(policy_role, tools=tools)})
    engine = PolicyEngine(replace(guardrails, policy=replace(guardrails.policy, roles=roles)))
    slow = ToolLayer(_slow_registry(0.6), engine, DatabaseAuditSink(sessions), sessions)
    ctx = _ctx(_task(project["project"]), root)
    result = slow.invoke(_call(ctx, "slow_write", {"path": "generated/backend/late.py"}), ctx)
    assert (
        result.outcome is ToolOutcome.ERROR
        and result.error is not None
        and result.error.code == "tool.timeout"
    )
    time.sleep(0.8)  # let the abandoned worker reach its cancellation point
    assert not (root / "generated/backend/late.py").exists()
    assert not [p for p in (root / "generated/backend").iterdir() if p.name.endswith(".tmp")]


# ------------------------------------------------------------------------------- registry integrity


def test_registry_rejects_duplicates_and_policy_grants_beyond_the_spec() -> None:
    registry = build_default_registry()
    spec = registry.get("read_project_file", 1)
    assert spec is not None
    with pytest.raises(ValueError, match="already registered"):
        registry.register(spec)
    narrow = ToolRegistry()
    for item in registry.specs():
        roles = (
            item.allowed_roles - {AgentRole.TESTER}
            if item.name == "write_project_file"
            else item.allowed_roles
        )
        narrow.register(item.__class__(**{**item.__dict__, "allowed_roles": roles}))
    with pytest.raises(ConfigError, match="tester"):
        narrow.check_consistency(load_guardrails(REPO_CONFIG))


def test_every_spec_declares_the_spec_11_2_attributes() -> None:
    for spec in build_default_registry().specs():
        assert spec.name and spec.version >= 1 and spec.input_model and spec.output_model
        assert spec.allowed_roles and spec.timeout_s > 0 and isinstance(spec.side_effect, SideEffect)
        assert spec.requires_approval in {True, False} and spec.audit in {"full", "summary"}
    names = {s.name for s in build_default_registry().specs()}
    assert names == {
        "read_project_file",
        "list_project_files",
        "write_project_file",
        "read_extracted_document",
        "search_project_knowledge",
        # Part E (DV-025): allowlisted sandbox recipes; no path, no command, never a shell
        "request_build",
        "request_test_run",
        "request_migration_check",
    }
    assert not any("shell" in n or "exec" in n for n in names)


def test_approval_required_tools_are_refused_without_an_approval(
    sessions: sessionmaker[Session], project: dict[str, str], root: Path
) -> None:
    registry = build_default_registry()
    base = registry.get("read_project_file", 1)
    assert base is not None
    gated = ToolRegistry()
    for item in registry.specs():
        gated.register(
            item.__class__(**{**item.__dict__, "requires_approval": item.name == "read_project_file"})
        )
    layer = ToolLayer(
        gated, PolicyEngine(load_guardrails(REPO_CONFIG)), DatabaseAuditSink(sessions), sessions
    )
    ctx = _ctx(_task(project["project"]), root)
    result = layer.invoke(_call(ctx, "read_project_file", {"path": "generated/backend/app.py"}), ctx)
    _assert_denied(result, "deny.approval_required", sessions)


def test_tool_specs_reject_unsafe_declarations() -> None:
    base = build_default_registry().get("read_project_file", 1)
    assert base is not None
    fields = dict(base.__dict__)
    for bad in (
        {"side_effect": SideEffect.EXECUTE},  # no command execution before the sandbox (E)
        {"side_effect": SideEffect.EXTERNAL},  # no external effects before publishing (P)
        {"timeout_s": 0},
        {"access": Access.NONE},  # a path tool must declare its access
        {"path_arg": None},
    ):
        with pytest.raises(ValueError):
            ToolSpec(**{**fields, **bad})


def test_registry_rejects_unknown_tools_and_access_mismatch_in_policy() -> None:
    from dataclasses import replace  # noqa: PLC0415
    from types import MappingProxyType  # noqa: PLC0415

    from devteam_harness.guardrails.config import ToolGrant  # noqa: PLC0415
    from devteam_harness.tools import describe  # noqa: PLC0415

    guardrails = load_guardrails(REPO_CONFIG)
    registry = build_default_registry()
    role = AgentRole.BACKEND_DEVELOPER
    policy_role = guardrails.policy.roles[role]

    def with_grant(tool: str, grant: ToolGrant) -> Any:
        tools = MappingProxyType({**policy_role.tools, tool: grant})
        roles = MappingProxyType({**guardrails.policy.roles, role: replace(policy_role, tools=tools)})
        return replace(guardrails, policy=replace(guardrails.policy, roles=roles))

    with pytest.raises(ConfigError, match="unknown tool"):
        registry.check_consistency(with_grant("delete_everything", ToolGrant(read=(), write=())))
    with pytest.raises(ConfigError, match="access"):
        registry.check_consistency(with_grant("read_project_file", ToolGrant(read=(), write=("generated/",))))
    info = describe(registry.specs()[0])
    assert info["name"] == "read_project_file" and "properties" in info["input_schema"]
