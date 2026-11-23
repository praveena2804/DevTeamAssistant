"""Hash chain for the audit log (HC-21, HS-11), version 1.

Each chained event stores ``hash = SHA-256(prev_hash || "\\n" || canonical(event))``. Because every
hash depends on the one before it, editing, deleting, inserting or reordering any event breaks the
chain at that point, and :func:`devteam_state.repositories.audit.verify_chain` reports where.

Events written before the chain existed (Part B) are **not rewritten**: the anchor event (seq 1) stores
their count and a digest over their canonical form, so changes to them are detected too.

This module is imported by migration ``0003_harness_core``; the canonical form is versioned and must not
change. A new format would be ``canonical_v2`` with its own anchor.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol

GENESIS = "0" * 64
ANCHOR_ACTION = "audit.chain_anchor"
CHAIN_VERSION = 1


class EventLike(Protocol):
    id: str
    ts: datetime
    principal_id: str | None
    action: str
    project_id: str | None
    target_type: str | None
    target_id: str | None
    outcome: str
    request_id: str | None
    trace_id: str | None
    details: dict[str, Any]


def _ts(value: datetime) -> str:
    """UTC, naive, microseconds: identical whether the value was just created or read back from SQLite."""
    if value.tzinfo is not None:
        value = value.astimezone(UTC).replace(tzinfo=None)
    return value.isoformat(timespec="microseconds")


def canonical_v1(event: EventLike, seq: int | None) -> bytes:
    payload = {
        "v": CHAIN_VERSION,
        "seq": seq,
        "id": event.id,
        "ts": _ts(event.ts),
        "principal_id": event.principal_id,
        "action": event.action,
        "project_id": event.project_id,
        "target_type": event.target_type,
        "target_id": event.target_id,
        "outcome": event.outcome,
        "request_id": event.request_id,
        "trace_id": event.trace_id,
        "details": event.details,
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def link_hash(prev_hash: str, event: EventLike, seq: int) -> str:
    return hashlib.sha256(prev_hash.encode() + b"\n" + canonical_v1(event, seq)).hexdigest()


def pre_chain_digest(events: list[EventLike]) -> str:
    """Digest over events written before the chain (ordered by ts, id)."""
    digest = hashlib.sha256()
    for event in events:
        digest.update(hashlib.sha256(canonical_v1(event, None)).digest())
    return digest.hexdigest()
