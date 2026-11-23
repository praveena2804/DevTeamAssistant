"""Debugger & Code Reviewer behaviour (Part D minimal; real local model through the LLM Gateway, D-44).

``repair`` (answer contract ``RepairDraft``): the model states the suspected root cause and writes repair
notes for the failure named in the task. The notes are written through the ``write_project_file`` tool
to ``REPAIR_NOTES.md`` inside the task's first permitted folder (never under ``tests/``: this agent edits
tests only in an approved test-maintenance task). Part N replaces this with real repairs.

In Part D the Test Runner and Integration services are stand-ins that never report a failure, so a
repair task arises only when post-task verification routes one here.
"""

from __future__ import annotations

from typing import Any

from devteam_core.contracts.agents import (
    Blocker,
    DebuggerReviewerInput,
    DebuggerReviewerOutput,
    FileOutput,
    is_test_path,
)
from devteam_core.contracts.drafts import RepairDraft
from devteam_harness.agent_sdk import AgentContext, DataItem

HEADER = (
    "> Written by the Debugger & Code Reviewer agent with a local AI model during a DevTeam AI Part D run.\n"
    "> Repair notes only: nothing was changed, built, run or tested.\n\n"
)


class Behaviour:
    def run(self, ctx: AgentContext, given: Any) -> dict[str, Any] | str:
        assert isinstance(given, DebuggerReviewerInput)  # noqa: S101 - checked by the executor
        task = given.task
        folders = [p for p in task.permitted_paths if p.endswith("/") and not is_test_path(p + "x")]
        brief = "\n".join(
            [
                f"Failure to repair: {task.objective}",
                "Requirement keys: " + (", ".join(task.requirement_ids) or "none"),
                "Log files: " + (", ".join(given.log_refs) or "none"),
            ]
        )
        answer = ctx.llm.generate("repair", DataItem("failure_report", brief))
        if not answer.valid or answer.parsed is None:
            return answer.text
        draft = RepairDraft.model_validate(dict(answer.parsed))
        files: tuple[FileOutput, ...] = ()
        blockers: tuple[Blocker, ...] = ()
        if folders:
            path = folders[0] + "REPAIR_NOTES.md"
            content = (
                HEADER + f"## Suspected root cause\n\n{draft.root_cause}\n\n## Notes\n\n{draft.content}\n"
            )
            written = ctx.tools.call("write_project_file", path=path, content=content)
            if written.ok and isinstance(written.value, dict):
                files = (FileOutput(path=path, sha256=str(written.value["sha256_after"])),)
            else:
                blockers = (Blocker(description=f"the notes could not be written ({written.error_code})"),)
        else:
            blockers = (Blocker(description="the task names no folder this agent may write"),)
        output = DebuggerReviewerOutput(
            project_id=given.project_id,
            run_id=given.run_id,
            task_id=task.task_id,
            files=files,
            blockers=blockers,
            root_cause=draft.root_cause,
        )
        return output.model_dump(mode="json")


BEHAVIOUR = Behaviour()
