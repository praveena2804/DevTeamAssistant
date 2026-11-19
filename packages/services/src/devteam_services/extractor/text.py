"""TXT and Markdown extraction with encoding detection (D-22 (b): MD is read as plain text)."""

import re

from charset_normalizer import from_bytes

from devteam_services.extractor.models import Block, Extraction, ExtractionFailed, ExtractionWarning

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
# A short, single-line paragraph such as "2.1 User Roles" or "3. Scope" in a plain-text BRD.
_NUMBERED_HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+(\S.{0,78})$")


def decode_text(data: bytes) -> tuple[str, str]:
    """Return ``(text, encoding)``. Raises ``ExtractionFailed`` when the bytes are not text."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="strict"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError as exc:
            raise ExtractionFailed("upload.not_text", "This file could not be read as text.") from exc
    try:
        return data.decode("utf-8", errors="strict"), "utf-8"
    except UnicodeDecodeError:
        pass
    best = from_bytes(data).best()
    if best is None or best.encoding is None:
        raise ExtractionFailed("upload.not_text", "This file could not be read as text.")
    return str(best), best.encoding


def _is_numbered_heading(lines: list[str]) -> re.Match[str] | None:
    if len(lines) != 1:
        return None
    line = lines[0].strip()
    match = _NUMBERED_HEADING.fullmatch(line)
    if match is None or line.endswith((".", ",", ";")):
        return None
    return match


def extract_text(data: bytes, *, markdown: bool) -> Extraction:
    text, encoding = decode_text(data)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[Block] = []
    current: list[str] = []
    in_code = False

    def flush() -> None:
        if not current:
            return
        match = None if markdown else _is_numbered_heading(current)
        if match is not None:
            level = match.group(1).count(".") + 1
            blocks.append(Block(kind="heading", text=current[0].strip(), level=level))
        else:
            paragraph = "\n".join(line.rstrip() for line in current).strip()
            if paragraph:
                blocks.append(Block(kind="paragraph", text=paragraph))
        current.clear()

    for line in text.split("\n"):
        if markdown and line.lstrip().startswith("```"):
            in_code = not in_code
            current.append(line)
            continue
        heading = None if (in_code or not markdown) else _MD_HEADING.match(line.strip())
        if heading is not None:
            flush()
            blocks.append(Block(kind="heading", text=heading.group(2).strip(), level=len(heading.group(1))))
        elif not line.strip() and not in_code:
            flush()
        else:
            current.append(line)
    flush()

    warnings: list[ExtractionWarning] = []
    if encoding not in {"utf-8", "utf-8-sig", "ascii"}:
        warnings.append(
            ExtractionWarning("encoding", f"The text was read using the '{encoding}' character encoding.")
        )
    return Extraction(extractor="markdown" if markdown else "text", blocks=blocks, warnings=warnings)
