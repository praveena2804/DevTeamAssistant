"""Part F.5: RAG index manifest and jobs (HC-08; Spec §10.2; Part F plan §6.4).

- ``document_chunks.chunker_version``: the chunker that produced each chunk (existing rows: "1", the only
  version so far). The index is rebuilt when the chunker changes, so the harness must know it per chunk.
- ``rag_collections``: one vector collection per project and identity (embedding model + digest + chunker
  version). SQLite stays the source of truth; the vector store only holds vectors and metadata.
- ``rag_index_documents``: which documents (by content checksum and chunker version) a collection holds, so a
  new or changed upload is detected as not yet indexed (per-document freshness).
- ``rag_index_jobs``: index/rebuild requests (queued by the API, run by the worker; the API never embeds).

Downgrade drops the three tables and the column.

Revision ID: 0009_rag_index
Revises: 0008_model_calls
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_rag_index"
down_revision: str | None = "0008_model_calls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("document_chunks") as batch:
        batch.add_column(sa.Column("chunker_version", sa.String(10), nullable=True))
    op.execute("UPDATE document_chunks SET chunker_version = '1' WHERE chunker_version IS NULL")
    op.create_table(
        "rag_collections",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), nullable=False, index=True),
        sa.Column("name", sa.String(120), nullable=False, unique=True),
        sa.Column("embedding_model", sa.String(130), nullable=False),
        sa.Column("embedding_digest", sa.String(64), nullable=False),
        sa.Column("chunker_version", sa.String(10), nullable=False),
        sa.Column("dimensions", sa.Integer, nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('building','ready','retired')", name="status_values"),
    )
    op.create_table(
        "rag_index_documents",
        sa.Column(
            "collection_id",
            sa.String(40),
            sa.ForeignKey("rag_collections.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("document_id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), nullable=False, index=True),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("chunker_version", sa.String(10), nullable=False),
        sa.Column("chunk_count", sa.Integer, nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "rag_index_jobs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), nullable=False, index=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, index=True),
        sa.Column("requested_by", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(62), nullable=True),
        sa.Column("detail", sa.String(500), nullable=True),
        sa.CheckConstraint("kind IN ('index','rebuild')", name="kind_values"),
        sa.CheckConstraint("status IN ('queued','running','done','failed','blocked')", name="status_values"),
    )


def downgrade() -> None:
    op.drop_table("rag_index_jobs")
    op.drop_table("rag_index_documents")
    op.drop_table("rag_collections")
    with op.batch_alter_table("document_chunks") as batch:
        batch.drop_column("chunker_version")
