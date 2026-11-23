"""Append-only, hash-chained audit store (HC-21; HS-05 foundation, HS-11 chain).

* Rows are never updated or deleted (database triggers, migration 0002).
* Every new row extends the hash chain (migration 0003, :mod:`devteam_state.audit_chain`). Appenders are
  serialised by updating the single ``audit_chain_head`` row first, which takes SQLite's write lock for the
  rest of the transaction; the unique ``seq`` index is the backstop.
* :func:`verify_chain` recomputes everything and reports the first broken entry.
"""

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.ids import new_id
from devteam_core.logging import redact_value
from devteam_state.audit_chain import ANCHOR_ACTION, GENESIS, link_hash, pre_chain_digest
from devteam_state.models import AuditChainHead, AuditEvent

Outcome = Literal["success", "denied", "error"]


class AuditChainError(RuntimeError):
    """The chain head is missing (schema not migrated) or inconsistent at append time."""


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
    """Append one chained event. ``details`` are redacted (HC-17) before they are stored or hashed."""
    redacted = redact_value(details or {})
    # Take the write lock first so no other appender can read the same head.
    locked = session.execute(
        update(AuditChainHead).where(AuditChainHead.id == 1).values(seq=AuditChainHead.seq)
    )
    head = session.get(AuditChainHead, 1, populate_existing=True)
    if head is None or getattr(locked, "rowcount", 1) == 0:
        raise AuditChainError("audit chain head missing: run the database migrations")
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
    seq = head.seq + 1
    event.seq = seq
    event.prev_hash = head.hash
    event.hash = link_hash(head.hash, event, seq)
    session.add(event)
    head.seq = seq
    head.hash = event.hash
    session.flush()
    return event


@dataclass(frozen=True, slots=True)
class ChainReport:
    ok: bool
    checked: int  # chained entries verified
    pre_chain_rows: int  # Part B entries covered by the anchor
    broken_at: int | None = None  # seq of the first bad entry
    reason: str | None = None


def verify_chain(session: Session) -> ChainReport:  # noqa: PLR0911 - one return per failure kind
    chained = list(
        session.scalars(select(AuditEvent).where(AuditEvent.seq.is_not(None)).order_by(AuditEvent.seq))
    )
    legacy = list(
        session.scalars(
            select(AuditEvent).where(AuditEvent.seq.is_(None)).order_by(AuditEvent.ts, AuditEvent.id)
        )
    )
    if not chained:
        return ChainReport(False, 0, len(legacy), 1, "the chain anchor is missing")
    anchor = chained[0]
    if anchor.seq != 1 or anchor.action != ANCHOR_ACTION or anchor.prev_hash != GENESIS:
        return ChainReport(False, 0, len(legacy), 1, "the chain anchor is missing or altered")
    covered = anchor.details.get("pre_chain_rows")
    if covered != len(legacy) or anchor.details.get("pre_chain_digest") != pre_chain_digest(legacy):  # type: ignore[arg-type]
        return ChainReport(
            False, 0, len(legacy), 1, "entries from before the chain (Part B) were changed, removed or added"
        )
    previous = GENESIS
    for expected_seq, event in enumerate(chained, start=1):
        if event.seq != expected_seq:
            return ChainReport(False, expected_seq - 1, len(legacy), expected_seq, "an entry is missing")
        if event.prev_hash != previous:
            return ChainReport(
                False, expected_seq - 1, len(legacy), expected_seq, "the link to the previous entry is broken"
            )
        if event.hash != link_hash(previous, event, expected_seq):
            return ChainReport(False, expected_seq - 1, len(legacy), expected_seq, "the entry was altered")
        previous = event.hash
    head = session.get(AuditChainHead, 1)
    last = chained[-1]
    if head is None or head.seq != last.seq or head.hash != last.hash:
        return ChainReport(
            False,
            len(chained),
            len(legacy),
            (last.seq or 0) + 1,
            "the latest entries were removed (head mismatch)",
        )
    return ChainReport(True, len(chained), len(legacy))


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
