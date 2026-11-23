"""Input/output contracts v1 for all ten agents (Spec §6.2-§6.11; roadmap §4.1; AC-C-03).

Design rules enforced by these models:

- Outputs are typed records or **references** (record IDs, workspace paths with checksums). No output
  has a field in which an agent could claim that tests passed, a requirement is verified, a project is
  Verified, or an approval was given (Spec §6.1). ``extra="forbid"`` rejects any such added field.
- Every record an agent emits must belong to the project it was given (cross-project leakage).
- Write locations are checked structurally here (workspace-relative, no traversal); the Controlled File
  Service (C.3) enforces the agent's real write scope.
- :func:`check_output_against_input` holds the cross-checks between what an agent was given and what
  it returned. The Agent Executor (C.4) calls it after schema validation.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from devteam_core.contracts._base import (
    ApprovalId,
    ArtifactId,
    ChunkId,
    Contract,
    DocumentId,
    FeasibilityItemId,
    GapId,
    Name,
    ProjectId,
    RecordRef,
    RelPath,
    RunId,
    Sha256,
    ShortText,
    SpecVersionId,
    TaskId,
    TestRunId,
    TraceKey,
    path_in_scope,
)
from devteam_core.contracts.base import SourceRef
from devteam_core.contracts.records import (
    AcceptanceCriterion,
    BusinessRule,
    ClarificationQuestion,
    Constraint,
    DecisionRecord,
    EntityDefinition,
    Gap,
    GapCategory,
    Integration,
    Permission,
    Requirement,
    Role,
    TechnologyNeed,
    Workflow,
)
from devteam_core.contracts.statuses import AgentRole
from devteam_core.contracts.tasks import AgentTask, TaskPlan

_MANY = 500


class ContractViolationError(ValueError):
    """An agent output is inconsistent with the input it was given."""


# -------------------------------------------------------------------------------------- shared parts


class FileOutput(Contract):
    """A file the agent asks the harness to write (the harness computes the authoritative checksum)."""

    path: RelPath
    sha256: Sha256


class ExecutionKind(StrEnum):
    """What an agent may *request*; the harness decides and runs it (Spec §11.2)."""

    BUILD = "build"
    TEST_RUN = "test_run"
    MIGRATION_CHECK = "migration_check"
    STATIC_CHECK = "static_check"


class ExecutionRequest(Contract):
    kind: ExecutionKind
    target: RelPath | None = None
    requirement_ids: tuple[TraceKey, ...] = Field(default=(), max_length=_MANY)


class RequirementMapping(Contract):
    requirement_id: TraceKey
    paths: tuple[RelPath, ...] = Field(min_length=1, max_length=100)


class Blocker(Contract):
    description: ShortText
    requirement_ids: tuple[TraceKey, ...] = Field(default=(), max_length=_MANY)


class AgentIO(Contract):
    project_id: ProjectId
    run_id: RunId


def _project_of(records: Iterable[Contract]) -> set[str]:
    return {str(r.project_id) for r in records if hasattr(r, "project_id")}


# ------------------------------------------------------------------------------- 1. BRD Analyst (§6.2)


class ChecklistStatus(StrEnum):
    FOUND = "found"
    MISSING_OR_UNCLEAR = "missing_or_unclear"


class ChecklistItem(Contract):
    category: GapCategory
    status: ChecklistStatus
    source_refs: tuple[SourceRef, ...] = Field(default=(), max_length=50)

    @model_validator(mode="after")
    def _evidence(self) -> Self:
        if self.status is ChecklistStatus.FOUND and not self.source_refs:
            raise ValueError(f"checklist item '{self.category.value}' marked found without source evidence")
        return self


class BrdAnalystInput(AgentIO):
    document_ids: tuple[DocumentId, ...] = Field(min_length=1, max_length=50)
    chunk_ids: tuple[ChunkId, ...] = Field(min_length=1, max_length=_MANY)  # one bounded batch


class BrdAnalystOutput(AgentIO):
    requirements: tuple[Requirement, ...] = Field(default=(), max_length=_MANY)
    business_rules: tuple[BusinessRule, ...] = Field(default=(), max_length=_MANY)
    roles: tuple[Role, ...] = Field(default=(), max_length=_MANY)
    permissions: tuple[Permission, ...] = Field(default=(), max_length=_MANY)
    entities: tuple[EntityDefinition, ...] = Field(default=(), max_length=_MANY)
    workflows: tuple[Workflow, ...] = Field(default=(), max_length=_MANY)
    integrations: tuple[Integration, ...] = Field(default=(), max_length=_MANY)
    technology_needs: tuple[TechnologyNeed, ...] = Field(default=(), max_length=_MANY)
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = Field(default=(), max_length=_MANY)
    constraints: tuple[Constraint, ...] = Field(default=(), max_length=_MANY)
    gaps: tuple[Gap, ...] = Field(default=(), max_length=_MANY)
    checklist: tuple[ChecklistItem, ...] = Field(min_length=len(GapCategory), max_length=len(GapCategory))

    def all_records(self) -> tuple[Contract, ...]:
        return (
            *self.requirements,
            *self.business_rules,
            *self.roles,
            *self.permissions,
            *self.entities,
            *self.workflows,
            *self.integrations,
            *self.technology_needs,
            *self.acceptance_criteria,
            *self.constraints,
            *self.gaps,
        )

    def all_source_refs(self) -> tuple[SourceRef, ...]:
        refs: list[SourceRef] = []
        for record in self.all_records():
            refs.extend(getattr(record, "source_refs", ()))
        for item in self.checklist:
            refs.extend(item.source_refs)
        return tuple(refs)

    @model_validator(mode="after")
    def _baseline_rules(self) -> Self:
        if {item.category for item in self.checklist} != set(GapCategory):
            raise ValueError("the completeness checklist must give every category exactly one status")
        keys = [r.requirement_id for r in self.requirements] + [b.rule_id for b in self.business_rules]
        dupes = sorted({k for k in keys if keys.count(k) > 1})
        if dupes:
            raise ValueError(f"stable IDs must be unique: {', '.join(dupes)}")
        known_reqs = {r.requirement_id for r in self.requirements}
        for criterion in self.acceptance_criteria:
            unknown = [k for k in criterion.requirement_ids if k not in known_reqs]
            if unknown:
                raise ValueError(f"acceptance criterion references unknown requirement(s): {unknown}")
        role_ids = {r.role_id for r in self.roles}
        for permission in self.permissions:
            if permission.role_id not in role_ids:
                raise ValueError(f"permission {permission.permission_id} references an unknown role")
        if _project_of(self.all_records()) - {self.project_id}:
            raise ValueError("every record must belong to the analysed project")
        return self


# ------------------------------------------------------------------- 2. Requirements Finalizer (§6.3)


class RequirementsFinalizerInput(AgentIO):
    requirement_ids: tuple[TraceKey, ...] = Field(default=(), max_length=_MANY)
    gap_ids: tuple[GapId, ...] = Field(default=(), max_length=_MANY)
    critical_gap_ids: tuple[GapId, ...] = Field(default=(), max_length=_MANY)  # decided by code rules
    feasibility_item_ids: tuple[FeasibilityItemId, ...] = Field(default=(), max_length=_MANY)
    answers_submitted: bool = False  # True once the single clarification round has happened


class RequirementsFinalizerOutput(AgentIO):
    questions: tuple[ClarificationQuestion, ...] = Field(default=(), max_length=100)
    decisions: tuple[DecisionRecord, ...] = Field(default=(), max_length=_MANY)
    excluded_requirement_ids: tuple[TraceKey, ...] = Field(default=(), max_length=_MANY)
    summary_draft: FileOutput | None = None  # REQUIREMENTS_CONFIRMED.md draft

    @model_validator(mode="after")
    def _own_project(self) -> Self:
        if _project_of((*self.questions, *self.decisions)) - {self.project_id}:
            raise ValueError("every record must belong to the project")
        return self


# ------------------------------------------------------------------------- 3. Head Agent (§6.4)


class RoutingMethod(StrEnum):
    RULES = "rules"
    LLM_CLASSIFICATION = "llm_classification"


class RoutingDecision(Contract):
    task_id: TaskId
    failure_ref: RecordRef
    owner: AgentRole
    method: RoutingMethod
    reason: ShortText


class HeadInput(AgentIO):
    spec_version_id: SpecVersionId
    spec_checksum: Sha256
    in_scope_keys: tuple[TraceKey, ...] = Field(default=(), max_length=_MANY)
    feasibility_item_ids: tuple[FeasibilityItemId, ...] = Field(default=(), max_length=_MANY)
    failed_task_ids: tuple[TaskId, ...] = Field(default=(), max_length=_MANY)


class HeadOutput(AgentIO):
    plan: TaskPlan | None = None
    routing_decisions: tuple[RoutingDecision, ...] = Field(default=(), max_length=_MANY)
    progress_summary: ShortText | None = None

    @model_validator(mode="after")
    def _routing_targets_specialists(self) -> Self:
        for decision in self.routing_decisions:
            if decision.owner is AgentRole.HEAD:
                raise ValueError("the Head Agent cannot route a failure to itself")
        if self.plan is not None and (self.plan.project_id, self.plan.run_id) != (
            self.project_id,
            self.run_id,
        ):
            raise ValueError("the plan belongs to another project or run")
        return self


# ---------------------------------------------------------------------- 4-7. Build agents (§6.5-§6.8)


class BuildTaskInput(AgentIO):
    task: AgentTask
    spec_version_id: SpecVersionId
    contract_artifact_id: ArtifactId | None = None  # frozen OpenAPI contract (after the Architect)
    context_refs: tuple[RecordRef, ...] = Field(default=(), max_length=_MANY)

    @model_validator(mode="after")
    def _same_task_scope(self) -> Self:
        task = self.task
        if (task.project_id, task.run_id, task.spec_version_id) != (
            self.project_id,
            self.run_id,
            self.spec_version_id,
        ):
            raise ValueError("the task belongs to another project, run or spec version")
        return self


class BuildTaskOutput(AgentIO):
    task_id: TaskId
    files: tuple[FileOutput, ...] = Field(default=(), max_length=_MANY)
    implementation_mapping: tuple[RequirementMapping, ...] = Field(default=(), max_length=_MANY)
    execution_requests: tuple[ExecutionRequest, ...] = Field(default=(), max_length=50)
    blockers: tuple[Blocker, ...] = Field(default=(), max_length=_MANY)

    @model_validator(mode="after")
    def _unique_files(self) -> Self:
        paths = [f.path.casefold() for f in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("each file may appear only once in an output")
        return self


class ArchitectInput(BuildTaskInput):
    feasibility_item_ids: tuple[FeasibilityItemId, ...] = Field(default=(), max_length=_MANY)


class ArchitectOutput(BuildTaskOutput):
    openapi: FileOutput | None = None
    adrs: tuple[FileOutput, ...] = Field(default=(), max_length=50)


class DatabaseDeveloperInput(BuildTaskInput):
    entity_ids: tuple[RecordRef, ...] = Field(default=(), max_length=_MANY)


class DatabaseDeveloperOutput(BuildTaskOutput):
    """Migrations are *files*; applying them is the harness's job (Spec §6.6)."""


