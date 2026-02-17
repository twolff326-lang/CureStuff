"""Add LLM confidence score column to hypotheses table.

Closes the feedback loop: LLM confidence assessments now flow back into
the composite scoring as a multiplicative gate, replacing purely decorative
storage of Claude's biological reasoning.

Revision ID: 010
Revises: 009
Create Date: 2026-02-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # LLM confidence score (0-100), populated after confidence assessment runs
    op.add_column(
        "hypotheses",
        sa.Column("llm_confidence_score", sa.Float(), nullable=True),
    )
    # The final score that users see: composite * confidence gate
    op.add_column(
        "hypotheses",
        sa.Column("adjusted_score", sa.Float(), nullable=True),
    )
    # Recommendation from the LLM (proceed_immediately, proceed_with_caution, etc.)
    op.add_column(
        "hypotheses",
        sa.Column("llm_recommendation", sa.String(50), nullable=True),
    )
    # Index for the new adjusted_score (primary ranking column)
    op.create_index(
        "ix_hypotheses_adjusted_score_desc",
        "hypotheses",
        [sa.text("adjusted_score DESC NULLS LAST")],
    )


def downgrade() -> None:
    op.drop_index("ix_hypotheses_adjusted_score_desc", table_name="hypotheses")
    op.drop_column("hypotheses", "llm_recommendation")
    op.drop_column("hypotheses", "adjusted_score")
    op.drop_column("hypotheses", "llm_confidence_score")
