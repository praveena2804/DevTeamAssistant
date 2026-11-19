"""Sample BRD corpus for Part B (PART-B-PLAN §6), generated deterministically.

Good files: the same synthetic "Library Lending" BRD as DOCX (headings + table), multi-page PDF (font-size
headings + bold sub-headings), Markdown and TXT (UTF-8 and cp1252).
Bad files: encrypted PDF, owner-password-only PDF (must be accepted), corrupt DOCX, password-protected
DOCX (OLE container), disguised executable, empty file, zip-bomb DOCX, too-many-entries DOCX, image-only PDF.

Run ``uv run python tests/fixtures/brds.py <folder>`` to write the corpus for a manual UI demo.
"""

import io
import sys
import zipfile
from pathlib import Path

# (level, title, paragraphs) — level 0 = paragraph-only continuation. Every heading below must be
# findable with a correct reference after extraction (Spec §23 scenario #1, AC-B-05).
BRD_TITLE = "Library Lending System - Business Requirements"
BRD_SECTIONS: list[tuple[int, str, list[str]]] = [
    (
        1,
        "1 Introduction",
        [
            "The city library wants a simple web application to manage book lending for its members.",
            "This document describes the business requirements for the first release.",
        ],
    ),
    (
        1,
        "2 Scope",
        [
            "In scope: members, books, loans and returns. Out of scope: payments and e-books.",
        ],
    ),
    (
        2,
        "2.1 Users and Roles",
        [
            "Librarians manage the catalogue and loans. Members browse books and see their own loans.",
        ],
    ),
    (
        1,
        "3 Functional Requirements",
        [
            "FR-001 The librarian shall add, edit and remove books in the catalogue.",
            "FR-002 The librarian shall register a loan of a book to a member.",
            "FR-003 A member shall see the list of books currently on loan to them.",
        ],
    ),
    (
        1,
        "4 Business Rules",
        [
            "BR-001 A member may borrow at most five books at the same time.",
            "BR-002 A loan lasts fourteen days and may be renewed once.",
        ],
    ),
    (
        1,
        "5 Acceptance Criteria",
        [
            "AC-001 Registering a sixth simultaneous loan for one member is refused with a clear message.",
        ],
    ),
]
ROLE_TABLE = [["Role", "Can do"], ["Librarian", "Manage books and loans"], ["Member", "View own loans"]]
EXPECTED_HEADINGS = [title for _, title, _ in BRD_SECTIONS]


# --------------------------------------------------------------------------- DOCX
def brd_docx() -> bytes:
    from docx import Document  # noqa: PLC0415 - test-only dependency path

    document = Document()
    document.add_heading(BRD_TITLE, level=0)
    for level, title, paragraphs in BRD_SECTIONS:
        document.add_heading(title, level=level)
        for paragraph in paragraphs:
            document.add_paragraph(paragraph)
        if title.startswith("2.1"):
            table = document.add_table(rows=len(ROLE_TABLE), cols=2)
            for r, row in enumerate(ROLE_TABLE):
                for c, value in enumerate(row):
                    table.cell(r, c).text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- PDF (minimal writer)
def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(pages: list[list[tuple[str, float, str]]]) -> bytes:
    """Write a valid PDF. Each page is a list of (font, size, text) lines; font is 'R' or 'B'."""
    objects: list[bytes] = []

    def add(obj: bytes) -> int:
        objects.append(obj)
        return len(objects)

    font_r = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_b = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    pages_id = len(objects) + 1 + 2 * len(pages)  # reserved: after all page + content objects
    page_ids = []
    for lines in pages:
        y = 780.0
        ops = []
        for font, size, text in lines:
            if text:
                ops.append(f"BT /F{font} {size} Tf 56 {y:.1f} Td ({_pdf_escape(text)}) Tj ET")
            y -= size * 1.25 + (size * 0.9 if size > 11 or font == "B" else 0)
        stream = "\n".join(ops).encode("latin-1")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page_ids.append(
            add(
                (
                    f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 595 842] "
                    f"/Resources << /Font << /FR {font_r} 0 R /FB {font_b} 0 R >> >> "
                    f"/Contents {content} 0 R >>"
                ).encode()
            )
        )
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    assert add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()) == pages_id
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out.getvalue()


def brd_pdf() -> bytes:
    """Three pages: sections 1-2.1 on page 1, 3 on page 2, 4-5 on page 3."""
    page_plan = [[0, 1, 2], [3], [4, 5]]
    pages = []
    for index, section_indexes in enumerate(page_plan):
        lines: list[tuple[str, float, str]] = []
        if index == 0:
            lines.append(("B", 20, BRD_TITLE))
        for s in section_indexes:
            level, title, paragraphs = BRD_SECTIONS[s]
            lines.append(("B", 16 if level == 1 else 13, title))
            for paragraph in paragraphs:
                lines.append(("R", 10, paragraph))
                lines.append(("R", 10, ""))  # blank line = paragraph gap
        pages.append(lines)
    return build_pdf(pages)


def image_only_pdf() -> bytes:
    return build_pdf([[], []])


def encrypted_pdf(*, user_password: str) -> bytes:
    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415

    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(brd_pdf())))
    writer.encrypt(user_password=user_password, owner_password="owner-secret", algorithm="AES-128")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


# --------------------------------------------------------------------------- text formats
def brd_markdown() -> bytes:
    lines = [f"# {BRD_TITLE}", ""]
    for level, title, paragraphs in BRD_SECTIONS:
        lines += [f"{'#' * (level + 1)} {title}", ""]
        for paragraph in paragraphs:
            lines += [paragraph, ""]
    return "\n".join(lines).encode("utf-8")


def brd_text(encoding: str = "utf-8") -> bytes:
    lines = [BRD_TITLE, ""]
    for _level, title, paragraphs in BRD_SECTIONS:
        lines += [title, ""]
        for paragraph in paragraphs:
            lines += [paragraph, ""]
    lines += ["Note: café opening hours are décided by the library – see notice board.", ""]  # noqa: RUF001 - deliberate cp1252 character
    return "\r\n".join(lines).encode(encoding)


# --------------------------------------------------------------------------- bad files
def corrupt_docx() -> bytes:
    good = brd_docx()
    return good[: len(good) // 2]  # truncated archive: central directory missing


def ole_docx() -> bytes:
    return bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 1024  # password-protected Office container


def disguised_executable() -> bytes:
    return b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 512


def zip_bomb_docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", b"\x00" * (8 * 1024 * 1024))  # ~1000:1 ratio
    return buffer.getvalue()


def many_entries_docx(entries: int = 2100) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<w:document/>")
        for i in range(entries):
            archive.writestr(f"word/media/x{i}.bin", b"x")
    return buffer.getvalue()


CORPUS = {
    "sample_brd.docx": brd_docx,
    "sample_brd.pdf": brd_pdf,
    "sample_brd.md": brd_markdown,
    "sample_brd.txt": brd_text,
    "corrupt.docx": corrupt_docx,
    "protected.docx": ole_docx,
    "invoice.pdf": disguised_executable,
    "scanned.pdf": image_only_pdf,
}


def main(argv: list[str]) -> int:
    target = Path(argv[1]) if len(argv) > 1 else Path("data/sample_brds")
    target.mkdir(parents=True, exist_ok=True)
    for name, builder in CORPUS.items():
        (target / name).write_bytes(builder())
    (target / "locked.pdf").write_bytes(encrypted_pdf(user_password="secret"))
    print(f"wrote {len(CORPUS) + 1} files to {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
