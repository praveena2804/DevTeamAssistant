"""Provenance and source references (Spec §6.1, §6.2; roadmap §13 C.1).

Every analysed item says where it came from:

- ``brd_fact``: stated in the BRD. Must cite at least one source chunk.
- ``user_answer``: given by the user in the clarification form. Must reference the answer.
- ``assumption``: a minor, disclosed, overridable default (Spec §5.4.2). Must give a rationale.
- ``recommendation``: a suggestion (for example a catalog alternative). Must give a rationale.

Agents cite **chunk IDs**; code resolves page/section/paragraph from the stored chunk (Spec §6.2
"implementation method"), so a model cannot invent page numbers.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from devteam_core.contracts._base import AnswerId, ChunkId, Contract, DocumentId, ShortText


class Provenance(StrEnum):
    BRD_FACT = "brd_fact"
    USER_ANSWER = "user_answer"
    ASSUMPTION = "assumption"
    RECOMMENDATION = "recommendation"


# Spec §5.4.1 "Never assume": business rules, features, technologies, roles, permissions, entities,
# login, integrations, deployment, and material security/privacy/retention requirements must come from
# the BRD or explicit user input.
NEVER_ASSUME = frozenset({Provenance.BRD_FACT, Provenance.USER_ANSWER})

MAX_SOURCE_REFS = 50


class SourceRef(Contract):
    """Points at one extracted chunk (Part B). Page/section/paragraph are filled in by code."""

    document_id: DocumentId
    chunk_id: ChunkId
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    section: ShortText | None = None
    paragraph_start: int | None = Field(default=None, ge=1)
    paragraph_end: int | None = Field(default=None, ge=1)
    quote: ShortText | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def _ranges(self) -> Self:
        for start, end, label in (
            (self.page_start, self.page_end, "page"),
            (self.paragraph_start, self.paragraph_end, "paragraph"),
        ):
            if end is not None and (start is None or end < start):
                raise ValueError(f"{label}_end requires {label}_start and must not precede it")
        return self


class Sourced(Contract):
    """Mixin for records that must state their provenance."""

    provenance: Provenance
    source_refs: tuple[SourceRef, ...] = Field(default=(), max_length=MAX_SOURCE_REFS)
    answer_id: AnswerId | None = None
    rationale: ShortText | None = None

    @model_validator(mode="after")
    def _provenance_rules(self) -> Self:
        allowed = type(self).permitted_provenance()
        if self.provenance not in allowed:
            names = ", ".join(sorted(p.value for p in allowed))
            raise ValueError(
                f"{type(self).__name__} must come from the BRD or the user ({names}); "
                f"'{self.provenance.value}' is not allowed (Spec §5.4.1 never assume)"
            )
        match self.provenance:
            case Provenance.BRD_FACT:
                if not self.source_refs:
                    raise ValueError("a brd_fact must cite at least one source chunk")
                if self.answer_id is not None:
                    raise ValueError("a brd_fact must not reference a user answer")
            case Provenance.USER_ANSWER:
                if self.answer_id is None:
                    raise ValueError("a user_answer must reference the clarification answer")
            case Provenance.ASSUMPTION | Provenance.RECOMMENDATION:
                if self.rationale is None:
                    raise ValueError(f"an {self.provenance.value} must state its rationale")
                if self.answer_id is not None:
                    raise ValueError(f"an {self.provenance.value} must not reference a user answer")
        return self

    @classmethod
    def permitted_provenance(cls) -> frozenset[Provenance]:
        return frozenset(Provenance)


class NeverAssumed(Sourced):
    """Records covered by Spec §5.4.1: only ``brd_fact`` or ``user_answer`` are accepted."""

    @classmethod
    def permitted_provenance(cls) -> frozenset[Provenance]:
        return NEVER_ASSUME
