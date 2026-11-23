"""Requirements Finalizer behaviour (Part D minimal; real local model through the LLM Gateway, D-44).

* ``consolidate`` (answer contract ``ScopeDraft``): the model reviews the accepted requirement keys
  and may propose exclusions. An exclusion is accepted only for a key it was given **and** only after
  the single clarification round (the user's answers decide scope, Spec §5.4). Otherwise the answer
  is returned as rejected so the harness retries with that feedback (F-10a).
* ``draft_questions`` (answer contract ``QuestionsDraft``): one question per critical gap. Which gaps
  are critical is decided by code (``critical_gap_ids``), never by the model; Part D has no critical-gap
  producer yet (Requirements Policy Engine, Part I), so this path does not occur in Part D runs.

The requirement keys come from the material given; descriptions are not available to this agent's
v1 input contract, so the model sees keys only (Part I adds the requirement records).
"""

from __future__ import annotations

from typing import Any

from devteam_core.contracts.agents import RequirementsFinalizerInput, RequirementsFinalizerOutput
from devteam_core.contracts.drafts import ScopeDraft
from devteam_harness.agent_sdk import AgentContext, DataItem


class Behaviour:
    def run(self, ctx: AgentContext, given: Any) -> dict[str, Any] | str:
        assert isinstance(given, RequirementsFinalizerInput)  # noqa: S101 - checked by the executor
        if given.critical_gap_ids and not given.answers_submitted:
            return "clarification questions need the gap records, which arrive in Part I"
        keys = "\n".join(given.requirement_ids) or "(no requirement keys were accepted)"
        brief = (
            f"Accepted requirement keys:\n{keys}\n\n"
            f"Clarification answers submitted: {'yes' if given.answers_submitted else 'no'}"
        )
        answer = ctx.llm.generate("consolidate", DataItem("requirement_keys", brief))
        if not answer.valid or answer.parsed is None:
            return answer.text
        draft = ScopeDraft.model_validate(dict(answer.parsed))
        known = set(given.requirement_ids)
        unknown = [k for k in draft.excluded if k not in known]
        if unknown or (draft.excluded and not given.answers_submitted):
            return answer.text + "\n(rejected: only the user's clarification answers may exclude given keys)"
        output = RequirementsFinalizerOutput(
            project_id=given.project_id,
            run_id=given.run_id,
            excluded_requirement_ids=tuple(dict.fromkeys(draft.excluded)),
        )
        return output.model_dump(mode="json")


BEHAVIOUR = Behaviour()
