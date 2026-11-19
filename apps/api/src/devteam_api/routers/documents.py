"""BRD upload, extraction detail and paginated chunks (FR-001, FR-003; AC-B-01…03)."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile, status

from devteam_api.deps import Audit, DbSession, ProjectUser, api_error, limit_rate, request_id
from devteam_api.routers.projects import load_project
from devteam_api.schemas import (
    ERROR_RESPONSES,
    ChunkOut,
    ChunkPage,
    DocumentDetail,
    DocumentOut,
    ErrorResponse,
    FileResult,
    OutlineItem,
    UploadResponse,
    WarningOut,
)
from devteam_core.ids import is_valid_id
from devteam_harness import audit
from devteam_harness.validation import UploadRejected
from devteam_services.intake import IncomingFile, IntakeService
from devteam_state.models import SourceDocument
from devteam_state.repositories import documents as documents_repo
from devteam_state.repositories import projects as projects_repo

router = APIRouter(
    prefix="/api", tags=["documents"], dependencies=[Depends(limit_rate)], responses=ERROR_RESPONSES
)
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Project or document not found"}
}


def _document_out(doc: SourceDocument) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        project_id=doc.project_id,
        role=doc.role,
        original_filename=doc.original_filename,
        media_type=doc.media_type,
        size_bytes=doc.size_bytes,
        sha256=doc.sha256,
        status=doc.status,
        page_count=doc.page_count,
        chunk_count=doc.chunk_count,
        char_count=doc.char_count,
        warnings=[WarningOut(**w) for w in doc.warnings],
        extractor=doc.extractor,
        extractor_version=doc.extractor_version,
        created_at=doc.created_at,
    )


def _load_document(db: DbSession, project_id: str, document_id: str) -> SourceDocument:
    project = load_project(db, project_id)
    doc = documents_repo.get(db, project.id, document_id) if is_valid_id(document_id, "doc") else None
    if doc is None:  # also the answer for a valid document ID that belongs to another project
        raise api_error(status.HTTP_404_NOT_FOUND, "document.not_found", "Document not found.")
    return doc


@router.post(
    "/projects/{project_id}/documents",
    response_model=UploadResponse,
    name="document.upload_batch",
    summary="Upload 1-10 files; each file gets its own result",
    responses={**NOT_FOUND, 400: {"model": ErrorResponse, "description": "Too many files"}},
)
def upload_documents(
    *,
    project_id: str,
    request: Request,
    db: DbSession,
    scope: Audit,
    files: Annotated[list[UploadFile], File(description="PDF, DOCX, TXT or MD files")],
    principal: ProjectUser,
) -> UploadResponse:
    project = load_project(db, project_id)
    scope.set(files=len(files))
    intake: IntakeService = request.app.state.intake
    try:
        outcomes = intake.process(
            db,
            project_id=project.id,
            principal_id=principal.id,
            files=[IncomingFile(filename=f.filename, stream=f.file) for f in files],
        )
    except UploadRejected as exc:
        raise api_error(status.HTTP_400_BAD_REQUEST, exc.code, exc.message) from exc
    for outcome in outcomes:  # one audit event per file (PART-B-PLAN §5.7)
        audit.record(
            db,
            action="document.upload",
            outcome="success" if outcome.status == "extracted" else "error",
            principal_id=principal.id,
            request_id=request_id(request),
            project_id=project.id,
            target_type="document",
            target_id=outcome.document_id,
            details={"filename": outcome.filename, "code": outcome.code, "chunks": outcome.chunk_count},
        )
    projects_repo.touch(db, project)
    extracted = sum(o.status == "extracted" for o in outcomes)
    scope.set(extracted=extracted, rejected=len(outcomes) - extracted)
    return UploadResponse(
        extracted=extracted,
        rejected=len(outcomes) - extracted,
        results=[
            FileResult(
                filename=o.filename,
                status=o.status,
                document_id=o.document_id,
                role=o.role,
                code=o.code,
                message=o.message,
                chunk_count=o.chunk_count,
                page_count=o.page_count,
                warnings=[WarningOut(**w) for w in o.warnings],
            )
            for o in outcomes
        ],
    )


@router.get(
    "/projects/{project_id}/documents",
    response_model=list[DocumentOut],
    name="document.list",
    responses=NOT_FOUND,
)
def list_documents(project_id: str, db: DbSession, _: ProjectUser) -> list[DocumentOut]:
    project = load_project(db, project_id)
    return [_document_out(d) for d in documents_repo.list_for_project(db, project.id)]


@router.get(
    "/projects/{project_id}/documents/{document_id}",
    response_model=DocumentDetail,
    name="document.read",
    responses=NOT_FOUND,
)
def read_document(project_id: str, document_id: str, db: DbSession, _: ProjectUser) -> DocumentDetail:
    doc = _load_document(db, project_id, document_id)
    return DocumentDetail(
        **_document_out(doc).model_dump(), outline=[OutlineItem(**item) for item in doc.outline]
    )


@router.get(
    "/projects/{project_id}/documents/{document_id}/chunks",
    response_model=ChunkPage,
    name="document.chunks",
    responses=NOT_FOUND,
)
def list_chunks(
    project_id: str,
    document_id: str,
    db: DbSession,
    _: ProjectUser,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ChunkPage:
    doc = _load_document(db, project_id, document_id)
    items, total = documents_repo.list_chunks(db, doc.project_id, doc.id, offset=offset, limit=limit)
    return ChunkPage(
        items=[
            ChunkOut(
                id=c.id,
                ordinal=c.ordinal,
                kind=c.kind,
                text=c.text,
                section_path=c.section_path,
                page_start=c.page_start,
                page_end=c.page_end,
                paragraph_start=c.paragraph_start,
                paragraph_end=c.paragraph_end,
                reference=c.reference,
            )
            for c in items
        ],
        total=total,
        offset=offset,
        limit=limit,
    )
