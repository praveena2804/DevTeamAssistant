"""Closed status and name enumerations (Spec §5, §6, §18.1, §20.2; roadmap §4.1, §6.4, §6.5).

These are closed ``StrEnum`` types: a value outside the list is rejected at every boundary. Changing a
list is a contract change (``schema_version`` bump + snapshot update) and, for ``AgentRole``, requires
an ADR and owner approval (ADR-0002).
"""

from enum import StrEnum


class AgentRole(StrEnum):
    """The ten agents, exactly (roadmap §4.1, ADR-0002). Adding an eleventh is prohibited."""

    BRD_ANALYST = "brd_analyst"
    REQUIREMENTS_FINALIZER = "requirements_finalizer"
    HEAD = "head"
    ARCHITECT = "architect"
    DATABASE_DEVELOPER = "database_developer"
    BACKEND_DEVELOPER = "backend_developer"
    FRONTEND_DEVELOPER = "frontend_developer"
    TESTER = "tester"
    DEBUGGER_REVIEWER = "debugger_reviewer"
    DOCUMENTER = "documenter"


EXPECTED_AGENT_COUNT = 10

DISCOVERY_ROLES = frozenset({AgentRole.BRD_ANALYST, AgentRole.REQUIREMENTS_FINALIZER})
# Agents the Head Agent may schedule in a build plan (roadmap §4.3: Head → 4…9).
BUILD_PLAN_ROLES = frozenset(
    {
        AgentRole.ARCHITECT,
        AgentRole.DATABASE_DEVELOPER,
        AgentRole.BACKEND_DEVELOPER,
        AgentRole.FRONTEND_DEVELOPER,
        AgentRole.TESTER,
        AgentRole.DEBUGGER_REVIEWER,
    }
)
# Roles whose tasks run against an approved, frozen specification (F-11b).
SPEC_BOUND_ROLES = frozenset({AgentRole.HEAD, AgentRole.DOCUMENTER} | BUILD_PLAN_ROLES)


class RunStatus(StrEnum):
    """Run lifecycle (roadmap §6.4). Transitions are enforced by ``devteam_state.machines`` (C.3)."""

    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_FOR_HUMAN = "waiting_for_human"
    WAITING_FOR_RESOURCES = "waiting_for_resources"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED, RunStatus.BLOCKED}
)


class TaskStatus(StrEnum):
    """Task statuses (Spec §20.2, nine values)."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIALLY_COMPLETED = "partially_completed"
    BLOCKED = "blocked"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = frozenset(
    {
        TaskStatus.COMPLETED,
        TaskStatus.PARTIALLY_COMPLETED,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
        TaskStatus.SKIPPED,
        TaskStatus.CANCELLED,
    }
)


class ProjectVerificationStatus(StrEnum):
    """Project statuses (Spec §18.1). Written by the Completion Gate only; no agent contract has it."""

    GENERATED = "generated"
    BUILD_PASSED = "build_passed"
    TESTS_PASSED = "tests_passed"
    REQUIREMENTS_VERIFIED = "requirements_verified"
    PARTIALLY_VERIFIED = "partially_verified"
    BLOCKED = "blocked"
    FAILED = "failed"
    VERIFIED = "verified"


class FeasibilityStatus(StrEnum):
    """Spec §5.3."""

    SUPPORTED = "supported"
    SUPPORTED_WITH_SUBSTITUTION = "supported_with_substitution"
    PARTIAL = "partial"
    NOT_SUPPORTED = "not_supported"


class Stage(StrEnum):
    """Workflow stages (Spec §5.1-§5.7)."""

    INTAKE = "intake"
    ANALYSIS = "analysis"
    FEASIBILITY = "feasibility"
    REQUIREMENTS = "requirements"
    BUILD = "build"
    DELIVERY = "delivery"
    CHANGE_REQUEST = "change_request"


# Stages that may only run against an approved frozen spec (roadmap §6.5).
SPEC_BOUND_STAGES = frozenset({Stage.BUILD, Stage.DELIVERY})


class GateName(StrEnum):
    """Human gate nodes (roadmap §6.5)."""

    FEASIBILITY_DECISION = "feasibility_decision"
    CLARIFICATION_FORM = "clarification_form"
    PROJECT_APPROVAL = "project_approval"
    TEST_MAINTENANCE_APPROVAL = "test_maintenance_approval"
    PUBLISH_APPROVAL = "publish_approval"
    CHANGE_APPROVAL = "change_approval"
    DB_SWITCH_APPROVAL = "db_switch_approval"


class ApprovalType(StrEnum):
    """Approval types (roadmap §5.1 HC-13)."""

    FEASIBILITY_DECISION = "feasibility_decision"
    SPEC_APPROVAL = "spec_approval"
    SUBSTITUTION = "substitution"
    TEST_MAINTENANCE = "test_maintenance"
    PUBLISH_GITHUB = "publish_github"
    DESTRUCTIVE_DB = "destructive_db"
    DB_SWITCH = "db_switch"


class ModelSlot(StrEnum):
    """Model slots (docs/architecture/MODEL_ROUTING.md; D-37 adds the optional ``review`` slot)."""

    INSTRUCT = "instruct"
    CODER = "coder"
    REVIEW = "review"
    CODER_LARGE = "coder_large"
    FALLBACK = "fallback"
    EMBEDDING = "embedding"
