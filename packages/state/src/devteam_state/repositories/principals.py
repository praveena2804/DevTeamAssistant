"""Principals and role assignments (HC-15)."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.ids import new_id
from devteam_state.models import Principal, PrincipalRole


@dataclass(frozen=True)
class PrincipalRecord:
    id: str
    kind: str
    display_name: str
    roles: frozenset[str]


def ensure_local_human(
    session: Session, *, display_name: str, roles: frozenset[str], clock: Clock = system_clock
) -> PrincipalRecord:
    """Return the single local human principal, creating it (and any missing roles) on first use."""
    principal = session.scalars(
        select(Principal).where(Principal.kind == "human").order_by(Principal.created_at, Principal.id)
    ).first()
    if principal is None:
        principal = Principal(
            id=new_id("prn", clock), kind="human", display_name=display_name, created_at=clock.now()
        )
        session.add(principal)
        session.flush()
    existing = set(
        session.scalars(select(PrincipalRole.role).where(PrincipalRole.principal_id == principal.id))
    )
    for role in sorted(roles - existing):
        session.add(PrincipalRole(principal_id=principal.id, role=role))
    session.flush()
    return PrincipalRecord(
        id=principal.id,
        kind=principal.kind,
        display_name=principal.display_name,
        roles=frozenset(existing | roles),
    )
