"""BRD Analyst: the agent's declaration (``AgentSpec``, HS-16; D-37 operation IDs).

The harness enforces everything declared here (central policy ∩ this spec ∩ task scope ∩ stage). This
package may import only ``devteam_core`` and ``devteam_harness.agent_sdk`` (CLAUDE.md §5).
"""

from devteam_core.contracts.agents import BrdAnalystInput, BrdAnalystOutput
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_harness.agent_sdk import AgentKind, AgentSpec, OperationSpec

SPEC = AgentSpec(
    role=AgentRole.BRD_ANALYST,
    kind=AgentKind.DISCOVERY,
    default_slot=ModelSlot.INSTRUCT,
    operations=(OperationSpec(operation_id="brd_analyst.analyse_section", output_schema="AnalysisDraft"),),
    prompt_id="brd_analyst.system",
    prompt_version=1,
    input_model=BrdAnalystInput,
    output_model=BrdAnalystOutput,
    tools=("read_extracted_document", "search_project_knowledge"),
    write_paths=(),
)
