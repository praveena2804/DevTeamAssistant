"""Requirements Finalizer: the agent's declaration (``AgentSpec``, HS-16; D-37 operation IDs).

The harness enforces everything declared here (central policy ∩ this spec ∩ task scope ∩ stage). This
package may import only ``devteam_core`` and ``devteam_harness.agent_sdk`` (CLAUDE.md §5).
"""

from devteam_core.contracts.agents import RequirementsFinalizerInput, RequirementsFinalizerOutput
from devteam_core.contracts.statuses import AgentRole, ModelSlot
from devteam_harness.agent_sdk import AgentKind, AgentSpec, OperationSpec

SPEC = AgentSpec(
    role=AgentRole.REQUIREMENTS_FINALIZER,
    kind=AgentKind.DISCOVERY,
    default_slot=ModelSlot.INSTRUCT,
    operations=(
        OperationSpec(operation_id="requirements_finalizer.draft_questions", output_schema="QuestionsDraft"),
        OperationSpec(operation_id="requirements_finalizer.consolidate", output_schema="ScopeDraft"),
    ),
    prompt_id="requirements_finalizer.system",
    prompt_version=1,
    input_model=RequirementsFinalizerInput,
    output_model=RequirementsFinalizerOutput,
    tools=("read_extracted_document", "search_project_knowledge"),
    write_paths=(),
)
