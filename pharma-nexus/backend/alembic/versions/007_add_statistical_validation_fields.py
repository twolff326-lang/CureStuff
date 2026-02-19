"""Add statistical validation fields to hypotheses and validation_results table.

Adds p-value, FDR-adjusted p-value, confidence intervals, and a validation_results
table for tracking retrospective validation metrics over time.

Revision ID: 007
Revises: 006
Create Date: 2026-02-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add statistical fields to hypotheses table
    op.add_column(
        "hypotheses",
        sa.Column("p_value", sa.Float, nullable=True, comment="Permutation-based p-value against null distribution"),
    )
    op.add_column(
        "hypotheses",
        sa.Column("fdr_adjusted_p_value", sa.Float, nullable=True, comment="BH FDR-corrected p-value"),
    )
    op.add_column(
        "hypotheses",
        sa.Column("confidence_interval", JSONB, nullable=True, comment="Bootstrap 95% CI for composite score"),
    )
    op.add_column(
        "hypotheses",
        sa.Column("scoring_method_version", sa.String(50), nullable=True, server_default="v2_statistical", comment="Scoring method version for reproducibility"),
    )

    # Create validation_results table for tracking validation over time
    op.create_table(
        "validation_results",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("run_date", sa.DateTime, server_default=sa.func.now(), nullable=False),
        sa.Column("validation_type", sa.String(50), nullable=False, comment="retrospective, ablation, calibration, null_distribution"),
        sa.Column("scoring_method_version", sa.String(50), nullable=False),
        sa.Column("n_hypotheses", sa.Integer, nullable=False),
        sa.Column("n_ground_truth_matched", sa.Integer),
        sa.Column("roc_auc", sa.Float, comment="ROC AUC for retrospective validation"),
        sa.Column("pr_auc", sa.Float, comment="Precision-Recall AUC"),
        sa.Column("mean_rank_percentile", sa.Float, comment="Mean percentile rank of true positives"),
        sa.Column("brier_score", sa.Float, comment="Calibration Brier score"),
        sa.Column("results", JSONB, nullable=False, comment="Full validation results JSON"),
        sa.Column("weights_used", JSONB, comment="Scoring weights used for this validation run"),
    )

    # Add index on hypotheses p_value for filtering significant results
    op.create_index(
        "ix_hypotheses_p_value",
        "hypotheses",
        ["p_value"],
    )
    op.create_index(
        "ix_hypotheses_fdr_p_value",
        "hypotheses",
        ["fdr_adjusted_p_value"],
    )
    op.create_index(
        "ix_validation_results_type_date",
        "validation_results",
        ["validation_type", "run_date"],
    )


def downgrade() -> None:
    op.drop_index("ix_validation_results_type_date")
    op.drop_index("ix_hypotheses_fdr_p_value")
    op.drop_index("ix_hypotheses_p_value")
    op.drop_table("validation_results")
    op.drop_column("hypotheses", "scoring_method_version")
    op.drop_column("hypotheses", "confidence_interval")
    op.drop_column("hypotheses", "fdr_adjusted_p_value")
    op.drop_column("hypotheses", "p_value")
