"""Architecture Agent behaviour (Part D minimal; real local model through the LLM Gateway, D-44).

The model (operation ``architect.design``, answer contract ``DocumentDraft``) writes architecture notes
for the approved requirements. The document goes through the policy-checked ``write_project_file`` tool to
``generated/docs/ARCHITECTURE.md``, inside this agent's write scope, under a header saying a local model
wrote it and that nothing was built, run, tested or verified. The output reports the file by the checksum
the harness computed. Part J replaces this with real architecture and API contracts.

The model is given the task (objective, requirement keys, done criteria) and, if present, the frozen
requirement list ``generated/spec/REQUIREMENTS.md`` written by the Spec Freeze service. Both travel as
labelled, untrusted data.
"""

from __future__ import annotations

from typing import Any

from devteam_core.contracts.agents import ArchitectInput, ArchitectOutput, Blocker, FileOutput
from devteam_core.contracts.drafts import DocumentDraft
from devteam_harness.agent_sdk import AgentContext, DataItem

DOCUMENT_PATH = "generated/docs/ARCHITECTURE.md"
SPEC_PATH = "generated/spec/REQUIREMENTS.md"
HEADER = (
    "> Written by the Architecture Agent with a local AI model during a DevTeam AI Part D run.\n"
    "> Design notes only: not built, not run, not tested and not verified.\n\n"
)


def _brief(ctx: AgentContext, given: ArchitectInput) -> tuple[DataItem, ...]:
    task = given.task
    lines = [
        f"Objective: {task.objective}",
        "Requirement keys: " + (", ".join(task.requirement_ids) or "none"),
        "Done when: " + "; ".join(task.done_criteria),
    ]
    items = [DataItem("task_brief", "\n".join(lines))]
    spec = ctx.tools.call("read_project_file", path=SPEC_PATH)
    if spec.ok and isinstance(spec.value, dict) and spec.value.get("content"):
        items.append(DataItem("requirements", str(spec.value["content"])[:6000]))
    return tuple(items)


class Behaviour:
    def run(self, ctx: AgentContext, given: Any) -> dict[str, Any] | str:
        assert isinstance(given, ArchitectInput)  # noqa: S101 - checked by the executor
        answer = ctx.llm.generate("design", _brief(ctx, given))
        if not answer.valid or answer.parsed is None:
            return answer.text
        draft = DocumentDraft.model_validate(dict(answer.parsed))
        content = HEADER + f"# {draft.title}\n\n{draft.content}\n"
        written = ctx.tools.call("write_project_file", path=DOCUMENT_PATH, content=content)
        files: tuple[FileOutput, ...] = ()
        blockers: tuple[Blocker, ...] = ()
        if written.ok and isinstance(written.value, dict):
            files = (FileOutput(path=DOCUMENT_PATH, sha256=str(written.value["sha256_after"])),)
        else:
            blockers = (Blocker(description=f"the document could not be written ({written.error_code})"),)
        output = ArchitectOutput(
            project_id=given.project_id,
            run_id=given.run_id,
            task_id=given.task.task_id,
            files=files,
            blockers=blockers,
        )
        return output.model_dump(mode="json")


BEHAVIOUR = Behaviour()
