"""The Part C tool catalogue (HC-06). There is no shell tool, now or later (CLAUDE.md §5).

| Tool | Access | Roles (also granted by the central policy) |
|---|---|---|
| ``read_project_file`` | read | Head (list only), build agents 4-9, Documenter |
| ``list_project_files`` | read | Head, build agents 4-9, Documenter |
| ``write_project_file`` | write | build agents 4-9, Documenter |
| ``read_extracted_document`` | none (project records) | BRD Analyst, Requirements Finalizer |
| ``search_project_knowledge`` | none | BRD Analyst, Requirements Finalizer, Architect (interface; Part F) |

Returned file contents and document text are untrusted data: the executor wraps them with the input
guardrail before they reach a model (C.4).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from devteam_core.contracts._base import DocumentId
from devteam_core.contracts.statuses import AgentRole
from devteam_core.contracts.tools import SideEffect
from devteam_harness.guardrails.policy import Access
from devteam_harness.tools.files import WriteOutcome
from devteam_harness.tools.layer import ToolInvocation, ToolNotFoundError, ToolUnavailableError
from devteam_harness.tools.registry import ToolRegistry, ToolSpec
from devteam_state.repositories import documents

if TYPE_CHECKING:
    from devteam_harness.sandbox.service import SandboxService

_BUILD = frozenset(
    {
        AgentRole.ARCHITECT,
        AgentRole.DATABASE_DEVELOPER,
        AgentRole.BACKEND_DEVELOPER,
        AgentRole.FRONTEND_DEVELOPER,
        AgentRole.TESTER,
        AgentRole.DEBUGGER_REVIEWER,
    }
)
_DISCOVERY = frozenset({AgentRole.BRD_ANALYST, AgentRole.REQUIREMENTS_FINALIZER})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# ----------------------------------------------------------------------------------------- schemas


# Path arguments are plain bounded strings on purpose: the structural path rules are applied by the
# policy engine (so an unsafe path is a recorded *policy denial*, deny.path_invalid) and again by the
# Controlled File Service against the real filesystem.
ToolPath = Field(min_length=1, max_length=1_024)


class ReadFileArgs(_Strict):
    path: str = ToolPath


class ReadFileResult(_Strict):
    path: str
    content: str


class ListArgs(_Strict):
    path: str = ToolPath


class ListEntry(_Strict):
    path: str
    is_dir: bool
    size: int


class ListResult(_Strict):
    entries: tuple[ListEntry, ...]


class WriteFileArgs(_Strict):
    path: str = ToolPath
    content: str = Field(max_length=10_485_760)


class WriteFileResult(_Strict):
    path: str
    sha256_before: str | None
    sha256_after: str
    bytes_written: int
    _change: WriteOutcome | None = PrivateAttr(default=None)


class ReadDocumentArgs(_Strict):
    document_id: DocumentId
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)


class DocumentChunkView(_Strict):
    chunk_id: str
    reference: str
    text: str


class ReadDocumentResult(_Strict):
    document_id: str
    total: int
    chunks: tuple[DocumentChunkView, ...]


class SearchArgs(_Strict):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class SearchResult(_Strict):
    hits: tuple[DocumentChunkView, ...]


# ---------------------------------------------------------------------------------------- handlers


def _read(inv: ToolInvocation) -> ReadFileResult:
    return ReadFileResult(path=inv.args.path, content=inv.ctx.files.read_text(inv.args.path))


def _list(inv: ToolInvocation) -> ListResult:
    entries = inv.ctx.files.list_dir(inv.args.path)
    return ListResult(entries=tuple(ListEntry(path=e.path, is_dir=e.is_dir, size=e.size) for e in entries))


def _write(inv: ToolInvocation) -> WriteFileResult:
    outcome = inv.ctx.files.write_text(inv.args.path, inv.args.content, cancelled=inv.cancelled)
    result = WriteFileResult(
        path=outcome.path,
        sha256_before=outcome.before_sha256,
        sha256_after=outcome.after_sha256,
        bytes_written=outcome.bytes_written,
    )
    result._change = outcome
    return result


def _read_document(inv: ToolInvocation) -> ReadDocumentResult:
    project_id = inv.ctx.task.project_id  # isolation: only the task's own project
    with inv.sessions() as session:
        if documents.get(session, project_id, inv.args.document_id) is None:
            raise ToolNotFoundError("no such document in this project")
        chunks, total = documents.list_chunks(
            session, project_id, inv.args.document_id, offset=inv.args.offset, limit=inv.args.limit
        )
        views = tuple(DocumentChunkView(chunk_id=c.id, reference=c.reference, text=c.text) for c in chunks)
    return ReadDocumentResult(document_id=inv.args.document_id, total=total, chunks=views)


def _search(_inv: ToolInvocation) -> SearchResult:
    raise ToolUnavailableError("project knowledge search is available from Part F (RAG)")


def build_default_registry(sandbox: SandboxService | None = None) -> ToolRegistry:
    """The tool catalogue. Part E adds the three execute tools; without a configured ``sandbox`` they are
    registered but refuse every call as Blocked (fail closed), so the central policy stays consistent."""
    from devteam_harness.sandbox.tools import blocked_tool_specs, sandbox_tool_specs  # noqa: PLC0415

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="read_project_file",
            version=1,
            input_model=ReadFileArgs,
            output_model=ReadFileResult,
            side_effect=SideEffect.READ,
            access=Access.READ,
            path_arg="path",
            allowed_roles=_BUILD | {AgentRole.DOCUMENTER},
            timeout_s=10,
            handler=_read,
        )
    )
    registry.register(
        ToolSpec(
            name="list_project_files",
            version=1,
            input_model=ListArgs,
            output_model=ListResult,
            side_effect=SideEffect.READ,
            access=Access.READ,
            path_arg="path",
            allowed_roles=_BUILD | {AgentRole.HEAD, AgentRole.DOCUMENTER},
            timeout_s=10,
            handler=_list,
        )
    )
    registry.register(
        ToolSpec(
            name="write_project_file",
            version=1,
            input_model=WriteFileArgs,
            output_model=WriteFileResult,
            side_effect=SideEffect.WRITE_WORKSPACE,
            access=Access.WRITE,
            path_arg="path",
            allowed_roles=_BUILD | {AgentRole.DOCUMENTER},
            timeout_s=15,
            handler=_write,
            summary_exclude=frozenset({"content"}),
        )
    )
    registry.register(
        ToolSpec(
            name="read_extracted_document",
            version=1,
            input_model=ReadDocumentArgs,
            output_model=ReadDocumentResult,
            side_effect=SideEffect.READ,
            access=Access.NONE,
            path_arg=None,
            allowed_roles=_DISCOVERY,
            timeout_s=10,
            handler=_read_document,
        )
    )
    registry.register(
        ToolSpec(
            name="search_project_knowledge",
            version=1,
            input_model=SearchArgs,
            output_model=SearchResult,
            side_effect=SideEffect.READ,
            access=Access.NONE,
            path_arg=None,
            allowed_roles=_DISCOVERY | {AgentRole.ARCHITECT},
            timeout_s=5,
            handler=_search,
        )
    )
    for spec in sandbox_tool_specs(sandbox) if sandbox is not None else blocked_tool_specs():
        registry.register(spec)
    return registry
