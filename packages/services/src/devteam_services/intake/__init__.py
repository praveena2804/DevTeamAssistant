"""Project intake: validate → store → extract → chunk → persist, one file at a time.

Each file gets its own outcome; one bad file never stops the others (Spec §5.1, AC-B-02). A rejected
file leaves nothing behind (no stored bytes, no database rows). Auditing is the caller's job (the API
knows the principal and request ID).
"""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import BinaryIO, Literal

from sqlalchemy.orm import Session

from devteam_core.clock import Clock, system_clock
from devteam_core.config import UploadSettings
from devteam_core.ids import new_id
from devteam_core.logging import get_logger
from devteam_harness.validation import (
    MEDIA_TYPES,
    DocxLimits,
    UploadRejected,
    check_content,
    check_extension,
    sanitize_filename,
)
from devteam_services.extractor import (
    CHUNKER_VERSION,
    EXTRACTOR_VERSION,
    Chunk,
    Extraction,
    ExtractionFailed,
    chunk_blocks,
    extract,
)
from devteam_services.workspace import StoredUpload, WorkspaceManager
from devteam_state.models import DocumentChunk, SourceDocument
from devteam_state.repositories import documents as documents_repo

_log = get_logger("devteam_services.intake")
_DISPLAY_FALLBACK_CHARS = 60


@dataclass(frozen=True)
class IncomingFile:
    filename: str | None
    stream: BinaryIO


@dataclass
class FileOutcome:
    filename: str
    status: Literal["extracted", "rejected"]
    document_id: str | None = None
    role: str | None = None
    code: str | None = None
    message: str | None = None
    chunk_count: int = 0
    page_count: int | None = None
    warnings: list[dict[str, object]] = field(default_factory=list)


def _display_name(raw: str | None) -> str:
    """A safe label for a rejected file's name (never used as a path)."""
    if not raw:
        return "(no name)"
    printable = "".join(ch if ch.isprintable() else "?" for ch in raw)
    return printable[:_DISPLAY_FALLBACK_CHARS]


