"""Append-only audit store (HC-21 foundation). Rows are never updated or deleted (DB triggers)."""

from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.ids import new_id
from devteam_core.logging import redact_value
from devteam_state.models import AuditEvent

Outcome = Literal["success", "denied", "error"]


def append(
    session: Session,
    *,
    action: str,
    outcome: Outcome,
    principal_id: str | None = None,
    project_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    details: dict[str, Any] | None = None,
    clock: Clock = system_clock,
) -> AuditEvent:
    """Append one event. ``details`` are redacted (HC-17) before they are stored."""
    redacted = redact_value(details or {})
    event = AuditEvent(
        id=new_id("aud", clock),
        ts=clock.now(),
        principal_id=principal_id,
        action=action,
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        request_id=request_id,
        trace_id=trace_id,
        details=redacted if isinstance(redacted, dict) else {"value": redacted},
    )
    session.add(event)
    session.flush()
    return event


def list_events(
    session: Session, *, project_id: str | None, offset: int, limit: int
) -> tuple[list[AuditEvent], int]:
    query = select(AuditEvent)
    count = select(func.count(AuditEvent.id))
    if project_id is not None:
        query = query.where(AuditEvent.project_id == project_id)
        count = count.where(AuditEvent.project_id == project_id)
    total = session.scalar(count) or 0
    items = list(
        session.scalars(
            query.order_by(AuditEvent.ts.desc(), AuditEvent.id.desc()).offset(offset).limit(limit)
        )
    )
    return items, int(total)
