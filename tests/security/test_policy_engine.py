"""C.2 policy engine (HC-01, HS-08): deny by default, role x tool x path x stage, audited denials.

Written before the engine (test-first, CLAUDE.md §9). ``EXPECTED`` is an independent statement of the
specification (roadmap §4.1, Spec §6): the matrix test compares the engine against it for every role,
tool and stage, so a wrong YAML file or a wrong engine both fail.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from devteam_core.contracts.statuses import AgentRole, Stage
from devteam_core.contracts.tasks import AgentTask
from devteam_core.errors import ConfigError
from devteam_core.ids import new_id
from devteam_harness.guardrails import (
    Access,
    Guardrails,
    PolicyDecision,
    PolicyEnforcer,
    PolicyEngine,
    PolicyRequest,
    load_guardrails,
)
from devteam_harness.iam import Principal
from devteam_harness.iam.principals import ServiceName, ServicePrincipal, issue_agent_principal
from tests.fixtures import contract_samples as s

pytestmark = pytest.mark.security

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config"

R, W = "read", "write"
BUILD_STAGES = {Stage.BUILD, Stage.CHANGE_REQUEST}
FILES_RO = {"read_project_file": R, "list_project_files": R}

# Independent oracle: role -> (stages, {tool: access}, write scopes)
EXPECTED: dict[AgentRole, tuple[set[Stage], dict[str, str], tuple[str, ...]]] = {
    AgentRole.BRD_ANALYST: (
        {Stage.ANALYSIS},
        {"read_extracted_document": R, "search_project_knowledge": R},
        (),
    ),
    AgentRole.REQUIREMENTS_FINALIZER: (
        {Stage.REQUIREMENTS},
        {"read_extracted_document": R, "search_project_knowledge": R},
        (),
    ),
    AgentRole.HEAD: ({Stage.BUILD, Stage.DELIVERY, Stage.CHANGE_REQUEST}, {"list_project_files": R}, ()),
    AgentRole.ARCHITECT: (
        BUILD_STAGES,
        {**FILES_RO, "write_project_file": W, "search_project_knowledge": R},
        ("generated/docs/", "generated/contracts/"),
    ),
    AgentRole.DATABASE_DEVELOPER: (
        BUILD_STAGES,
        {**FILES_RO, "write_project_file": W},
        ("generated/database/", "generated/backend/models/"),
    ),
    AgentRole.BACKEND_DEVELOPER: (
        BUILD_STAGES,
        {**FILES_RO, "write_project_file": W},
        ("generated/backend/", "generated/config/"),
    ),
    AgentRole.FRONTEND_DEVELOPER: (
        BUILD_STAGES,
        {**FILES_RO, "write_project_file": W},
        ("generated/frontend/",),
    ),
    AgentRole.TESTER: (BUILD_STAGES, {**FILES_RO, "write_project_file": W}, ("generated/tests/",)),
    AgentRole.DEBUGGER_REVIEWER: (
        BUILD_STAGES,
        {**FILES_RO, "write_project_file": W},
        ("generated/backend/", "generated/frontend/", "generated/database/", "generated/config/"),
    ),
    AgentRole.DOCUMENTER: (
        {Stage.DELIVERY, Stage.CHANGE_REQUEST},
        {**FILES_RO, "write_project_file": W},
        ("generated/docs/",),
    ),
}
ALL_TOOLS = (
    "read_extracted_document",
    "search_project_knowledge",
    "read_project_file",
    "list_project_files",
    "write_project_file",
    "run_shell",  # never registered, never allowed
    "request_github_publish",  # not granted to anyone in Part C
)


class RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, **event: Any) -> str:
        self.events.append(event)
        return new_id("aud")


@pytest.fixture(scope="module")
def guardrails() -> Guardrails:
    return load_guardrails(REPO_CONFIG)


@pytest.fixture(scope="module")
def engine(guardrails: Guardrails) -> PolicyEngine:
    return PolicyEngine(guardrails)


def _task(role: AgentRole, scopes: tuple[str, ...] = ("generated/",), **kw: Any) -> AgentTask:
    spec = kw.pop("spec_version_id", s.SPEC)
    return s.task(role, operation_id=f"{role.value}.work", permitted_paths=scopes, spec_version_id=spec, **kw)


def _request(role: AgentRole, tool: str, stage: Stage, **kw: Any) -> PolicyRequest:
    task = kw.pop("task", None) or _task(role)
    access = kw.pop("access", Access.READ if tool != "write_project_file" else Access.WRITE)
    defaults: dict[str, Any] = {
        "principal": issue_agent_principal(task),
        "tool": tool,
        "access": access,
        "path": None,
        "stage": stage,
        "task": task,
        "spec_approved": True,
        "spec_write_paths": ("generated/",),
    }
    defaults.update(kw)
    return PolicyRequest(**defaults)


def _path_for(role: AgentRole, tool: str) -> str | None:
    if tool == "write_project_file":
        scope = EXPECTED[role][2][0] if EXPECTED[role][2] else "generated/docs/"
        return scope if not scope.endswith("/") else scope + "file.txt"
    if tool in FILES_RO:
        return "generated/backend/app.py"
    return None


# ------------------------------------------------------------------------------------- the matrix


@pytest.mark.parametrize("role", list(AgentRole))
@pytest.mark.parametrize("tool", ALL_TOOLS)
@pytest.mark.parametrize("stage", list(Stage))
def test_policy_matrix_matches_the_specification(
    engine: PolicyEngine, role: AgentRole, tool: str, stage: Stage
) -> None:
    stages, tools, _ = EXPECTED[role]
    request = _request(role, tool, stage, path=_path_for(role, tool))
    decision = engine.decide(request)
    expected = stage in stages and tool in tools
    assert decision.allowed is expected, (role, tool, stage, decision)
    if not expected:
        assert decision.rule_id.startswith("deny.")


@pytest.mark.parametrize("role", list(AgentRole))
def test_writes_only_inside_the_role_write_scopes(engine: PolicyEngine, role: AgentRole) -> None:
    stages, tools, scopes = EXPECTED[role]
    stage = sorted(stages)[0]
    areas = ["docs", "contracts", "database", "backend", "backend/models", "frontend", "tests", "config"]
    for area in areas:
        path = f"generated/{area}/x.py"
        decision = engine.decide(_request(role, "write_project_file", stage, path=path))
        allowed = "write_project_file" in tools and any(path.startswith(sc) for sc in scopes)
        assert decision.allowed is allowed, (role, path, decision)


# --------------------------------------------------------------------------------- path attacks


@pytest.mark.parametrize(
    "path",
    [
        "../secret.txt",
        "generated/backend/../../uploads/brd.pdf",
        "generated/backend/../frontend/x.tsx",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "C:\\Windows\\win.ini",
        "\\\\server\\share\\x",
        "generated/backend/app.py::$DATA",
        "%2e%2e/secret",
        "generated/backend/CON",
        "generated/backend/app.py.",
        "",
    ],
)
def test_traversal_and_windows_path_attacks_are_denied(engine: PolicyEngine, path: str) -> None:
    decision = engine.decide(
        _request(AgentRole.BACKEND_DEVELOPER, "write_project_file", Stage.BUILD, path=path)
    )
    assert not decision.allowed and decision.rule_id == "deny.path_invalid"


@pytest.mark.parametrize(
    "path", ["uploads/brd.pdf", "extracted/doc.json", "runs/r1/result.json", "package/app.zip"]
)
def test_platform_areas_are_never_writable(engine: PolicyEngine, path: str) -> None:
    task = _task(
        AgentRole.BACKEND_DEVELOPER, scopes=("uploads/", "extracted/", "runs/", "package/", "generated/")
    )
    decision = engine.decide(
        _request(
            AgentRole.BACKEND_DEVELOPER,
            "write_project_file",
            Stage.BUILD,
            path=path,
            task=task,
            spec_write_paths=("uploads/", "extracted/", "runs/", "package/"),
        )
    )
    assert not decision.allowed and decision.rule_id in {"deny.path_protected", "deny.path_out_of_scope"}


def test_raw_uploads_are_never_readable(engine: PolicyEngine) -> None:
    decision = engine.decide(
        _request(AgentRole.ARCHITECT, "read_project_file", Stage.BUILD, path="uploads/brd.pdf")
    )
    assert not decision.allowed


def test_case_variants_do_not_escape_scope(engine: PolicyEngine) -> None:
    ok = engine.decide(
        _request(
            AgentRole.FRONTEND_DEVELOPER, "write_project_file", Stage.BUILD, path="Generated/Frontend/App.tsx"
        )
    )
    assert ok.allowed  # NTFS is case-insensitive: same folder, still in scope
    bad = engine.decide(
        _request(
            AgentRole.FRONTEND_DEVELOPER, "write_project_file", Stage.BUILD, path="GENERATED/BACKEND/app.py"
        )
    )
    assert not bad.allowed and bad.rule_id == "deny.path_out_of_scope"
    protected = engine.decide(
        _request(AgentRole.ARCHITECT, "read_project_file", Stage.BUILD, path="UPLOADS/brd.pdf")
    )
    assert not protected.allowed


def test_write_without_a_path_is_denied(engine: PolicyEngine) -> None:
    decision = engine.decide(
        _request(AgentRole.BACKEND_DEVELOPER, "write_project_file", Stage.BUILD, path=None)
    )
    assert not decision.allowed and decision.rule_id == "deny.path_required"


# ----------------------------------------------------------------- intersection: spec ∩ task ∩ policy


def test_task_scope_narrows_the_role(engine: PolicyEngine) -> None:
    task = _task(AgentRole.BACKEND_DEVELOPER, scopes=("generated/backend/api/",))
    inside = _request(
        AgentRole.BACKEND_DEVELOPER,
        "write_project_file",
        Stage.BUILD,
        task=task,
        path="generated/backend/api/orders.py",
    )
    outside = _request(
        AgentRole.BACKEND_DEVELOPER,
        "write_project_file",
        Stage.BUILD,
        task=task,
        path="generated/backend/models/order.py",
    )
    assert engine.decide(inside).allowed
    assert engine.decide(outside).rule_id == "deny.path_out_of_scope"


def test_task_scope_cannot_widen_the_role(engine: PolicyEngine) -> None:
    """A malicious handoff granting the frontend agent the backend folder does not widen its permission."""
    task = _task(AgentRole.FRONTEND_DEVELOPER, scopes=("generated/backend/", "generated/frontend/"))
    decision = engine.decide(
        _request(
            AgentRole.FRONTEND_DEVELOPER,
            "write_project_file",
            Stage.BUILD,
            task=task,
            path="generated/backend/app.py",
        )
    )
    assert decision.rule_id == "deny.path_out_of_scope"


def test_agent_spec_scope_narrows_too(engine: PolicyEngine) -> None:
    decision = engine.decide(
        _request(
            AgentRole.BACKEND_DEVELOPER,
            "write_project_file",
            Stage.BUILD,
            path="generated/config/app.env",
            spec_write_paths=("generated/backend/",),
        )
    )
    assert decision.rule_id == "deny.path_out_of_scope"


def test_frozen_files_cannot_be_written(engine: PolicyEngine) -> None:
    request = _request(
        AgentRole.ARCHITECT,
        "write_project_file",
        Stage.BUILD,
        path="generated/contracts/openapi.json",
        frozen_paths=frozenset({"generated/contracts/openapi.json"}),
    )
    assert engine.decide(request).rule_id == "deny.path_frozen"
    folder_freeze = request.model_copy(update={"frozen_paths": frozenset({"generated/contracts/"})})
    assert engine.decide(folder_freeze).rule_id == "deny.path_frozen"


def test_debugger_cannot_write_tests_without_approved_maintenance(engine: PolicyEngine) -> None:
    task = _task(AgentRole.DEBUGGER_REVIEWER, scopes=("generated/",))
    request = _request(
        AgentRole.DEBUGGER_REVIEWER,
        "write_project_file",
        Stage.BUILD,
        task=task,
        path="generated/backend/tests/test_orders.py",
    )
    assert engine.decide(request).rule_id == "deny.test_edit_requires_approval"
    approved = request.model_copy(update={"test_maintenance_approved": True})
    assert engine.decide(approved).allowed
    assert engine.decide(request.model_copy(update={"path": "generated/backend/orders.py"})).allowed


# --------------------------------------------------------------- stage rule (approval enforcement)


@pytest.mark.parametrize("stage", [Stage.BUILD, Stage.DELIVERY, Stage.CHANGE_REQUEST])
def test_spec_bound_stages_require_an_approved_spec(engine: PolicyEngine, stage: Stage) -> None:
    role = AgentRole.HEAD
    request = _request(role, "list_project_files", stage, path="generated/", spec_approved=False)
    assert engine.decide(request).rule_id == "deny.spec_not_approved"
    assert engine.decide(request.model_copy(update={"spec_approved": True})).allowed


def test_task_without_spec_version_denied_in_build(engine: PolicyEngine) -> None:
    """Defence in depth: a task that bypassed validation is refused by the request model AND the engine."""
    task = _task(AgentRole.BACKEND_DEVELOPER).model_copy(update={"spec_version_id": None})  # no validation
    with pytest.raises(ValidationError):
        _request(
            AgentRole.BACKEND_DEVELOPER,
            "read_project_file",
            Stage.BUILD,
            task=task,
            path="generated/backend/app.py",
        )
    bypass = PolicyRequest.model_construct(
        principal=issue_agent_principal(task),
        tool="read_project_file",
        access=Access.READ,
        path="generated/backend/app.py",
        stage=Stage.BUILD,
        task=task,
        spec_approved=True,
        spec_write_paths=("generated/",),
        frozen_paths=frozenset(),
        test_maintenance_approved=False,
    )
    assert engine.decide(bypass).rule_id == "deny.spec_not_approved"


# -------------------------------------------------------------------------- identity and escalation


def test_principal_must_match_the_task(engine: PolicyEngine) -> None:
    mine = _task(AgentRole.BACKEND_DEVELOPER)
    theirs = _task(AgentRole.BACKEND_DEVELOPER)
    request = _request(
        AgentRole.BACKEND_DEVELOPER,
        "read_project_file",
        Stage.BUILD,
        task=theirs,
        principal=issue_agent_principal(mine),
        path="generated/backend/app.py",
    )
    assert engine.decide(request).rule_id == "deny.task_mismatch"


def test_agent_cannot_borrow_another_roles_identity(engine: PolicyEngine) -> None:
    """A tester task presented with an architect principal (role escalation) is denied."""
    tester_task = _task(AgentRole.TESTER)
    forged = issue_agent_principal(_task(AgentRole.ARCHITECT)).__class__(
        role=AgentRole.ARCHITECT, task_id=tester_task.task_id, run_id=s.RUN, project_id=s.PROJECT
    )
    request = _request(
        AgentRole.TESTER,
        "write_project_file",
        Stage.BUILD,
        task=tester_task,
        principal=forged,
        path="generated/docs/arch.md",
    )
    assert engine.decide(request).rule_id == "deny.task_mismatch"


def test_cross_project_principal_denied(engine: PolicyEngine) -> None:
    task = _task(AgentRole.BACKEND_DEVELOPER)
    principal = issue_agent_principal(task)
    other = principal.__class__(
        role=principal.role, task_id=principal.task_id, run_id=principal.run_id, project_id=s.OTHER_PROJECT
    )
    request = _request(
        AgentRole.BACKEND_DEVELOPER,
        "read_project_file",
        Stage.BUILD,
        task=task,
        principal=other,
        path="generated/backend/app.py",
    )
    assert engine.decide(request).rule_id == "deny.task_mismatch"


def test_humans_and_services_cannot_use_agent_tools(engine: PolicyEngine) -> None:
    human = Principal(id=s.HUMAN, kind="human", display_name="Owner", roles=frozenset({"project_user"}))
    for principal in (human, ServicePrincipal(ServiceName.EXECUTOR)):
        request = _request(
            AgentRole.BACKEND_DEVELOPER,
            "read_project_file",
            Stage.BUILD,
            principal=principal,
            path="generated/backend/app.py",
        )
        assert engine.decide(request).rule_id == "deny.not_an_agent"


def test_unknown_tool_is_denied_by_default(engine: PolicyEngine) -> None:
    for tool in ("run_shell", "delete_project", "write_project_file_v2", "READ_PROJECT_FILE"):
        decision = engine.decide(_request(AgentRole.BACKEND_DEVELOPER, tool, Stage.BUILD, access=Access.NONE))
        assert not decision.allowed and decision.rule_id == "deny.default"


def test_read_access_cannot_be_used_to_write(engine: PolicyEngine) -> None:
    request = _request(
        AgentRole.HEAD, "list_project_files", Stage.BUILD, access=Access.WRITE, path="generated/x"
    )
    assert engine.decide(request).rule_id == "deny.access_not_granted"


def test_decision_is_immutable(engine: PolicyEngine) -> None:
    decision = engine.decide(_request(AgentRole.HEAD, "run_shell", Stage.BUILD, access=Access.NONE))
    with pytest.raises((AttributeError, TypeError, ValueError)):
        decision.allowed = True  # type: ignore[misc]
    assert isinstance(decision, PolicyDecision)


# -------------------------------------------------------------------------------- audited denials


def test_denials_are_audited_and_allows_are_not_silently_dropped(engine: PolicyEngine) -> None:
    sink = RecordingSink()
    enforcer = PolicyEnforcer(engine, sink)
    denied = enforcer.authorize(
        _request(AgentRole.BACKEND_DEVELOPER, "write_project_file", Stage.BUILD, path="../../Windows/win.ini")
    )
    assert not denied.allowed
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["action"] == "policy.denied" and event["outcome"] == "denied"
    assert event["principal_id"].startswith("agent:backend_developer:tsk_")
    assert event["details"]["rule_id"] == "deny.path_invalid"
    assert event["details"]["tool"] == "write_project_file"
    allowed = enforcer.authorize(
        _request(
            AgentRole.BACKEND_DEVELOPER, "write_project_file", Stage.BUILD, path="generated/backend/app.py"
        )
    )
    assert allowed.allowed and len(sink.events) == 1  # allowed calls are audited by the tool layer (C.3)


def test_denial_fails_closed_when_audit_is_unavailable(engine: PolicyEngine) -> None:
    class BrokenSink:
        def record(self, **_: Any) -> str:
            raise OSError("disk full")

    enforcer = PolicyEnforcer(engine, BrokenSink())
    with pytest.raises(OSError):
        enforcer.authorize(_request(AgentRole.HEAD, "run_shell", Stage.BUILD, access=Access.NONE))


def test_audited_path_is_bounded_and_redacted(engine: PolicyEngine) -> None:
    sink = RecordingSink()
    secret_like = "ghp_" + "A" * 40
    PolicyEnforcer(engine, sink).authorize(
        _request(
            AgentRole.BACKEND_DEVELOPER,
            "write_project_file",
            Stage.BUILD,
            path=f"../{secret_like}" + "x" * 500,
        )
    )
    recorded = sink.events[0]["details"]["path"]
    assert secret_like not in recorded and len(recorded) <= 200


# ------------------------------------------------------------------------- policy manipulation


def _copy_config(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(REPO_CONFIG, target)
    return target


def _edit(config: Path, name: str, mutate: Any) -> None:
    path = config / "guardrails" / name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.mark.parametrize(
    ("file", "mutate", "why"),
    [
        (
            "policies.yaml",
            lambda d: d["roles"]["head"]["tools"].update(
                {"write_project_file": {"write": ["generated/backend/"]}}
            ),
            "Head Agent may never write files",
        ),
        (
            "policies.yaml",
            lambda d: d["roles"]["tester"]["tools"]["write_project_file"].update(
                {"write": ["generated/backend/"]}
            ),
            "Tester writes only tests",
        ),
        (
            "policies.yaml",
            lambda d: d["roles"]["documenter"]["tools"]["write_project_file"].update(
                {"write": ["generated/"]}
            ),
            "whole generated tree",
        ),
        (
            "policies.yaml",
            lambda d: d["roles"]["backend_developer"]["tools"]["write_project_file"].update(
                {"write": ["uploads/"]}
            ),
            "protected area",
        ),
        (
            "policies.yaml",
            lambda d: d["roles"]["backend_developer"]["tools"]["write_project_file"].update(
                {"write": ["../"]}
            ),
            "traversal scope",
        ),
        (
            "policies.yaml",
            lambda d: d["roles"]["brd_analyst"].update({"stages": ["analysis", "build"]}),
            "discovery agent in build",
        ),
        (
            "policies.yaml",
            lambda d: d["roles"]["brd_analyst"]["tools"].update(
                {"write_project_file": {"write": ["generated/docs/"]}}
            ),
            "discovery agents never write files",
        ),
        (
            "policies.yaml",
            lambda d: d["roles"].update({"admin": {"stages": ["build"], "tools": {}}}),
            "eleventh role",
        ),
        ("policies.yaml", lambda d: d["roles"].pop("head"), "role missing"),
        ("policies.yaml", lambda d: d.update({"default": "allow"}), "allow-by-default switch"),
        ("policies.yaml", lambda d: d["roles"]["architect"]["tools"].update({"run_shell": {}}), "shell tool"),
        ("policies.yaml", lambda d: d.update({"version": 2}), "unknown version"),
        (
            "policies.yaml",
            lambda d: d["roles"]["head"]["tools"]["list_project_files"].update({"read": ["uploads/"]}),
            "raw uploads readable",
        ),
        (
            "protected_paths.yaml",
            lambda d: d.update({"never_writable": ["package/"]}),
            "built-in protection removed",
        ),
        ("protected_paths.yaml", lambda d: d.update({"never_readable": []}), "built-in protection removed"),
        (
            "input_guardrails.yaml",
            lambda d: d["injection_patterns"].append({"id": "bad", "pattern": "("}),
            "bad regex",
        ),
        (
            "input_guardrails.yaml",
            lambda d: d.update({"max_untrusted_chars": 10_000_000}),
            "limit out of range",
        ),
        ("output_guardrails.yaml", lambda d: d.update({"forbidden_claims": []}), "claims check disabled"),
    ],
)
def test_tampered_configuration_refuses_to_load(tmp_path: Path, file: str, mutate: Any, why: str) -> None:
    config = _copy_config(tmp_path)
    assert load_guardrails(config)  # baseline copy loads
    _edit(config, file, mutate)
    with pytest.raises(ConfigError):
        load_guardrails(config)


def test_missing_guardrail_file_fails_closed(tmp_path: Path) -> None:
    config = _copy_config(tmp_path)
    (config / "guardrails" / "policies.yaml").unlink()
    with pytest.raises(ConfigError):
        load_guardrails(config)


def test_policy_digest_changes_when_the_file_changes(tmp_path: Path, guardrails: Guardrails) -> None:
    config = _copy_config(tmp_path)
    _edit(config, "policies.yaml", lambda d: d["roles"]["head"]["tools"].pop("list_project_files"))
    narrowed = load_guardrails(config)  # narrowing is allowed
    assert narrowed.digest != guardrails.digest
    engine = PolicyEngine(narrowed)
    assert not engine.decide(
        _request(AgentRole.HEAD, "list_project_files", Stage.BUILD, path="generated/")
    ).allowed


def test_loaded_policy_cannot_be_mutated_at_runtime(guardrails: Guardrails) -> None:
    with pytest.raises((TypeError, AttributeError, ValueError)):
        guardrails.policy.roles[AgentRole.HEAD] = guardrails.policy.roles[AgentRole.ARCHITECT]  # type: ignore[index]


def test_protected_areas_hold_even_if_the_config_loader_were_bypassed(guardrails: Guardrails) -> None:
    """Defence in depth: the loader refuses write scopes over protected areas, but if a policy object were
    built by other means with such a scope, the engine still denies with ``deny.path_protected``."""
    from dataclasses import replace  # noqa: PLC0415
    from types import MappingProxyType  # noqa: PLC0415

    from devteam_harness.guardrails.config import RolePolicy, ToolGrant  # noqa: PLC0415

    role = AgentRole.BACKEND_DEVELOPER
    tampered_role = RolePolicy(
        stages=guardrails.policy.roles[role].stages,
        tools=MappingProxyType(
            {"write_project_file": ToolGrant(read=(), write=("uploads/", "generated/backend/"))}
        ),
    )
    tampered = replace(
        guardrails,
        policy=replace(
            guardrails.policy, roles=MappingProxyType({**guardrails.policy.roles, role: tampered_role})
        ),
    )
    engine = PolicyEngine(tampered)
    task = _task(role, scopes=("uploads/", "generated/backend/"))
    for path in ("uploads/brd.pdf", "UPLOADS/x.docx"):
        request = _request(
            role,
            "write_project_file",
            Stage.BUILD,
            task=task,
            path=path,
            spec_write_paths=("uploads/", "generated/"),
        )
        assert engine.decide(request).rule_id == "deny.path_protected"
    ok = _request(
        role,
        "write_project_file",
        Stage.BUILD,
        task=task,
        path="generated/backend/app.py",
        spec_write_paths=("uploads/", "generated/"),
    )
    assert engine.decide(ok).allowed
