"""Extractors (AC-B-01, AC-B-02): headings, pages, tables, encodings and per-file failures."""

from pathlib import Path

import pytest

from devteam_services.extractor import Extraction, ExtractionFailed, extract
from tests.fixtures import brds

LIMITS = {"max_pdf_pages": 300, "max_chars": 2_000_000}


def _extract(tmp_path: Path, name: str, data: bytes, **overrides: int) -> Extraction:
    path = tmp_path / name
    path.write_bytes(data)
    return extract(path, path.suffix, **{**LIMITS, **overrides})


def _headings(extraction: Extraction) -> list[str]:
    return [b.text for b in extraction.blocks if b.kind == "heading"]


@pytest.mark.parametrize(
    ("name", "builder"),
    [
        ("brd.docx", brds.brd_docx),
        ("brd.pdf", brds.brd_pdf),
        ("brd.md", brds.brd_markdown),
        ("brd.txt", brds.brd_text),
    ],
)
def test_every_format_finds_all_section_headings(tmp_path: Path, name: str, builder: object) -> None:
    extraction = _extract(tmp_path, name, builder())  # type: ignore[operator]
    for expected in brds.EXPECTED_HEADINGS:
        assert expected in _headings(extraction), f"{name}: missing heading {expected!r}"
    text = "\n".join(b.text for b in extraction.blocks)
    assert "FR-002 The librarian shall register a loan of a book to a member." in text
    assert "BR-001 A member may borrow at most five books at the same time." in text


def test_pdf_keeps_exact_page_numbers_and_heading_levels(tmp_path: Path) -> None:
    extraction = _extract(tmp_path, "brd.pdf", brds.brd_pdf())
    assert extraction.page_count == 3
    pages = {b.text: b.page for b in extraction.blocks if b.kind == "heading"}
    assert pages["1 Introduction"] == 1
    assert pages["3 Functional Requirements"] == 2
    assert pages["5 Acceptance Criteria"] == 3
    levels = {b.text: b.level for b in extraction.blocks if b.kind == "heading"}
    assert levels[brds.BRD_TITLE] < levels["2 Scope"] < levels["2.1 Users and Roles"]
    assert extraction.warnings == []


def test_docx_reads_heading_styles_and_tables(tmp_path: Path) -> None:
    extraction = _extract(tmp_path, "brd.docx", brds.brd_docx())
    levels = {b.text: b.level for b in extraction.blocks if b.kind == "heading"}
    assert levels[brds.BRD_TITLE] == 0  # Title sits above Heading 1
    assert levels["2 Scope"] == 1 and levels["2.1 Users and Roles"] == 2
    tables = [b.text for b in extraction.blocks if b.kind == "table"]
    assert tables == ["Role | Can do\nLibrarian | Manage books and loans\nMember | View own loans"]
    assert all(b.page is None for b in extraction.blocks)  # DOCX has no fixed pages


def test_markdown_headings_ignore_code_fences(tmp_path: Path) -> None:
    data = b"# Real\n\nText\n\n```\n# not a heading\n```\n\n## Second\n\nMore"
    extraction = _extract(tmp_path, "a.md", data)
    assert _headings(extraction) == ["Real", "Second"]


@pytest.mark.parametrize("encoding", ["utf-8", "cp1252", "utf-16"])
def test_text_encodings_are_detected(tmp_path: Path, encoding: str) -> None:
    extraction = _extract(tmp_path, "brd.txt", brds.brd_text(encoding))
    text = "\n".join(b.text for b in extraction.blocks)
    assert "café opening hours are décided" in text
    codes = [w.code for w in extraction.warnings]
    assert ("encoding" in codes) == (encoding != "utf-8")


def test_txt_numbered_lines_become_headings_but_sentences_do_not(tmp_path: Path) -> None:
    data = b"1 Overview\n\n1. The system shall log in users.\n\n2.3 Data Retention\n\nBody."
    assert _headings(_extract(tmp_path, "a.txt", data)) == ["1 Overview", "2.3 Data Retention"]


def test_owner_password_only_pdf_is_readable(tmp_path: Path) -> None:
    extraction = _extract(tmp_path, "brd.pdf", brds.encrypted_pdf(user_password=""))
    assert "3 Functional Requirements" in _headings(extraction)


@pytest.mark.parametrize(
    ("name", "builder", "code"),
    [
        ("locked.pdf", lambda: brds.encrypted_pdf(user_password="secret"), "upload.encrypted"),
        ("broken.pdf", lambda: b"%PDF-1.4\nthis is not really a pdf", "upload.corrupt"),
        ("corrupt.docx", brds.corrupt_docx, "upload.corrupt"),
    ],
)
def test_unreadable_files_fail_with_clear_codes(
    tmp_path: Path, name: str, builder: object, code: str
) -> None:
    with pytest.raises(ExtractionFailed) as info:
        _extract(tmp_path, name, builder())  # type: ignore[operator]
    assert info.value.code == code
    assert info.value.message


def test_page_limit(tmp_path: Path) -> None:
    with pytest.raises(ExtractionFailed) as info:
        _extract(tmp_path, "brd.pdf", brds.brd_pdf(), max_pdf_pages=2)
    assert info.value.code == "upload.too_many_pages"
    assert "3 pages" in info.value.message


def test_image_only_pdf_reports_empty_pages(tmp_path: Path) -> None:
    extraction = _extract(tmp_path, "scan.pdf", brds.image_only_pdf())
    assert extraction.blocks == []
    assert [w.page for w in extraction.warnings if w.code == "empty_page"] == [1, 2]


def test_long_documents_are_truncated_with_a_warning(tmp_path: Path) -> None:
    extraction = _extract(tmp_path, "brd.md", brds.brd_markdown(), max_chars=200)
    assert extraction.char_count <= 200
    assert any(w.code == "truncated" for w in extraction.warnings)
