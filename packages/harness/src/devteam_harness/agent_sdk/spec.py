"""``AgentSpec``: an agent's declaration (HS-16; roadmap §5.2; D-37 operation IDs).

The spec is checked against ceilings in code when it is created, so an agent package cannot declare
more than the specification allows:

- the input/output models must be exactly the agent's v1 contracts (``AGENT_IO``);
- every operation ID belongs to the agent (``<role>.<operation>``) and is unique;
- tools are from the Part C catalogue only (no shell, ever);
- write paths lie inside the agent's write ceiling (the Head Agent and discovery agents write nothing);
- the agent kind matches its role; embeddings are never an agent operation.

A spec grants nothing by itself: effective permission is still central policy ∩ spec ∩ task ∩ stage.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from devteam_core.contracts._base import PathScope, path_in_scope
from devteam_core.contracts.agents import AGENT_IO, AgentIO
from devteam_core.contracts.llm import SchemaName
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.contracts.tasks import BudgetProfileName, OperationId
from devteam_harness.guardrails.config import KNOWN_TOOLS, WRITE_CEILINGS


class AgentKind(StrEnum):
    DISCOVERY = "discovery"
    ORCHESTRATION = "orchestration"
    BUILD = "build"
    DELIVERY = "delivery"


ROLE_KIND: dict[AgentRole, AgentKind] = {
    AgentRole.BRD_ANALYST: AgentKind.DISCOVERY,
    AgentRole.REQUIREMENTS_FINALIZER: AgentKind.DISCOVERY,
    AgentRole.HEAD: AgentKind.ORCHESTRATION,
    AgentRole.ARCHITECT: AgentKind.BUILD,
    AgentRole.DATABASE_DEVELOPER: AgentKind.BUILD,
    AgentRole.BACKEND_DEVELOPER: AgentKind.BUILD,
    AgentRole.FRONTEND_DEVELOPER: AgentKind.BUILD,
    AgentRole.TESTER: AgentKind.BUILD,
    AgentRole.DEBUGGER_REVIEWER: AgentKind.BUILD,
    AgentRole.DOCUMENTER: AgentKind.DELIVERY,
}


class OperationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: OperationId
    slot: ModelSlot | None = None  # None = the agent's default slot
    output_schema: SchemaName | None = None  # contract name the model's answer must validate against

    @model_validator(mode="after")
    def _no_embeddings(self) -> Self:
        if self.slot is ModelSlot.EMBEDDING:
            raise ValueError("embeddings are not an agent operation")
        return self


class AgentSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: AgentRole
    kind: AgentKind
    default_slot: ModelSlot
    operations: tuple[OperationSpec, ...] = Field(min_length=1, max_length=20)
    prompt_id: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,80}$")
    prompt_version: int = Field(ge=1)
    input_model: type[AgentIO]
    output_model: type[AgentIO]
    tools: tuple[str, ...] = Field(default=(), max_length=20)
    write_paths: tuple[PathScope, ...] = Field(default=(), max_length=20)
    budget_profile: BudgetProfileName = "default"

    @model_validator(mode="after")
    def _within_ceilings(self) -> Self:
        if ROLE_KIND[self.role] is not self.kind:
            raise ValueError(f"{self.role.value} is a {ROLE_KIND[self.role].value} agent")
        if self.default_slot is ModelSlot.EMBEDDING:
            raise ValueError("the embedding slot cannot be an agent's default slot")
        if (self.input_model, self.output_model) != AGENT_IO[self.role]:
            raise ValueError(f"{self.role.value} must use its v1 input/output contracts")
        ids = [op.operation_id for op in self.operations]
        if len(set(ids)) != len(ids):
            raise ValueError("operation IDs must be unique")
        foreign = [op for op in ids if not op.startswith(f"{self.role.value}.")]
        if foreign:
            raise ValueError(f"operations {foreign} do not belong to {self.role.value}")
        unknown = sorted(set(self.tools) - KNOWN_TOOLS)
        if unknown:
            raise ValueError(f"unknown tools {unknown} (the catalogue has no shell tool)")
        if len(set(self.tools)) != len(self.tools):
            raise ValueError("duplicate tools")
        ceiling = WRITE_CEILINGS[self.role]
        outside = [p for p in self.write_paths if not any(path_in_scope(p, c) for c in ceiling)]
        if outside:
            raise ValueError(f"write paths {outside} exceed the {self.role.value} ceiling")
        return self

    def operation(self, operation_id: str) -> OperationSpec | None:
        return next((op for op in self.operations if op.operation_id == operation_id), None)

    @property
    def operation_ids(self) -> frozenset[str]:
        return frozenset(op.operation_id for op in self.operations)
