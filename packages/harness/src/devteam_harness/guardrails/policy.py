"""Deterministic, deny-by-default policy engine (HC-01, HS-08).

Effective permission = central policy ∩ AgentSpec write paths ∩ task ``permitted_paths`` ∩ stage rule.
A task (a handoff) can only narrow an agent's scope, never widen it (CLAUDE.md §5).

Checks run in a fixed order and the first failing check decides; every denial names its rule:

=================================  =================================================================
``deny.not_an_agent``              only agent principals use agent tools (humans/services do not)
``deny.task_mismatch``             principal role/task/run/project must equal the task's
``deny.default``                   tool not granted to this role (includes every unknown tool)
``deny.stage``                     role not active in this stage
``deny.spec_not_approved``         build/delivery/change-request work needs an approved frozen spec
``deny.access_not_granted``        e.g. a read grant used for writing
``deny.path_required``             a path tool called without a path
``deny.path_invalid``              traversal, absolute, drive, UNC, stream, reserved name, encoding …
``deny.path_protected``            platform-owned areas (uploads, extraction, runs, packages)
``deny.path_frozen``               frozen contract files
``deny.path_out_of_scope``         outside policy ∩ AgentSpec ∩ task scope
``deny.test_edit_requires_approval``  Debugger editing tests without approved test maintenance
=================================  =================================================================

The engine is pure: it reads only its immutable configuration and the request. Auditing of denials is
done by :class:`PolicyEnforcer`, which fails closed if the audit record cannot be written.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from devteam_core.contracts._base import check_relative_path, path_in_scope
from devteam_core.contracts.agents import is_test_path
from devteam_core.contracts.statuses import AgentRole, Stage
from devteam_core.contracts.tasks import AgentTask
from devteam_core.logging import redact_value
from devteam_harness.guardrails.config import Guardrails, ToolGrant
from devteam_harness.iam.principals import AgentPrincipal, AnyPrincipal
from devteam_state.repositories.audit import Outcome

SPEC_APPROVAL_STAGES = frozenset({Stage.BUILD, Stage.DELIVERY, Stage.CHANGE_REQUEST})
_AUDIT_PATH_CHARS = 120


class Access(StrEnum):
    NONE = "none"
    READ = "read"
    WRITE = "write"


class PolicyRequest(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    principal: Any  # AnyPrincipal; checked by the engine, never trusted
    tool: str
    access: Access
    path: str | None = None
    stage: Stage
    task: AgentTask | None
    spec_approved: bool  # verified by the caller against SQLite (build-entry guard, D/I)
    spec_write_paths: tuple[str, ...]  # from the agent's AgentSpec (C.4)
    frozen_paths: frozenset[str] = Field(default_factory=frozenset)
    test_maintenance_approved: bool = False


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    rule_id: str
    reason: str


def _deny(rule: str, reason: str) -> PolicyDecision:
    return PolicyDecision(allowed=False, rule_id=f"deny.{rule}", reason=reason)


def _in_any(path: str, scopes: tuple[str, ...] | frozenset[str]) -> bool:
    return any(path_in_scope(path, scope) for scope in scopes)


class PolicyEngine:
    def __init__(self, guardrails: Guardrails) -> None:
        self._rules = guardrails.policy
        self.digest = guardrails.digest

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        principal = request.principal
        if not isinstance(principal, AgentPrincipal):
            return _deny("not_an_agent", "only agents use agent tools")
        task = request.task
        if task is None or (principal.role, principal.task_id, principal.run_id, principal.project_id) != (
            task.agent_type,
            task.task_id,
            task.run_id,
            task.project_id,
        ):
            return _deny("task_mismatch", "the agent identity does not belong to this task")
        role_policy = self._rules.roles.get(principal.role)
        grant = role_policy.tools.get(request.tool) if role_policy else None
        if role_policy is None or grant is None:
            return _deny("default", f"'{request.tool}' is not granted to {principal.role.value}")
        if request.stage not in role_policy.stages:
            return _deny("stage", f"{principal.role.value} is not active in the {request.stage.value} stage")
        if request.stage in SPEC_APPROVAL_STAGES and (
            not request.spec_approved or task.spec_version_id is None
        ):
            return _deny("spec_not_approved", "this stage requires an approved, frozen specification")
        return self._check_access(principal.role, grant, request, task)

    def _check_access(  # noqa: PLR0911 - one return per rule keeps every denial auditable
        self, role: AgentRole, grant: ToolGrant, request: PolicyRequest, task: AgentTask
    ) -> PolicyDecision:
        allow = PolicyDecision(allowed=True, rule_id=f"allow.{role.value}.{request.tool}", reason="granted")
        if not grant.needs_path:
            return (
                allow
                if request.access in {Access.NONE, Access.READ}
                else _deny("access_not_granted", "this tool grants no write access")
            )
        scopes = grant.write if request.access is Access.WRITE else grant.read
        if request.access is Access.NONE or not scopes:
            return _deny("access_not_granted", f"{request.access.value} access is not granted for this tool")
        path = request.path
        if path is None:
            return _deny("path_required", "this tool requires a workspace path")
        try:
            check_relative_path(path, allow_directory=request.access is Access.READ)
        except ValueError:
            return _deny("path_invalid", "the path is not a safe workspace-relative path")
        if request.access is Access.WRITE:
            if _in_any(path, self._rules.never_writable):
                return _deny("path_protected", "platform-owned area")
            if _in_any(path, request.frozen_paths):
                return _deny("path_frozen", "the file is frozen")
            effective = (scopes, task.permitted_paths, request.spec_write_paths)
            if not all(_in_any(path, layer) for layer in effective):
                return _deny("path_out_of_scope", "outside the agent's write scope")
            if (
                role is AgentRole.DEBUGGER_REVIEWER
                and is_test_path(path)
                and not request.test_maintenance_approved
            ):
                return _deny("test_edit_requires_approval", "tests change only in approved test maintenance")
            return allow
        if _in_any(path, self._rules.never_readable):
            return _deny("path_protected", "platform-owned area")
        if not _in_any(path, scopes):
            return _deny("path_out_of_scope", "outside the agent's read scope")
        return allow


class AuditSink(Protocol):
    """Where harness components write audit events (``devteam_harness.audit.DatabaseAuditSink``)."""

    def record(
        self,
        *,
        action: str,
        outcome: Outcome,
        principal_id: str | None,
        project_id: str | None,
        details: dict[str, Any],
    ) -> str: ...


def _safe_path(path: str | None) -> str | None:
    if path is None:
        return None
    # Truncate first, then redact: a truncated token is still caught by the token patterns.
    return str(redact_value(path[:_AUDIT_PATH_CHARS]))


class PolicyEnforcer:
    """Engine + audit: every denial is recorded before the decision is returned (fail closed)."""

    def __init__(self, engine: PolicyEngine, sink: AuditSink) -> None:
        self._engine = engine
        self._sink = sink

    def authorize(self, request: PolicyRequest) -> PolicyDecision:
        decision = self._engine.decide(request)
        if not decision.allowed:
            principal: AnyPrincipal | Any = request.principal
            task = request.task
            self._sink.record(
                action="policy.denied",
                outcome="denied",
                principal_id=getattr(principal, "id", None),
                project_id=task.project_id if task else None,
                details={
                    "rule_id": decision.rule_id,
                    "tool": request.tool[:64],
                    "access": request.access.value,
                    "stage": request.stage.value,
                    "path": _safe_path(request.path),
                    "task_id": task.task_id if task else None,
                    "policy_digest": self._engine.digest,
                },
            )
        return decision
