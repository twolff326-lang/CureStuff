"""Add missing columns to hypotheses table.

The Hypothesis model accumulated several new columns (causal_dependency_score,
mutation_context_score, polypharmacology_score, batch_id,
generation_pipeline_run_id, dimension_confidence_intervals) that were never
added via migration.  This migration brings the DB schema in sync.

Revision ID: 016
Revises: 015
Create Date: 2026-02-23
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def _column_exists(conn, table: str, column: str) -> bool:
    inspector = inspect(conn)
    columns = [c["name"] for c in inspector.get_columns(table)]
    return column in columns


def upgrade() -> None:
    conn = op.get_bind()

    if not _column_exists(conn, "hypotheses", "causal_dependency_score"):
        op.add_column(
            "hypotheses",
            sa.Column(
                "causal_dependency_score",
                sa.Float(),
                nullable=True,
                comment="DepMap CRISPR gene dependency score",
            ),
        )

    if not _column_exists(conn, "hypotheses", "mutation_context_score"):
        op.add_column(
            "hypotheses",
            sa.Column(
                "mutation_context_score",
                sa.Float(),
                nullable=True,
                server_default="0.0",
                comment="Mutation-conditional vulnerability score",
            ),
        )

    if not _column_exists(conn, "hypotheses", "polypharmacology_score"):
        op.add_column(
            "hypotheses",
            sa.Column(
                "polypharmacology_score",
                sa.Float(),
                nullable=True,
                server_default="0.0",
                comment="Off-target bioassay activity score",
            ),
        )

    if not _column_exists(conn, "hypotheses", "batch_id"):
        op.add_column(
            "hypotheses",
            sa.Column(
                "batch_id",
                sa.String(36),
                nullable=True,
                comment="UUID grouping hypotheses from the same generation batch",
            ),
        )
        op.create_index("ix_hypotheses_batch_id", "hypotheses", ["batch_id"])

    if not _column_exists(conn, "hypotheses", "generation_pipeline_run_id"):
        op.add_column(
            "hypotheses",
            sa.Column(
                "generation_pipeline_run_id",
                sa.Integer(),
                sa.ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
                nullable=True,
                comment="Pipeline run that generated this hypothesis",
            ),
        )

    if not _column_exists(conn, "hypotheses", "dimension_confidence_intervals"):
        op.add_column(
            "hypotheses",
            sa.Column(
                "dimension_confidence_intervals",
                JSONB(),
                nullable=True,
                comment="Per-dimension bootstrap 95% CIs",
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()

    if _column_exists(conn, "hypotheses", "dimension_confidence_intervals"):
        op.drop_column("hypotheses", "dimension_confidence_intervals")
    if _column_exists(conn, "hypotheses", "generation_pipeline_run_id"):
        op.drop_column("hypotheses", "generation_pipeline_run_id")
    if _column_exists(conn, "hypotheses", "batch_id"):
        op.drop_index("ix_hypotheses_batch_id", "hypotheses")
        op.drop_column("hypotheses", "batch_id")
    if _column_exists(conn, "hypotheses", "polypharmacology_score"):
        op.drop_column("hypotheses", "polypharmacology_score")
    if _column_exists(conn, "hypotheses", "mutation_context_score"):
        op.drop_column("hypotheses", "mutation_context_score")
    if _column_exists(conn, "hypotheses", "causal_dependency_score"):
        op.drop_column("hypotheses", "causal_dependency_score")
