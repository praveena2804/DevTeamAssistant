"""Part B: principals/roles, projects, source documents, chunks, append-only audit events.

Revision ID: 0002_intake_iam_audit
Revises: 0001_baseline
Create Date: 2026-11-19
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_intake_iam_audit"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TZ = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "principals",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
    )
    op.create_table(
        "principal_roles",
        sa.Column(
            "principal_id",
            sa.String(40),
            sa.ForeignKey("principals.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(32), primary_key=True),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("name_key", sa.String(100), nullable=False, unique=True),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.String(40), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.Column("updated_at", _TZ, nullable=False),
    )
    op.create_table(
        "source_documents",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("stored_name", sa.String(64), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("page_count", sa.Integer, nullable=True),
        sa.Column("chunk_count", sa.Integer, nullable=False),
        sa.Column("char_count", sa.Integer, nullable=False),
        sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("outline", sa.JSON, nullable=False),
        sa.Column("extractor", sa.String(32), nullable=False),
        sa.Column("extractor_version", sa.String(16), nullable=False),
        sa.Column("uploaded_by", sa.String(40), sa.ForeignKey("principals.id"), nullable=False),
        sa.Column("created_at", _TZ, nullable=False),
        sa.UniqueConstraint("project_id", "sha256", name="uq_source_documents_project_sha256"),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column(
            "document_id",
            sa.String(40),
            sa.ForeignKey("source_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("section_path", sa.Text, nullable=False),
        sa.Column("page_start", sa.Integer, nullable=True),
        sa.Column("page_end", sa.Integer, nullable=True),
        sa.Column("paragraph_start", sa.Integer, nullable=False),
        sa.Column("paragraph_end", sa.Integer, nullable=False),
        sa.Column("reference", sa.Text, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
    )
    op.create_index(
        "ix_document_chunks_document_ordinal", "document_chunks", ["document_id", "ordinal"], unique=True
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("ts", _TZ, nullable=False, index=True),
        sa.Column("principal_id", sa.String(40), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("project_id", sa.String(40), nullable=True, index=True),
        sa.Column("target_type", sa.String(32), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("details", sa.JSON, nullable=False),
    )
    # HC-21: the audit store is append-only at the database level, not only in application code.
    op.execute(
        "CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events "
        "BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
    op.drop_table("audit_events")
    op.drop_index("ix_document_chunks_document_ordinal", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_table("source_documents")
    op.drop_table("projects")
    op.drop_table("principal_roles")
    op.drop_table("principals")
