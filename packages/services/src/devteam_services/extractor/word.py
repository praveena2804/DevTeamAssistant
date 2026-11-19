"""DOCX extraction with python-docx: heading styles, paragraphs and tables in document order.

DOCX has no fixed pages, so references use section + paragraph (FR-003 "where possible").
python-docx parses XML with entity resolution disabled; archive size limits are checked before this runs
(HC-20 zip-bomb protection).
"""

import re
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from devteam_services.extractor.models import Block, Extraction, ExtractionFailed

_HEADING_STYLE = re.compile(r"^heading\s*([1-9])$", re.IGNORECASE)


def _heading_level(paragraph: Paragraph) -> int | None:
    """Heading level, ``0`` for the document title (above Heading 1), ``None`` for body text."""
    style = paragraph.style
    name = (style.name or "") if style is not None else ""
    if name.lower() == "title":
        return 0
    match = _HEADING_STYLE.match(name.strip())
    return int(match.group(1)) if match else None


def _table_text(table: Table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            value = " ".join(cell.text.split())
            if not cells or cells[-1] != value:  # merged cells repeat their text
                cells.append(value)
        if any(cells):
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def extract_docx(path: Path) -> Extraction:
    try:
        document = Document(str(path))
        blocks: list[Block] = []
        for child in document.element.body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if not text:
                    continue
                level = _heading_level(paragraph)
                if level is None:
                    blocks.append(Block(kind="paragraph", text=text))
                else:
                    blocks.append(Block(kind="heading", text=text, level=level))
            elif tag == "tbl":
                table_text = _table_text(Table(child, document))
                if table_text:
                    blocks.append(Block(kind="table", text=table_text))
    except Exception as exc:  # python-docx/lxml raise many types on malformed input; all mean 'unreadable'
        raise ExtractionFailed(
            "upload.corrupt", "This Word file is damaged or is not a real .docx document."
        ) from exc
    return Extraction(extractor="python-docx", blocks=blocks)
