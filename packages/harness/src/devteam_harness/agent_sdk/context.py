"""What an agent receives (roadmap §5.2): an ``AgentContext`` with controlled access only.

The context exposes:

- ``ctx.llm.generate(operation, data)``: a model call for one of the agent's **declared** operations.
  ``data`` is untrusted content (BRD text, files, other agents' artefacts); the harness wraps and labels
  it. The agent cannot write system prompts, choose a model, change limits or name another agent's
  operation;
- ``ctx.tools.call(tool, **args)``: a policy-checked tool call made under the identity the executor
  issued for this task. The agent cannot choose its principal or task;
- ``ctx.input`` (the validated input contract), ``ctx.feedback`` (rule violations of its previous
  attempt, written by the harness), ``ctx.log`` (a bound logger), ``ctx.role``, ``ctx.task_id``.

It has no file handles, database session, settings, secrets or HTTP client. The access objects hold
only a closure created by the executor; the static rules for ``agents/**`` forbid reaching into
private attributes, ``getattr`` and similar introspection.

A denied tool call or model request raises ``PolicyDeniedError``. The executor records every denial
itself, so catching the exception does not hide it: the attempt still ends as ``policy_denied``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from devteam_core.contracts.agents import AgentIO
from devteam_core.contracts.statuses import AgentRole


@dataclass(frozen=True, slots=True)
class DataItem:
    """One piece of untrusted content for a model call, labelled with its origin (e.g. ``brd_chunk``)."""

    label: str
    content: str


@dataclass(frozen=True, slots=True)
class ModelAnswer:
    text: str
    parsed: Mapping[str, Any] | None
    valid: bool
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolAnswer:
    ok: bool
    value: Any = None
    error_code: str | None = None
    message: str | None = None


GenerateFn = Callable[[str, tuple[DataItem, ...]], ModelAnswer]
CallToolFn = Callable[[str, int, dict[str, Any]], ToolAnswer]


class LLMAccess:
    __slots__ = ("_generate",)

    def __init__(self, generate: GenerateFn) -> None:
        self._generate = generate

    def generate(self, operation: str, data: Sequence[DataItem] | DataItem = ()) -> ModelAnswer:
        items = (data,) if isinstance(data, DataItem) else tuple(data)
        if not all(isinstance(item, DataItem) for item in items):
            raise TypeError("model input must be DataItem values")
        return self._generate(operation, items)


class ToolAccess:
    __slots__ = ("_call",)

    def __init__(self, call: CallToolFn) -> None:
        self._call = call

    def call(self, tool: str, version: int = 1, **args: Any) -> ToolAnswer:
        return self._call(tool, version, dict(args))


@dataclass(frozen=True, slots=True)
class AgentContext:
    role: AgentRole
    task_id: str
    input: AgentIO
    llm: LLMAccess
    tools: ToolAccess
    log: Any
    feedback: tuple[str, ...] = field(default=())


__all__ = ["AgentContext", "DataItem", "LLMAccess", "ModelAnswer", "ToolAccess", "ToolAnswer"]
