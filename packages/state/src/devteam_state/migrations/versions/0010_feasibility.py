"""Part G: feasibility reports, items and requirement effects; item-bound substitution approvals.

- ``feasibility_reports``: one row per evaluation (``FeasibilityReport`` contract): coverage numbers, the
  catalog file hash and the template verifications the evaluation relied on, the rendered report path and
  checksum. A re-evaluation adds a new report; nothing is edited.
- ``feasibility_items``: one row per subject (technology need or integration) (D-61): catalog key, status,
  reason, offered alternative, affected requirement keys, raised gap, and an item checksum that a
  substitution approval is bound to.
- ``feasibility_requirement_effects``: each requirement's effect (buildable, partial, pending substitution,
  blocked) and the items that cause it.
- ``approvals.subject_ref`` (nullable): ``substitution:<item_id>@<item_checksum>`` binds a substitution
  approval to exactly one item (AC-G-04). ``ADD COLUMN`` keeps the existing append-only triggers.

The three feasibility tables are append-only (triggers), like ``model_calls``. Downgrade drops them and the
column (substitution approvals then lose their binding; none exist before Part I offers acceptance).

Revision ID: 0010_feasibility
Revises: 0009_rag_index
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_feasibility"
down_revision: str | None = "0009_rag_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("feasibility_reports", "feasibility_items", "feasibility_requirement_effects")


def _append_only(table: str) -> None:
    for verb in ("update", "delete"):
        op.execute(
            f"CREATE TRIGGER {table}_no_{verb} BEFORE {verb.upper()} ON {table} "
            f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
        )


def upgrade() -> None:
    op.create_table(
        "feasibility_reports",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id"), nullable=False, index=True),
        sa.Column("analysis_task_id", sa.String(40), nullable=False),
        sa.Column("catalog_hash", sa.String(64), nullable=False),
        sa.Column("verification_ids", sa.JSON, nullable=False),
        sa.Column("numerator", sa.Integer, nullable=False),
        sa.Column("denominator", sa.Integer, nullable=False),
        sa.Column("coverage_pct", sa.Float, nullable=True),
        sa.Column("threshold_pct", sa.Integer, nullable=False),
        sa.Column("recommendation", sa.String(20), nullable=False),
        sa.Column("item_count", sa.Integer, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),  # the validated FeasibilityReport contract
        sa.Column("report_path", sa.String(240), nullable=False),
        sa.Column("report_checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.CheckConstraint("numerator >= 0 AND numerator <= denominator", name="coverage_numbers"),
        sa.CheckConstraint(
            "recommendation IN ('proceed','stop_recommended','nothing_to_build','not_applicable')",
            name="recommendation_values",
        ),
    )
    op.create_table(
        "feasibility_items",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column(
            "report_id", sa.String(40), sa.ForeignKey("feasibility_reports.id"), nullable=False, index=True
        ),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("subject_kind", sa.String(20), nullable=False),
        sa.Column("subject_id", sa.String(40), nullable=False),
        sa.Column("subject_name", sa.String(200), nullable=False),
        sa.Column("catalog_key", sa.String(40), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("alternative", sa.String(40), nullable=True),
        sa.Column("requirement_ids", sa.JSON, nullable=False),
        sa.Column("gap", sa.JSON, nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),  # the validated FeasibilityItem contract
        sa.Column("item_checksum", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('supported','supported_with_substitution','partial','not_supported')",
            name="status_values",
        ),
    )
    op.create_table(
        "feasibility_requirement_effects",
        sa.Column("report_id", sa.String(40), sa.ForeignKey("feasibility_reports.id"), primary_key=True),
        sa.Column("requirement_key", sa.String(10), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("effect", sa.String(24), nullable=False),
        sa.Column("cause_item_ids", sa.JSON, nullable=False),
        sa.CheckConstraint(
            "effect IN ('buildable','partial','pending_substitution','blocked')", name="effect_values"
        ),
    )
    for table in TABLES:
        _append_only(table)
    op.add_column("approvals", sa.Column("subject_ref", sa.String(120), nullable=True))


def downgrade() -> None:
    op.execute("ALTER TABLE approvals DROP COLUMN subject_ref")
    for table in reversed(TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_delete")
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update")
        op.drop_table(table)
