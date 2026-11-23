"""Part E: sandbox executions, template verifications and the LLM/sandbox resource lease.

- ``sandbox_executions``: one row per container run (``SandboxExecution`` contract). Append-only.
  ``project_id`` is NULL for platform work (template verification), as for ``audit_events``.
- ``template_verifications``: requests and results of starter-kit self-tests (D-49). The status only
  moves forward (``queued → running → passed/failed/blocked``; a finished result may become ``stale``);
  a trigger enforces it.
- ``resource_lock``: a single row (D-46). Whoever holds it (an LLM call or a sandbox run) has the
  machine's memory; anyone else waits. Stale holders (no heartbeat) are taken over.

Downgrade drops the three tables; no existing table is changed.

Revision ID: 0007_sandbox
Revises: 0006_agent_outputs
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_sandbox"
down_revision: str | None = "0006_agent_outputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VERIFICATION_STATUSES = "('queued','running','passed','failed','blocked','stale')"
_EXECUTION_STATUSES = "('completed','failed','timed_out','oom_killed','blocked','cancelled')"


def upgrade() -> None:
    op.create_table(
        "template_verifications",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("template_id", sa.String(64), nullable=False, index=True),
        sa.Column("template_version", sa.String(20), nullable=False),
        sa.Column("template_checksum", sa.String(64), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        sa.Column("recipes", sa.JSON, nullable=False),
        sa.Column("image_digests", sa.JSON, nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=True),
        sa.Column("peak_memory_mb", sa.Float, nullable=True),
        sa.Column("requested_by", sa.String(200), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(2000), nullable=True),
        sa.CheckConstraint(f"status IN {_VERIFICATION_STATUSES}", name="status_values"),
    )
    op.execute(
        "CREATE TRIGGER template_verifications_forward_only "
        "BEFORE UPDATE OF status ON template_verifications "
        "FOR EACH ROW WHEN NOT ("
        " NEW.status = OLD.status"
        " OR (OLD.status = 'queued' AND NEW.status IN ('running','blocked'))"
        " OR (OLD.status = 'running' AND NEW.status IN ('passed','failed','blocked'))"
        " OR (OLD.status IN ('passed','failed','blocked') AND NEW.status = 'stale'))"
        " BEGIN SELECT RAISE(ABORT, 'template verification status only moves forward'); END"
    )
    op.execute(
        "CREATE TRIGGER template_verifications_final_frozen BEFORE UPDATE ON template_verifications "
        "FOR EACH ROW WHEN OLD.status IN ('passed','failed','blocked','stale') AND NOT ("
        " NEW.status = 'stale' AND NEW.recipes IS OLD.recipes AND NEW.image_digests IS OLD.image_digests"
        " AND NEW.template_checksum IS OLD.template_checksum AND NEW.finished_at IS OLD.finished_at)"
        " BEGIN SELECT RAISE(ABORT, 'a finished template verification is never edited'); END"
    )
    op.create_table(
        "sandbox_executions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), nullable=True, index=True),
        sa.Column("run_id", sa.String(40), nullable=True, index=True),
        sa.Column(
            "verification_id",
            sa.String(40),
            sa.ForeignKey("template_verifications.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("recipe", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"status IN {_EXECUTION_STATUSES}", name="status_values"),
        sa.CheckConstraint("(project_id IS NULL) <> (verification_id IS NULL)", name="one_owner"),
    )
    op.execute(
        "CREATE TRIGGER sandbox_executions_no_update BEFORE UPDATE ON sandbox_executions FOR EACH ROW "
        "BEGIN SELECT RAISE(ABORT, 'a recorded sandbox execution is never edited'); END"
    )
    op.create_table(
        "resource_lock",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("holder", sa.String(16), nullable=False),
        sa.Column("owner", sa.String(120), nullable=False),
        sa.Column("pid", sa.Integer, nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="single_row"),
        sa.CheckConstraint("holder IN ('llm','sandbox')", name="holder_values"),
    )


def downgrade() -> None:
    op.drop_table("resource_lock")
    op.drop_table("sandbox_executions")
    op.drop_table("template_verifications")
