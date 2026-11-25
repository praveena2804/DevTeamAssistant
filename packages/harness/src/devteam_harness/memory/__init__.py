"""Project memory (HC-08; Spec §10.1 "approved project specification and decisions"). Read-only.

Facts are read from SQLite (the source of truth), never from the vector store: the frozen specification
versions (with checksum and requirement keys) and the recorded human decisions. Each fact is short,
metadata-only text the Context Manager may add as a ``memory`` item; it never contains document text.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from devteam_state.models import ApprovalRow, SpecVersionRow


@dataclass(frozen=True, slots=True)
class MemoryFact:
    kind: str  # spec_version | decision
    text: str
    reference: str


class ProjectMemory:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def facts(self, project_id: str, *, limit: int = 20) -> tuple[MemoryFact, ...]:
        with self._sessions() as session:
            specs = session.scalars(
                select(SpecVersionRow)
                .where(SpecVersionRow.project_id == project_id, SpecVersionRow.status == "frozen")
                .order_by(SpecVersionRow.version.desc())
                .limit(limit)
            )
            out: list[MemoryFact] = []
            for spec in specs:
                keys = spec.payload.get("requirement_keys") if isinstance(spec.payload, dict) else None
                key_text = ", ".join(str(k) for k in keys[:50]) if isinstance(keys, list) else "none recorded"
                out.append(
                    MemoryFact(
                        "spec_version",
                        f"Approved specification v{spec.version} (checksum {spec.checksum[:12]}): "
                        f"requirements {key_text}.",
                        spec.id,
                    )
                )
            decisions = session.scalars(
                select(ApprovalRow)
                .where(ApprovalRow.project_id == project_id)
                .order_by(ApprovalRow.decided_at.desc())
                .limit(limit)
            )
            out.extend(
                MemoryFact(
                    "decision",
                    f"Decision '{a.approval_type}': {a.decision} on {a.decided_at.date().isoformat()}.",
                    a.id,
                )
                for a in decisions
            )
        return tuple(out[:limit])


__all__ = ["MemoryFact", "ProjectMemory"]
