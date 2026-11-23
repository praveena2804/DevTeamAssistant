"""Spec-version records (D-39): draft → frozen, checksum recomputable.

The checksum is the SHA-256 of the canonical JSON of the payload (sorted keys, no whitespace). The
build-entry guard (D.3) re-reads the row and calls :func:`checksum_matches`, so an edit made outside
this module, for example directly in the database file with the triggers removed, is detected.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.ids import new_id
from devteam_state.models import SpecVersionRow

DRAFT = "draft"
FROZEN = "frozen"


class SpecVersionError(ValueError):
    """A spec version operation was refused (wrong project, already frozen, ...)."""


def spec_checksum(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_draft(
    session: Session, *, project_id: str, run_id: str, payload: dict[str, Any], clock: Clock = system_clock
) -> SpecVersionRow:
    """Add the project's next spec version as a draft (version numbers start at 1, never reused)."""
    latest = session.scalar(
        select(func.max(SpecVersionRow.version)).where(SpecVersionRow.project_id == project_id)
    )
    row = SpecVersionRow(
        id=new_id("spv", clock),
        project_id=project_id,
        run_id=run_id,
        version=(latest or 0) + 1,
        status=DRAFT,
        checksum=spec_checksum(payload),
        payload=payload,
        created_at=clock.now(),
    )
    session.add(row)
    session.flush()
    return row


def get_spec_version(session: Session, project_id: str, spec_version_id: str) -> SpecVersionRow | None:
    row = session.get(SpecVersionRow, spec_version_id)
    return row if row is not None and row.project_id == project_id else None


def freeze(
    session: Session,
    *,
    project_id: str,
    spec_version_id: str,
    approval_id: str,
    approved_by: str,
    clock: Clock = system_clock,
) -> SpecVersionRow:
    """Mark a draft frozen. The caller (Spec Freeze service) has already verified the approval."""
    row = get_spec_version(session, project_id, spec_version_id)
    if row is None:
        raise SpecVersionError("spec version not found for this project")
    if row.status != DRAFT:
        raise SpecVersionError("only a draft spec version can be frozen")
    if not checksum_matches(row):
        raise SpecVersionError("spec version content does not match its checksum")
    row.status = FROZEN
    row.approval_id = approval_id
    row.approved_by = approved_by
    row.frozen_at = clock.now()
    session.flush()
    return row


def checksum_matches(row: SpecVersionRow) -> bool:
    return spec_checksum(row.payload) == row.checksum
