"""Extraction data model shared by every extractor and the chunker."""

from dataclasses import dataclass, field
from typing import Literal

BlockKind = Literal["heading", "paragraph", "table"]


@dataclass(frozen=True)
class Block:
    """One structural element in document order."""

    kind: BlockKind
    text: str
    page: int | None = None  # 1-based page number; None for formats without fixed pages (DOCX, TXT, MD)
    level: int = 0  # heading level (1 = top); 0 for non-headings


@dataclass(frozen=True)
class ExtractionWarning:
    code: str
    message: str
    page: int | None = None


@dataclass
class Extraction:
    extractor: str
    blocks: list[Block]
    page_count: int | None = None
    warnings: list[ExtractionWarning] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(block.text) for block in self.blocks)


class ExtractionFailed(Exception):  # noqa: N818 - mirrors UploadRejected; carries code + plain message
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
