"""API request/response models for Part B (published in ``apps/api/openapi.json``)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_STRICT = ConfigDict(extra="forbid")


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    detail: ErrorDetail


ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"model": ErrorResponse, "description": "Not signed in"},
    403: {"model": ErrorResponse, "description": "Origin or role not allowed"},
    429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
}


# ---------------------------------------------------------------- session
class SessionRequest(BaseModel):
    model_config = _STRICT
    token: str = Field(min_length=1, max_length=200)


class SessionInfo(BaseModel):
    principal_id: str
    display_name: str
    roles: list[str]


# ---------------------------------------------------------------- projects
def _clean(value: str) -> str:
    return " ".join(value.split())


class ProjectCreate(BaseModel):
    model_config = _STRICT
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("name must not be blank")
        return cleaned


class ProjectUpdate(BaseModel):
    model_config = _STRICT
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = _clean(value)
        if not cleaned:
            raise ValueError("name must not be blank")
        return cleaned


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime
    document_count: int


# ---------------------------------------------------------------- documents
class WarningOut(BaseModel):
    code: str
    message: str
    page: int | None = None


class OutlineItem(BaseModel):
    level: int
    title: str
    page: int | None = None


class DocumentOut(BaseModel):
    id: str
    project_id: str
    role: Literal["primary_brd", "supporting"]
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    status: str
    page_count: int | None
    chunk_count: int
    char_count: int
    warnings: list[WarningOut]
    extractor: str
    extractor_version: str
    created_at: datetime


class DocumentDetail(DocumentOut):
    outline: list[OutlineItem]


class FileResult(BaseModel):
    filename: str
    status: Literal["extracted", "rejected"]
    document_id: str | None = None
    role: str | None = None
    code: str | None = None
    message: str | None = None
    chunk_count: int = 0
    page_count: int | None = None
    warnings: list[WarningOut] = []


class UploadResponse(BaseModel):
    extracted: int
    rejected: int
    results: list[FileResult]


class ChunkOut(BaseModel):
    id: str
    ordinal: int
    kind: str
    text: str
    section_path: str
    page_start: int | None
    page_end: int | None
    paragraph_start: int
    paragraph_end: int
    reference: str


class ChunkPage(BaseModel):
    items: list[ChunkOut]
    total: int
    offset: int
    limit: int


# ---------------------------------------------------------------- audit
class AuditEventOut(BaseModel):
    id: str
    ts: datetime
    principal_id: str | None
    action: str
    project_id: str | None
    target_type: str | None
    target_id: str | None
    outcome: str
    request_id: str | None
    details: dict[str, Any]


class AuditIntegrity(BaseModel):
    """Result of recomputing the audit hash chain (HC-21)."""

    ok: bool
    checked: int
    pre_chain_rows: int
    broken_at: int | None
    reason: str | None


class AuditPage(BaseModel):
    items: list[AuditEventOut]
    total: int
    offset: int
    limit: int
