"""Events written to audit and progress streams (Spec §11.4, roadmap HC-10/HC-21).

Events carry **metadata only**. A model-call event has token counts, durations and prompt *hashes*,
never prompt or answer text; a tool-call event has a short validated argument summary, never file
contents. Unknown fields are rejected, so content cannot be added by accident.
"""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, TypeAdapter, field_validator, model_validator

from devteam_core.contracts._base import (
    ApprovalId,
    AwareDatetime,
    Contract,
    EventId,
    Name,
    ProjectId,
    RelPath,
    RunId,
    Sha256,
    ShortText,
    TaskId,
)
from devteam_core.contracts.llm import Provider
from devteam_core.contracts.records import ApprovalDecision
from devteam_core.contracts.statuses import ApprovalType, RunStatus, TaskStatus
from devteam_core.contracts.tools import ActorRef, PolicyRuleId, ToolName, ToolOutcome

SummaryValue = Annotated[str, StringConstraints(max_length=200)]
MAX_SUMMARY_KEYS = 20


class EventBase(Contract):
    event_id: EventId
    ts: AwareDatetime
    project_id: ProjectId | None = None
    run_id: RunId | None = None
    task_id: TaskId | None = None
    actor: ActorRef


class RunTransitioned(EventBase):
    kind: Literal["run.transitioned"] = "run.transitioned"
    from_status: RunStatus
    to_status: RunStatus
    reason: ShortText | None = None


class TaskTransitioned(EventBase):
    kind: Literal["task.transitioned"] = "task.transitioned"
    from_status: TaskStatus
    to_status: TaskStatus
    reason: ShortText | None = None


class ToolCallRecorded(EventBase):
    kind: Literal["tool.called"] = "tool.called"
    tool: ToolName
    tool_version: int = Field(ge=1)
    outcome: ToolOutcome
    args_summary: dict[Annotated[str, StringConstraints(max_length=40)], SummaryValue] = Field(
        default_factory=dict
    )
    policy_rule_id: PolicyRuleId | None = None
    duration_ms: int = Field(ge=0)

    @field_validator("args_summary")
    @classmethod
    def _small(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_SUMMARY_KEYS:
            raise ValueError(f"argument summaries are limited to {MAX_SUMMARY_KEYS} keys")
        return value


class ModelCallRecorded(EventBase):
    kind: Literal["model.called"] = "model.called"
    provider: Provider
    model_id: Name
    operation_id: Name
    prompt_id: Name
    prompt_version: int = Field(ge=1)
    prompt_hash: Sha256
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    output_valid: bool
    attempt: int = Field(ge=1, le=4)


class ApprovalRecorded(EventBase):
    kind: Literal["approval.recorded"] = "approval.recorded"
    approval_id: ApprovalId
    approval_type: ApprovalType
    decision: ApprovalDecision

    @model_validator(mode="after")
    def _human_only(self) -> Self:
        if not self.actor.startswith("prn_"):
            raise ValueError("only a human principal can record an approval")
        return self


class FileChanged(EventBase):
    kind: Literal["file.changed"] = "file.changed"
    path: RelPath
    before_sha256: Sha256 | None = None
    after_sha256: Sha256 | None = None
    bytes_written: int = Field(ge=0)
    diff_ref: RelPath | None = None

    @model_validator(mode="after")
    def _a_change(self) -> Self:
        if self.before_sha256 is None and self.after_sha256 is None:
            raise ValueError("a file change needs a before or after checksum")
        return self


Event = Annotated[
    RunTransitioned
    | TaskTransitioned
    | ToolCallRecorded
    | ModelCallRecorded
    | ApprovalRecorded
    | FileChanged,
    Field(discriminator="kind"),
]
EVENT_ADAPTER: TypeAdapter[Event] = TypeAdapter(Event)
EVENT_TYPES: tuple[type[EventBase], ...] = (
    RunTransitioned,
    TaskTransitioned,
    ToolCallRecorded,
    ModelCallRecorded,
    ApprovalRecorded,
    FileChanged,
)
