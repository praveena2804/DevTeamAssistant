"""Platform state tables (SQLAlchemy 2 declarative models).

Part B adds: principals and roles (HC-15), projects, source documents and chunks (intake), and the
append-only audit store (HC-21 foundation). Every domain table carries ``project_id`` (Working Plan §11).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Principal(Base):
    __tablename__ = "principals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # "human" in Part B; agent/service principals in Part C
    display_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PrincipalRole(Base):
    __tablename__ = "principal_roles"

    principal_id: Mapped[str] = mapped_column(
        ForeignKey("principals.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(32), primary_key=True)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    name_key: Mapped[str] = mapped_column(String(100), unique=True)  # case-folded name for uniqueness
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SourceDocument(Base):
    __tablename__ = "source_documents"
    __table_args__ = (UniqueConstraint("project_id", "sha256", name="uq_source_documents_project_sha256"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # primary_brd | supporting
    original_filename: Mapped[str] = mapped_column(String(255))  # display only, never a path
    stored_name: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    outline: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    extractor: Mapped[str] = mapped_column(String(32))
    extractor_version: Mapped[str] = mapped_column(String(16))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("principals.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (Index("ix_document_chunks_document_ordinal", "document_id", "ordinal", unique=True),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("source_documents.id", ondelete="CASCADE"))
    ordinal: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))  # text | table
    text: Mapped[str] = mapped_column(Text)
    section_path: Mapped[str] = mapped_column(Text, default="")
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paragraph_start: Mapped[int] = mapped_column(Integer)
    paragraph_end: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(String(64))


class AuditEvent(Base):
    """Append-only (database triggers reject UPDATE and DELETE, migration 0002) and hash-chained (0003).

    ``seq``/``prev_hash``/``hash`` are NULL only for rows written before migration 0003; those rows are
    covered by the chain anchor (seq 1), whose details hold their count and digest.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    principal_id: Mapped[str | None] = mapped_column(String(80), nullable=True)  # agent ids are ~56 chars
    action: Mapped[str] = mapped_column(String(64))
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))  # success | denied | error
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    seq: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)
    prev_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AuditChainHead(Base):
    """Single row (id = 1) holding the latest chain position; updating it serialises appenders."""

    __tablename__ = "audit_chain_head"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seq: Mapped[int] = mapped_column(Integer)
    hash: Mapped[str] = mapped_column(String(64))


class Run(Base):
    """A workflow run (roadmap §6.4). Status changes only through ``devteam_state.machines``."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_kind: Mapped[str] = mapped_column(String(16))  # initial | change_request
    stage: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    spec_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    spec_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentTaskRow(Base):
    """A persisted ``AgentTask`` contract (``payload``) plus the fields queried and transitioned."""

    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    agent_type: Mapped[str] = mapped_column(String(32))
    operation_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TaskAttempt(Base):
    """Attempt ledger (roadmap §6.7): idempotency key ``run_id:node:task_id:attempt``."""

    __tablename__ = "task_attempts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id", ondelete="CASCADE"))
    node: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[str] = mapped_column(String(16))  # started | completed | failed
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRow(Base):
    """A human approval decision (HC-13). Append-only (database triggers); verified on every use."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    approval_type: Mapped[str] = mapped_column(String(32))
    spec_version_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    spec_checksum: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # "<run>:<type>:<spec version or ->": one decision per run, type and spec version (no double submit)
    decision_key: Mapped[str] = mapped_column(String(200), unique=True)
    decision: Mapped[str] = mapped_column(String(16))
    decided_by: Mapped[str] = mapped_column(String(40))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    audit_event_id: Mapped[str] = mapped_column(String(40))


class SecretReferenceRow(Base):
    """A pointer to a keyring secret (HC-17). There is deliberately no value column."""

    __tablename__ = "secret_references"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(100))
    ref: Mapped[str] = mapped_column(String(120), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class UsageCounter(Base):
    """Persisted budget usage (HC-05) for one task or one run; ``key`` = ``<run>:<scope>:<task or ->``."""

    __tablename__ = "usage_counters"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    scope: Mapped[str] = mapped_column(String(8))  # run | task
    key: Mapped[str] = mapped_column(String(120), unique=True)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    bytes_written: Mapped[int] = mapped_column(Integer, default=0)
    active_ms: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TraceSpanRow(Base):
    """A finished OpenTelemetry span (HC-11, D-30: local storage only). Attributes are allowlisted."""

    __tablename__ = "trace_spans"

    span_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(32), index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    name: Mapped[str] = mapped_column(String(100))
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # unset | ok | error
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


__all__ = [
    "NAMING_CONVENTION",
    "AgentTaskRow",
    "ApprovalRow",
    "AuditChainHead",
    "AuditEvent",
    "Base",
    "DocumentChunk",
    "Principal",
    "PrincipalRole",
    "Project",
    "Run",
    "SecretReferenceRow",
    "SourceDocument",
    "TaskAttempt",
    "TraceSpanRow",
    "UsageCounter",
]
