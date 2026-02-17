"""Add expression_score_cache table for precomputed analysis results.

Revision ID: 004
Revises: 003
Create Date: 2026-02-17

Adds:
  - expression_score_cache table for caching differential expression,
    drug expression scores, and pathway activity scores
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "expression_score_cache",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("cache_type", sa.String(50), nullable=False),
        sa.Column("entity_id_1", sa.Integer, nullable=False),
        sa.Column("entity_id_2", sa.Integer, nullable=False),
        sa.Column("score", sa.Float),
        sa.Column("details", JSONB),
        sa.Column(
            "computed_at",
            sa.DateTime,
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_cache_lookup",
        "expression_score_cache",
        ["cache_type", "entity_id_1", "entity_id_2"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_cache_lookup", table_name="expression_score_cache")
    op.drop_table("expression_score_cache")
