"""Tool call and tool result shapes (HC-06, Spec §11.2).

The executor and tool layer (C.3/C.4) build these; agents never construct a principal themselves.
Arguments are JSON values with a hard size cap, so a tool call cannot carry a document-sized payload
into the audit trail.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Self

from pydantic import Field, JsonValue, StringConstraints, field_validator, model_validator

from devteam_core.contracts._base import AuditEventId, Contract, TaskId, ToolCallId
from devteam_core.contracts.errors import ErrorCategory, ErrorRef
from devteam_core.contracts.statuses import AgentRole

# Structural ceilings at the top of the approved F-9i range (10 MB per file write) plus envelope overhead.
# The configured limits (default 1 MB) are enforced by the Controlled File Service (C.3).
MAX_TOOL_ARGS_BYTES = 10 * 1024 * 1024 + 64 * 1024
MAX_TOOL_VALUE_BYTES = 10 * 1024 * 1024 + 64 * 1024

ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{2,63}$")]
PolicyRuleId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{1,80}$")]

_ROLES = "|".join(r.value for r in AgentRole)
AGENT_PRINCIPAL_PATTERN = rf"^agent:({_ROLES}):tsk_[0-7][0-9A-HJKMNP-TV-Z]{{25}}$"
SERVICE_PRINCIPAL_PATTERN = r"^service:[a-z][a-z0-9_]{1,40}$"
HUMAN_PRINCIPAL_PATTERN = r"^prn_[0-7][0-9A-HJKMNP-TV-Z]{25}$"
ActorRef = Annotated[
    str,
    StringConstraints(
        pattern=f"({AGENT_PRINCIPAL_PATTERN})|({SERVICE_PRINCIPAL_PATTERN})|({HUMAN_PRINCIPAL_PATTERN})"
    ),
]


def agent_principal(role: AgentRole, task_id: str) -> str:
    return f"agent:{role.value}:{task_id}"


def _json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class SideEffect(StrEnum):
    """Side-effect classification (Spec §11.2)."""

    NONE = "none"
    READ = "read"
    WRITE_WORKSPACE = "write_workspace"
    EXECUTE = "execute"
    EXTERNAL = "external"


class ToolCall(Contract):
    call_id: ToolCallId
    tool: ToolName
    tool_version: int = Field(ge=1)
    principal: ActorRef
    task_id: TaskId | None = None
    args: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("args")
    @classmethod
    def _bounded(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if _json_size(value) > MAX_TOOL_ARGS_BYTES:
            raise ValueError(f"tool arguments exceed {MAX_TOOL_ARGS_BYTES} bytes")
        return value

    @model_validator(mode="after")
    def _agent_bound_to_task(self) -> Self:
        if self.principal.startswith("agent:") and (
            self.task_id is None or not self.principal.endswith(f":{self.task_id}")
        ):
            raise ValueError("an agent principal is only valid for its own task")
        return self


class ToolOutcome(StrEnum):
    OK = "ok"
    DENIED = "denied"
    ERROR = "error"


class ToolResult(Contract):
    call_id: ToolCallId
    outcome: ToolOutcome
    value: JsonValue = None
    error: ErrorRef | None = None
    policy_rule_id: PolicyRuleId | None = None
    audit_event_id: AuditEventId

    @field_validator("value")
    @classmethod
    def _bounded(cls, value: JsonValue) -> JsonValue:
        if value is not None and _json_size(value) > MAX_TOOL_VALUE_BYTES:
            raise ValueError(f"tool result exceeds {MAX_TOOL_VALUE_BYTES} bytes")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        match self.outcome:
            case ToolOutcome.OK:
                if self.error is not None:
                    raise ValueError("a successful result has no error")
            case ToolOutcome.DENIED:
                if self.error is None or self.error.category is not ErrorCategory.POLICY_DENIED:
                    raise ValueError("a denial must carry a policy_denied error")
                if self.policy_rule_id is None or self.value is not None:
                    raise ValueError("a denial names the deciding rule and returns no value")
            case ToolOutcome.ERROR:
                if self.error is None or self.value is not None:
                    raise ValueError("an error result carries an error and no value")
        return self
