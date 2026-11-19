"""Project repository."""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.ids import new_id
from devteam_state.models import Project, SourceDocument


@dataclass(frozen=True)
class ProjectSummary:
    project: Project
    document_count: int


def clean_name(name: str) -> str:
    return " ".join(name.split())


def name_key(name: str) -> str:
    return clean_name(name).casefold()


def name_taken(session: Session, name: str, *, exclude_id: str | None = None) -> bool:
    query = select(Project.id).where(Project.name_key == name_key(name))
    if exclude_id is not None:
        query = query.where(Project.id != exclude_id)
    return session.scalars(query).first() is not None


def create(
    session: Session, *, name: str, description: str, created_by: str, clock: Clock = system_clock
) -> Project:
    now = clock.now()
    project = Project(
        id=new_id("prj", clock),
        name=clean_name(name),
        name_key=name_key(name),
        description=description,
        status="active",
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(project)
    session.flush()
    return project


def get(session: Session, project_id: str) -> Project | None:
    return session.get(Project, project_id)


def list_all(session: Session) -> list[ProjectSummary]:
    counts = (
        select(SourceDocument.project_id, func.count(SourceDocument.id).label("n"))
        .group_by(SourceDocument.project_id)
        .subquery()
    )
    rows = session.execute(
        select(Project, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.project_id == Project.id)
        .order_by(Project.updated_at.desc(), Project.id)
    ).all()
    return [ProjectSummary(project=row[0], document_count=int(row[1])) for row in rows]


def document_count(session: Session, project_id: str) -> int:
    return int(
        session.scalar(select(func.count(SourceDocument.id)).where(SourceDocument.project_id == project_id))
        or 0
    )


def update(
    session: Session,
    project: Project,
    *,
    name: str | None = None,
    description: str | None = None,
    clock: Clock = system_clock,
) -> Project:
    if name is not None:
        project.name = clean_name(name)
        project.name_key = name_key(name)
    if description is not None:
        project.description = description
    project.updated_at = clock.now()
    session.flush()
    return project


def touch(session: Session, project: Project, *, clock: Clock = system_clock) -> None:
    project.updated_at = clock.now()
    session.flush()
