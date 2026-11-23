"""Agent SDK (HS-16): the **only** harness module agent packages may import.

Agents may use exactly the names in ``__all__`` (the registry lives in ``agent_sdk.registry`` and is
for the platform, not for agents); the static rules for ``agents/**`` reject any other
import from the harness (import-linter, Ruff banned-API and the AST rules in the security suite).
"""

from typing import Any, Protocol

from devteam_core.contracts.agents import AgentIO
from devteam_harness.agent_sdk.context import AgentContext, DataItem, ModelAnswer, ToolAnswer
from devteam_harness.agent_sdk.spec import AgentKind, AgentSpec, OperationSpec


class AgentBehaviour(Protocol):
    """An agent's behaviour (Part D stubs, Part H onward real). Returns its raw output for the guards."""

    def run(self, ctx: AgentContext, given: AgentIO) -> dict[str, Any] | str: ...


__all__ = [
    "AgentBehaviour",
    "AgentContext",
    "AgentKind",
    "AgentSpec",
    "DataItem",
    "ModelAnswer",
    "OperationSpec",
    "ToolAnswer",
]
