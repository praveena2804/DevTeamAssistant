"""Model request/response shapes for the centralized LLM Gateway (HC-02; no LiteLLM, D-35).

Prompt-injection containment starts here:

- ``system`` messages must be ``trusted`` (written by the platform's prompt registry);
- untrusted content (BRD text, retrieved chunks, tool output, other agents' artefacts) may only travel
  as a ``user`` message, labelled with where it came from. The input guardrail (C.2) wraps it in
  delimiters and flags injection patterns; it is data, never instructions.

Generation limits are bounded by the approved F-9 ranges; the configured defaults live in
``config/budgets.yaml`` (C.4). Usage is recorded as numbers only; prompts and outputs are never
written to audit or traces.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Self

from pydantic import Field, StringConstraints, model_validator

from devteam_core.contracts._base import (
    Contract,
    ModelRequestId,
    Name,
    RunId,
    Sha256,
    ShortText,
    TaskId,
)
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.contracts.tasks import OperationId

MAX_MESSAGE_CHARS = 60_000  # ~ the whole 4,096-8,192 token window, a hard structural cap
MAX_MESSAGES = 64

# F-9a / F-9c approved ranges (AP-008)
MIN_OUTPUT_TOKENS, MAX_OUTPUT_TOKENS = 128, 2_048
MIN_CONTEXT_WINDOW, MAX_CONTEXT_WINDOW = 2_048, 8_192

ContentLabel = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,40}$")]
SchemaName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Za-z0-9]{1,80}$")]


class ChatRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Trust(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


class ChatMessage(Contract):
    role: ChatRole
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    trust: Trust
    label: ContentLabel | None = None  # e.g. "brd_chunk", "test_log"; required for untrusted content

    @model_validator(mode="after")
    def _trust_rules(self) -> Self:
        if self.role is ChatRole.SYSTEM and self.trust is not Trust.TRUSTED:
            raise ValueError("system messages must be trusted platform prompts")
        if self.trust is Trust.UNTRUSTED:
            if self.role is not ChatRole.USER:
                raise ValueError("untrusted content may only be sent as a user message")
            if self.label is None:
                raise ValueError("untrusted content must be labelled with its origin")
        return self


class ModelParams(Contract):
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1_024, ge=MIN_OUTPUT_TOKENS, le=MAX_OUTPUT_TOKENS)
    context_window: int = Field(default=4_096, ge=MIN_CONTEXT_WINDOW, le=MAX_CONTEXT_WINDOW)
    seed: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _fits(self) -> Self:
        if self.max_output_tokens >= self.context_window:
            raise ValueError("the answer budget must leave room for input inside the context window")
        return self


class ModelRequest(Contract):
    request_id: ModelRequestId
    role: AgentRole
    operation_id: OperationId
    slot: ModelSlot
    task_id: TaskId | None = None
    run_id: RunId | None = None
    messages: tuple[ChatMessage, ...] = Field(min_length=1, max_length=MAX_MESSAGES)
    output_schema: SchemaName | None = None  # name of the contract the answer must validate against
    params: ModelParams = Field(default_factory=ModelParams)
    prompt_id: Name
    prompt_version: int = Field(ge=1)
    prompt_hash: Sha256

    @model_validator(mode="after")
    def _shape(self) -> Self:
        if not self.operation_id.startswith(f"{self.role.value}."):
            raise ValueError(f"operation '{self.operation_id}' does not belong to agent '{self.role}'")
        if self.slot is ModelSlot.EMBEDDING:
            raise ValueError("embeddings use the gateway's embed() call, not generate()")
        system_positions = [i for i, m in enumerate(self.messages) if m.role is ChatRole.SYSTEM]
        if any(i != 0 for i in system_positions):
            raise ValueError("only the first message may be a system message")
        return self


class ModelUsage(Contract):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class Provider(StrEnum):
    FAKE = "fake"  # tests only (Part C)
    OLLAMA = "ollama"  # Part F, loopback only


class OutputValidation(Contract):
    valid: bool
    errors: tuple[ShortText, ...] = Field(default=(), max_length=20)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.valid == bool(self.errors):
            raise ValueError("a valid result has no errors; an invalid one lists them")
        return self


class RouteRule(StrEnum):
    """Which routing rule chose the model (MODEL_ROUTING §3.2); recorded on every ``ModelCall``."""

    OPERATION = "operation"
    AGENT = "agent"
    SLOT = "slot"
    GLOBAL = "global"


class RecoveryStep(StrEnum):
    """Where on the recovery ladder a call ran (Spec §9.4; Part F plan §6.2)."""

    NONE = "none"
    REDUCED_CONTEXT = "reduced_context"
    SMALLER_MODEL = "smaller_model"


SERVICE_PRINCIPAL = r"^service:[a-z][a-z0-9_]{1,40}$"
ModelTag = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,80}(:[A-Za-z0-9._-]{1,40})?$")]
EmbedPurpose = Annotated[str, StringConstraints(pattern=r"^rag\.(index|query)$")]
MAX_EMBED_INPUTS = 64
MAX_EMBED_CHARS = 8_000


class EmbedRequest(Contract):
    """An embedding call by a platform service (never an agent): indexing or a retrieval query (HC-08).

    Texts live in memory only; records keep their SHA-256 (``ModelCall.input_hash``), never the text.
    """

    request_id: ModelRequestId
    principal_id: Annotated[str, StringConstraints(pattern=SERVICE_PRINCIPAL)]
    purpose: EmbedPurpose
    project_id: Annotated[str, StringConstraints(pattern=r"^prj_[0-7][0-9A-HJKMNP-TV-Z]{25}$")]
    texts: tuple[Annotated[str, StringConstraints(min_length=1, max_length=MAX_EMBED_CHARS)], ...] = Field(
        min_length=1, max_length=MAX_EMBED_INPUTS
    )


class EmbedResponse(Contract):
    request_id: ModelRequestId
    model_id: ModelTag
    model_digest: Sha256 | None = None
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int = Field(ge=0)
    duration_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def _shape(self) -> Self:
        dims = {len(v) for v in self.vectors}
        if len(dims) > 1 or 0 in dims:
            raise ValueError("every embedding vector must have the same, non-zero dimension")
        return self


ModelCallId = Annotated[str, StringConstraints(pattern=r"^mcl_[0-7][0-9A-HJKMNP-TV-Z]{25}$")]


class ModelCall(Contract):
    """One model call (HC-05/GW-08), metadata only: **never prompt, input or answer text** (AC-F-01).

    ``project_id`` is ``None`` only for platform service calls with no project (evaluation, inventory),
    as for ``audit_events``. Exactly one of ``agent`` or ``service`` names who called.
    """

    call_id: ModelCallId
    project_id: Annotated[str, StringConstraints(pattern=r"^prj_[0-7][0-9A-HJKMNP-TV-Z]{25}$")] | None = None
    run_id: RunId | None = None
    task_id: TaskId | None = None
    agent: AgentRole | None = None
    service: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,40}$")] | None = None
    kind: Annotated[str, StringConstraints(pattern=r"^(generate|embed)$")]
    operation_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{1,40}\.[a-z][a-z0-9_]{1,60}$")]
    slot: ModelSlot
    model_tag: ModelTag
    model_digest: Sha256 | None = None
    route_rule: RouteRule
    recovery_step: RecoveryStep = RecoveryStep.NONE
    prompt_id: Name | None = None
    prompt_version: int | None = Field(default=None, ge=1)
    prompt_hash: Sha256 | None = None
    input_hash: Sha256
    schema_name: SchemaName | None = None
    cache_hit: bool = False
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    free_ram_mb: int | None = Field(default=None, ge=0)
    outcome: Annotated[str, StringConstraints(pattern=r"^(valid|invalid|error|refused)$")]
    error_code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{1,60}$")] | None = None
    attempt: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def _who(self) -> Self:
        if (self.agent is None) == (self.service is None):
            raise ValueError("a model call is made by exactly one agent or one platform service")
        if self.agent is not None and self.task_id is None:
            raise ValueError("an agent's model call belongs to a task")
        if self.kind == "embed" and self.slot is not ModelSlot.EMBEDDING:
            raise ValueError("embeddings use the embedding slot")
        if self.kind == "generate" and self.slot is ModelSlot.EMBEDDING:
            raise ValueError("generation never uses the embedding slot")
        if (self.outcome in {"error", "refused"}) != (self.error_code is not None):
            raise ValueError("an error or refusal carries an error code; a completed call does not")
        return self


class ModelResponse(Contract):
    request_id: ModelRequestId
    provider: Provider
    model_id: Name
    model_digest: Sha256 | None = None
    route_rule: RouteRule | None = None
    recovery_step: RecoveryStep = RecoveryStep.NONE
    cache_hit: bool = False
    output_text: str = Field(max_length=MAX_MESSAGE_CHARS)  # in memory only; never audited or traced
    parsed: dict[str, Any] | None = None
    usage: ModelUsage
    duration_ms: int = Field(ge=0)
    attempt: int = Field(default=1, ge=1, le=4)
    validation: OutputValidation

    @model_validator(mode="after")
    def _parsed_only_if_valid(self) -> Self:
        if self.parsed is not None and not self.validation.valid:
            raise ValueError("an invalid answer cannot carry a parsed result")
        return self
