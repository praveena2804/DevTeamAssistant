"""Frontend Developer: the agent's declaration (``AgentSpec``, HS-16; D-37 operation IDs).

The harness enforces everything declared here (central policy ∩ this spec ∩ task scope ∩ stage). This
package may import only ``devteam_core`` and ``devteam_harness.agent_sdk`` (CLAUDE.md §5).
"""

from devteam_core.contracts.agents import FrontendDeveloperInput, FrontendDeveloperOutput
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_harness.agent_sdk import AgentKind, AgentSpec, OperationSpec

SPEC = AgentSpec(
    role=AgentRole.FRONTEND_DEVELOPER,
    kind=AgentKind.BUILD,
    default_slot=ModelSlot.CODER,
    operations=(
        OperationSpec(operation_id="frontend_developer.generate_code", output_schema="DocumentDraft"),
    ),
    prompt_id="frontend_developer.system",
    prompt_version=1,
    input_model=FrontendDeveloperInput,
    output_model=FrontendDeveloperOutput,
    tools=("read_project_file", "list_project_files", "write_project_file"),
    write_paths=("generated/frontend/",),
)
