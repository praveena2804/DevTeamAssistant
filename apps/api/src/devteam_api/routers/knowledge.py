"""Project knowledge index status and model-call records (Part F.5/F.6; HC-08; AC-F-01).

The API never embeds or searches (D-57): it reads the index manifest and job queue from SQLite and queues a
rebuild for the worker. Model-call records are metadata only (never prompt, input or answer text).
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict

from devteam_api.deps import Audit, DbSession, ProjectUser, api_error, limit_rate
from devteam_api.routers.projects import load_project
from devteam_api.schemas import ERROR_RESPONSES
from devteam_core.ids import is_valid_id, new_id
from devteam_state.models import Run
from devteam_state.repositories import model_calls as model_calls_repo
from devteam_state.repositories import rag as rag_repo

router = APIRouter(
    prefix="/api", tags=["knowledge"], dependencies=[Depends(limit_rate)], responses=ERROR_RESPONSES
)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IndexJobOut(_Out):
    job_id: str
    kind: str
    status: str
    created_at: datetime
    finished_at: datetime | None
    error_code: str | None
    detail: str | None


class IndexStatusOut(_Out):
    project_id: str
    collection: str | None
    embedding_model: str | None
    embedding_digest: str | None  # first 12 characters
    chunker_version: str | None
    documents_extracted: int
    documents_indexed: int
    index_complete: bool
    latest_job: IndexJobOut | None


class ModelCallOut(_Out):
    call_id: str
    task_id: str | None
    agent: str | None
    service: str | None
    kind: str
    operation_id: str
    slot: str
    model_tag: str
    model_digest: str | None  # first 12 characters
    route_rule: str
    recovery_step: str
    cache_hit: bool
    input_tokens: int
    output_tokens: int
    duration_ms: int
    outcome: str
    error_code: str | None
    attempt: int


def _job_out(row: object) -> IndexJobOut | None:
    if row is None:
        return None
    return IndexJobOut(
        job_id=row.id, kind=row.kind, status=row.status, created_at=row.created_at,  # type: ignore[attr-defined]
        finished_at=row.finished_at, error_code=row.error_code, detail=row.detail,  # type: ignore[attr-defined]
    )  # fmt: skip


@router.get(
    "/projects/{project_id}/knowledge/index", response_model=IndexStatusOut, name="knowledge.index_status"
)
def index_status(project_id: str, db: DbSession, _: ProjectUser) -> IndexStatusOut:
    project = load_project(db, project_id)
    ready = rag_repo.ready_collection(db, project.id)
    documents = rag_repo.project_documents(db, project.id)
    indexed = rag_repo.indexed_documents(db, ready.id) if ready is not None else {}
    fresh = sum(
        1
        for d in documents
        if d.document_id in indexed and indexed[d.document_id].content_sha256 == d.content_sha256
    )
    return IndexStatusOut(
        project_id=project.id,
        collection=ready.name if ready else None,
        embedding_model=ready.embedding_model if ready else None,
        embedding_digest=ready.embedding_digest[:12] if ready else None,
        chunker_version=ready.chunker_version if ready else None,
        documents_extracted=len(documents),
        documents_indexed=fresh,
        index_complete=ready is not None and fresh == len(documents),
        latest_job=_job_out(rag_repo.latest_job(db, project.id)),
    )


@router.post(
    "/projects/{project_id}/knowledge/index",
    response_model=IndexJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    name="knowledge.rebuild",
)
def rebuild(project_id: str, db: DbSession, scope: Audit, principal: ProjectUser) -> IndexJobOut:
    project = load_project(db, project_id)
    job_id = rag_repo.queue_index_job(
        db, job_id=new_id("rij"), project_id=project.id, kind="rebuild", requested_by=principal.id,
        now=datetime.now(UTC),
    )  # fmt: skip
    db.flush()
    scope.set(target_type="project", target_id=project.id, job_id=job_id)
    job = rag_repo.latest_job(db, project.id)
    out = _job_out(job)
    assert out is not None  # noqa: S101 - just queued
    return out


@router.get(
    "/projects/{project_id}/runs/{run_id}/model-calls",
    response_model=list[ModelCallOut],
    name="knowledge.model_calls",
)
def model_calls(project_id: str, run_id: str, db: DbSession, _: ProjectUser) -> list[ModelCallOut]:
    project = load_project(db, project_id)
    run = db.get(Run, run_id) if is_valid_id(run_id, "run") else None
    if run is None or run.project_id != project.id:
        raise api_error(status.HTTP_404_NOT_FOUND, "run.not_found", "Run not found.")
    return [
        ModelCallOut(
            call_id=c.call_id, task_id=c.task_id, agent=c.agent.value if c.agent else None, service=c.service,
            kind=c.kind, operation_id=c.operation_id, slot=c.slot.value, model_tag=c.model_tag,
            model_digest=c.model_digest[:12] if c.model_digest else None, route_rule=c.route_rule.value,
            recovery_step=c.recovery_step.value, cache_hit=c.cache_hit, input_tokens=c.input_tokens,
            output_tokens=c.output_tokens, duration_ms=c.duration_ms, outcome=c.outcome,
            error_code=c.error_code,
            attempt=c.attempt,
        )
        for c in model_calls_repo.calls_for_run(db, project.id, run_id)
    ]  # fmt: skip
