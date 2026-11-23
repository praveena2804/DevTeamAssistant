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


class ModelResponse(Contract):
    request_id: ModelRequestId
    provider: Provider
    model_id: Name
    model_digest: Sha256 | None = None
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
