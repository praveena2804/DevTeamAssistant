"""HC-21 audit (foundation): records user actions with the current correlation and trace IDs.

Part B stores events append-only (database triggers). Part C adds the hash chain and tool/model/file/
approval/transition event types (HS-11).
"""

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from devteam_core import tracing
from devteam_core.logging import get_logger
from devteam_state.repositories import audit as audit_repo
from devteam_state.repositories.audit import Outcome

_log = get_logger("devteam_harness.audit")


def record(
    session: Session,
    *,
    action: str,
    outcome: Outcome,
    principal_id: str | None,
    request_id: str | None = None,
    project_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> str:
    """Append one audit event in ``session`` (the caller commits). Returns the event ID."""
    ctx = tracing.current()
    event = audit_repo.append(
        session,
        action=action,
        outcome=outcome,
        principal_id=principal_id,
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
        trace_id=ctx.trace_id if ctx else None,
        details=details,
    )
    _log.info("audit_event", action=action, outcome=outcome, audit_id=event.id, project_id=project_id)
    return event.id


class DatabaseAuditSink:
    """Audit sink for harness components (policy, tool layer): each record in its own short transaction.

    Callers must not hold an open write transaction on the same database while calling it (SQLite has a
    single writer); the tool layer and executor never do.
    """

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def record(
        self,
        *,
        action: str,
        outcome: Outcome,
        principal_id: str | None,
        project_id: str | None,
        details: dict[str, Any],
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> str:
        with self._sessions.begin() as session:
            return record(
                session,
                action=action,
                outcome=outcome,
                principal_id=principal_id,
                project_id=project_id,
                target_type=target_type,
                target_id=target_id,
                details=details,
            )


__all__ = ["DatabaseAuditSink", "Outcome", "record"]
