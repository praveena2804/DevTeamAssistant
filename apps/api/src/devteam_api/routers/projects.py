"""Projects: list, create, read, update. Deletion is excluded from Part B (D-31, DV-006)."""

from typing import Any

from fastapi import APIRouter, Depends, Request, status

from devteam_api.deps import Audit, DbSession, ProjectUser, api_error, limit_rate
from devteam_api.schemas import ERROR_RESPONSES, ErrorResponse, ProjectCreate, ProjectOut, ProjectUpdate
from devteam_core.ids import is_valid_id
from devteam_services.workspace import WorkspaceManager
from devteam_state.models import Project
from devteam_state.repositories import projects as projects_repo

router = APIRouter(
    prefix="/api", tags=["projects"], dependencies=[Depends(limit_rate)], responses=ERROR_RESPONSES
)
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Project not found"}
}


def _out(project: Project, document_count: int) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        description=project.description,
        status=project.status,
        created_at=project.created_at,
        updated_at=project.updated_at,
        document_count=document_count,
    )


def load_project(db: DbSession, project_id: str) -> Project:
    """404 for unknown *or malformed* IDs, so probing reveals nothing."""
    project = projects_repo.get(db, project_id) if is_valid_id(project_id, "prj") else None
    if project is None:
        raise api_error(status.HTTP_404_NOT_FOUND, "project.not_found", "Project not found.")
    return project


@router.get("/projects", response_model=list[ProjectOut], name="project.list")
def list_projects(db: DbSession, _: ProjectUser) -> list[ProjectOut]:
    return [_out(s.project, s.document_count) for s in projects_repo.list_all(db)]


@router.post(
    "/projects",
    response_model=ProjectOut,
    status_code=status.HTTP_201_CREATED,
    name="project.create",
    responses={409: {"model": ErrorResponse, "description": "Name already used"}},
)
def create_project(
    body: ProjectCreate,
    request: Request,
    db: DbSession,
    scope: Audit,
    principal: ProjectUser,
) -> ProjectOut:
    scope.set(name=body.name)
    if projects_repo.name_taken(db, body.name):
        raise api_error(
            status.HTTP_409_CONFLICT, "project.name_taken", "A project with this name already exists."
        )
    project = projects_repo.create(db, name=body.name, description=body.description, created_by=principal.id)
    workspaces: WorkspaceManager = request.app.state.workspaces
    workspaces.create(project.id)
    scope.set(project_id=project.id, target_type="project", target_id=project.id)
    return _out(project, 0)


@router.get("/projects/{project_id}", response_model=ProjectOut, name="project.read", responses=NOT_FOUND)
def read_project(project_id: str, db: DbSession, _: ProjectUser) -> ProjectOut:
    project = load_project(db, project_id)
    return _out(project, projects_repo.document_count(db, project.id))


@router.patch(
    "/projects/{project_id}",
    response_model=ProjectOut,
    name="project.update",
    responses={**NOT_FOUND, 409: {"model": ErrorResponse, "description": "Name already used"}},
)
def update_project(
    project_id: str,
    body: ProjectUpdate,
    db: DbSession,
    scope: Audit,
    _: ProjectUser,
) -> ProjectOut:
    project = load_project(db, project_id)
    scope.set(target_type="project", target_id=project.id, fields=sorted(body.model_dump(exclude_none=True)))
    if body.name is not None and projects_repo.name_taken(db, body.name, exclude_id=project.id):
        raise api_error(
            status.HTTP_409_CONFLICT, "project.name_taken", "A project with this name already exists."
        )
    projects_repo.update(db, project, name=body.name, description=body.description)
    return _out(project, projects_repo.document_count(db, project.id))