class BackendDeveloperInput(BuildTaskInput):
    pass


class BackendDeveloperOutput(BuildTaskOutput):
    pass


class FrontendDeveloperInput(BuildTaskInput):
    pass


class FrontendDeveloperOutput(BuildTaskOutput):
    pass


# --------------------------------------------------------------------- 8. Testing & Validation (§6.9)


class TestMapping(Contract):
    __test__ = False

    test_id: Name  # e.g. "tests/api/test_orders.py::test_create_order"
    requirement_ids: tuple[TraceKey, ...] = Field(min_length=1, max_length=50)


class TesterInput(BuildTaskInput):
    pass


class TesterOutput(BuildTaskOutput):
    """No field records results: only the harness runner can (Spec §6.9)."""

    test_plan: FileOutput | None = None
    test_mappings: tuple[TestMapping, ...] = Field(default=(), max_length=_MANY)


# ------------------------------------------------------------------ 9. Debugger & Code Reviewer (§6.10)


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class ReviewFinding(Contract):
    severity: Severity
    path: RelPath
    description: ShortText


def is_test_path(path: str) -> bool:
    """True for files under a ``tests``/``test`` folder or named ``test_*`` / ``*_test`` / ``*.test.*``."""
    parts = path.casefold().split("/")
    name = parts[-1]
    stem = name.split(".", 1)[0]
    return (
        any(p in {"tests", "test", "__tests__"} for p in parts[:-1])
        or stem.startswith("test_")
        or stem.endswith("_test")
        or ".test." in name
        or ".spec." in name
    )


