"""Model catalogue and routing configuration: ``config/models.yaml`` (Part F; MODEL_ROUTING.md §3).

Validated when the platform starts; any failure stops start-up (MODEL_ROUTING §3.3):

1. every model named anywhere is declared in ``models:``;
2. every ``agents`` key is one of the ten canonical agents;
3. every ``operations`` key is declared by its agent (checked by the runtime against the agent registry,
   because this package cannot see agent specs);
4. every slot exists; a ``null`` slot inherits from another slot, without cycles;
5. an override names exactly one of ``slot`` or ``model``;
6. duplicate YAML keys are a load error (never "last one wins");
7. a model can be routed to only if ``approved`` **and** it has a ``benchmark_ref``;
8. a model's ``peak_ram_mb`` must fit the hardware profile's ``max_model_ram_mb``;
9. fallback ladders contain only approved chat models whose ``peak_ram_mb`` is not larger than the models
   they back up;

plus: the ``embedding`` slot is never overridden; embedding models are never used for chat and chat models
never for embeddings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_core.errors import ConfigError

MODEL_TAG = r"^[a-z0-9][a-z0-9._-]{0,80}(:[A-Za-z0-9._-]{1,40})?$"
CHAT_SLOTS = frozenset(s for s in ModelSlot if s is not ModelSlot.EMBEDDING)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Timeouts(_Strict):
    connect_s: float = Field(default=5, gt=0, le=60)
    read_s: float = Field(gt=0, le=3600)
    total_s: float = Field(gt=0, le=3600)


class ModelEntry(_Strict):
    kind: Literal["chat", "embedding"] = "chat"
    approved: bool = False
    benchmark_ref: str | None = None
    peak_ram_mb: int | None = Field(default=None, gt=0, le=131_072)
    num_ctx: int = Field(default=4096, ge=512, le=131_072)
    reduced_num_ctx: int | None = Field(default=None, ge=512, le=131_072)
    timeouts: Timeouts
    slow_after_s: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.reduced_num_ctx is not None and self.reduced_num_ctx >= self.num_ctx:
            raise ValueError("reduced_num_ctx must be smaller than num_ctx")
        if self.timeouts.read_s > self.timeouts.total_s:
            raise ValueError("timeouts.read_s cannot exceed timeouts.total_s")
        if self.slow_after_s is not None and self.slow_after_s > self.timeouts.total_s:
            raise ValueError("slow_after_s cannot exceed timeouts.total_s")
        if self.approved and (not self.benchmark_ref or self.peak_ram_mb is None):
            raise ValueError("an approved model needs a benchmark_ref and a measured peak_ram_mb (rule 7)")
        return self

    @property
    def routable(self) -> bool:
        return self.approved and bool(self.benchmark_ref)


class SlotRef(_Strict):
    model: str | None = Field(default=None, pattern=MODEL_TAG)
    inherit: ModelSlot | None = None


class Override(_Strict):
    slot: ModelSlot | None = None
    model: str | None = Field(default=None, pattern=MODEL_TAG)

    @model_validator(mode="after")
    def _exactly_one(self) -> Self:
        if (self.slot is None) == (self.model is None):
            raise ValueError("an override names exactly one of 'slot' or 'model' (rule 5)")
        if self.slot is ModelSlot.EMBEDDING:
            raise ValueError("the embedding slot cannot be chosen by an override")
        return self


class ModelCatalog(_Strict):
    schema_version: Literal[1] = 1
    default_slot: ModelSlot = ModelSlot.INSTRUCT
    ram_margin_mb: int = Field(default=512, ge=0, le=16_384)
    models: dict[str, ModelEntry]
    slots: dict[ModelSlot, SlotRef]
    fallback_ladders: dict[ModelSlot, tuple[str, ...]] = Field(default_factory=dict)
    agents: dict[AgentRole, Override] = Field(default_factory=dict)
    operations: dict[str, Override] = Field(default_factory=dict)

    # ---------------------------------------------------------------------------------- resolution

    def slot_model(self, slot: ModelSlot) -> str | None:
        """The model a slot resolves to (following ``inherit``), or ``None``."""
        seen: set[ModelSlot] = set()
        current: ModelSlot | None = slot
        while current is not None:
            if current in seen:
                raise ConfigError(f"models.yaml: slot inheritance cycle at '{current.value}' (rule 4)")
            seen.add(current)
            ref = self.slots.get(current)
            if ref is None:
                return None
            if ref.model is not None:
                return ref.model
            current = ref.inherit
        return None

    def entry(self, tag: str) -> ModelEntry:
        return self.models[tag]

    # ---------------------------------------------------------------------------------- validation

    def validate_rules(self, *, max_model_ram_mb: int | None) -> None:  # noqa: PLR0912 - one rule per branch
        problems: list[str] = []
        for slot in ModelSlot:
            if slot not in self.slots:
                problems.append(f"slot '{slot.value}' is missing (rule 4)")
        named: set[str] = set()
        for slot, ref in self.slots.items():
            if ref.model is not None:
                named.add(ref.model)
            if slot is ModelSlot.EMBEDDING and ref.inherit is not None:
                problems.append("the embedding slot cannot inherit from a chat slot")
        for ladder in self.fallback_ladders.values():
            named.update(ladder)
        named.update(o.model for o in (*self.agents.values(), *self.operations.values()) if o.model)
        problems.extend(
            f"model '{m}' is not declared in models: (rule 1)" for m in sorted(named - set(self.models))
        )
        if problems:
            raise ConfigError("models.yaml: " + "; ".join(problems))
        for slot in ModelSlot:
            tag = self.slot_model(slot)  # raises on cycles
            if tag is None:
                if slot in {ModelSlot.EMBEDDING, ModelSlot.REVIEW, ModelSlot.CODER_LARGE}:
                    continue
                problems.append(f"slot '{slot.value}' resolves to no model (rule 4)")
                continue
            entry = self.models[tag]
            wanted = "embedding" if slot is ModelSlot.EMBEDDING else "chat"
            if entry.kind != wanted:
                problems.append(f"slot '{slot.value}' needs a {wanted} model, '{tag}' is {entry.kind}")
            problems.extend(self._routable_problems(tag, f"slot '{slot.value}'", max_model_ram_mb))
        if self.slot_model(self.default_slot) is None:
            problems.append("default_slot resolves to no model (rule 4)")
        for key, override in (*((a.value, o) for a, o in self.agents.items()), *self.operations.items()):
            if override.model is not None:
                if self.models[override.model].kind != "chat":
                    problems.append(f"override '{key}' names an embedding model")
                problems.extend(
                    self._routable_problems(override.model, f"override '{key}'", max_model_ram_mb)
                )
        for slot, ladder in self.fallback_ladders.items():
            if slot is ModelSlot.EMBEDDING:
                problems.append("the embedding slot has no fallback ladder")
                continue
            base = self.slot_model(slot)
            base_ram = self.models[base].peak_ram_mb if base else None
            for tag in ladder:
                entry = self.models[tag]
                if entry.kind != "chat":
                    problems.append(f"ladder '{slot.value}': '{tag}' is not a chat model (rule 9)")
                problems.extend(self._routable_problems(tag, f"ladder '{slot.value}'", max_model_ram_mb))
                if base_ram is not None and entry.peak_ram_mb is not None and entry.peak_ram_mb > base_ram:
                    problems.append(
                        f"ladder '{slot.value}': '{tag}' needs more memory than '{base}' (rule 9)"
                    )
        if problems:
            raise ConfigError("models.yaml: " + "; ".join(problems))

    def _routable_problems(self, tag: str, where: str, max_ram: int | None) -> list[str]:
        entry = self.models[tag]
        out: list[str] = []
        if not entry.routable:
            out.append(f"{where}: model '{tag}' is not approved with a benchmark_ref (rule 7)")
        if max_ram is not None and entry.peak_ram_mb is not None and entry.peak_ram_mb > max_ram:
            out.append(
                f"{where}: model '{tag}' needs {entry.peak_ram_mb} MB > profile limit {max_ram} MB (rule 8)"
            )
        return out


class _NoDuplicatesLoader(yaml.SafeLoader):
    """Rule 6: a duplicate key is an error, never 'last one wins'."""


def _construct_mapping(
    loader: _NoDuplicatesLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _value in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise ConfigError(f"models.yaml: duplicate key '{key}' (rule 6)")
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_NoDuplicatesLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def parse_model_catalog(text: str, *, max_model_ram_mb: int | None = None) -> ModelCatalog:
    data = yaml.load(text, Loader=_NoDuplicatesLoader)  # noqa: S506 - SafeLoader subclass
    if not isinstance(data, dict):
        raise ConfigError("models.yaml must be a mapping")
    try:
        catalog = ModelCatalog.model_validate(data)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors(include_url=False)[:10]
        )
        raise ConfigError(f"models.yaml is invalid: {details}") from None
    catalog.validate_rules(max_model_ram_mb=max_model_ram_mb)
    return catalog


def load_model_catalog(config_dir: Path, *, max_model_ram_mb: int | None = None) -> ModelCatalog:
    path = config_dir / "models.yaml"
    if not path.is_file():
        raise ConfigError(f"model catalogue not found: {path}")
    return parse_model_catalog(path.read_text(encoding="utf-8"), max_model_ram_mb=max_model_ram_mb)


__all__ = [
    "CHAT_SLOTS",
    "ModelCatalog",
    "ModelEntry",
    "Override",
    "SlotRef",
    "Timeouts",
    "load_model_catalog",
    "parse_model_catalog",
]
