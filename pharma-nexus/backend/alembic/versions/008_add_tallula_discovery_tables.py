"""Add Tallula Algorithm discovery tables.

The Tallula Algorithm (Stochastic Resonance Ensemble Discovery) stores its
run metadata and per-hypothesis discovery classifications in two new tables.

Revision ID: 008
Revises: 007
Create Date: 2026-02-19 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- tallula_runs: one row per algorithm execution --
    op.create_table(
        "tallula_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("n_lenses", sa.Integer, nullable=False),
        sa.Column("dropout_rate", sa.Float, nullable=False),
        sa.Column("dirichlet_alpha", sa.Float, nullable=False),
        sa.Column("seed", sa.Integer, nullable=True),
        sa.Column("n_hypotheses_input", sa.Integer, nullable=False),
        sa.Column(
            "cancer_type_id", sa.Integer,
            sa.ForeignKey("cancer_types.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("n_resonant", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_robust", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_fragile", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_moderate", sa.Integer, nullable=False, server_default="0"),
        sa.Column("n_weak", sa.Integer, nullable=False, server_default="0"),
        sa.Column("parameters", JSONB, nullable=False, server_default="{}"),
        sa.Column("summary", JSONB, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tallula_runs_created", "tallula_runs", ["created_at"])

    # -- tallula_discoveries: one row per hypothesis per run --
    op.create_table(
        "tallula_discoveries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "run_id", sa.Integer,
            sa.ForeignKey("tallula_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "hypothesis_id", sa.Integer,
            sa.ForeignKey("hypotheses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("drug_id", sa.Integer, nullable=False),
        sa.Column("cancer_type_id", sa.Integer, nullable=False),
        sa.Column("discovery_class", sa.String(20), nullable=False),
        sa.Column("deterministic_score", sa.Float, nullable=False),
        sa.Column("ubiquity", sa.Float, nullable=False),
        sa.Column("resonance", sa.Float, nullable=False),
        sa.Column("fragility_index", sa.Float, nullable=False),
        sa.Column("critical_dimension", sa.String(50), nullable=True),
        sa.Column("score_mean", sa.Float, nullable=True),
        sa.Column("score_median", sa.Float, nullable=True),
        sa.Column("score_std", sa.Float, nullable=True),
        sa.Column("score_max", sa.Float, nullable=True),
        sa.Column("score_min", sa.Float, nullable=True),
        sa.Column("resonance_profile", JSONB, nullable=True),
        sa.Column("ablation_impacts", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tallula_disc_run_id", "tallula_discoveries", ["run_id"])
    op.create_index("ix_tallula_disc_hypothesis_id", "tallula_discoveries", ["hypothesis_id"])
    op.create_index("ix_tallula_disc_drug_id", "tallula_discoveries", ["drug_id"])
    op.create_index("ix_tallula_disc_cancer_type_id", "tallula_discoveries", ["cancer_type_id"])
    op.create_index("ix_tallula_disc_class", "tallula_discoveries", ["discovery_class"])
    op.create_index(
        "ix_tallula_disc_resonance",
        "tallula_discoveries",
        [sa.text("resonance DESC")],
    )
    op.create_index(
        "ix_tallula_disc_run_class",
        "tallula_discoveries",
        ["run_id", "discovery_class"],
    )


def downgrade() -> None:
    op.drop_index("ix_tallula_disc_run_class")
    op.drop_index("ix_tallula_disc_resonance")
    op.drop_index("ix_tallula_disc_class")
    op.drop_index("ix_tallula_disc_cancer_type_id")
    op.drop_index("ix_tallula_disc_drug_id")
    op.drop_index("ix_tallula_disc_hypothesis_id")
    op.drop_index("ix_tallula_disc_run_id")
    op.drop_table("tallula_discoveries")
    op.drop_index("ix_tallula_runs_created")
    op.drop_table("tallula_runs")
