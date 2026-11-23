"""Prompt and skill registry interfaces (HC-16, HS-15; D-36). The file-backed registry comes in Part F.

- A prompt is identified by ``(prompt_id, version)`` and carries the SHA-256 of its text. Versions are
  immutable: registering different text under an existing version is refused, and a stored template
  whose text no longer matches its hash is refused when read (tampering).
- A **skill** is versioned guidance text attached to an agent role (and optionally some operations).
  **A skill grants no permission**: the model has no field for tools, paths, stages, budgets or approvals,
  and unknown fields are rejected. The harness stays the security boundary.

The in-memory implementations serve the executor tests and the Part C demonstration.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Annotated, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from devteam_core.contracts.statuses import AgentRole
from devteam_core.contracts.tasks import OperationId
from devteam_harness.errors import FatalError

MAX_PROMPT_CHARS = 20_000
MAX_SKILL_CHARS = 8_000
PromptName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.]{1,80}$")]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PromptTemplate(_Frozen):
    prompt_id: PromptName
    version: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def create(cls, prompt_id: str, version: int, text: str) -> Self:
        return cls(prompt_id=prompt_id, version=version, text=text, sha256=sha256_text(text))

    @model_validator(mode="after")
    def _hash_matches(self) -> Self:
        if sha256_text(self.text) != self.sha256:
            raise ValueError("the prompt text does not match its recorded hash")
        return self


class SkillSpec(_Frozen):
    """Guidance only. Deliberately no tools/paths/stages/budget/approval fields (D-36)."""

    skill_id: PromptName
    version: int = Field(ge=1)
    applies_to: tuple[AgentRole, ...] = Field(min_length=1, max_length=10)
    operations: tuple[OperationId, ...] = Field(default=(), max_length=50)  # empty = every operation
    text: str = Field(min_length=1, max_length=MAX_SKILL_CHARS)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if sha256_text(self.text) != self.sha256:
            raise ValueError("the skill text does not match its recorded hash")
        roles = {r.value for r in self.applies_to}
        for op in self.operations:
            if op.split(".", 1)[0] not in roles:
                raise ValueError(f"operation '{op}' does not belong to the skill's agents")
        return self


class PromptRegistry(Protocol):
    def get(self, prompt_id: str, version: int) -> PromptTemplate: ...


class SkillRegistry(Protocol):
    def skills_for(self, role: AgentRole, operation_id: str) -> tuple[SkillSpec, ...]: ...


class PromptNotFoundError(FatalError):
    def __init__(self, prompt_id: str, version: int) -> None:
        super().__init__("prompt.not_found", f"prompt {prompt_id} v{version} is not registered")


class InMemoryPromptRegistry:
    def __init__(self) -> None:
        self._items: dict[tuple[str, int], PromptTemplate] = {}
        self._lock = threading.Lock()

    def register(self, template: PromptTemplate) -> PromptTemplate:
        key = (template.prompt_id, template.version)
        with self._lock:
            existing = self._items.get(key)
            if existing is not None:
                if existing.sha256 != template.sha256:
                    raise FatalError(
                        "prompt.version_immutable", f"{key[0]} v{key[1]} already exists with other text"
                    )
                return existing  # the first registration stays the stored one
            self._items[key] = template
        return template

    def get(self, prompt_id: str, version: int) -> PromptTemplate:
        template = self._items.get((prompt_id, version))
        if template is None:
            raise PromptNotFoundError(prompt_id, version)
        if sha256_text(template.text) != template.sha256:  # defence in depth against in-memory tampering
            raise FatalError("prompt.tampered", f"{prompt_id} v{version} no longer matches its hash")
        return template


class InMemorySkillRegistry:
    def __init__(self, skills: tuple[SkillSpec, ...] = ()) -> None:
        self._skills = tuple(skills)

    def skills_for(self, role: AgentRole, operation_id: str) -> tuple[SkillSpec, ...]:
        return tuple(
            s
            for s in self._skills
            if role in s.applies_to and (not s.operations or operation_id in s.operations)
        )


__all__ = [
    "InMemoryPromptRegistry",
    "InMemorySkillRegistry",
    "PromptNotFoundError",
    "PromptRegistry",
    "PromptTemplate",
    "SkillRegistry",
    "SkillSpec",
    "sha256_text",
]
