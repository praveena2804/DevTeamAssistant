"""Database Developer: the agent's declaration (``AgentSpec``, HS-16; D-37 operation IDs).

The harness enforces everything declared here (central policy ∩ this spec ∩ task scope ∩ stage). This
package may import only ``devteam_core`` and ``devteam_harness.agent_sdk`` (CLAUDE.md §5).
"""

from devteam_core.contracts.agents import DatabaseDeveloperInput, DatabaseDeveloperOutput
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_harness.agent_sdk import AgentKind, AgentSpec, OperationSpec

SPEC = AgentSpec(
    role=AgentRole.DATABASE_DEVELOPER,
    kind=AgentKind.BUILD,
    default_slot=ModelSlot.CODER,
    operations=(OperationSpec(operation_id="database_developer.generate_code"),),
    prompt_id="database_developer.system",
    prompt_version=1,
    input_model=DatabaseDeveloperInput,
    output_model=DatabaseDeveloperOutput,
    tools=("read_project_file", "list_project_files", "write_project_file"),
    write_paths=("generated/database/", "generated/backend/models/"),
)
