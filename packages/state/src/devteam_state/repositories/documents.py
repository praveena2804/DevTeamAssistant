"""Source documents and chunks. Every function requires ``project_id`` (NFR-004 isolation)."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from devteam_state.models import DocumentChunk, SourceDocument


def add(session: Session, document: SourceDocument, chunks: Sequence[DocumentChunk]) -> SourceDocument:
    session.add(document)
    session.flush()
    session.add_all(chunks)
    session.flush()
    return document


def list_for_project(session: Session, project_id: str) -> list[SourceDocument]:
    return list(
        session.scalars(
            select(SourceDocument)
            .where(SourceDocument.project_id == project_id)
            .order_by(SourceDocument.created_at, SourceDocument.id)
        )
    )


def get(session: Session, project_id: str, document_id: str) -> SourceDocument | None:
    return session.scalars(
        select(SourceDocument).where(
            SourceDocument.project_id == project_id, SourceDocument.id == document_id
        )
    ).first()


def find_by_sha256(session: Session, project_id: str, sha256: str) -> SourceDocument | None:
    return session.scalars(
        select(SourceDocument).where(SourceDocument.project_id == project_id, SourceDocument.sha256 == sha256)
    ).first()


def has_primary(session: Session, project_id: str) -> bool:
    found = session.scalars(
        select(SourceDocument.id).where(
            SourceDocument.project_id == project_id, SourceDocument.role == "primary_brd"
        )
    ).first()
    return found is not None


def list_chunks(
    session: Session, project_id: str, document_id: str, *, offset: int, limit: int
) -> tuple[list[DocumentChunk], int]:
    where = (DocumentChunk.project_id == project_id, DocumentChunk.document_id == document_id)
    total = session.scalar(select(func.count(DocumentChunk.id)).where(*where)) or 0
    items = list(
        session.scalars(
            select(DocumentChunk).where(*where).order_by(DocumentChunk.ordinal).offset(offset).limit(limit)
        )
    )
    return items, int(total)