class IntakeService:
    def __init__(self, limits: UploadSettings, workspaces: WorkspaceManager, clock: Clock = system_clock):
        self.limits = limits
        self.workspaces = workspaces
        self.clock = clock
        self.docx_limits = DocxLimits(
            max_uncompressed_bytes=limits.max_docx_uncompressed_mb * 1024 * 1024,
            max_entries=limits.max_docx_entries,
            max_compression_ratio=limits.max_docx_compression_ratio,
        )

    def process(
        self, session: Session, *, project_id: str, principal_id: str, files: Sequence[IncomingFile]
    ) -> list[FileOutcome]:
        if len(files) > self.limits.max_files_per_upload:
            raise UploadRejected(
                "upload.too_many_files",
                f"Too many files at once: {len(files)}. Upload at most "
                f"{self.limits.max_files_per_upload} files per batch.",
            )
        outcomes = []
        for incoming in files:
            savepoint = session.begin_nested()
            try:
                outcome = self._process_one(session, project_id, principal_id, incoming)
            except Exception:
                savepoint.rollback()
                _log.exception("intake_file_failed", project_id=project_id)
                outcome = FileOutcome(
                    filename=_display_name(incoming.filename),
                    status="rejected",
                    code="upload.internal_error",
                    message="This file could not be processed because of an internal error.",
                )
            else:
                if outcome.status == "extracted":
                    savepoint.commit()
                else:
                    savepoint.rollback()
            outcomes.append(outcome)
        return outcomes

    def _process_one(
        self, session: Session, project_id: str, principal_id: str, incoming: IncomingFile
    ) -> FileOutcome:
        display = _display_name(incoming.filename)
        stored: StoredUpload | None = None
        try:
            name = sanitize_filename(incoming.filename)
            display = name
            ext = check_extension(name, self.limits.allowed_extensions)
            document_id = new_id("doc", self.clock)
            stored = self.workspaces.receive_upload(
                project_id, document_id, ext, incoming.stream, max_bytes=self.limits.max_file_bytes
            )
            check_content(stored.temp_path, ext, docx_limits=self.docx_limits)
            existing = documents_repo.find_by_sha256(session, project_id, stored.sha256)
            if existing is not None:
                raise UploadRejected(
                    "upload.duplicate",
                    f"This file was already uploaded to this project as '{existing.original_filename}'.",
                )
            extraction = extract(
                stored.temp_path,
                ext,
                max_pdf_pages=self.limits.max_pdf_pages,
                max_chars=self.limits.max_extracted_chars,
            )
            chunks = chunk_blocks(
                extraction.blocks,
                document_sha256=stored.sha256,
                display_name=name,
                target_chars=self.limits.chunk_target_chars,
                max_chars=self.limits.chunk_max_chars,
            )
            if not chunks:
                raise ExtractionFailed(
                    "upload.no_text",
                    "No readable text was found. The file may be a scanned image; "
                    "reading text from images is not supported yet.",
                )
        except (UploadRejected, ExtractionFailed) as exc:
            if stored is not None:
                self.workspaces.discard(stored)
            return FileOutcome(filename=display, status="rejected", code=exc.code, message=exc.message)
        except BaseException:
            if stored is not None:
                self.workspaces.discard(stored)
            raise

        try:
            return self._persist(
                session,
                project_id=project_id,
                principal_id=principal_id,
                name=name,
                stored=stored,
                extraction=extraction,
                chunks=chunks,
            )
        except BaseException:
            self.workspaces.discard(stored)
            raise

    def _persist(
        self,
        session: Session,
        *,
        project_id: str,
        principal_id: str,
        name: str,
        stored: StoredUpload,
        extraction: Extraction,
        chunks: list[Chunk],
    ) -> FileOutcome:
        role = "supporting" if documents_repo.has_primary(session, project_id) else "primary_brd"
        warnings = [asdict(w) for w in extraction.warnings]
        if not any(block.kind == "heading" for block in extraction.blocks):
            warnings.append(
                {
                    "code": "no_headings",
                    "message": "No section headings were found; references use page and paragraph only.",
                    "page": None,
                }
            )
        outline = [
            {"level": b.level, "title": b.text[:200], "page": b.page}
            for b in extraction.blocks
            if b.kind == "heading"
        ]
        self.workspaces.finalise(stored)
        document = SourceDocument(
            id=stored.document_id,
            project_id=project_id,
            role=role,
            original_filename=name,
            stored_name=stored.final_path.name,
            media_type=MEDIA_TYPES[stored.ext],
            size_bytes=stored.size,
            sha256=stored.sha256,
            status="extracted",
            page_count=extraction.page_count,
            chunk_count=len(chunks),
            char_count=extraction.char_count,
            warnings=warnings,
            outline=outline,
            extractor=extraction.extractor,
            extractor_version=f"{EXTRACTOR_VERSION}+chunker{CHUNKER_VERSION}",
            uploaded_by=principal_id,
            created_at=self.clock.now(),
        )
        rows = [
            DocumentChunk(
                id=c.id,
                project_id=project_id,
                document_id=stored.document_id,
                ordinal=c.ordinal,
                kind=c.kind,
                text=c.text,
                section_path=c.section_path,
                page_start=c.page_start,
                page_end=c.page_end,
                paragraph_start=c.paragraph_start,
                paragraph_end=c.paragraph_end,
                reference=c.reference,
                content_sha256=c.content_sha256,
            )
            for c in chunks
        ]
        documents_repo.add(session, document, rows)
        self.workspaces.write_extraction(
            project_id,
            stored.document_id,
            {
                "document_id": stored.document_id,
                "sha256": stored.sha256,
                "extractor": extraction.extractor,
                "page_count": extraction.page_count,
                "warnings": warnings,
                "blocks": [asdict(b) for b in extraction.blocks],
            },
        )
        return FileOutcome(
            filename=name,
            status="extracted",
            document_id=stored.document_id,
            role=role,
            chunk_count=len(chunks),
            page_count=extraction.page_count,
            warnings=warnings,
        )


__all__ = ["FileOutcome", "IncomingFile", "IntakeService"]
