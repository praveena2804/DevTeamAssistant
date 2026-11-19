"""HC-20 upload validation rules (AC-B-02): filename matrix, extension allowlist, content signatures."""

import io
import zipfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from devteam_harness.validation import (
    DocxLimits,
    UploadRejected,
    check_content,
    check_extension,
    sanitize_filename,
)

pytestmark = pytest.mark.security
ALLOWED = (".pdf", ".docx", ".txt", ".md")
LIMITS = DocxLimits(max_uncompressed_bytes=100 * 1024 * 1024, max_entries=2000, max_compression_ratio=100)


@pytest.mark.parametrize(
    "name",
    ["requirements.pdf", "BRD v2 (final).docx", "notes_2026-11-19.md", "Lastenheft-Übersicht.txt", "a.pdf"],
)
def test_safe_names_are_accepted(name: str) -> None:
    assert sanitize_filename(name) == name


@pytest.mark.parametrize(
    ("label", "name"),
    [
        ("none", None),
        ("empty", ""),
        ("traversal", "../../etc/passwd.txt"),
        ("traversal-windows", "..\\..\\boot.ini.txt"),
        ("dotdot-inside", "a..b.pdf"),
        ("absolute-posix", "/etc/passwd.txt"),
        ("drive", "C:\\Windows\\win.ini.txt"),
        ("drive-relative", "C:evil.pdf"),
        ("unc", "\\\\server\\share\\x.pdf"),
        ("ads", "brd.txt:secret"),
        ("reserved-con", "CON.txt"),
        ("reserved-lower", "nul.pdf"),
        ("reserved-com", "COM1.docx"),
        ("reserved-lpt", "lpt9.md"),
        ("reserved-con-spaces", "CON .txt"),
        ("trailing-dot", "brd.pdf."),
        ("trailing-space", "brd.pdf "),
        ("leading-space", " brd.pdf"),
        ("hidden", ".env.txt"),
        ("control-char", "br\x00d.pdf"),
        ("newline", "brd\n.pdf"),
        ("angle", "<script>.pdf"),
        ("pipe", "a|b.pdf"),
        ("question", "what?.pdf"),
        ("too-long", "x" * 117 + ".pdf"),
        ("dot-only", "."),
        ("extension-only", ".pdf"),
    ],
)
def test_unsafe_names_are_rejected(label: str, name: str | None) -> None:
    with pytest.raises(UploadRejected) as info:
        sanitize_filename(name)
    assert info.value.code == "upload.unsafe_filename", label


@given(st.text(max_size=200))
def test_sanitized_names_never_contain_path_parts(name: str) -> None:
    try:
        clean = sanitize_filename(name)
    except UploadRejected:
        return
    assert "/" not in clean and "\\" not in clean and ":" not in clean and ".." not in clean
    assert Path(clean).name == clean


@pytest.mark.parametrize(
    ("name", "ext"), [("a.PDF", ".pdf"), ("b.Docx", ".docx"), ("c.txt", ".txt"), ("d.md", ".md")]
)
def test_allowed_extensions(name: str, ext: str) -> None:
    assert check_extension(name, ALLOWED) == ext


@pytest.mark.parametrize("name", ["a.exe", "a.doc", "a.xlsx", "a.zip", "a.html", "noext", "a.pdf.exe"])
def test_other_extensions_are_rejected(name: str) -> None:
    with pytest.raises(UploadRejected) as info:
        check_extension(name, ALLOWED)
    assert info.value.code == "upload.unsupported_type"


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _zip(entries: dict[str, bytes], *, compress: bool = True) -> bytes:
    buffer = io.BytesIO()
    mode = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(buffer, "w", compression=mode) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


VALID_DOCX = _zip({"[Content_Types].xml": b"<Types/>", "word/document.xml": b"<w:document/>"})


@pytest.mark.parametrize(
    ("ext", "data", "code"),
    [
        (".pdf", b"MZ\x90\x00" + b"\x00" * 100, "upload.type_mismatch"),  # executable renamed .pdf
        (".pdf", b"PK\x03\x04rest", "upload.type_mismatch"),
        (".pdf", b"hello world", "upload.type_mismatch"),
        (".docx", bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 64, "upload.encrypted"),
        (".docx", b"%PDF-1.7\n", "upload.type_mismatch"),
        (".docx", b"PK\x03\x04 truncated", "upload.corrupt"),
        (".docx", _zip({"word/document.xml": b"x"}), "upload.corrupt"),  # missing [Content_Types].xml
        (
            ".docx",
            _zip({"[Content_Types].xml": b"x", "word/document.xml": b"\x00" * (4 * 1024 * 1024)}),
            "upload.archive_too_large",
        ),  # zip bomb by ratio
        (".txt", b"%PDF-1.4 pretending", "upload.type_mismatch"),
        (".txt", b"MZ\x90\x00", "upload.type_mismatch"),
        (".md", b"\x89PNG\r\n\x1a\n", "upload.type_mismatch"),
        (".txt", b"text with a \x00 NUL byte", "upload.not_text"),
        (".txt", b"", "upload.empty"),
    ],
)
def test_content_rejections(tmp_path: Path, ext: str, data: bytes, code: str) -> None:
    path = _write(tmp_path, f"f{ext}", data)
    with pytest.raises(UploadRejected) as info:
        check_content(path, ext, docx_limits=LIMITS)
    assert info.value.code == code
    assert info.value.message  # a plain-language explanation is always present


def test_too_many_archive_entries(tmp_path: Path) -> None:
    entries = {"[Content_Types].xml": b"x", "word/document.xml": b"x"} | {f"m/{i}": b"x" for i in range(30)}
    path = _write(tmp_path, "f.docx", _zip(entries))
    small = DocxLimits(max_uncompressed_bytes=10**9, max_entries=20, max_compression_ratio=100)
    with pytest.raises(UploadRejected) as info:
        check_content(path, ".docx", docx_limits=small)
    assert info.value.code == "upload.archive_too_large"


def test_total_uncompressed_size_limit(tmp_path: Path) -> None:
    entries = {"[Content_Types].xml": b"x", "word/document.xml": b"y" * 5000}
    path = _write(tmp_path, "f.docx", _zip(entries, compress=False))
    small = DocxLimits(max_uncompressed_bytes=1000, max_entries=100, max_compression_ratio=100)
    with pytest.raises(UploadRejected) as info:
        check_content(path, ".docx", docx_limits=small)
    assert info.value.code == "upload.archive_too_large"


@pytest.mark.parametrize(
    ("ext", "data"),
    [
        (".pdf", b"%PDF-1.7\n..."),
        (".docx", VALID_DOCX),
        (".txt", "plain text, café".encode()),
        (".md", b"# Title\n\nBody"),
        (".txt", "UTF-16 text".encode("utf-16")),  # has NUL bytes but a BOM: allowed
    ],
)
def test_valid_content_passes(tmp_path: Path, ext: str, data: bytes) -> None:
    check_content(_write(tmp_path, f"f{ext}", data), ext, docx_limits=LIMITS)
