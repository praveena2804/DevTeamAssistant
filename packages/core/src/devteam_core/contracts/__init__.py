"""Shared contracts (HS-06, roadmap §13 C.1): every interface agents, harness and services exchange.

``CONTRACT_MODELS`` is the single registry used for JSON Schema export and snapshot tests. A test
asserts that every ``Contract`` subclass defined in this package is registered, so nothing can be
added without appearing in the exported schemas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from devteam_core.contracts._base import SCHEMA_VERSION, Contract
from devteam_core.contracts.agents import (
    AGENT_IO,
    ALL_AGENT_IO_MODELS,
    Blocker,
    ChecklistItem,
    ContractViolationError,
    ExecutionRequest,
    FileOutput,
    RequirementMapping,
    ReviewFinding,
    RoutingDecision,
    TestMapping,
    check_output_against_input,
)
from devteam_core.contracts.base import SourceRef
from devteam_core.contracts.drafts import DRAFT_MODELS
from devteam_core.contracts.errors import ErrorCategory, ErrorRef, is_auto_retryable
from devteam_core.contracts.events import EVENT_ADAPTER, EVENT_TYPES
from devteam_core.contracts.llm import (
    ChatMessage,
    EmbedRequest,
    EmbedResponse,
    ModelCall,
    ModelParams,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    OutputValidation,
)
from devteam_core.contracts.orchestration import (
    DispatchRecord,
    GateDecision,
    GateResume,
    PlanIssue,
    PlanValidationReport,
    ServiceOutcome,
)
from devteam_core.contracts.records import DOMAIN_RECORDS, EntityAttribute, EntityRelationship
from devteam_core.contracts.sandbox import SANDBOX_MODELS
from devteam_core.contracts.state import RunState
from devteam_core.contracts.statuses import EXPECTED_AGENT_COUNT, AgentRole
from devteam_core.contracts.tasks import AgentTask, TaskPlan
from devteam_core.contracts.tools import ToolCall, ToolResult

SCHEMA_DIR = Path(__file__).parent / "schemas"

_REGISTERED: tuple[type[Contract], ...] = (
    SourceRef,
    ErrorRef,
    *DOMAIN_RECORDS,
    EntityAttribute,
    EntityRelationship,
    AgentTask,
    TaskPlan,
    *ALL_AGENT_IO_MODELS,
    ChecklistItem,
    FileOutput,
    ExecutionRequest,
    RequirementMapping,
    Blocker,
    RoutingDecision,
    TestMapping,
    ReviewFinding,
    ToolCall,
    ToolResult,
    ChatMessage,
    ModelParams,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    OutputValidation,
    EmbedRequest,
    EmbedResponse,
    ModelCall,
    *EVENT_TYPES,
    RunState,
    GateDecision,
    GateResume,
    DispatchRecord,
    PlanIssue,
    PlanValidationReport,
    ServiceOutcome,
    *DRAFT_MODELS,
    *SANDBOX_MODELS,
)
CONTRACT_MODELS: dict[str, type[Contract]] = {model.__name__: model for model in _REGISTERED}


def contract_version(model: type[Contract]) -> int:
    """The model's own ``schema_version`` (1 unless the contract has been bumped, e.g. ``RunState`` v2)."""
    version = model.model_fields["schema_version"].default
    if not isinstance(version, int) or version < SCHEMA_VERSION:
        raise TypeError(f"{model.__name__} has no valid schema_version")
    return version


def export_json_schemas() -> dict[str, dict[str, Any]]:
    """JSON Schema of every registered contract, keyed by file name ``<Model>.v<version>.json``."""
    return {
        f"{name}.v{contract_version(model)}.json": model.model_json_schema(mode="validation")
        for name, model in sorted(CONTRACT_MODELS.items())
    }


def render_schema(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


__all__ = [
    "AGENT_IO",
    "CONTRACT_MODELS",
    "EVENT_ADAPTER",
    "EXPECTED_AGENT_COUNT",
    "SCHEMA_DIR",
    "SCHEMA_VERSION",
    "AgentRole",
    "Contract",
    "ContractViolationError",
    "ErrorCategory",
    "check_output_against_input",
    "contract_version",
    "export_json_schemas",
    "is_auto_retryable",
    "render_schema",
]
