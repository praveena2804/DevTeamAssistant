"""Context Manager (HC-08; Spec §9.4 "minimum relevant project/RAG context for each agent"; plan §6.5).

Assembles what one model call may see, within token budgets per role:

1. **required** items (the task input and records the step needs) always go first and are never trimmed;
   if they alone exceed the input budget the call is refused (``budget_exhausted``), never silently cut;
2. **retrieved** chunks by score, whole chunks only, up to the role's retrieval budget;
3. **memory** notes, whole notes only, up to the role's memory budget.

Truncation drops whole items from the end of each list, never the inside of an item. The token estimate is
the platform's conservative estimate (or a calibrated one supplied by the caller). Agents start using this in
Part H; in Part F it is the tested building block.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from devteam_core.contracts.statuses import AgentRole
from devteam_harness.budgets.manager import estimate_tokens
from devteam_harness.errors import BudgetExhaustedError

Kind = Literal["required", "retrieved", "memory"]


@dataclass(frozen=True, slots=True)
class ContextItem:
    kind: Kind
    label: str
    text: str
    score: float = 0.0
    reference: str | None = None


@dataclass(frozen=True, slots=True)
class ContextBudget:
    input_tokens: int
    retrieval_tokens: int
    memory_tokens: int


@dataclass(frozen=True, slots=True)
class Assembled:
    items: tuple[ContextItem, ...]
    tokens: int
    dropped: tuple[str, ...] = field(default_factory=tuple)  # labels of items left out


DISCOVERY = frozenset({AgentRole.BRD_ANALYST, AgentRole.REQUIREMENTS_FINALIZER})


class ContextManager:
    def __init__(
        self, budgets: Mapping[AgentRole, ContextBudget], *, estimate: Callable[[str], int] = estimate_tokens
    ) -> None:
        self._budgets = dict(budgets)
        self._estimate = estimate

    def budget(self, role: AgentRole) -> ContextBudget:
        return self._budgets[role]

    def assemble(
        self,
        role: AgentRole,
        *,
        required: Sequence[ContextItem] = (),
        retrieved: Sequence[ContextItem] = (),
        memory: Sequence[ContextItem] = (),
    ) -> Assembled:
        budget = self._budgets[role]
        items: list[ContextItem] = list(required)
        used = sum(self._estimate(i.text) for i in required)
        if used > budget.input_tokens:
            raise BudgetExhaustedError("context_required", budget.input_tokens, used)
        dropped: list[str] = []
        for pool, cap in (
            (sorted(retrieved, key=lambda i: i.score, reverse=True), budget.retrieval_tokens),
            (list(memory), budget.memory_tokens),
        ):
            spent = 0
            for item in pool:
                cost = self._estimate(item.text)
                if spent + cost <= cap and used + cost <= budget.input_tokens:
                    items.append(item)
                    spent += cost
                    used += cost
                else:
                    dropped.append(item.label)
        return Assembled(items=tuple(items), tokens=used, dropped=tuple(dropped))


def budgets_for_roles(
    input_tokens: int, *, discovery: tuple[int, int], build: tuple[int, int], documenter: tuple[int, int]
) -> dict[AgentRole, ContextBudget]:
    """Per-role budgets from ``context:`` settings: (retrieval, memory) per role group."""
    out: dict[AgentRole, ContextBudget] = {}
    for role in AgentRole:
        retrieval, memory = (
            discovery if role in DISCOVERY else documenter if role is AgentRole.DOCUMENTER else build
        )
        out[role] = ContextBudget(input_tokens, retrieval, memory)
    return out


__all__ = ["Assembled", "ContextBudget", "ContextItem", "ContextManager", "budgets_for_roles"]
