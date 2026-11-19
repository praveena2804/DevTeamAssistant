"""Heading-aware, deterministic chunker.

* A new chunk starts at every heading; whole paragraphs are packed up to ``target_chars``.
* A paragraph longer than ``max_chars`` is split at sentence or word boundaries.
* Tables become their own ``table`` chunks.
* Paragraph numbers (¶) count paragraphs and tables from the start of the document (1-based).
* Chunk IDs depend only on the document checksum, the chunker version and the ordinal, so the same file
  always produces the same IDs (PART-B-PLAN §4).
"""

import hashlib
import re
from dataclasses import dataclass, field

from devteam_services.extractor.models import Block

CHUNKER_VERSION = "1"
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_MAX_TITLE_CHARS = 80
EN_DASH = chr(0x2013)


@dataclass(frozen=True)
class Chunk:
    id: str
    ordinal: int
    kind: str  # text | table
    text: str
    section_path: str
    page_start: int | None
    page_end: int | None
    paragraph_start: int  # 0 when the chunk holds only a heading
    paragraph_end: int
    reference: str
    content_sha256: str


@dataclass
class _Buffer:
    parts: list[str] = field(default_factory=list)
    pages: list[int] = field(default_factory=list)
    paragraphs: list[int] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(len(p) for p in self.parts) + max(len(self.parts) - 1, 0) * 2

    def empty(self) -> bool:
        return not self.parts


def chunk_id(document_sha256: str, ordinal: int) -> str:
    digest = hashlib.sha256(f"{document_sha256}:{CHUNKER_VERSION}:{ordinal}".encode()).hexdigest()
    return f"chk_{digest[:26]}"


def _range(values: list[int], prefix: str) -> str | None:
    if not values:
        return None
    low, high = min(values), max(values)
    return f"{prefix}{low}" if low == high else f"{prefix}{low}{EN_DASH}{high}"


def _split_long(text: str, target: int, hard_max: int) -> list[str]:
    pieces: list[str] = []
    current = ""
    for raw in _SENTENCE_END.split(text):
        sentence = raw
        while len(sentence) > hard_max:
            cut = sentence.rfind(" ", 0, hard_max)
            cut = cut if cut > hard_max // 2 else hard_max
            head, sentence = sentence[:cut].rstrip(), sentence[cut:].lstrip()
            if current:
                pieces.append(current)
                current = ""
            pieces.append(head)
        if current and len(current) + 1 + len(sentence) > target:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_blocks(  # noqa: PLR0915 - one linear pass; splitting it hides the packing rules
    blocks: list[Block],
    *,
    document_sha256: str,
    display_name: str,
    target_chars: int,
    max_chars: int,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    sections: list[tuple[int, str]] = []
    buffer = _Buffer()
    paragraph_no = 0

    def section_path() -> str:
        return " > ".join(title for _, title in sections)

    def emit(kind: str, text: str, pages: list[int], paragraphs: list[int]) -> None:
        ordinal = len(chunks)
        path = section_path()
        ref_parts = [display_name]
        page_ref = _range(pages, "p.")
        if page_ref:
            ref_parts.append(page_ref)
        if sections:
            ref_parts.append(f"§ {sections[-1][1]}")
        para_ref = _range(paragraphs, "¶")
        if para_ref:
            ref_parts.append(para_ref)
        chunks.append(
            Chunk(
                id=chunk_id(document_sha256, ordinal),
                ordinal=ordinal,
                kind=kind,
                text=text,
                section_path=path,
                page_start=min(pages) if pages else None,
                page_end=max(pages) if pages else None,
                paragraph_start=min(paragraphs) if paragraphs else 0,
                paragraph_end=max(paragraphs) if paragraphs else 0,
                reference=" · ".join(ref_parts),
                content_sha256=hashlib.sha256(text.encode()).hexdigest(),
            )
        )

    def flush() -> None:
        if not buffer.empty():
            emit("text", "\n\n".join(buffer.parts), buffer.pages, buffer.paragraphs)
        buffer.parts.clear()
        buffer.pages.clear()
        buffer.paragraphs.clear()

    for block in blocks:
        pages = [block.page] if block.page is not None else []
        if block.kind == "heading":
            flush()
            title = block.text[:_MAX_TITLE_CHARS]
            while sections and sections[-1][0] >= block.level:
                sections.pop()
            sections.append((block.level, title))
            buffer.parts.append(block.text)  # the heading opens the first chunk of its section
            buffer.pages.extend(pages)
            continue
        paragraph_no += 1
        if block.kind == "table":
            flush()
            rows = block.text.split("\n")
            piece: list[str] = []
            for row in rows:
                if piece and sum(len(r) + 1 for r in piece) + len(row) > max_chars:
                    emit("table", "\n".join(piece), pages, [paragraph_no])
                    piece = []
                piece.append(row[:max_chars])
            if piece:
                emit("table", "\n".join(piece), pages, [paragraph_no])
            continue
        if len(block.text) > max_chars:
            for part in _split_long(block.text, target_chars, max_chars):
                if not buffer.empty() and buffer.size + len(part) > target_chars:
                    flush()
                buffer.parts.append(part)
                buffer.pages.extend(pages)
                buffer.paragraphs.append(paragraph_no)
                flush()
            continue
        has_paragraphs = bool(buffer.paragraphs)
        if has_paragraphs and buffer.size + 2 + len(block.text) > target_chars:
            flush()
        buffer.parts.append(block.text)
        buffer.pages.extend(pages)
        buffer.paragraphs.append(paragraph_no)
    flush()
    return chunks
