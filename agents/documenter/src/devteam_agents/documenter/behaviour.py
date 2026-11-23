"""Documentation & Reporting Agent behaviour (Part D minimal; real local model, D-44).

``write_section`` (answer contract ``DocumentDraft``): the model writes run notes **only from the facts
it is given** (the task objective, which orchestration fills with the run's recorded task and service
outcomes, and the evidence references). The notes go through the ``write_project_file`` tool to
``generated/docs/RUN_NOTES.md`` and are reported as ``docs/RUN_NOTES.md`` (Documenter paths are
relative to ``generated/``). Anything not given is listed as not generated, never invented (Spec §6.11).
Part O replaces this with the real documentation set.
"""

from __future__ import annotations

from typing import Any

from devteam_core.contracts.agents import DocumenterInput, DocumenterOutput, FileOutput
from devteam_core.contracts.drafts import DocumentDraft
from devteam_harness.agent_sdk import AgentContext, DataItem

WRITE_PATH = "generated/docs/RUN_NOTES.md"
REPORTED_PATH = "docs/RUN_NOTES.md"
NOT_GENERATED = ("README.md", "PROJECT_DOCUMENTATION.md", "API reference", "test report")
HEADER = (
    "> Written by the Documentation & Reporting agent with a local AI model during a DevTeam AI Part D run,\n"
    "> from the run's recorded facts only. Nothing in this run was built, tested or verified.\n\n"
)


class Behaviour:
    def run(self, ctx: AgentContext, given: Any) -> dict[str, Any] | str:
        assert isinstance(given, DocumenterInput)  # noqa: S101 - checked by the executor
        task = given.task
        facts = "\n".join(
            [
                f"Run facts: {task.objective}",
                "Requirement keys: " + (", ".join(task.requirement_ids) or "none"),
                "Evidence references: " + (", ".join(given.evidence_refs) or "none"),
            ]
        )
        answer = ctx.llm.generate("write_section", DataItem("run_facts", facts))
        if not answer.valid or answer.parsed is None:
            return answer.text
        draft = DocumentDraft.model_validate(dict(answer.parsed))
        content = HEADER + f"# {draft.title}\n\n{draft.content}\n"
        written = ctx.tools.call("write_project_file", path=WRITE_PATH, content=content)
        documents: tuple[FileOutput, ...] = ()
        not_generated: tuple[str, ...] = NOT_GENERATED
        if written.ok and isinstance(written.value, dict):
            documents = (FileOutput(path=REPORTED_PATH, sha256=str(written.value["sha256_after"])),)
        else:
            not_generated = (*NOT_GENERATED, "RUN_NOTES.md")
        output = DocumenterOutput(
            project_id=given.project_id,
            run_id=given.run_id,
            task_id=task.task_id,
            documents=documents,
            not_generated=not_generated,
        )
        return output.model_dump(mode="json")


BEHAVIOUR = Behaviour()
