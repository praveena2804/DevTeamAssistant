"""Repositories for the Part E tables: resource lease, template verifications, sandbox executions.

Guarantees the sandbox relies on:

* the resource lease has exactly one row; a live holder keeps it, a stale one (no heartbeat for
  ``ttl_s``) can be taken over (D-46); acquiring and releasing happen in short write transactions;
* a template verification is claimed by at most one worker (``queued → running`` inside the caller's
  write transaction); a finished result is never edited (database triggers);
* a sandbox execution is stored once, as its validated contract, and never edited (trigger).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.contracts.sandbox import (
    SandboxExecution,
    TemplateVerification,
    TemplateVerificationStatus,
)
from devteam_state.models import ResourceLock, SandboxExecutionRow, TemplateVerificationRow

Holder = Literal["llm", "sandbox"]
LOCK_ROW_ID = 1


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# ----------------------------------------------------------------------------------- resource lease (D-46)


@dataclass(frozen=True, slots=True)
class ResourceLeaseResult:
    acquired: bool
    held_by: Holder | None = None  # who holds it when not acquired
    taken_over_from: str | None = None


def acquire_resource_lock(
    session: Session, *, holder: Holder, owner: str, pid: int, ttl_s: float, clock: Clock = system_clock
) -> ResourceLeaseResult:
    """Take the single LLM/sandbox lease if it is free, already ours, or stale."""
    now = clock.now()
    row = session.get(ResourceLock, LOCK_ROW_ID)
    if row is None:
        try:
            with session.begin_nested():
                session.add(
                    ResourceLock(
                        id=LOCK_ROW_ID, holder=holder, owner=owner, pid=pid, acquired_at=now, heartbeat_at=now
                    )
                )
        except IntegrityError:
            return ResourceLeaseResult(acquired=False)
        return ResourceLeaseResult(acquired=True)
    if row.owner == owner:
        row.heartbeat_at = now
        session.flush()
        return ResourceLeaseResult(acquired=True)
    if now - _aware(row.heartbeat_at) <= timedelta(seconds=ttl_s):
        return ResourceLeaseResult(acquired=False, held_by=row.holder)  # type: ignore[arg-type]
    previous = row.owner
    row.holder, row.owner, row.pid, row.acquired_at, row.heartbeat_at = holder, owner, pid, now, now
    session.flush()
    return ResourceLeaseResult(acquired=True, taken_over_from=previous)


def renew_resource_lock(session: Session, *, owner: str, clock: Clock = system_clock) -> bool:
    row = session.get(ResourceLock, LOCK_ROW_ID)
    if row is None or row.owner != owner:
        return False
    row.heartbeat_at = clock.now()
    session.flush()
    return True


def release_resource_lock(session: Session, *, owner: str) -> bool:
    row = session.get(ResourceLock, LOCK_ROW_ID)
    if row is None or row.owner != owner:
        return False
    session.delete(row)
    session.flush()
    return True


def current_resource_lock(session: Session) -> ResourceLock | None:
    return session.get(ResourceLock, LOCK_ROW_ID)


# ---------------------------------------------------------------------------- template verifications


def _to_contract(row: TemplateVerificationRow) -> TemplateVerification:
    return TemplateVerification.model_validate(
        {
            "verification_id": row.id,
            "template_id": row.template_id,
            "template_version": row.template_version,
            "template_checksum": row.template_checksum,
            "status": row.status,
            "recipes": row.recipes,
            "image_digests": row.image_digests,
            "duration_ms": row.duration_ms,
            "peak_memory_mb": row.peak_memory_mb,
            "requested_by": row.requested_by,
            "requested_at": _aware(row.requested_at),
            "finished_at": _aware(row.finished_at) if row.finished_at else None,
            "reason": row.reason,
        }
    )


def add_verification(session: Session, verification: TemplateVerification) -> None:
    if verification.status is not TemplateVerificationStatus.QUEUED:
        raise ValueError("a new template verification starts as queued")
    session.add(
        TemplateVerificationRow(
            id=verification.verification_id,
            template_id=verification.template_id,
            template_version=verification.template_version,
            template_checksum=verification.template_checksum,
            status=verification.status.value,
            recipes=[],
            image_digests={},
            requested_by=verification.requested_by,
            requested_at=verification.requested_at,
        )
    )
    session.flush()


def get_verification(session: Session, verification_id: str) -> TemplateVerification | None:
    row = session.get(TemplateVerificationRow, verification_id)
    return None if row is None else _to_contract(row)


def claim_next_verification(session: Session, *, clock: Clock = system_clock) -> TemplateVerification | None:
    """Move the oldest queued verification to ``running`` and return it (one claimer only)."""
    row = session.scalars(
        select(TemplateVerificationRow)
        .where(TemplateVerificationRow.status == TemplateVerificationStatus.QUEUED.value)
        .order_by(TemplateVerificationRow.requested_at, TemplateVerificationRow.id)
        .limit(1)
    ).first()
    if row is None:
        return None
    claimed = session.execute(
        update(TemplateVerificationRow)
        .where(
            TemplateVerificationRow.id == row.id,
            TemplateVerificationRow.status == TemplateVerificationStatus.QUEUED.value,
        )
        .values(status=TemplateVerificationStatus.RUNNING.value, started_at=clock.now())
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        return None
    session.flush()
    session.refresh(row)
    return _to_contract(row)


def finish_verification(session: Session, result: TemplateVerification) -> None:
    """Store a final result (passed / failed / blocked). The contract has already checked honesty."""
    final = {
        TemplateVerificationStatus.PASSED,
        TemplateVerificationStatus.FAILED,
        TemplateVerificationStatus.BLOCKED,
    }
    if result.status not in final:
        raise ValueError("finish_verification needs a final status")
    row = session.get(TemplateVerificationRow, result.verification_id)
    if row is None:
        raise LookupError(result.verification_id)
    row.status = result.status.value
    row.template_checksum = result.template_checksum
    row.recipes = [outcome.model_dump(mode="json") for outcome in result.recipes]
    row.image_digests = {str(key): value for key, value in result.image_digests.items()}
    row.duration_ms = result.duration_ms
    row.peak_memory_mb = result.peak_memory_mb
    row.finished_at = result.finished_at
    row.reason = result.reason
    session.flush()


def mark_stale(
    session: Session, *, template_id: str, current_checksum: str, current_digests: dict[str, str]
) -> int:
    """Flag finished results whose template or image no longer matches as ``stale``. Returns how many."""
    rows = session.scalars(
        select(TemplateVerificationRow).where(
            TemplateVerificationRow.template_id == template_id,
            TemplateVerificationRow.status.in_(["passed", "failed", "blocked"]),
        )
    ).all()
    changed = 0
    for row in rows:
        digests_match = all(
            current_digests.get(key) == value for key, value in (row.image_digests or {}).items()
        )
        if row.template_checksum != current_checksum or not digests_match:
            row.status = TemplateVerificationStatus.STALE.value
            changed += 1
    session.flush()
    return changed


def latest_verifications(session: Session) -> dict[str, TemplateVerification]:
    """The newest verification per template (any status)."""
    rows = session.scalars(
        select(TemplateVerificationRow).order_by(
            TemplateVerificationRow.requested_at.desc(), TemplateVerificationRow.id.desc()
        )
    ).all()
    latest: dict[str, TemplateVerification] = {}
    for row in rows:
        latest.setdefault(row.template_id, _to_contract(row))
    return latest


def latest_finished_verifications(session: Session) -> dict[str, TemplateVerification]:
    """The newest *finished* verification per template (passed, failed, blocked or stale).

    Queued and running requests are ignored: an older result stays authoritative until a new one finishes
    (Part G effective catalog status).
    """
    rows = session.scalars(
        select(TemplateVerificationRow)
        .where(TemplateVerificationRow.status.in_(["passed", "failed", "blocked", "stale"]))
        .order_by(TemplateVerificationRow.requested_at.desc(), TemplateVerificationRow.id.desc())
    ).all()
    latest: dict[str, TemplateVerification] = {}
    for row in rows:
        latest.setdefault(row.template_id, _to_contract(row))
    return latest


def has_active_verification(session: Session, template_id: str) -> bool:
    return (
        session.scalars(
            select(TemplateVerificationRow.id).where(
                TemplateVerificationRow.template_id == template_id,
                TemplateVerificationRow.status.in_(["queued", "running"]),
            )
        ).first()
        is not None
    )


# ----------------------------------------------------------------------------------- sandbox executions


def record_execution(session: Session, execution: SandboxExecution) -> None:
    session.add(
        SandboxExecutionRow(
            id=execution.execution_id,
            project_id=execution.project_id,
            run_id=execution.run_id,
            verification_id=execution.verification_id,
            recipe=execution.recipe,
            status=execution.status.value,
            payload=execution.model_dump(mode="json"),
            started_at=execution.started_at,
            finished_at=execution.finished_at,
        )
    )
    session.flush()


def executions_for_verification(session: Session, verification_id: str) -> list[SandboxExecution]:
    rows = session.scalars(
        select(SandboxExecutionRow)
        .where(SandboxExecutionRow.verification_id == verification_id)
        .order_by(SandboxExecutionRow.started_at, SandboxExecutionRow.id)
    ).all()
    return [SandboxExecution.model_validate(row.payload) for row in rows]


def executions_for_project(session: Session, project_id: str) -> list[SandboxExecution]:
    rows = session.scalars(
        select(SandboxExecutionRow)
        .where(SandboxExecutionRow.project_id == project_id)
        .order_by(SandboxExecutionRow.started_at, SandboxExecutionRow.id)
    ).all()
    return [SandboxExecution.model_validate(row.payload) for row in rows]
