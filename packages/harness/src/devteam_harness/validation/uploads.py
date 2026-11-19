"""Upload validation (HC-20, HS-02). Pure functions: no database, no network.

Order of checks (PART-B-PLAN §5.2): filename → extension allowlist → size (by the caller, while
streaming) → content signature / structure → (extraction-time) encryption and corruption.

Every rejection carries a stable ``code`` and a plain-language ``message`` for the owner.
The user-supplied filename is kept for display only; it never becomes a filesystem path.
"""

import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePath

MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
}

MAX_FILENAME_LENGTH = 120
_WINDOWS_FORBIDDEN = set('<>:"|?*')
_RESERVED_STEMS = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{base}{n}" for base in ("COM", "LPT") for n in [*range(1, 10), "¹", "²", "³"]
}
_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
_BINARY_SIGNATURES = (
    b"%PDF-",
    b"PK\x03\x04",
    _OLE_MAGIC,
    b"MZ",  # Windows executable
    b"\x7fELF",
    b"\x89PNG",
    b"\xff\xd8\xff",  # JPEG
    b"GIF8",
)
_DOCX_REQUIRED = ("[Content_Types].xml", "word/document.xml")


class UploadRejected(Exception):  # noqa: N818 - domain name, not an error class hierarchy
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DocxLimits:
    max_uncompressed_bytes: int
    max_entries: int
    max_compression_ratio: int


def sanitize_filename(raw: str | None) -> str:
    """Validate a client filename for display. Rejects anything that is not a plain, safe file name."""
    unsafe = UploadRejected(
        "upload.unsafe_filename",
        "The file name is not allowed. Use a simple name such as 'requirements.pdf'.",
    )
    if raw is None:
        raise unsafe
    name = unicodedata.normalize("NFC", raw)
    if not name or name != name.strip() or len(name) > MAX_FILENAME_LENGTH:
        raise unsafe
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise unsafe
    if "/" in name or "\\" in name or name in {".", ".."} or ".." in name:
        raise unsafe  # folder parts, traversal
    if _WINDOWS_FORBIDDEN & set(name):
        raise unsafe  # includes ':' (drive letters, NTFS alternate data streams)
    if name.endswith((".", " ")) or name.startswith("."):
        raise unsafe
    stem = name.split(".", 1)[0].rstrip(" ").upper()
    if not stem or stem in _RESERVED_STEMS:
        raise unsafe
    return name


def check_extension(filename: str, allowed: tuple[str, ...]) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in allowed:
        readable = ", ".join(e.lstrip(".").upper() for e in allowed)
        raise UploadRejected(
            "upload.unsupported_type", f"This file type is not supported. Supported types: {readable}."
        )
    return ext


def _mismatch(ext: str) -> UploadRejected:
    return UploadRejected(
        "upload.type_mismatch",
        f"The file content does not match its {ext.lstrip('.').upper()} extension.",
    )


def _check_docx(path: Path, limits: DocxLimits) -> None:
    corrupt = UploadRejected("upload.corrupt", "This Word file is damaged or is not a real .docx document.")
    too_large = UploadRejected(
        "upload.archive_too_large", "This Word file expands to an unsafe size and was refused."
    )
    if not zipfile.is_zipfile(path):
        raise corrupt
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_entries:
                raise too_large
            total = 0
            for info in infos:
                total += info.file_size
                compressed = max(info.compress_size, 1)
                if (
                    info.file_size > 1024 * 1024
                    and info.file_size / compressed > limits.max_compression_ratio
                ):
                    raise too_large
            if total > limits.max_uncompressed_bytes:
                raise too_large
            names = {info.filename for info in infos}
            if not all(required in names for required in _DOCX_REQUIRED):
                raise corrupt
    except zipfile.BadZipFile as exc:
        raise corrupt from exc


def check_content(path: Path, ext: str, *, docx_limits: DocxLimits) -> None:
    """Verify that the stored bytes really are the claimed type (no ``libmagic`` needed)."""
    with path.open("rb") as handle:
        head = handle.read(4096)
    if not head:
        raise UploadRejected("upload.empty", "The file is empty.")
    if ext == ".pdf":
        if not head.startswith(b"%PDF-"):
            raise _mismatch(ext)
        return
    if ext == ".docx":
        if head.startswith(_OLE_MAGIC):
            raise UploadRejected(
                "upload.encrypted",
                "This Word file is password-protected or uses the old .doc format. "
                "Remove the password or save it as .docx, then upload again.",
            )
        if not head.startswith(b"PK\x03\x04"):
            raise _mismatch(ext)
        _check_docx(path, docx_limits)
        return
    # .txt / .md
    if head.startswith(_BINARY_SIGNATURES):
        raise _mismatch(ext)
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return  # UTF-16 text legitimately contains NUL bytes; decoding is verified by the extractor
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            if b"\x00" in block:
                raise UploadRejected(
                    "upload.not_text", "This file is not plain text (it contains binary data)."
                )
