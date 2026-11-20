"""``RunState``: the LangGraph run state (roadmap §6.8, final contract).

It holds **references only** (IDs, checksums, enums). Domain facts live in SQLite. Approval
enforcement layer: a run in the build or delivery stage must carry the approved spec version and
checksum (the build-entry guard re-verifies them against SQLite in Part D/I).
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from devteam_core.contracts._base import (
    ArtifactId,
    Contract,
    PlanId,
    ProjectId,
    RecordRef,
    RunId,
    Sha256,
    SpecVersionId,
    TaskId,
    TraceIdHex,
)
from devteam_core.contracts.errors import ErrorRef
from devteam_core.contracts.statuses import SPEC_BOUND_STAGES, GateName, Stage


class RunState(Contract):
    project_id: ProjectId
    run_id: RunId
    run_kind: Literal["initial", "change_request"]
    stage: Stage
    spec_version_id: SpecVersionId | None = None  # set only by freeze_spec
    spec_checksum: Sha256 | None = None
    contract_version_id: ArtifactId | None = None
    plan_id: PlanId | None = None
    current_task_id: TaskId | None = None
    pending_gate: GateName | None = None
    blocker_ids: tuple[RecordRef, ...] = Field(default=(), max_length=200)
    last_error: ErrorRef | None = None
    trace_id: TraceIdHex

    @model_validator(mode="after")
    def _approval_bound(self) -> Self:
        if (self.spec_version_id is None) != (self.spec_checksum is None):
            raise ValueError("spec_version_id and spec_checksum are set together")
        if self.stage in SPEC_BOUND_STAGES and self.spec_version_id is None:
            raise ValueError(f"the {self.stage.value} stage requires an approved, frozen spec version")
        if self.run_kind == "change_request" and self.spec_version_id is None:
            raise ValueError("a change request runs against an existing frozen spec")
        if self.stage is Stage.INTAKE and self.plan_id is not None:
            raise ValueError("no plan can exist during intake")
        return self
