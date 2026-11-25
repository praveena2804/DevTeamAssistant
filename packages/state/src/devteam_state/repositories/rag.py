"""RAG index manifest and jobs (Part F.5; HC-08). SQLite is the source of truth; the vector store is not."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from devteam_state.models import (
    DocumentChunk,
    RagCollectionRow,
    RagIndexDocumentRow,
    RagIndexJobRow,
    SourceDocument,
)

INDEXABLE = "extracted"  # only fully extracted documents are indexed


@dataclass(frozen=True, slots=True)
class DocumentState:
    document_id: str
    content_sha256: str
    chunker_version: str
    chunk_count: int


# ------------------------------------------------------------------------------------- jobs


def queue_index_job(
    session: Session, *, job_id: str, project_id: str, kind: str, requested_by: str, now: datetime
) -> str:
    """Queue an index/rebuild job; one active (queued or running) job per project at a time."""
    active = session.scalar(
        select(RagIndexJobRow).where(
            RagIndexJobRow.project_id == project_id, RagIndexJobRow.status.in_(("queued", "running"))
        )
    )
    if active is not None:
        return active.id
    session.add(
        RagIndexJobRow(
            id=job_id, project_id=project_id, kind=kind, status="queued", requested_by=requested_by,
            created_at=now,
        )
    )  # fmt: skip
    return job_id


def claim_next_index_job(session: Session, *, now: datetime) -> RagIndexJobRow | None:
    job = session.scalar(
        select(RagIndexJobRow).where(RagIndexJobRow.status == "queued").order_by(RagIndexJobRow.created_at)
    )
    if job is not None:
        job.status = "running"
        job.started_at = now
    return job


def finish_index_job(
    session: Session, job_id: str, *, status: str, now: datetime, error_code: str | None = None,
    detail: str | None = None,
) -> None:  # fmt: skip
    job = session.get(RagIndexJobRow, job_id)
    if job is None:
        return
    job.status = status
    job.finished_at = now
    job.error_code = error_code
    job.detail = detail[:500] if detail else None


def latest_job(session: Session, project_id: str) -> RagIndexJobRow | None:
    return session.scalar(
        select(RagIndexJobRow)
        .where(RagIndexJobRow.project_id == project_id)
        .order_by(RagIndexJobRow.created_at.desc())
    )


# -------------------------------------------------------------------------------- collections


def ready_collection(session: Session, project_id: str) -> RagCollectionRow | None:
    return session.scalar(
        select(RagCollectionRow).where(
            RagCollectionRow.project_id == project_id, RagCollectionRow.status == "ready"
        )
    )


def collections(session: Session, project_id: str) -> list[RagCollectionRow]:
    return list(session.scalars(select(RagCollectionRow).where(RagCollectionRow.project_id == project_id)))


def add_collection(session: Session, row: RagCollectionRow) -> None:
    session.add(row)


def set_collection_status(session: Session, collection_id: str, status: str, *, now: datetime) -> None:
    row = session.get(RagCollectionRow, collection_id)
    if row is not None:
        row.status = status
        row.updated_at = now


def drop_collection_record(session: Session, collection_id: str) -> None:
    session.execute(delete(RagIndexDocumentRow).where(RagIndexDocumentRow.collection_id == collection_id))
    session.execute(delete(RagCollectionRow).where(RagCollectionRow.id == collection_id))


def indexed_documents(session: Session, collection_id: str) -> dict[str, RagIndexDocumentRow]:
    rows = session.scalars(
        select(RagIndexDocumentRow).where(RagIndexDocumentRow.collection_id == collection_id)
    )
    return {r.document_id: r for r in rows}


def record_document(
    session: Session, *, collection_id: str, project_id: str, state: DocumentState, now: datetime
) -> None:
    session.merge(
        RagIndexDocumentRow(
            collection_id=collection_id, document_id=state.document_id, project_id=project_id,
            content_sha256=state.content_sha256, chunker_version=state.chunker_version,
            chunk_count=state.chunk_count, indexed_at=now,
        )
    )  # fmt: skip


def forget_document(session: Session, collection_id: str, document_id: str) -> None:
    session.execute(
        delete(RagIndexDocumentRow).where(
            RagIndexDocumentRow.collection_id == collection_id, RagIndexDocumentRow.document_id == document_id
        )
    )


# ----------------------------------------------------------------------------------- sources


def project_documents(session: Session, project_id: str) -> list[DocumentState]:
    """Every extracted document of the project with its checksum, chunker version and chunk count."""
    docs = session.scalars(
        select(SourceDocument).where(
            SourceDocument.project_id == project_id, SourceDocument.status == INDEXABLE
        )
    )
    out: list[DocumentState] = []
    for doc in docs:
        versions = set(
            session.scalars(
                select(DocumentChunk.chunker_version).where(
                    DocumentChunk.project_id == project_id, DocumentChunk.document_id == doc.id
                )
            )
        )
        version = versions.pop() if len(versions) == 1 else "mixed"
        out.append(DocumentState(doc.id, doc.sha256, version or "unknown", doc.chunk_count))
    return sorted(out, key=lambda d: d.document_id)


def chunks_of(session: Session, project_id: str, document_id: str) -> list[DocumentChunk]:
    return list(
        session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.project_id == project_id, DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.ordinal)
        )
    )


def chunks_by_ids(session: Session, project_id: str, chunk_ids: list[str]) -> dict[str, DocumentChunk]:
    """The project's own chunks among ``chunk_ids`` (a foreign or deleted ID is simply absent)."""
    if not chunk_ids:
        return {}
    rows = session.scalars(
        select(DocumentChunk).where(DocumentChunk.project_id == project_id, DocumentChunk.id.in_(chunk_ids))
    )
    return {r.id: r for r in rows}


__all__ = [
    "DocumentState",
    "add_collection",
    "chunks_by_ids",
    "chunks_of",
    "claim_next_index_job",
    "collections",
    "drop_collection_record",
    "finish_index_job",
    "forget_document",
    "indexed_documents",
    "latest_job",
    "project_documents",
    "queue_index_job",
    "ready_collection",
    "record_document",
    "set_collection_status",
]
