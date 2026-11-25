"""Model router (GW-04; MODEL_ROUTING.md §3.2; D-06, D-37). Pure: no I/O.

Precedence, most specific first:

1. per-operation override (``operations``),
2. per-agent override (``agents``),
3. the slot the operation declares in its ``AgentSpec`` (or the agent's default slot),
4. the global ``default_slot`` (only if that slot resolves to no model).

An override names a slot or a model. The chosen ``RouteRule`` is recorded on every ``ModelCall``.

Recovery (Spec §9.4) is **not** part of precedence: :meth:`Router.recovery` lists the rungs to try after a
*resource* failure: the same model with a reduced context, then the slot's fallback ladder (smaller models
only). The embedding slot has no overrides and no ladder.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from devteam_core.config.models import ModelCatalog, ModelEntry
from devteam_core.contracts.llm import RecoveryStep, RouteRule
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.errors import ConfigError
from devteam_harness.errors import ResourceUnavailableError


@dataclass(frozen=True, slots=True)
class ModelChoice:
    tag: str
    entry: ModelEntry
    slot: ModelSlot
    rule: RouteRule
    step: RecoveryStep = RecoveryStep.NONE
    num_ctx: int | None = None  # reduced context for the REDUCED_CONTEXT rung


class Router:
    def __init__(self, catalog: ModelCatalog) -> None:
        self.catalog = catalog

    def check_operations(self, declared: Mapping[AgentRole, Iterable[str]]) -> None:
        """Rule 3: every operation override must be declared by its agent (from the agent registry)."""
        known = {op for ops in declared.values() for op in ops}
        unknown = sorted(set(self.catalog.operations) - known)
        if unknown:
            raise ConfigError(f"models.yaml: operations not declared by any agent: {unknown} (rule 3)")

    def resolve(self, role: AgentRole, operation_id: str, slot: ModelSlot) -> ModelChoice:
        if slot is ModelSlot.EMBEDDING:
            raise ConfigError("generation never routes to the embedding slot")
        override = self.catalog.operations.get(operation_id)
        rule = RouteRule.OPERATION
        if override is None:
            override = self.catalog.agents.get(role)
            rule = RouteRule.AGENT
        if override is not None:
            if override.model is not None:
                return self._choice(override.model, slot, rule)
            assert override.slot is not None  # noqa: S101 - validated: exactly one of slot/model
            slot = override.slot
            tag = self.catalog.slot_model(slot)
            if tag is not None:
                return self._choice(tag, slot, rule)
        tag = self.catalog.slot_model(slot)
        if tag is not None:
            return self._choice(tag, slot, RouteRule.SLOT)
        default = self.catalog.default_slot
        tag = self.catalog.slot_model(default)
        if tag is None:  # validated at start-up; defensive
            raise ConfigError("no model is configured for the default slot")
        return self._choice(tag, default, RouteRule.GLOBAL)

    def recovery(self, choice: ModelChoice) -> tuple[ModelChoice, ...]:
        """Rungs to try after a resource failure, in order (never a larger model, never external)."""
        rungs: list[ModelChoice] = []
        if choice.step is RecoveryStep.NONE and choice.entry.reduced_num_ctx is not None:
            rungs.append(
                ModelChoice(
                    choice.tag,
                    choice.entry,
                    choice.slot,
                    choice.rule,
                    RecoveryStep.REDUCED_CONTEXT,
                    num_ctx=choice.entry.reduced_num_ctx,
                )
            )
        for tag in self.catalog.fallback_ladders.get(choice.slot, ()):
            if tag == choice.tag:
                continue
            entry = self.catalog.models[tag]
            rungs.append(ModelChoice(tag, entry, choice.slot, choice.rule, RecoveryStep.SMALLER_MODEL))
        return tuple(rungs)

    def embedding(self) -> ModelChoice:
        tag = self.catalog.slot_model(ModelSlot.EMBEDDING)
        if tag is None:
            raise ResourceUnavailableError(
                "rag.embedding_not_configured", "no embedding model is configured (config/models.yaml)"
            )
        return self._choice(tag, ModelSlot.EMBEDDING, RouteRule.SLOT)

    def all_models(self) -> tuple[str, ...]:
        """Every model the platform may call (slots + ladders + overrides), for the installed-models check."""
        tags: set[str] = set()
        for slot in ModelSlot:
            tag = self.catalog.slot_model(slot)
            if tag is not None:
                tags.add(tag)
        for ladder in self.catalog.fallback_ladders.values():
            tags.update(ladder)
        overrides = (*self.catalog.agents.values(), *self.catalog.operations.values())
        tags.update(o.model for o in overrides if o.model)
        return tuple(sorted(tags))

    def _choice(self, tag: str, slot: ModelSlot, rule: RouteRule) -> ModelChoice:
        entry = self.catalog.models[tag]
        if not entry.routable:  # validated at start-up; defensive at run time too
            raise ConfigError(f"model '{tag}' is not approved for routing")
        return ModelChoice(tag, entry, slot, rule)


__all__ = ["ModelChoice", "Router"]
