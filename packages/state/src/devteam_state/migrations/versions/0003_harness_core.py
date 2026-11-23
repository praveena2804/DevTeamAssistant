"""Part C.3: hash-chained audit log (HC-21) and run/task/attempt tables (HC-19).

* ``audit_events`` gains ``seq``, ``prev_hash``, ``hash`` (nullable: Part B rows stay untouched).
* ``audit_chain_head`` (single row) serialises appenders and records the latest position.
* An anchor event (seq 1) covers all existing rows with their count and digest. Existing rows are
  **not modified**: the append-only triggers stay in force throughout the upgrade.
* ``runs``, ``agent_tasks``, ``task_attempts`` for the run/task state machines and the attempt ledger.

``audit_events.principal_id`` is declared ``String(80)`` in the models (agent ids are ~56 characters).
SQLite does not enforce declared lengths, so no table rebuild (which would rewrite the audit log) is done.

Revision ID: 0003_harness_core
Revises: 0002_intake_iam_audit
Create Date: 2026-11-20
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from types import SimpleNamespace

import sqlalchemy as sa
from alembic import op

from devteam_core.ids import new_id
from devteam_state.audit_chain import ANCHOR_ACTION, GENESIS, link_hash, pre_chain_digest

revision: str = "0003_harness_core"
down_revision: str | None = "0002_intake_iam_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)
_COLUMNS = (
    "id, ts, principal_id, action, project_id, target_type, target_id, outcome, request_id, trace_id, details"
)


def _anchor() -> None:
    bind = op.get_bind()
    query = f"SELECT {_COLUMNS} FROM audit_events ORDER BY ts, id"  # noqa: S608 - constant column list
    rows = bind.execute(sa.text(query)).mappings().all()
    events = []
    for row in rows:
        details = row["details"]
        if isinstance(details, str):
            import json  # noqa: PLC0415

            details = json.loads(details)
        ts = row["ts"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        events.append(SimpleNamespace(**{**dict(row), "details": details, "ts": ts}))
    anchor = SimpleNamespace(
        id=new_id("aud"),
        ts=datetime.now(UTC),
        principal_id="service:audit",
        action=ANCHOR_ACTION,
        project_id=None,
        target_type="audit_chain",
        target_id=None,
        outcome="success",
        request_id=None,
        trace_id=None,
        details={
            "chain_version": 1,
            "pre_chain_rows": len(events),
            "pre_chain_digest": pre_chain_digest(events),
        },
    )
    anchor_hash = link_hash(GENESIS, anchor, 1)
    plain = [sa.column(name) for name in _COLUMNS.split(", ") if name not in {"ts", "details"}]
    table = sa.table(
        "audit_events",
        *plain,
        sa.column("ts", _TZ),
        sa.column("details", sa.JSON),
        sa.column("seq"),
        sa.column("prev_hash"),
        sa.column("hash"),
    )
    op.execute(
        table.insert().values(
            id=anchor.id,
            ts=anchor.ts,
            principal_id=anchor.principal_id,
            action=anchor.action,
            project_id=None,
            target_type=anchor.target_type,
            target_id=None,
            outcome="success",
            request_id=None,
            trace_id=None,
            details=anchor.details,
            seq=1,
            prev_hash=GENESIS,
            hash=anchor_hash,
        )
    )
    op.execute(
        sa.text("INSERT INTO audit_chain_head (id, seq, hash) VALUES (1, 1, :h)").bindparams(h=anchor_hash)
    )


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("seq", sa.Integer, nullable=True))
    op.add_column("audit_events", sa.Column("prev_hash", sa.String(64), nullable=True))
    op.add_column("audit_events", sa.Column("hash", sa.String(64), nullable=True))
    op.create_index("uq_audit_events_seq", "audit_events", ["seq"], unique=True)
    op.create_table(
        "audit_chain_head",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.CheckConstraint("id = 1", name="single_row"),
    )
    op.execute(
        "CREATE TRIGGER audit_chain_head_no_delete BEFORE DELETE ON audit_chain_head "
        "BEGIN SELECT RAISE(ABORT, 'audit_chain_head cannot be deleted'); END"
    )
    op.execute(
        "CREATE TRIGGER audit_chain_head_forward_only BEFORE UPDATE ON audit_chain_head "
        "WHEN NEW.seq < OLD.seq BEGIN SELECT RAISE(ABORT, 'audit chain cannot move backwards'); END"
    )
    _anchor()

    op.create_table(
        "runs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("run_kind", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("spec_version_id", sa.String(40), nullable=True),
        sa.Column("spec_checksum", sa.String(64), nullable=True),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
        sa.Column("heartbeat_at", _TZ, nullable=True),
    )
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column(
            "run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("agent_type", sa.String(32), nullable=False),
        sa.Column("operation_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
    )
    op.create_table(
        "task_attempts",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column(
            "run_id", sa.String(40), sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column(
            "task_id", sa.String(40), sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("node", sa.String(64), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("key", sa.String(200), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("started_at", _TZ, nullable=False),
        sa.Column("finished_at", _TZ, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("task_attempts")
    op.drop_table("agent_tasks")
    op.drop_table("runs")
    # Removing the chain means removing chained rows and columns: the append-only triggers are lifted
    # for the duration of the downgrade only, then restored exactly as migration 0002 created them.
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
    op.execute("DELETE FROM audit_events WHERE seq IS NOT NULL")
    op.execute("DROP TRIGGER IF EXISTS audit_chain_head_forward_only")
    op.execute("DROP TRIGGER IF EXISTS audit_chain_head_no_delete")
    op.drop_table("audit_chain_head")
    op.drop_index("uq_audit_events_seq", table_name="audit_events")
    with op.batch_alter_table("audit_events") as batch:
        batch.drop_column("hash")
        batch.drop_column("prev_hash")
        batch.drop_column("seq")
    op.execute(
        "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
    )
