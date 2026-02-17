"""Add validation_cases and hypothesis_outcomes tables.

Supports ground truth benchmarking and outcome tracking feedback loop
for calibrating the scoring system.

Revision ID: 009
Revises: 008
Create Date: 2026-02-17 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "validation_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("drug_name", sa.String(500), nullable=False),
        sa.Column("drug_drugbank_id", sa.String(20), nullable=True),
        sa.Column("cancer_name", sa.String(300), nullable=False),
        sa.Column("cancer_tcga_code", sa.String(20), nullable=True),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("outcome_detail", sa.Text(), nullable=True),
        sa.Column("fda_approved", sa.Boolean(), server_default="false"),
        sa.Column("approval_year", sa.Integer(), nullable=True),
        sa.Column("max_trial_phase", sa.String(20), nullable=True),
        sa.Column("reference_pmids", postgresql.JSONB(), server_default="[]"),
        sa.Column("reference_nct_ids", postgresql.JSONB(), server_default="[]"),
        sa.Column(
            "matched_drug_id",
            sa.Integer(),
            sa.ForeignKey("drugs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "matched_cancer_type_id",
            sa.Integer(),
            sa.ForeignKey("cancer_types.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "matched_hypothesis_id",
            sa.Integer(),
            sa.ForeignKey("hypotheses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("predicted_composite_score", sa.Float(), nullable=True),
        sa.Column("predicted_strength", sa.String(20), nullable=True),
        sa.Column("dimension_scores_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("source", sa.String(100), nullable=False, server_default="curated"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_validation_cases_outcome", "validation_cases", ["outcome"])
    op.create_index(
        "ix_validation_cases_drug_cancer",
        "validation_cases",
        ["drug_drugbank_id", "cancer_tcga_code"],
    )

    op.create_table(
        "hypothesis_outcomes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "hypothesis_id",
            sa.Integer(),
            sa.ForeignKey("hypotheses.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("outcome", sa.String(30), nullable=False),
        sa.Column("outcome_notes", sa.Text(), nullable=True),
        sa.Column("composite_score_at_review", sa.Float(), nullable=True),
        sa.Column("dimension_scores_at_review", postgresql.JSONB(), nullable=True),
        sa.Column("reviewer", sa.String(200), nullable=True),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("supporting_evidence", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_hypothesis_outcomes_outcome", "hypothesis_outcomes", ["outcome"]
    )


def downgrade() -> None:
    op.drop_table("hypothesis_outcomes")
    op.drop_table("validation_cases")
