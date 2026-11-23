"""Head Agent: the agent's declaration (``AgentSpec``, HS-16; D-37 operation IDs).

The harness enforces everything declared here (central policy ∩ this spec ∩ task scope ∩ stage). This
package may import only ``devteam_core`` and ``devteam_harness.agent_sdk`` (CLAUDE.md §5).
"""

from devteam_core.contracts.agents import HeadInput, HeadOutput
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_harness.agent_sdk import AgentKind, AgentSpec, OperationSpec

SPEC = AgentSpec(
    role=AgentRole.HEAD,
    kind=AgentKind.ORCHESTRATION,
    default_slot=ModelSlot.INSTRUCT,
    operations=(
        OperationSpec(operation_id="head.plan"),
        OperationSpec(operation_id="head.classify_failure"),
    ),
    prompt_id="head.system",
    prompt_version=1,
    input_model=HeadInput,
    output_model=HeadOutput,
    tools=("list_project_files",),
    write_paths=(),
)
