"""Part C.4: approvals (HC-13), secret references (HC-17), usage counters (HC-05), trace spans (HC-11).

* ``approvals`` is append-only (triggers): a decision is never edited or deleted; ``decision_key`` makes
  a second decision for the same run, type and spec version impossible (double submission).
* ``secret_references`` holds pointers only; there is no value column.
* ``usage_counters`` persists per-task and per-run budget usage.
* ``trace_spans`` is the only trace store (D-30: local storage, no network exporter).

Revision ID: 0004_execution
Revises: 0003_harness_core
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_execution"
down_revision: str | None = "0003_harness_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)


def _project_fk(nullable: bool = False) -> sa.Column[str]:
    return sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=nullable, index=True)


def upgrade() -> None:
    op.create_table(
        "approvals",
        sa.Column("id", sa.String(40), primary_key=True),
        _project_fk(),
        sa.Column("run_id", sa.String(40), sa.ForeignKey("runs.id"), nullable=False, index=True),
        sa.Column("approval_type", sa.String(32), nullable=False),
        sa.Column("spec_version_id", sa.String(40), nullable=True),
        sa.Column("spec_checksum", sa.String(64), nullable=True),
        sa.Column("decision_key", sa.String(200), nullable=False, unique=True),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decided_by", sa.String(40), nullable=False),
        sa.Column("decided_at", _TZ, nullable=False),
        sa.Column("audit_event_id", sa.String(40), nullable=False),
    )
    op.execute(
        "CREATE TRIGGER approvals_no_update BEFORE UPDATE ON approvals "
        "BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER approvals_no_delete BEFORE DELETE ON approvals "
        "BEGIN SELECT RAISE(ABORT, 'approvals are append-only'); END"
    )
    op.create_table(
        "secret_references",
        sa.Column("id", sa.String(40), primary_key=True),
        _project_fk(nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("ref", sa.String(120), nullable=False, unique=True),
        sa.Column("created_at", _TZ, nullable=False),
    )
    op.create_table(
        "usage_counters",
        sa.Column("id", sa.String(40), primary_key=True),
        _project_fk(),
        sa.Column(
            "run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("task_id", sa.String(40), nullable=True),
        sa.Column("scope", sa.String(8), nullable=False),
        sa.Column("key", sa.String(120), nullable=False, unique=True),
        sa.Column("model_calls", sa.Integer, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("tool_calls", sa.Integer, nullable=False),
        sa.Column("bytes_written", sa.Integer, nullable=False),
        sa.Column("active_ms", sa.Integer, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
    )
    op.create_table(
        "trace_spans",
        sa.Column("span_id", sa.String(16), primary_key=True),
        sa.Column("trace_id", sa.String(32), nullable=False, index=True),
        sa.Column("parent_span_id", sa.String(16), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=True, index=True),
        sa.Column("start_ts", _TZ, nullable=False),
        sa.Column("end_ts", _TZ, nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attributes", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("trace_spans")
    op.drop_table("usage_counters")
    op.drop_table("secret_references")
    op.execute("DROP TRIGGER IF EXISTS approvals_no_delete")
    op.execute("DROP TRIGGER IF EXISTS approvals_no_update")
    op.drop_table("approvals")
