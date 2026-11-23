"""Head Agent behaviour (Part D; real local model through the LLM Gateway, D-44).

The plan itself is **not** written by the model in Part D: orchestration builds it with the Head's
deterministic template planner and checks it with the plan validator (``template_planner``,
``validator``; roadmap §6.3). The model call here (operation ``plan``, answer contract
``SummaryDraft``) writes the one-line progress summary of the scope it is given. Part J adds the model
planner, whose plans go through the same validator.
"""

from __future__ import annotations

from typing import Any

from devteam_core.contracts.agents import HeadInput, HeadOutput
from devteam_core.contracts.drafts import SummaryDraft
from devteam_harness.agent_sdk import AgentContext, DataItem


class Behaviour:
    def run(self, ctx: AgentContext, given: Any) -> dict[str, Any] | str:
        assert isinstance(given, HeadInput)  # noqa: S101 - checked by the executor
        keys = ", ".join(given.in_scope_keys) or "none"
        brief = (
            f"Approved specification version: {given.spec_version_id}\n"
            f"In-scope requirement keys ({len(given.in_scope_keys)}): {keys}\n"
            "Build order: architecture, database, backend, frontend, then tests."
        )
        answer = ctx.llm.generate("plan", DataItem("build_scope", brief))
        if not answer.valid or answer.parsed is None:
            return answer.text
        draft = SummaryDraft.model_validate(dict(answer.parsed))
        output = HeadOutput(project_id=given.project_id, run_id=given.run_id, progress_summary=draft.summary)
        return output.model_dump(mode="json")


BEHAVIOUR = Behaviour()
