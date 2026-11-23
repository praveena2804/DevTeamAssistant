"""API models for runs, gates, progress, trace and history (Part D, LG-08; published in openapi.json).

Every model carries metadata only: identifiers, statuses, counts, durations and short reasons. Prompts,
model answers, document text and file contents are never part of a run response; generated files are
listed by path and checksum. ``run_mode`` and ``verified`` say plainly that a Part D run is a trial run
that verifies nothing.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

_STRICT = ConfigDict(extra="forbid")


class RunStart(BaseModel):
    model_config = _STRICT
    run_kind: Literal["initial", "change_request"] = "initial"


class RunOut(BaseModel):
    id: str
    project_id: str
    run_kind: str
    status: str
    stage: str
    mode: str
    current_node: str | None
    pending_gate: str | None
    control_request: str
    spec_version_id: str | None
    created_at: datetime
    updated_at: datetime
    verified: Literal[False] = False
    label: str = "Part D trial run: real local AI, stand-in services, nothing built, tested or verified"


class RunDetail(RunOut):
    completed_tasks: int
    total_tasks: int
    last_event_seq: int
    blocker: str | None = None


class GateInfo(BaseModel):
    run_id: str
    gate: str
    approval_type: str
    roles: list[str]
    spec_version_id: str | None = None
    spec_checksum: str | None = None
    requirements: list[dict[str, str]] = Field(default_factory=list)  # key + description (approval gate)
    notes: list[str] = Field(default_factory=list)


class GateDecisionIn(BaseModel):
    model_config = _STRICT
    decision: Literal["approved", "rejected"]


class GateDecisionOut(BaseModel):
    run_id: str
    gate: str
    approval_id: str
    decision: str


class FileRef(BaseModel):
    path: str
    sha256: str


class TaskOut(BaseModel):
    task_id: str
    agent: str
    operation_id: str
    status: str
    attempts: int
    max_attempts: int
    depends_on: list[str]
    requirement_ids: list[str]
    blocker_reason: str | None
    files: list[FileRef]
    agent_returned_output: bool
    harness_verified: Literal[False] = False


class RunEventOut(BaseModel):
    seq: int
    kind: str
    ts: datetime
    payload: dict[str, Any]


class SpanOut(BaseModel):
    span_id: str
    parent_span_id: str | None
    name: str
    start_ts: datetime
    duration_ms: int
    status: str
    attributes: dict[str, Any]


class TraceOut(BaseModel):
    run_id: str
    trace_id: str | None
    spans: list[SpanOut]


class HistoryItem(BaseModel):
    ts: datetime
    action: str
    outcome: str
    principal_id: str | None
    target_type: str | None
    target_id: str | None
    details: dict[str, Any]


class NodeTiming(BaseModel):
    node: str
    runs: int
    total_ms: int


class MetricsOut(BaseModel):
    run_id: str
    model_calls: int
    input_tokens: int
    output_tokens: int
    model_ms: int
    nodes: list[NodeTiming]
    latest_samples: dict[str, int]
    queue_depth: int
