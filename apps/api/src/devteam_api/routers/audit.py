"""Read-only views of the audit trail (HC-21)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from devteam_api.deps import DbSession, ProjectUser, limit_rate
from devteam_api.routers.projects import NOT_FOUND, load_project
from devteam_api.schemas import ERROR_RESPONSES, AuditEventOut, AuditIntegrity, AuditPage
from devteam_state.models import AuditEvent
from devteam_state.repositories import audit as audit_repo

router = APIRouter(
    prefix="/api", tags=["audit"], dependencies=[Depends(limit_rate)], responses=ERROR_RESPONSES
)


def _page(items: list[AuditEvent], total: int, offset: int, limit: int) -> AuditPage:
    return AuditPage(
        items=[
            AuditEventOut(
                id=e.id,
                ts=e.ts,
                principal_id=e.principal_id,
                action=e.action,
                project_id=e.project_id,
                target_type=e.target_type,
                target_id=e.target_id,
                outcome=e.outcome,
                request_id=e.request_id,
                details=e.details,
            )
            for e in items
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/audit", response_model=AuditPage, name="audit.list")
def list_audit(
    db: DbSession,
    _: ProjectUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    items, total = audit_repo.list_events(db, project_id=None, offset=offset, limit=limit)
    return _page(items, total, offset, limit)


@router.get("/audit/verify", response_model=AuditIntegrity, name="audit.verify")
def verify_audit(db: DbSession, _: ProjectUser) -> AuditIntegrity:
    """Recompute the whole hash chain and report the first broken entry, if any."""
    report = audit_repo.verify_chain(db)
    return AuditIntegrity(
        ok=report.ok,
        checked=report.checked,
        pre_chain_rows=report.pre_chain_rows,
        broken_at=report.broken_at,
        reason=report.reason,
    )


@router.get(
    "/projects/{project_id}/audit", response_model=AuditPage, name="audit.project", responses=NOT_FOUND
)
def list_project_audit(
    project_id: str,
    db: DbSession,
    _: ProjectUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    project = load_project(db, project_id)
    items, total = audit_repo.list_events(db, project_id=project.id, offset=offset, limit=limit)
    return _page(items, total, offset, limit)
