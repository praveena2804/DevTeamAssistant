"""Part D.1: orchestration state (HC-07, HC-19, HC-10; Part D plan §3.9).

* ``runs`` gains run-control and display columns: ``mode`` (D-38), ``current_node``, ``pending_gate``,
  ``control_request`` (none/pause/cancel) and ``trace_id``.
* ``spec_versions`` (D-39): draft → frozen, guarded by triggers: inserted only as ``draft``; a draft may
  only be frozen (with its approval recorded); a frozen version and every checksum are immutable; no
  version is ever deleted.
* ``task_plans``: validated Head plans with their validation report.
* ``run_jobs`` + ``worker_lease`` (single row, CHECK id = 1): the worker queue and lock (D-41).
* ``run_events``: progress events for SSE replay; never updated.
* ``node_effects``: two-phase idempotency ledger for service nodes; the phase only moves forward.

``task_attempts.status`` gains the value ``interrupted`` (D-40); it is a plain string column, so no
schema change is needed for it.

Downgrade drops the new tables and removes the ``runs`` columns with SQLite's native
``ALTER TABLE ... DROP COLUMN``: a batch (copy-and-rename) rebuild would drop ``runs`` and, with
foreign keys on, cascade-delete its child rows.

Revision ID: 0005_orchestration
Revises: 0004_execution
Create Date: 2026-11-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_orchestration"
down_revision: str | None = "0004_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_RUN_COLUMNS = ("trace_id", "control_request", "pending_gate", "current_node", "mode")


def _project_fk() -> sa.Column[str]:
    return sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True)


def _run_fk(*, cascade: bool = True) -> sa.Column[str]:
    target = sa.ForeignKey("runs.id", ondelete="CASCADE") if cascade else sa.ForeignKey("runs.id")
    return sa.Column("run_id", sa.String(40), target, nullable=False, index=True)


def _trigger(name: str, timing: str, table: str, condition: str, message: str) -> None:
    when = f" WHEN {condition}" if condition else ""
    op.execute(
        f"CREATE TRIGGER {name} {timing} ON {table} FOR EACH ROW{when} "
        f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
    )


def upgrade() -> None:
    op.add_column("runs", sa.Column("mode", sa.String(8), nullable=False, server_default="stub"))
    op.add_column("runs", sa.Column("current_node", sa.String(40), nullable=True))
    op.add_column("runs", sa.Column("pending_gate", sa.String(40), nullable=True))
    op.add_column("runs", sa.Column("control_request", sa.String(8), nullable=False, server_default="none"))
    op.add_column("runs", sa.Column("trace_id", sa.String(32), nullable=True))

    op.create_table(
        "spec_versions",
        sa.Column("id", sa.String(40), primary_key=True),
        _project_fk(),
        _run_fk(cascade=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("frozen_at", _TZ, nullable=True),
        sa.Column("approval_id", sa.String(40), nullable=True),
        sa.Column("approved_by", sa.String(40), nullable=True),
        sa.UniqueConstraint("project_id", "version", name="uq_spec_versions_project_version"),
        sa.CheckConstraint("status IN ('draft', 'frozen')", name="status_values"),
        sa.CheckConstraint("version >= 1", name="version_positive"),
    )
    _trigger(
        "spec_versions_insert_draft_only",
        "BEFORE INSERT",
        "spec_versions",
        "NEW.status <> 'draft' OR NEW.approval_id IS NOT NULL OR NEW.frozen_at IS NOT NULL",
        "a spec version is created as a draft",
    )
    # One update trigger with its rules in a fixed order (several BEFORE UPDATE triggers would fire in an
    # unspecified order, so the reported reason would vary).
    op.execute(
        "CREATE TRIGGER spec_versions_guard_update BEFORE UPDATE ON spec_versions FOR EACH ROW BEGIN "
        "SELECT RAISE(ABORT, 'a frozen spec version is immutable') WHERE OLD.status = 'frozen'; "
        "SELECT RAISE(ABORT, 'spec version content and checksum are immutable') "
        "WHERE NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id OR NEW.run_id IS NOT OLD.run_id "
        "OR NEW.version IS NOT OLD.version OR NEW.checksum IS NOT OLD.checksum "
        "OR NEW.payload IS NOT OLD.payload OR NEW.created_at IS NOT OLD.created_at; "
        "SELECT RAISE(ABORT, 'a draft may only be frozen, with its approval recorded') "
        "WHERE NEW.status <> 'frozen' OR NEW.approval_id IS NULL OR NEW.approved_by IS NULL "
        "OR NEW.frozen_at IS NULL; "
        "END"
    )
    _trigger(
        "spec_versions_no_delete", "BEFORE DELETE", "spec_versions", "", "spec versions are never deleted"
    )

    op.create_table(
        "task_plans",
        sa.Column("id", sa.String(40), primary_key=True),
        _project_fk(),
        _run_fk(),
        sa.Column("spec_version_id", sa.String(40), sa.ForeignKey("spec_versions.id"), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("report", sa.JSON, nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.CheckConstraint("source IN ('template', 'model')", name="source_values"),
    )
    _trigger("task_plans_no_update", "BEFORE UPDATE", "task_plans", "", "a stored plan is never edited")

    op.create_table(
        "run_jobs",
        sa.Column("id", sa.String(40), primary_key=True),
        _project_fk(),
        _run_fk(),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("status", sa.String(12), nullable=False, index=True),
        sa.Column("available_at", _TZ, nullable=False),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.CheckConstraint("kind IN ('start', 'resume', 'gate_decision')", name="kind_values"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'done', 'failed', 'cancelled')", name="status_values"
        ),
    )
    op.create_table(
        "worker_lease",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("owner", sa.String(40), nullable=False),
        sa.Column("pid", sa.Integer, nullable=False),
        sa.Column("acquired_at", _TZ, nullable=False),
        sa.Column("heartbeat_at", _TZ, nullable=False),
        sa.CheckConstraint("id = 1", name="single_row"),
    )
    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        _project_fk(),
        _run_fk(),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("ts", _TZ, nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_run_events_run_seq"),
    )
    _trigger("run_events_no_update", "BEFORE UPDATE", "run_events", "", "run events are never edited")
    op.create_table(
        "node_effects",
        sa.Column("id", sa.String(40), primary_key=True),
        _project_fk(),
        _run_fk(),
        sa.Column("node", sa.String(40), nullable=False),
        sa.Column("key", sa.String(200), nullable=False, unique=True),
        sa.Column("phase", sa.String(10), nullable=False),
        sa.Column("result_ref", sa.String(80), nullable=True),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.CheckConstraint("phase IN ('intent', 'performed', 'confirmed')", name="phase_values"),
    )
    _trigger(
        "node_effects_forward_only",
        "BEFORE UPDATE",
        "node_effects",
        "OLD.phase = 'confirmed' OR (OLD.phase = 'performed' AND NEW.phase = 'intent') "
        "OR NEW.key IS NOT OLD.key OR NEW.run_id IS NOT OLD.run_id OR NEW.node IS NOT OLD.node",
        "a node effect only moves forward: intent, performed, confirmed",
    )


def downgrade() -> None:
    op.drop_table("node_effects")
    op.drop_table("run_events")
    op.drop_table("worker_lease")
    op.drop_table("run_jobs")
    op.drop_table("task_plans")
    op.drop_table("spec_versions")
    for column in _RUN_COLUMNS:
        op.execute(f"ALTER TABLE runs DROP COLUMN {column}")