class DebuggerReviewerInput(BuildTaskInput):
    failed_test_run_id: TestRunId | None = None
    log_refs: tuple[RelPath, ...] = Field(default=(), max_length=50)
    test_maintenance_approval_id: ApprovalId | None = None  # only for an approved test-maintenance task


class DebuggerReviewerOutput(BuildTaskOutput):
    root_cause: ShortText | None = None
    review_findings: tuple[ReviewFinding, ...] = Field(default=(), max_length=_MANY)


# ------------------------------------------------------------------ 10. Documentation & Reporting (§6.11)

DOCUMENTER_ROOT_FILES = frozenset({"readme.md", "project_documentation.md"})


class DocumenterInput(AgentIO):
    spec_version_id: SpecVersionId
    task: AgentTask
    evidence_refs: tuple[RecordRef, ...] = Field(default=(), max_length=_MANY)


class DocumenterOutput(AgentIO):
    task_id: TaskId
    documents: tuple[FileOutput, ...] = Field(default=(), max_length=100)
    not_generated: tuple[Name, ...] = Field(default=(), max_length=100)  # marked, never invented

    @model_validator(mode="after")
    def _docs_only(self) -> Self:
        for doc in self.documents:
            folded = doc.path.casefold()
            if not (folded.startswith("docs/") or folded in DOCUMENTER_ROOT_FILES):
                raise ValueError(f"the Documenter may write only under docs/ or root docs, not '{doc.path}'")
        return self


