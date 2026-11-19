"""PDF extraction: pypdf for encryption and page count, pdfplumber for words, tables and font sizes.

PDFs have no real heading structure. Headings are inferred (best effort) from font size and boldness;
page numbers are always exact, so every reference keeps its page even when headings are missed.
Image-only (scanned) pages yield no text and are reported as warnings; OCR is out of scope (Spec §5.1).
"""

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pdfplumber
from pypdf import PasswordType, PdfReader
from pypdf.errors import PdfReadError, PyPdfError

from devteam_services.extractor.models import Block, Extraction, ExtractionFailed, ExtractionWarning

_LINE_TOLERANCE = 2.5
_HEADING_SIZE_RATIO = 1.15
_MAX_HEADING_CHARS = 120
_MAX_LEVELS = 6


@dataclass
class _Line:
    page: int
    text: str
    size: float
    bold: bool
    top: float
    bottom: float


@dataclass
class _Table:
    page: int
    top: float
    text: str


def _corrupt() -> ExtractionFailed:
    return ExtractionFailed("upload.corrupt", "This PDF file is damaged or cannot be read.")


def _encrypted() -> ExtractionFailed:
    return ExtractionFailed(
        "upload.encrypted",
        "This PDF is password-protected. Remove the password and upload it again.",
    )


def _open_password(path: Path, max_pages: int) -> tuple[str | None, int]:
    """Return the password to use (``""`` for owner-password-only files) and the page count."""
    try:
        reader = PdfReader(path)
        password: str | None = None
        if reader.is_encrypted:
            try:
                result = reader.decrypt("")
            except (PyPdfError, NotImplementedError) as exc:
                raise _encrypted() from exc
            if result == PasswordType.NOT_DECRYPTED:
                raise _encrypted()
            password = ""
        page_count = len(reader.pages)
    except ExtractionFailed:
        raise
    except (PdfReadError, PyPdfError, ValueError, KeyError, OSError) as exc:
        raise _corrupt() from exc
    if page_count == 0:
        raise _corrupt()
    if page_count > max_pages:
        raise ExtractionFailed(
            "upload.too_many_pages",
            f"This PDF has {page_count} pages; the limit is {max_pages}. Split it into smaller files.",
        )
    return password, page_count


def _inside(word: dict[str, Any], bbox: tuple[float, float, float, float]) -> bool:
    x0, top, x1, bottom = bbox
    cx = (word["x0"] + word["x1"]) / 2
    cy = (word["top"] + word["bottom"]) / 2
    return bool(x0 <= cx <= x1 and top <= cy <= bottom)


def _page_lines(page: Any, page_no: int, table_boxes: list[tuple[float, float, float, float]]) -> list[_Line]:
    words = [
        w
        for w in page.extract_words(extra_attrs=["size", "fontname"], keep_blank_chars=False)
        if not any(_inside(w, box) for box in table_boxes)
    ]
    words.sort(key=lambda w: (round(w["top"], 1), w["x0"]))
    lines: list[list[dict[str, Any]]] = []
    for word in words:
        if lines and abs(word["top"] - lines[-1][0]["top"]) <= _LINE_TOLERANCE:
            lines[-1].append(word)
        else:
            lines.append([word])
    result: list[_Line] = []
    for group in lines:
        group.sort(key=lambda w: w["x0"])
        text = " ".join(w["text"] for w in group).strip()
        if not text:
            continue
        result.append(
            _Line(
                page=page_no,
                text=text,
                size=max(float(w["size"]) for w in group),
                bold=all("bold" in str(w["fontname"]).lower() for w in group),
                top=min(float(w["top"]) for w in group),
                bottom=max(float(w["bottom"]) for w in group),
            )
        )
    return result


def _table_text(rows: list[list[str | None]]) -> str:
    out = []
    for row in rows:
        cells = [" ".join((cell or "").split()) for cell in row]
        if any(cells):
            out.append(" | ".join(cells))
    return "\n".join(out)


def _heading_levels(lines: list[_Line], body: float) -> dict[float, int]:
    sizes = sorted(
        {round(line.size * 2) / 2 for line in lines if line.size >= body * _HEADING_SIZE_RATIO}, reverse=True
    )
    return {size: min(index + 1, _MAX_LEVELS) for index, size in enumerate(sizes)}


def _is_heading(line: _Line, body: float) -> bool:
    if len(line.text) > _MAX_HEADING_CHARS or line.text.endswith((".", ",", ";")):
        return False
    if line.size >= body * _HEADING_SIZE_RATIO:
        return True
    return line.bold and line.size >= body * 0.98 and len(line.text) <= 80


def extract_pdf(path: Path, *, max_pages: int) -> Extraction:
    password, page_count = _open_password(path, max_pages)
    lines: list[_Line] = []
    tables: list[_Table] = []
    warnings: list[ExtractionWarning] = []
    try:
        with pdfplumber.open(path, password=password) as pdf:
            for page_no, page in enumerate(pdf.pages, start=1):
                found = page.find_tables()
                boxes = [
                    (float(t.bbox[0]), float(t.bbox[1]), float(t.bbox[2]), float(t.bbox[3])) for t in found
                ]
                page_lines = _page_lines(page, page_no, boxes)
                page_tables = [
                    _Table(page=page_no, top=float(t.bbox[1]), text=_table_text(t.extract())) for t in found
                ]
                page_tables = [t for t in page_tables if t.text]
                if not page_lines and not page_tables:
                    warnings.append(
                        ExtractionWarning(
                            "empty_page",
                            f"Page {page_no} has no readable text (it may be a scanned image).",
                            page=page_no,
                        )
                    )
                lines.extend(page_lines)
                tables.extend(page_tables)
                page.close()
    except ExtractionFailed:
        raise
    except Exception as exc:  # pdfminer raises many unrelated exception types on malformed input
        raise _corrupt() from exc

    body = statistics.median([line.size for line in lines]) if lines else 0.0
    levels = _heading_levels(lines, body)
    smallest_heading_level = len(levels) + 1

    # Merge lines and tables in reading order (page, then vertical position).
    items: list[tuple[int, float, _Line | _Table]] = [(ln.page, ln.top, ln) for ln in lines]
    items += [(t.page, t.top, t) for t in tables]
    items.sort(key=lambda item: (item[0], item[1]))

    blocks: list[Block] = []
    paragraph: list[_Line] = []

    def flush() -> None:
        if paragraph:
            text = " ".join(line.text for line in paragraph)
            blocks.append(Block(kind="paragraph", text=text, page=paragraph[0].page))
            paragraph.clear()

    for _, _, item in items:
        if isinstance(item, _Table):
            flush()
            blocks.append(Block(kind="table", text=item.text, page=item.page))
            continue
        if _is_heading(item, body):
            flush()
            level = levels.get(round(item.size * 2) / 2, min(smallest_heading_level, _MAX_LEVELS))
            blocks.append(Block(kind="heading", text=item.text, page=item.page, level=level))
            continue
        if paragraph:
            prev = paragraph[-1]
            height = max(prev.bottom - prev.top, 1.0)
            if item.page != prev.page or item.top - prev.bottom > 0.6 * height:
                flush()
        paragraph.append(item)
    flush()
    return Extraction(extractor="pdfplumber", blocks=blocks, page_count=page_count, warnings=warnings)
