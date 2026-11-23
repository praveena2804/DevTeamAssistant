"""Debugger & Code Reviewer: the agent's declaration (``AgentSpec``, HS-16; D-37 operation IDs).

The harness enforces everything declared here (central policy ∩ this spec ∩ task scope ∩ stage). This
package may import only ``devteam_core`` and ``devteam_harness.agent_sdk`` (CLAUDE.md §5).
"""

from devteam_core.contracts.agents import DebuggerReviewerInput, DebuggerReviewerOutput
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_harness.agent_sdk import AgentKind, AgentSpec, OperationSpec

SPEC = AgentSpec(
    role=AgentRole.DEBUGGER_REVIEWER,
    kind=AgentKind.BUILD,
    default_slot=ModelSlot.CODER,
    operations=(
        OperationSpec(operation_id="debugger_reviewer.repair", output_schema="RepairDraft"),
        OperationSpec(
            operation_id="debugger_reviewer.review_code", slot=ModelSlot.REVIEW, output_schema="DocumentDraft"
        ),
    ),
    prompt_id="debugger_reviewer.system",
    prompt_version=1,
    input_model=DebuggerReviewerInput,
    output_model=DebuggerReviewerOutput,
    tools=("read_project_file", "list_project_files", "write_project_file"),
    write_paths=("generated/backend/", "generated/frontend/", "generated/database/", "generated/config/"),
)
