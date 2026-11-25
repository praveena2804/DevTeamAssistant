"""Part F: model-call records, LLM response cache, embedding cache and the model inventory.

- ``model_calls``: one row per model call (``ModelCall`` contract), metadata only: never prompt, input or
  answer text (AC-F-01). Append-only (triggers). ``project_id`` is NULL only for platform service calls
  without a project (evaluation), as for ``audit_events``.
- ``llm_response_cache``: D-28, off by default; a cached answer is re-validated on every hit. Keyed per
  project by model digest, prompt hash, input hash, parameters and schema hash.
- ``embedding_cache``: per project, embedding-model digest and text hash → vector.
- ``model_inventory``: written by the worker (installed tags, digests, Ollama version); the API reads it,
  so the API never contacts Ollama (Part F plan §3).

Downgrade drops the four tables; no existing table is changed.

Revision ID: 0008_model_calls
Revises: 0007_sandbox
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_model_calls"
down_revision: str | None = "0007_sandbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_calls",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), nullable=True, index=True),
        sa.Column("run_id", sa.String(40), nullable=True, index=True),
        sa.Column("task_id", sa.String(40), nullable=True, index=True),
        sa.Column("agent", sa.String(40), nullable=True),
        sa.Column("service", sa.String(41), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("operation_id", sa.String(110), nullable=False),
        sa.Column("slot", sa.String(20), nullable=False),
        sa.Column("model_tag", sa.String(130), nullable=False),
        sa.Column("model_digest", sa.String(64), nullable=True),
        sa.Column("route_rule", sa.String(12), nullable=False),
        sa.Column("recovery_step", sa.String(20), nullable=False),
        sa.Column("prompt_id", sa.String(120), nullable=True),
        sa.Column("prompt_version", sa.Integer, nullable=True),
        sa.Column("prompt_hash", sa.String(64), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("schema_name", sa.String(82), nullable=True),
        sa.Column("cache_hit", sa.Boolean, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("duration_ms", sa.Integer, nullable=False),
        sa.Column("free_ram_mb", sa.Integer, nullable=True),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("error_code", sa.String(62), nullable=True),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.CheckConstraint("kind IN ('generate','embed')", name="kind_values"),
        sa.CheckConstraint("outcome IN ('valid','invalid','error','refused')", name="outcome_values"),
        sa.CheckConstraint("(agent IS NULL) <> (service IS NULL)", name="one_caller"),
    )
    op.execute(
        "CREATE TRIGGER model_calls_no_update BEFORE UPDATE ON model_calls "
        "BEGIN SELECT RAISE(ABORT, 'model_calls is append-only'); END"
    )
    op.execute(
        "CREATE TRIGGER model_calls_no_delete BEFORE DELETE ON model_calls "
        "BEGIN SELECT RAISE(ABORT, 'model_calls is append-only'); END"
    )
    op.create_table(
        "llm_response_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(40), nullable=False, index=True),
        sa.Column("model_digest", sa.String(64), nullable=False),
        sa.Column("prompt_hash", sa.String(64), nullable=False),
        sa.Column("output_text", sa.Text, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False),
        sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "embedding_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("project_id", sa.String(40), nullable=False, index=True),
        sa.Column("model_digest", sa.String(64), nullable=False),
        sa.Column("dimensions", sa.Integer, nullable=False),
        sa.Column("vector", sa.LargeBinary, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "model_inventory",
        sa.Column("model_tag", sa.String(130), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=True),
        sa.Column("installed", sa.Boolean, nullable=False),
        sa.Column("server_version", sa.String(40), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("model_inventory")
    op.drop_table("embedding_cache")
    op.drop_table("llm_response_cache")
    op.execute("DROP TRIGGER IF EXISTS model_calls_no_delete")
    op.execute("DROP TRIGGER IF EXISTS model_calls_no_update")
    op.drop_table("model_calls")
