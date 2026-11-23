"""Documentation & Reporting Agent: the agent's declaration (``AgentSpec``, HS-16; D-37 operation IDs).

The harness enforces everything declared here (central policy ∩ this spec ∩ task scope ∩ stage). This
package may import only ``devteam_core`` and ``devteam_harness.agent_sdk`` (CLAUDE.md §5).
"""

from devteam_core.contracts.agents import DocumenterInput, DocumenterOutput
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_harness.agent_sdk import AgentKind, AgentSpec, OperationSpec

SPEC = AgentSpec(
    role=AgentRole.DOCUMENTER,
    kind=AgentKind.DELIVERY,
    default_slot=ModelSlot.INSTRUCT,
    operations=(OperationSpec(operation_id="documenter.write_section", output_schema="DocumentDraft"),),
    prompt_id="documenter.system",
    prompt_version=1,
    input_model=DocumenterInput,
    output_model=DocumenterOutput,
    tools=("read_project_file", "list_project_files", "write_project_file"),
    write_paths=("generated/docs/", "generated/README.md", "generated/PROJECT_DOCUMENTATION.md"),
)
