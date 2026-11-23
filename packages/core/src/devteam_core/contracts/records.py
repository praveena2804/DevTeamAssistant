"""Domain records (Spec §12, field values per PART-C-PLAN §2.1 F-1…F-8, approved AP-008).

Contracts only: database tables for these records are added by the part that first writes them.
Rules enforced here hold wherever a record is created (agent output, API, service):

- baseline records covered by Spec §5.4.1 accept only ``brd_fact`` / ``user_answer`` provenance;
- evidence and verification values that only the harness may produce require their proof
  (a test run or an approval record);
- approvals and spec versions name a **human** principal; agents have no approval authority.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from devteam_core.contracts._base import (
    AnswerId,
    ApprovalId,
    ArtifactId,
    AwareDatetime,
    ChangeRequestId,
    ConstraintId,
    Contract,
    CriterionId,
    DecisionId,
    EntityId,
    EvidenceId,
    FeasibilityItemId,
    GapId,
    IntegrationId,
    Name,
    PermissionId,
    PrincipalId,
    ProjectId,
    QuestionId,
    RecordRef,
    RelPath,
    RequirementKey,
    RoleId,
    RuleKey,
    RunId,
    SecretId,
    Sha256,
    ShortText,
    SpecVersionId,
    TechnologyNeedId,
    TestRunId,
    Text,
    TraceKey,
    WorkflowId,
)
from devteam_core.contracts.base import NeverAssumed, Provenance, Sourced, SourceRef
from devteam_core.contracts.statuses import ApprovalType, FeasibilityStatus

_MANY = 500  # structural cap on list sizes inside one record

# ------------------------------------------------------------------------------------- F-1 … F-8 values


class RequirementType(StrEnum):
    FUNCTIONAL = "functional"
    NON_FUNCTIONAL = "non_functional"


class RequirementPriority(StrEnum):
    """F-1 (MoSCoW). ``unspecified`` unless the BRD or the user states a priority."""

    MUST = "must"
    SHOULD = "should"
    COULD = "could"
    WONT = "wont"
    UNSPECIFIED = "unspecified"


class RequirementStatus(StrEnum):
    """F-2: scope, not progress. Progress lives in RequirementEvidence."""

    EXTRACTED = "extracted"
    CONFIRMED = "confirmed"
    EXCLUDED = "excluded"
    BLOCKED = "blocked"


class RuleVerificationStatus(StrEnum):
    """F-3."""

    UNVERIFIED = "unverified"
    TESTED = "tested"
    MANUAL_VERIFICATION = "manual_verification"
    BLOCKED = "blocked"


class ApprovalDecision(StrEnum):
    """F-4. 'Edit' is not a decision: it returns the draft for changes."""

    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ArtifactType(StrEnum):
    """F-5. Set by the Controlled File Service from the write path, never by an agent."""

    DOCUMENT = "document"
    SOURCE_FILE = "source_file"
    TEST_FILE = "test_file"
    CONTRACT = "contract"
    MIGRATION = "migration"
    CONFIG = "config"
    REPORT = "report"
    PACKAGE = "package"


class TestType(StrEnum):
    """F-6."""

    UNIT = "unit"
    API = "api"
    DATABASE = "database"
    INTEGRATION = "integration"
    UI = "ui"
    BUILD = "build"
    LINT = "lint"
    TYPECHECK = "typecheck"
    SECURITY = "security"


class EvidenceStatus(StrEnum):
    """F-7. Default ``not_run``: nothing looks green until the harness ran it."""

    PASSING = "passing"
    FAILING = "failing"
    NOT_RUN = "not_run"
    MANUAL = "manual"
    BLOCKED = "blocked"


class ChangeRequestStatus(StrEnum):
    """F-8 (provisional until Part Q)."""

    SUBMITTED = "submitted"
    ANALYSING = "analysing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    APPLIED = "applied"
    FAILED = "failed"


class TestResult(StrEnum):
    """Outcome of one executed test run (recorded by the Test Runner only)."""

    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"


class GapCategory(StrEnum):
    """The 13 mandatory completeness-checklist categories (Spec §6.2)."""

    GOAL = "goal"
    ACTORS = "actors"
    FEATURES = "features"
    WORKFLOWS = "workflows"
    DATA = "data"
    RULES = "rules"
    PERMISSIONS = "permissions"
    LOGIN = "login"
    TECHNOLOGY = "technology"
    INTEGRATIONS = "integrations"
    DEPLOYMENT = "deployment"
    ACCEPTANCE_CRITERIA = "acceptance_criteria"
    NON_FUNCTIONAL = "non_functional"


class GapKind(StrEnum):
    """Spec §6.2: conflicting, ambiguous, duplicate and missing statements."""

    MISSING = "missing"
    UNCLEAR = "unclear"
    CONFLICTING = "conflicting"
    DUPLICATE = "duplicate"


class Criticality(StrEnum):
    """Spec §5.4.2. Decided by code rules (Requirements Policy Engine), never by model opinion."""

    CRITICAL = "critical"
    MINOR = "minor"


class GapStatus(StrEnum):
    """Spec §5.4: a gap is resolved or explicitly excluded; never silently decided."""

    OPEN = "open"
    RESOLVED = "resolved"
    EXCLUDED = "excluded"


class TechnologyLayer(StrEnum):
    """Spec §6.2: frontend, backend, language, framework, runtime, database, auth, deployment."""

    FRONTEND = "frontend"
    BACKEND = "backend"
    LANGUAGE = "language"
    FRAMEWORK = "framework"
    RUNTIME = "runtime"
    DATABASE = "database"
    AUTHENTICATION = "authentication"
    DEPLOYMENT = "deployment"
    OTHER = "other"


class ConstraintCategory(StrEnum):
    """Spec §6.2: performance, security, privacy, availability and operational constraints."""

    PERFORMANCE = "performance"
    SECURITY = "security"
    PRIVACY = "privacy"
    AVAILABILITY = "availability"
    OPERATIONAL = "operational"
    OTHER = "other"


class RelationshipKind(StrEnum):
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"
    MANY_TO_ONE = "many_to_one"
    MANY_TO_MANY = "many_to_many"


# ---------------------------------------------------------------------------------------- baseline


class Requirement(NeverAssumed):
    requirement_id: RequirementKey
    project_id: ProjectId
    type: RequirementType
    description: Text
    priority: RequirementPriority = RequirementPriority.UNSPECIFIED
    status: RequirementStatus = RequirementStatus.EXTRACTED

    @model_validator(mode="after")
    def _key_matches_type(self) -> Self:
        expected = "FR-" if self.type is RequirementType.FUNCTIONAL else "NFR-"
        if not self.requirement_id.startswith(expected):
            raise ValueError(f"{self.type.value} requirement IDs must start with {expected}")
        return self


class BusinessRule(NeverAssumed):
    rule_id: RuleKey
    project_id: ProjectId
    description: Text
    requirement_ids: tuple[RequirementKey, ...] = Field(default=(), max_length=_MANY)
    verification_status: RuleVerificationStatus = RuleVerificationStatus.UNVERIFIED


class Role(NeverAssumed):
    role_id: RoleId
    project_id: ProjectId
    name: Name
    responsibilities: tuple[ShortText, ...] = Field(default=(), max_length=_MANY)


class Permission(NeverAssumed):
    permission_id: PermissionId
    project_id: ProjectId
    role_id: RoleId
    action: Name
    resource: Name


class EntityAttribute(Contract):
    name: Name
    data_type: Name | None = None  # BRD wording; mapped to a concrete type by the Database Developer
    required: bool | None = None
    unique: bool | None = None
    description: ShortText | None = None


class EntityRelationship(Contract):
    target_entity: Name
    kind: RelationshipKind
    description: ShortText | None = None


class EntityDefinition(NeverAssumed):
    entity_id: EntityId
    project_id: ProjectId
    name: Name
    attributes: tuple[EntityAttribute, ...] = Field(default=(), max_length=_MANY)
    relationships: tuple[EntityRelationship, ...] = Field(default=(), max_length=_MANY)

    @model_validator(mode="after")
    def _unique_attributes(self) -> Self:
        names = [a.name.casefold() for a in self.attributes]
        if len(names) != len(set(names)):
            raise ValueError("entity attribute names must be unique")
        return self


class Workflow(NeverAssumed):
    workflow_id: WorkflowId
    project_id: ProjectId
    name: Name
    description: Text
    steps: tuple[ShortText, ...] = Field(min_length=1, max_length=_MANY)


class Integration(NeverAssumed):
    integration_id: IntegrationId
    project_id: ProjectId
    name: Name
    description: Text
    catalog_key: Name | None = None  # validated against the Capability Catalog in Part G


class TechnologyNeed(NeverAssumed):
    tech_id: TechnologyNeedId
    project_id: ProjectId
    layer: TechnologyLayer
    name: Name
    version_spec: Name | None = None


class AcceptanceCriterion(NeverAssumed):
    criterion_id: CriterionId
    project_id: ProjectId
    requirement_ids: tuple[RequirementKey, ...] = Field(min_length=1, max_length=_MANY)
    description: Text


class Constraint(NeverAssumed):
    constraint_id: ConstraintId
    project_id: ProjectId
    category: ConstraintCategory
    description: Text


class Gap(Contract):
    """A missing/unclear/conflicting/duplicate item. Criticality is assigned by code rules only."""

    gap_id: GapId
    project_id: ProjectId
    category: GapCategory
    kind: GapKind
    criticality: Criticality
    description: Text
    source_refs: tuple[SourceRef, ...] = Field(default=(), max_length=50)
    status: GapStatus = GapStatus.OPEN

    @model_validator(mode="after")
    def _evidence_for_conflicts(self) -> Self:
        if self.kind in {GapKind.CONFLICTING, GapKind.DUPLICATE} and len(self.source_refs) < 2:
            raise ValueError(f"a {self.kind.value} gap must cite the (at least two) statements involved")
        return self


class FeasibilityItem(Contract):
    item_id: FeasibilityItemId
    project_id: ProjectId
    requirement_id: TraceKey
    status: FeasibilityStatus
    reason: ShortText | None = None
    alternative: ShortText | None = None

    @model_validator(mode="after")
    def _explained(self) -> Self:
        if self.status is FeasibilityStatus.SUPPORTED_WITH_SUBSTITUTION and self.alternative is None:
            raise ValueError("a substitution must name the alternative (it is never silent)")
        if self.status is not FeasibilityStatus.SUPPORTED and self.reason is None:
            raise ValueError("partial/not supported/substitution items must state the reason")
        return self


class ClarificationQuestion(Contract):
    question_id: QuestionId
    project_id: ProjectId
    gap_id: GapId
    topic: GapCategory
    prompt: ShortText
    options: tuple[Name, ...] = Field(default=(), max_length=20)
    allow_other: bool = True
    allow_not_needed: bool = False
    reason: ShortText  # why it matters (Spec §5.4)
    evidence_refs: tuple[SourceRef, ...] = Field(default=(), max_length=20)
    impact_rank: int = Field(ge=1, le=1_000)

    @model_validator(mode="after")
    def _unique_options(self) -> Self:
        if len({o.casefold() for o in self.options}) != len(self.options):
            raise ValueError("question options must be unique")
        return self


class ClarificationAnswer(Contract):
    answer_id: AnswerId
    question_id: QuestionId
    project_id: ProjectId
    answer_text: ShortText | None = None
    selected_options: tuple[Name, ...] = Field(default=(), max_length=20)
    not_needed: bool = False
    source_user: PrincipalId  # always a human principal

    @model_validator(mode="after")
    def _something_answered(self) -> Self:
        given = bool(self.answer_text) or bool(self.selected_options) or self.not_needed
        if not given:
            raise ValueError("an answer needs text, a selected option or 'not needed'")
        if self.not_needed and (self.answer_text or self.selected_options):
            raise ValueError("'not needed' cannot be combined with other answers")
        return self


class SpecVersion(Contract):
    version_id: SpecVersionId
    project_id: ProjectId
    version: int = Field(ge=1)
    checksum: Sha256
    approved_by: PrincipalId  # human principal only; agents cannot approve
    approved_at: AwareDatetime
    approval_id: ApprovalId


class DecisionRecord(Sourced):
    """Decisions, minor assumptions (Spec §5.4.2) and recommendations, with provenance."""

    decision_id: DecisionId
    project_id: ProjectId
    decision: ShortText
    timestamp: AwareDatetime


# ------------------------------------------------------------------------------------- build / verify


class Artifact(Contract):
    artifact_id: ArtifactId
    project_id: ProjectId
    path: RelPath
    type: ArtifactType
    version: int = Field(ge=1)
    checksum: Sha256


class TestRun(Contract):
    """Written by the Test Runner only (Spec §6.9: agents cannot record results)."""

    __test__ = False

    test_run_id: TestRunId
    project_id: ProjectId
    run_id: RunId
    test_type: TestType
    command: Name  # allowlisted recipe identifier, never a raw shell string
    result: TestResult
    exit_code: int
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    skipped: int = Field(ge=0)
    errors: int = Field(ge=0)
    logs_ref: RelPath | None = None
    timestamp: AwareDatetime

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.result is TestResult.PASSED and (self.failed or self.errors or self.exit_code != 0):
            raise ValueError("a passed run cannot report failures, errors or a non-zero exit code")
        if self.result is TestResult.FAILED and self.failed == 0 and self.exit_code == 0:
            raise ValueError("a failed run must report a failure or a non-zero exit code")
        return self


class RequirementEvidence(Contract):
    evidence_id: EvidenceId
    project_id: ProjectId
    requirement_id: TraceKey
    artifact_refs: tuple[ArtifactId, ...] = Field(default=(), max_length=_MANY)
    test_run_id: TestRunId | None = None
    approval_id: ApprovalId | None = None
    status: EvidenceStatus = EvidenceStatus.NOT_RUN

    @model_validator(mode="after")
    def _proof(self) -> Self:
        if self.status in {EvidenceStatus.PASSING, EvidenceStatus.FAILING} and self.test_run_id is None:
            raise ValueError(f"'{self.status.value}' evidence requires the executed test run")
        if self.status is EvidenceStatus.MANUAL and self.approval_id is None:
            raise ValueError("manual verification requires an approved reviewer record")
        return self


class ChangeRequest(Contract):
    change_id: ChangeRequestId
    project_id: ProjectId
    description: Text
    impact_scope: tuple[RecordRef, ...] = Field(default=(), max_length=_MANY)
    status: ChangeRequestStatus = ChangeRequestStatus.SUBMITTED
    created_at: AwareDatetime


class Approval(Contract):
    approval_id: ApprovalId
    project_id: ProjectId
    run_id: RunId
    approval_type: ApprovalType
    spec_version_id: SpecVersionId | None = None
    spec_checksum: Sha256 | None = None
    decision: ApprovalDecision
    decided_by: PrincipalId  # human principal only
    decided_at: AwareDatetime

    @model_validator(mode="after")
    def _bound_to_version(self) -> Self:
        if (self.spec_version_id is None) != (self.spec_checksum is None):
            raise ValueError("spec_version_id and spec_checksum must be given together")
        if self.approval_type is ApprovalType.SPEC_APPROVAL and self.spec_checksum is None:
            raise ValueError("a spec approval must be bound to the spec version and checksum")
        return self


class SecretReference(Contract):
    """A pointer to a secret in the OS keyring (HC-17). There is deliberately no value field."""

    secret_id: SecretId
    project_id: ProjectId | None = None  # None = platform secret
    name: Name
    ref: str = Field(pattern=r"^secret://(platform|prj_[0-9A-HJKMNP-TV-Z]{26})/[a-z][a-z0-9_.-]{0,63}$")
    created_at: AwareDatetime


DOMAIN_RECORDS: tuple[type[Contract], ...] = (
    Requirement,
    BusinessRule,
    Role,
    Permission,
    EntityDefinition,
    Workflow,
    Integration,
    TechnologyNeed,
    AcceptanceCriterion,
    Constraint,
    Gap,
    FeasibilityItem,
    ClarificationQuestion,
    ClarificationAnswer,
    SpecVersion,
    DecisionRecord,
    Artifact,
    TestRun,
    RequirementEvidence,
    ChangeRequest,
    Approval,
    SecretReference,
)

__all__ = [
    "DOMAIN_RECORDS",
    "AcceptanceCriterion",
    "Approval",
    "ApprovalDecision",
    "Artifact",
    "ArtifactType",
    "BusinessRule",
    "ChangeRequest",
    "ChangeRequestStatus",
    "ClarificationAnswer",
    "ClarificationQuestion",
    "Constraint",
    "ConstraintCategory",
    "Criticality",
    "DecisionRecord",
    "EntityAttribute",
    "EntityDefinition",
    "EntityRelationship",
    "EvidenceStatus",
    "FeasibilityItem",
    "Gap",
    "GapCategory",
    "GapKind",
    "GapStatus",
    "Integration",
    "Permission",
    "Provenance",
    "RelationshipKind",
    "Requirement",
    "RequirementEvidence",
    "RequirementPriority",
    "RequirementStatus",
    "RequirementType",
    "Role",
    "RuleVerificationStatus",
    "SecretReference",
    "SpecVersion",
    "TechnologyLayer",
    "TechnologyNeed",
    "TestResult",
    "TestRun",
    "TestType",
    "Workflow",
]
