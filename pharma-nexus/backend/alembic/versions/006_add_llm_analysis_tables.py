"""Add hypothesis_analyses, llm_usage_logs tables and hypotheses.critique column.

Revision ID: 006
Revises: 005
Create Date: 2025-01-01 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- hypothesis_analyses table --
    op.create_table(
        "hypothesis_analyses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "hypothesis_id",
            sa.Integer(),
            sa.ForeignKey("hypotheses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("analysis_type", sa.String(50), nullable=False),
        sa.Column("model_used", sa.String(100), nullable=False),
        sa.Column("content", JSONB, nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("generation_time_seconds", sa.Float()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_hypothesis_analyses_hypothesis_id",
        "hypothesis_analyses",
        ["hypothesis_id"],
    )
    op.create_index(
        "ix_hypothesis_analyses_analysis_type",
        "hypothesis_analyses",
        ["analysis_type"],
    )
    op.create_index(
        "ix_hypothesis_analyses_hyp_type",
        "hypothesis_analyses",
        ["hypothesis_id", "analysis_type"],
        unique=True,
    )

    # -- llm_usage_logs table --
    op.create_table(
        "llm_usage_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "hypothesis_id",
            sa.Integer(),
            sa.ForeignKey("hypotheses.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("analysis_type", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost_usd", sa.Float(), server_default="0.0"),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_llm_usage_logs_hypothesis_id",
        "llm_usage_logs",
        ["hypothesis_id"],
    )
    op.create_index(
        "ix_llm_usage_logs_created_at",
        "llm_usage_logs",
        ["created_at"],
    )
    op.create_index(
        "ix_llm_usage_logs_model",
        "llm_usage_logs",
        ["model"],
    )

    # -- Add critique JSONB column to hypotheses --
    op.add_column(
        "hypotheses",
        sa.Column("critique", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hypotheses", "critique")
    op.drop_table("llm_usage_logs")
    op.drop_table("hypothesis_analyses")
