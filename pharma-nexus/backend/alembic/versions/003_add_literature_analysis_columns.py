"""Add literature analysis columns, drug mechanism embedding, and full-text search.

Revision ID: 003
Revises: 002
Create Date: 2026-02-17

Adds:
  - literature.extracted_findings (JSONB) — Claude-extracted structured data
  - literature.analysis_status — pending/analyzed/failed
  - literature.search_vector — generated tsvector for full-text search
  - drugs.mechanism_embedding — Vector(384) for semantic drug search
  - GIN index on search_vector for fast full-text search
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Literature analysis columns
    op.add_column(
        "literature",
        sa.Column("extracted_findings", JSONB, nullable=True),
    )
    op.add_column(
        "literature",
        sa.Column(
            "analysis_status",
            sa.String(20),
            server_default="pending",
            nullable=False,
        ),
    )

    # Full-text search vector (generated column)
    op.execute("""
        ALTER TABLE literature ADD COLUMN search_vector tsvector
        GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(abstract, '')), 'B')
        ) STORED
    """)
    op.execute(
        "CREATE INDEX ix_literature_search_vector ON literature USING GIN (search_vector)"
    )

    # Drug mechanism embedding
    op.execute(
        "ALTER TABLE drugs ADD COLUMN mechanism_embedding vector(384)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_literature_search_vector")
    op.execute("ALTER TABLE literature DROP COLUMN IF EXISTS search_vector")
    op.drop_column("literature", "analysis_status")
    op.drop_column("literature", "extracted_findings")
    op.execute("ALTER TABLE drugs DROP COLUMN IF EXISTS mechanism_embedding")