# --------------------------------------------------------------------------------------- registry

AGENT_IO: dict[AgentRole, tuple[type[AgentIO], type[AgentIO]]] = {
    AgentRole.BRD_ANALYST: (BrdAnalystInput, BrdAnalystOutput),
    AgentRole.REQUIREMENTS_FINALIZER: (RequirementsFinalizerInput, RequirementsFinalizerOutput),
    AgentRole.HEAD: (HeadInput, HeadOutput),
    AgentRole.ARCHITECT: (ArchitectInput, ArchitectOutput),
    AgentRole.DATABASE_DEVELOPER: (DatabaseDeveloperInput, DatabaseDeveloperOutput),
    AgentRole.BACKEND_DEVELOPER: (BackendDeveloperInput, BackendDeveloperOutput),
    AgentRole.FRONTEND_DEVELOPER: (FrontendDeveloperInput, FrontendDeveloperOutput),
    AgentRole.TESTER: (TesterInput, TesterOutput),
    AgentRole.DEBUGGER_REVIEWER: (DebuggerReviewerInput, DebuggerReviewerOutput),
    AgentRole.DOCUMENTER: (DocumenterInput, DocumenterOutput),
}


def _fail(message: str) -> None:
    raise ContractViolationError(message)


def check_output_against_input(role: AgentRole, given: AgentIO, produced: AgentIO) -> None:  # noqa: PLR0912
    """Cross-checks between an agent's input and its output. Raises :class:`ContractViolationError`."""
    expected_in, expected_out = AGENT_IO[role]
    if type(given) is not expected_in or type(produced) is not expected_out:
        _fail(f"{role.value} must receive {expected_in.__name__} and return {expected_out.__name__}")
    if (produced.project_id, produced.run_id) != (given.project_id, given.run_id):
        _fail("output belongs to another project or run")

    if isinstance(given, BrdAnalystInput) and isinstance(produced, BrdAnalystOutput):
        allowed_chunks = set(given.chunk_ids)
        allowed_docs = set(given.document_ids)
        for ref in produced.all_source_refs():
            if ref.chunk_id not in allowed_chunks or ref.document_id not in allowed_docs:
                _fail(f"source {ref.chunk_id} was not in the material given to the analyst")

    elif isinstance(given, RequirementsFinalizerInput) and isinstance(produced, RequirementsFinalizerOutput):
        if produced.questions and given.answers_submitted:
            _fail("a second clarification round is not allowed (Spec §6.3)")
        critical = set(given.critical_gap_ids)
        for question in produced.questions:
            if question.gap_id not in critical:
                _fail(f"question {question.question_id} is not for a critical gap decided by code")

    elif isinstance(given, HeadInput) and isinstance(produced, HeadOutput):
        if produced.plan is not None:
            if produced.plan.spec_version_id != given.spec_version_id:
                _fail("the plan is not based on the approved spec version")
            missing = produced.plan.uncovered(given.in_scope_keys)
            if missing:
                _fail(f"the plan leaves in-scope items uncovered: {', '.join(sorted(missing))}")
        failed = set(given.failed_task_ids)
        for decision in produced.routing_decisions:
            if decision.task_id not in failed:
                _fail(f"routing decision for task {decision.task_id}, which is not a failed task")

    elif isinstance(given, BuildTaskInput) and isinstance(produced, BuildTaskOutput):
        _check_build_output(given, produced)

    elif (
        isinstance(given, DocumenterInput)
        and isinstance(produced, DocumenterOutput)
        and produced.task_id != given.task.task_id
    ):
        _fail("output is for a different task")


