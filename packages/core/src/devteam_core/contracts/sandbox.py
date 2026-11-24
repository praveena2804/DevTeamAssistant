"""Sandbox and starter-kit contracts (HC-14; Part E plan §3.7).

Honesty rules enforced here, not by convention:

- a ``SandboxExecution`` is ``completed`` only with exit code 0, and ``failed`` only with a non-zero exit
  code; ``blocked`` means **nothing ran** (no exit code at all);
- a ``TemplateVerification`` can be ``passed`` only if every recipe completed and every recipe that
  reports tests actually executed at least one test with no failures or errors. Unexecuted tests can
  therefore never be recorded as passed.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from devteam_core.contracts._base import (
    AwareDatetime,
    Contract,
    Name,
    ProjectId,
    RelPath,
    RunId,
    Sha256,
    ShortText,
    _id,
)

SandboxExecutionId = Annotated[str, _id("sbx")]
TemplateVerificationId = Annotated[str, _id("tvf")]
RecipeName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,39}$")]
TemplateName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
ImageDigest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
SemVer = Annotated[str, StringConstraints(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
_PINNED = re.compile(r"^(@?[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)?)==([0-9][0-9A-Za-z.+-]*)$")


class SandboxExecutionStatus(StrEnum):
    COMPLETED = "completed"  # the process ran and exited 0
    FAILED = "failed"  # the process ran and exited non-zero
    TIMED_OUT = "timed_out"  # killed by the wall-clock watchdog
    OOM_KILLED = "oom_killed"  # killed by the memory limit
    BLOCKED = "blocked"  # a safety precondition failed: nothing ran
    CANCELLED = "cancelled"  # stopped on request


class TemplateVerificationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    STALE = "stale"  # template or image changed since this result


class ContainerLimits(Contract):
    memory_mb: int = Field(ge=64, le=8192)
    cpus: float = Field(gt=0, le=8)
    pids: int = Field(ge=16, le=4096)
    wall_clock_s: int = Field(ge=1, le=3600)
    tmpfs_mb: int = Field(ge=16, le=2048)
    work_copy_mb: int = Field(ge=1, le=4096)
    output_kb: int = Field(ge=1, le=16_384)


class TestCounts(Contract):
    __test__ = False  # not a pytest test class

    tests: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    errors: int = Field(ge=0)
    skipped: int = Field(ge=0)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.passed + self.failed + self.errors + self.skipped != self.tests:
            raise ValueError("test counts do not add up")
        return self

    @property
    def clean(self) -> bool:
        """At least one test executed and none failed or errored."""
        return self.passed > 0 and self.failed == 0 and self.errors == 0


class RecipeSpec(Contract):
    """An allowlisted command: fixed argv, fixed environment, declared outputs. Never a shell string."""

    recipe: RecipeName
    image: Literal["python_api", "node_web"]
    argv: tuple[Annotated[str, StringConstraints(min_length=1, max_length=300)], ...] = Field(min_length=1)
    workdir: Annotated[str, StringConstraints(pattern=r"^/work(/[a-z0-9_.-]+)*$")]
    env: dict[Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")], str]
    timeout_s: int = Field(ge=1, le=3600)
    reports_tests: bool = False
    outputs: tuple[Annotated[str, StringConstraints(pattern=r"^/work/\.devteam/out/[a-z0-9_.-]+$")], ...] = ()
    tool: Literal["request_build", "request_test_run", "request_migration_check"]

    @field_validator("argv")
    @classmethod
    def _no_shell(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if value[0] in {"sh", "bash", "/bin/sh", "/bin/bash", "npm", "npx", "pip", "pip3"}:
            raise ValueError("recipes never run a shell or a package manager")
        return value


class SandboxExecution(Contract):
    execution_id: SandboxExecutionId
    project_id: ProjectId | None = None  # None = platform work (template verification)
    run_id: RunId | None = None
    verification_id: TemplateVerificationId | None = None
    recipe: RecipeName
    image: Name
    image_digest: ImageDigest | None = None
    engine_version: Annotated[str, StringConstraints(max_length=40)] | None = None
    limits: ContainerLimits
    status: SandboxExecutionStatus
    exit_code: int | None = None
    duration_ms: int = Field(ge=0)
    peak_memory_mb: float | None = Field(default=None, ge=0)
    tests: TestCounts | None = None
    outputs: tuple[RelPath, ...] = ()
    reason: ShortText | None = None
    started_at: AwareDatetime
    finished_at: AwareDatetime

    @model_validator(mode="after")
    def _honest(self) -> Self:
        status = self.status
        if status is SandboxExecutionStatus.COMPLETED and self.exit_code != 0:
            raise ValueError("completed requires exit code 0")
        if status is SandboxExecutionStatus.FAILED and (self.exit_code is None or self.exit_code == 0):
            raise ValueError("failed requires a non-zero exit code")
        if status is SandboxExecutionStatus.BLOCKED and (
            self.exit_code is not None or self.tests is not None
        ):
            raise ValueError("blocked means nothing ran: no exit code, no tests")
        if status is not SandboxExecutionStatus.COMPLETED and self.reason is None:
            raise ValueError(f"{status.value} requires a reason")
        if (self.project_id is None) == (self.verification_id is None):
            raise ValueError("an execution belongs to exactly one project or one template verification")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at before started_at")
        return self


class RecipeOutcome(Contract):
    recipe: RecipeName
    status: SandboxExecutionStatus
    execution_id: SandboxExecutionId | None = None
    exit_code: int | None = None
    tests: TestCounts | None = None
    reports_tests: bool = False
    duration_ms: int = Field(default=0, ge=0)
    peak_memory_mb: float | None = Field(default=None, ge=0)
    reason: ShortText | None = None

    @property
    def succeeded(self) -> bool:
        if self.status is not SandboxExecutionStatus.COMPLETED or self.exit_code != 0:
            return False
        if self.reports_tests:
            return self.tests is not None and self.tests.clean
        return True


class TemplateVerification(Contract):
    verification_id: TemplateVerificationId
    template_id: TemplateName
    template_version: SemVer
    template_checksum: Sha256 | None = None
    status: TemplateVerificationStatus
    recipes: tuple[RecipeOutcome, ...] = ()
    image_digests: dict[Literal["python_api", "node_web"], ImageDigest] = Field(default_factory=dict)
    duration_ms: int | None = Field(default=None, ge=0)
    peak_memory_mb: float | None = Field(default=None, ge=0)
    requested_by: Name
    requested_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    reason: ShortText | None = None

    @model_validator(mode="after")
    def _honest(self) -> Self:
        status = self.status
        if status is TemplateVerificationStatus.PASSED:
            if not self.recipes or not all(outcome.succeeded for outcome in self.recipes):
                raise ValueError(
                    "passed requires every recipe to complete and every test recipe to pass tests"
                )
            if self.template_checksum is None or not self.image_digests:
                raise ValueError("passed requires the template checksum and image digests")
        if status is TemplateVerificationStatus.FAILED and all(outcome.succeeded for outcome in self.recipes):
            raise ValueError("failed requires at least one unsuccessful recipe")
        if (
            status in {TemplateVerificationStatus.BLOCKED, TemplateVerificationStatus.FAILED}
            and self.reason is None
        ):
            raise ValueError(f"{status.value} requires a reason")
        finished = status not in {TemplateVerificationStatus.QUEUED, TemplateVerificationStatus.RUNNING}
        if finished and status is not TemplateVerificationStatus.STALE and self.finished_at is None:
            raise ValueError("a finished verification needs finished_at")
        return self


class TemplateDependencies(Contract):
    python: tuple[str, ...] = ()
    node: tuple[str, ...] = ()

    @field_validator("python", "node")
    @classmethod
    def _pinned(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        for item in value:
            if not _PINNED.fullmatch(item):
                raise ValueError(f"dependency must be pinned as name==version: {item!r}")
        return value


class TemplateManifest(Contract):
    """``template.yaml`` of a starter kit (Part E plan §3.6)."""

    template_id: TemplateName
    version: SemVer
    description: ShortText
    frontend: Literal["react", "html"]
    backend: Literal["fastapi"]
    database: Literal["sqlite"]  # D-16: PostgreSQL arrives in Part K
    auth: Literal["none"]  # D-04
    writable_slots: tuple[Annotated[str, StringConstraints(min_length=1, max_length=200)], ...]
    tester_slots: tuple[Annotated[str, StringConstraints(min_length=1, max_length=200)], ...] = ()
    protected_files: tuple[Annotated[str, StringConstraints(min_length=1, max_length=200)], ...]
    self_test: tuple[RecipeName, ...] = Field(min_length=1)
    dependencies: TemplateDependencies
    app_runtime_dependencies: tuple[Annotated[str, StringConstraints(min_length=1, max_length=100)], ...]
    env_placeholders: dict[Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")], str]


class MigrationCheckRequest(Contract):
    recipe: Literal["alembic_upgrade", "alembic_autogenerate"]
    engine: Literal["sqlite"] = "sqlite"  # D-16: PostgreSQL requests are refused until Part K
    message: Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,40}$")] | None = None


SANDBOX_MODELS: tuple[type[Contract], ...] = (
    ContainerLimits,
    TestCounts,
    RecipeSpec,
    SandboxExecution,
    RecipeOutcome,
    TemplateVerification,
    TemplateDependencies,
    TemplateManifest,
    MigrationCheckRequest,
)
