"""Model answer contracts (Part D, D-44): what a local model is asked to return for each operation.

They are deliberately small and flat so a small local model can fill them reliably. The LLM Gateway
sends the JSON Schema to Ollama as the required answer format and validates the answer against the
contract; the agent then maps a valid answer onto its full v1 output contract (``agents``), where the
remaining rules apply (citations only to given material, write scope, no claims).

A model never sees or returns record IDs it could invent: material is numbered (``[1]``, ``[2]`` ...)
and the agent maps the numbers back to the real chunk IDs. A number outside the given material is
dropped, never guessed.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from devteam_core.contracts._base import Contract, TraceKey
from devteam_core.contracts.records import GapCategory

DraftText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4_000)]
DraftLine = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
MaterialNumber = Annotated[int, Field(ge=1, le=500)]


class ChecklistFinding(Contract):
    category: GapCategory
    found: bool
    evidence: tuple[MaterialNumber, ...] = Field(default=(), max_length=5)  # numbers of the cited pieces


class RequirementDraft(Contract):
    kind: Literal["functional", "non_functional"]
    description: DraftLine
    evidence: MaterialNumber


class AnalysisDraft(Contract):
    """BRD Analyst ``analyse_section``: completeness checklist and requirements, with cited pieces."""

    checklist: tuple[ChecklistFinding, ...] = Field(default=(), max_length=len(GapCategory))
    requirements: tuple[RequirementDraft, ...] = Field(default=(), max_length=10)


class ScopeDraft(Contract):
    """Requirements Finalizer ``consolidate``: requirement keys to leave out of scope, with reasons."""

    excluded: tuple[TraceKey, ...] = Field(default=(), max_length=100)
    note: DraftLine


class QuestionsDraft(Contract):
    """Requirements Finalizer ``draft_questions``: one question per critical gap, in the given order."""

    questions: tuple[DraftLine, ...] = Field(default=(), max_length=20)


class SummaryDraft(Contract):
    """Head Agent ``plan`` / ``classify_failure``: a short progress summary of facts it was given."""

    summary: DraftLine


class DocumentDraft(Contract):
    """Build agents, Debugger and Documenter: one Markdown document written by the model."""

    title: DraftLine
    content: DraftText


class RepairDraft(Contract):
    """Debugger & Code Reviewer ``repair``: suspected root cause and repair notes."""

    root_cause: DraftLine
    content: DraftText


DRAFT_MODELS: tuple[type[Contract], ...] = (
    ChecklistFinding,
    RequirementDraft,
    AnalysisDraft,
    ScopeDraft,
    QuestionsDraft,
    SummaryDraft,
    DocumentDraft,
    RepairDraft,
)