def _check_build_output(given: BuildTaskInput, produced: BuildTaskOutput) -> None:
    task = given.task
    if produced.task_id != task.task_id:
        _fail("output is for a different task")
    written = [f.path for f in produced.files]
    if isinstance(produced, ArchitectOutput):
        written += [f.path for f in (produced.openapi, *produced.adrs) if f is not None]
    if isinstance(produced, TesterOutput) and produced.test_plan is not None:
        written.append(produced.test_plan.path)
    for path in written:
        if not any(path_in_scope(path, scope) for scope in task.permitted_paths):
            _fail(f"'{path}' is outside the task's permitted paths")
    if isinstance(given, DebuggerReviewerInput) and given.test_maintenance_approval_id is None:
        touched = [p for p in written if is_test_path(p)]
        if touched:
            _fail(f"repair tasks may not edit tests without an approved test-maintenance task: {touched}")
    in_task = set(task.requirement_ids)
    for mapping in produced.implementation_mapping:
        if mapping.requirement_id not in in_task:
            _fail(f"mapping claims requirement {mapping.requirement_id}, which is not in the task")
    if isinstance(produced, TesterOutput):
        for test in produced.test_mappings:
            stray = [k for k in test.requirement_ids if k not in in_task]
            if stray:
                _fail(f"test {test.test_id} maps to requirements outside the task: {stray}")


ALL_AGENT_IO_MODELS: tuple[type[AgentIO], ...] = tuple(m for pair in AGENT_IO.values() for m in pair)

__all__ = [
    "AGENT_IO",
    "ALL_AGENT_IO_MODELS",
    "AgentIO",
    "ContractViolationError",
    "check_output_against_input",
    "is_test_path",
]
