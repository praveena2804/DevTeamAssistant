"""Baseline: empty platform schema.

Establishes the Alembic revision chain for data/state.db (roadmap §13 Part A). Domain tables are added by
later revisions, starting in Part B. Upgrade and downgrade are both no-ops by design.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-19
"""

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
