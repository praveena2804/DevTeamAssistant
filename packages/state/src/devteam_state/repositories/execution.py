"""Repositories for the C.4 tables: approvals, usage counters, secret references, trace spans.

Every query that reads project data is scoped by ``project_id``. Approvals are insert-only here and
append-only in the database (triggers); usage counters are updated in place.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.ids import new_id
from devteam_state.models import ApprovalRow, SecretReferenceRow, TraceSpanRow, UsageCounter

# ------------------------------------------------------------------------------------------ approvals


def approval_decision_key(run_id: str, approval_type: str, spec_version_id: str | None) -> str:
    return f"{run_id}:{approval_type}:{spec_version_id or '-'}"


def add_approval(session: Session, row: ApprovalRow) -> ApprovalRow:
    session.add(row)
    session.flush()
    return row


def get_approval(session: Session, approval_id: str) -> ApprovalRow | None:
    return session.get(ApprovalRow, approval_id)


def find_decision(
    session: Session, run_id: str, approval_type: str, spec_version_id: str | None
) -> ApprovalRow | None:
    key = approval_decision_key(run_id, approval_type, spec_version_id)
    return session.scalars(select(ApprovalRow).where(ApprovalRow.decision_key == key)).first()


# ------------------------------------------------------------------------------------- usage counters

COUNTER_FIELDS = ("model_calls", "input_tokens", "output_tokens", "tool_calls", "bytes_written", "active_ms")


@dataclass(frozen=True, slots=True)
class Usage:
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    bytes_written: int = 0
    active_ms: int = 0

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def usage_key(run_id: str, task_id: str | None) -> str:
    return f"{run_id}:{'task' if task_id else 'run'}:{task_id or '-'}"


def get_usage(session: Session, project_id: str, run_id: str, task_id: str | None = None) -> Usage:
    row = session.scalars(
        select(UsageCounter).where(
            UsageCounter.project_id == project_id, UsageCounter.key == usage_key(run_id, task_id)
        )
    ).first()
    if row is None:
        return Usage()
    return Usage(**{name: getattr(row, name) for name in COUNTER_FIELDS})


def add_usage(
    session: Session,
    *,
    project_id: str,
    run_id: str,
    task_id: str | None,
    delta: Usage,
    clock: Clock = system_clock,
) -> Usage:
    """Add ``delta`` to the counter row (created on first use). Negative deltas are refused."""
    if any(getattr(delta, name) < 0 for name in COUNTER_FIELDS):
        raise ValueError("usage only ever grows")
    key = usage_key(run_id, task_id)
    row = session.scalars(select(UsageCounter).where(UsageCounter.key == key)).first()
    if row is None:
        row = UsageCounter(
            id=new_id("use", clock),
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            scope="task" if task_id else "run",
            key=key,
            updated_at=clock.now(),
            **dict.fromkeys(COUNTER_FIELDS, 0),
        )
        session.add(row)
    elif row.project_id != project_id:
        raise ValueError("the usage counter belongs to another project")
    for name in COUNTER_FIELDS:
        setattr(row, name, getattr(row, name) + getattr(delta, name))
    row.updated_at = clock.now()
    session.flush()
    return Usage(**{name: getattr(row, name) for name in COUNTER_FIELDS})


# ---------------------------------------------------------------------------------- secret references


def add_secret_reference(
    session: Session, *, name: str, ref: str, project_id: str | None = None, clock: Clock = system_clock
) -> SecretReferenceRow:
    row = SecretReferenceRow(
        id=new_id("sec", clock), project_id=project_id, name=name, ref=ref, created_at=clock.now()
    )
    session.add(row)
    session.flush()
    return row


def list_secret_references(session: Session, project_id: str | None) -> list[SecretReferenceRow]:
    query = select(SecretReferenceRow).where(
        SecretReferenceRow.project_id.is_(None)
        if project_id is None
        else SecretReferenceRow.project_id == project_id
    )
    return list(session.scalars(query.order_by(SecretReferenceRow.name)))


# ---------------------------------------------------------------------------------------- trace spans


@dataclass(frozen=True, slots=True)
class SpanRecord:
    span_id: str
    trace_id: str
    parent_span_id: str | None
    name: str
    project_id: str | None
    start_ts: datetime
    end_ts: datetime
    status: str
    attributes: dict[str, Any]


def add_spans(session: Session, spans: Iterable[SpanRecord]) -> int:
    count = 0
    for span in spans:
        duration = max(0, int((span.end_ts - span.start_ts).total_seconds() * 1000))
        session.add(
            TraceSpanRow(
                span_id=span.span_id,
                trace_id=span.trace_id,
                parent_span_id=span.parent_span_id,
                name=span.name,
                project_id=span.project_id,
                start_ts=span.start_ts,
                end_ts=span.end_ts,
                duration_ms=duration,
                status=span.status,
                attributes=span.attributes,
            )
        )
        count += 1
    session.flush()
    return count


def spans_for_trace(session: Session, trace_id: str) -> list[TraceSpanRow]:
    return list(
        session.scalars(
            select(TraceSpanRow).where(TraceSpanRow.trace_id == trace_id).order_by(TraceSpanRow.start_ts)
        )
    )
