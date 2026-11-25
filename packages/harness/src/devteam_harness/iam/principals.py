"""Agent and service principals (HC-15, HS-07).

Agents never create their own identity. The Agent Executor (C.4) calls :func:`issue_agent_principal`
for the task it is about to run, so an agent principal is always ``agent:<role>:<task_id>`` for exactly
that task, run and project. The policy engine re-checks this binding on every call.

Neither agents nor services hold approval authority (Spec §4: "No authority to bypass user approval";
"No independent approval authority"). Only human principals can approve; which human role may approve
which gate is configured in ``config/approvals.yaml`` (C.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from devteam_core.contracts.statuses import AgentRole
from devteam_core.contracts.tasks import AgentTask
from devteam_core.contracts.tools import agent_principal
from devteam_harness.iam.roles import Principal


@dataclass(frozen=True, slots=True)
class AgentPrincipal:
    role: AgentRole
    task_id: str
    run_id: str
    project_id: str

    @property
    def id(self) -> str:
        return agent_principal(self.role, self.task_id)

    @property
    def kind(self) -> str:
        return "agent"


class ServiceName(StrEnum):
    """Deterministic services and harness components that act on their own behalf (roadmap §4.2)."""

    EXECUTOR = "executor"
    TOOL_LAYER = "tool_layer"
    LLM_GATEWAY = "llm_gateway"
    APPROVAL_SERVICE = "approval_service"
    EXTRACTOR = "extractor"
    FEASIBILITY_GATE = "feasibility_gate"
    REQUIREMENTS_POLICY = "requirements_policy"
    SPEC_FREEZE = "spec_freeze"
    INTEGRATION_BUILDER = "integration_builder"
    TEST_RUNNER = "test_runner"
    MIGRATION_RUNNER = "migration_runner"
    TRACEABILITY = "traceability"
    COMPLETION_GATE = "completion_gate"
    PACKAGER = "packager"
    SECRET_SCANNER = "secret_scanner"  # noqa: S105 - a service name, not a secret
    GITHUB_DEPLOYER = "github_deployer"
    CHANGE_IMPACT = "change_impact"
    SECRETS_MIGRATION = "secrets_migration"
    RAG_INGESTOR = "rag_ingestor"  # Part F: embeddings for indexing and retrieval (HC-08)
    EVALUATOR = "evaluator"  # Part F: prompt evaluation runs (HC-09)


@dataclass(frozen=True, slots=True)
class ServicePrincipal:
    name: ServiceName

    @property
    def id(self) -> str:
        return f"service:{self.name.value}"

    @property
    def kind(self) -> str:
        return "service"


AnyPrincipal = Principal | AgentPrincipal | ServicePrincipal


def issue_agent_principal(task: AgentTask) -> AgentPrincipal:
    """The only way an agent identity is created: bound to one validated task."""
    if not isinstance(task, AgentTask):
        raise TypeError("an agent principal can only be issued for an AgentTask")
    return AgentPrincipal(
        role=task.agent_type, task_id=task.task_id, run_id=task.run_id, project_id=task.project_id
    )


def has_approval_authority(principal: AnyPrincipal) -> bool:
    """Agents and services never approve. Humans may, subject to the per-gate role rules (C.4)."""
    return isinstance(principal, Principal) and principal.kind == "human" and bool(principal.roles)


__all__ = [
    "AgentPrincipal",
    "AnyPrincipal",
    "ServiceName",
    "ServicePrincipal",
    "has_approval_authority",
    "issue_agent_principal",
]
