"""Architecture Agent: the agent's declaration (``AgentSpec``, HS-16; D-37 operation IDs).

The harness enforces everything declared here (central policy ∩ this spec ∩ task scope ∩ stage). This
package may import only ``devteam_core`` and ``devteam_harness.agent_sdk`` (CLAUDE.md §5).
"""

from devteam_core.contracts.agents import ArchitectInput, ArchitectOutput
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_harness.agent_sdk import AgentKind, AgentSpec, OperationSpec

SPEC = AgentSpec(
    role=AgentRole.ARCHITECT,
    kind=AgentKind.BUILD,
    default_slot=ModelSlot.INSTRUCT,
    operations=(OperationSpec(operation_id="architect.design", output_schema="DocumentDraft"),),
    prompt_id="architect.system",
    prompt_version=1,
    input_model=ArchitectInput,
    output_model=ArchitectOutput,
    tools=("read_project_file", "list_project_files", "write_project_file", "search_project_knowledge"),
    write_paths=("generated/docs/", "generated/contracts/"),
)
