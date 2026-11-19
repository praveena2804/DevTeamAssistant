"""Document extraction (FR-003): PDF, DOCX, TXT, MD (D-22 (b)) → ordered blocks → referenced chunks."""

from pathlib import Path

from devteam_services.extractor.chunker import CHUNKER_VERSION, Chunk, chunk_blocks, chunk_id
from devteam_services.extractor.models import Block, Extraction, ExtractionFailed, ExtractionWarning
from devteam_services.extractor.pdf import extract_pdf
from devteam_services.extractor.text import decode_text, extract_text
from devteam_services.extractor.word import extract_docx

EXTRACTOR_VERSION = "1"


def extract(path: Path, ext: str, *, max_pdf_pages: int, max_chars: int) -> Extraction:
    """Extract one validated file. Raises ``ExtractionFailed`` with a plain-language message."""
    if ext == ".pdf":
        extraction = extract_pdf(path, max_pages=max_pdf_pages)
    elif ext == ".docx":
        extraction = extract_docx(path)
    elif ext in {".txt", ".md"}:
        extraction = extract_text(path.read_bytes(), markdown=ext == ".md")
    else:  # pragma: no cover - the extension allowlist is checked before extraction
        raise ExtractionFailed("upload.unsupported_type", "This file type is not supported.")
    return _truncate(extraction, max_chars)


def _truncate(extraction: Extraction, max_chars: int) -> Extraction:
    if extraction.char_count <= max_chars:
        return extraction
    kept: list[Block] = []
    used = 0
    for block in extraction.blocks:
        if used + len(block.text) > max_chars:
            break
        kept.append(block)
        used += len(block.text)
    extraction.blocks = kept
    extraction.warnings.append(
        ExtractionWarning(
            "truncated", f"The document is very long; only the first {max_chars:,} characters were kept."
        )
    )
    return extraction


__all__ = [
    "CHUNKER_VERSION",
    "EXTRACTOR_VERSION",
    "Block",
    "Chunk",
    "Extraction",
    "ExtractionFailed",
    "ExtractionWarning",
    "chunk_blocks",
    "chunk_id",
    "decode_text",
    "extract",
]
