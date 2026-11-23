"""BRD Analyst behaviour (Part D minimal; real local model through the LLM Gateway, D-44).

1. Reads the given chunks through the ``read_extracted_document`` tool (project-scoped, audited).
2. Numbers them ``[1]`` … ``[n]`` and asks the model (operation ``analyse_section``, answer contract
   ``AnalysisDraft``) for the 13-item completeness checklist and up to ten requirements, each citing
   the number of the piece it comes from.
3. Maps the numbers back to the real chunk IDs. A citation outside the given pieces is dropped, so a
   checklist item without valid evidence stays "missing or unclear" and a requirement without a valid
   citation is not recorded (Spec §5.4.1: never assume).

An invalid model answer is returned as-is, so the harness rejects it and retries with feedback
(F-10a). Nothing is invented when the model fails. Part H replaces this with the full analysis.
"""

from __future__ import annotations

from typing import Any

from devteam_core.contracts.agents import (
    BrdAnalystInput,
    BrdAnalystOutput,
    ChecklistItem,
    ChecklistStatus,
)
from devteam_core.contracts.base import Provenance, SourceRef
from devteam_core.contracts.drafts import AnalysisDraft
from devteam_core.contracts.records import GapCategory, Requirement, RequirementType
from devteam_harness.agent_sdk import AgentContext, DataItem

MAX_PIECES = 12
MAX_PIECE_CHARS = 700
PAGE = 20


def _read_pieces(ctx: AgentContext, given: BrdAnalystInput) -> list[tuple[str, str, str]]:
    wanted = set(given.chunk_ids)
    pieces: list[tuple[str, str, str]] = []
    for document_id in given.document_ids:
        offset = 0
        while len(pieces) < MAX_PIECES:
            answer = ctx.tools.call(
                "read_extracted_document", document_id=document_id, offset=offset, limit=PAGE
            )
            value = answer.value if answer.ok and isinstance(answer.value, dict) else {}
            chunks = value.get("chunks") or []
            for chunk in chunks:
                if chunk.get("chunk_id") in wanted and len(pieces) < MAX_PIECES:
                    text = " ".join(str(chunk.get("text", "")).split())[:MAX_PIECE_CHARS]
                    if text:
                        pieces.append((document_id, str(chunk["chunk_id"]), text))
            offset += len(chunks)
            if not chunks or offset >= int(value.get("total", 0)):
                break
    return pieces


class Behaviour:
    def run(self, ctx: AgentContext, given: Any) -> dict[str, Any] | str:
        assert isinstance(given, BrdAnalystInput)  # noqa: S101 - the executor checked the contract
        pieces = _read_pieces(ctx, given)
        if not pieces:
            return "no readable material was found for the given chunks"
        material = "\n\n".join(f"[{n}] {text}" for n, (_, _, text) in enumerate(pieces, start=1))
        answer = ctx.llm.generate("analyse_section", DataItem("brd_material", material))
        if not answer.valid or answer.parsed is None:
            return answer.text
        draft = AnalysisDraft.model_validate(dict(answer.parsed))

        def cite(number: int) -> SourceRef | None:
            if 1 <= number <= len(pieces):
                document_id, chunk_id, _ = pieces[number - 1]
                return SourceRef(document_id=document_id, chunk_id=chunk_id)
            return None

        checklist = []
        for category in GapCategory:
            finding = next((f for f in draft.checklist if f.category is category), None)
            refs: list[SourceRef] = []
            for number in finding.evidence if finding is not None else ():
                ref = cite(number)
                if ref is not None and ref not in refs:
                    refs.append(ref)
            found = finding is not None and finding.found and bool(refs)
            checklist.append(
                ChecklistItem(
                    category=category,
                    status=ChecklistStatus.FOUND if found else ChecklistStatus.MISSING_OR_UNCLEAR,
                    source_refs=tuple(refs) if found else (),
                )
            )

        requirements = []
        counters = {"functional": 0, "non_functional": 0}
        for item in draft.requirements:
            ref = cite(item.evidence)
            if ref is None:
                continue  # an uncited requirement would be an assumption
            counters[item.kind] += 1
            prefix = "FR" if item.kind == "functional" else "NFR"
            requirements.append(
                Requirement(
                    requirement_id=f"{prefix}-{counters[item.kind]:03d}",
                    project_id=given.project_id,
                    type=RequirementType(item.kind),
                    description=item.description,
                    provenance=Provenance.BRD_FACT,
                    source_refs=(ref,),
                )
            )

        output = BrdAnalystOutput(
            project_id=given.project_id,
            run_id=given.run_id,
            requirements=tuple(requirements),
            checklist=tuple(checklist),
        )
        return output.model_dump(mode="json")


BEHAVIOUR = Behaviour()
