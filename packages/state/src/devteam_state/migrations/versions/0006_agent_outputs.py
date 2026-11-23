"""Part D.2: validated agent outputs (``agent_outputs``), append-only.

The graph state carries references only (roadmap §6.8), so an agent's validated output contract is kept
here, keyed by its task, for the steps that follow (for example the Requirements Finalizer reads the
requirement keys the BRD Analyst produced) and for resuming after a restart. A stored output is never
edited (trigger); one output per task.

Downgrade drops the table; no existing table is changed.

Revision ID: 0006_agent_outputs
Revises: 0005_orchestration
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_agent_outputs"
down_revision: str | None = "0005_orchestration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_outputs",
        sa.Column(
            "task_id", sa.String(40), sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column(
            "run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("agent_type", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "CREATE TRIGGER agent_outputs_no_update BEFORE UPDATE ON agent_outputs FOR EACH ROW "
        "BEGIN SELECT RAISE(ABORT, 'a stored agent output is never edited'); END"
    )


def downgrade() -> None:
    op.drop_table("agent_outputs")
