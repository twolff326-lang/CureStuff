"""Add gene_dependencies and combination_hypotheses tables.

These tables support DepMap gene dependency scoring and drug combination
synergy predictions.

Revision ID: 009
Revises: 008
Create Date: 2026-02-19 08:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects.postgresql import JSONB

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return inspect(bind).has_table(name)


def upgrade() -> None:
    # -- gene_dependencies: per-gene dependency scores from DepMap --
    if not _table_exists("gene_dependencies"):
        op.create_table(
            "gene_dependencies",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("gene_symbol", sa.String(50), nullable=False),
            sa.Column("depmap_id", sa.String(50), nullable=True),
            sa.Column("lineage", sa.String(100), nullable=False),
            sa.Column("gene_effect", sa.Float, nullable=False),
            sa.Column("num_cell_lines", sa.Integer, nullable=True),
            sa.Column("dependency_probability", sa.Float, nullable=True),
            sa.Column("is_common_essential", sa.Integer, server_default="0"),
            sa.Column("is_strongly_selective", sa.Integer, server_default="0"),
            sa.Column("selectivity_score", sa.Float, nullable=True),
            sa.Column("source", sa.String(50), nullable=False, server_default="depmap"),
            sa.Column("dataset_version", sa.String(50), nullable=True),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_gene_dependencies_gene_symbol", "gene_dependencies", ["gene_symbol"])
        op.create_index("ix_gene_dependencies_lineage", "gene_dependencies", ["lineage"])
        op.create_index("ix_gene_dep_gene_lineage", "gene_dependencies", ["gene_symbol", "lineage"], unique=True)
        op.create_index("ix_gene_dep_effect", "gene_dependencies", ["gene_effect"])

    # -- combination_hypotheses: drug pair synergy predictions --
    if not _table_exists("combination_hypotheses"):
        op.create_table(
            "combination_hypotheses",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("drug_a_id", sa.Integer, nullable=False),
            sa.Column("drug_b_id", sa.Integer, nullable=False),
            sa.Column("cancer_type_id", sa.Integer, nullable=False),
            sa.Column("hypothesis_a_id", sa.Integer, nullable=True),
            sa.Column("hypothesis_b_id", sa.Integer, nullable=True),
            sa.Column("pathway_complementarity_score", sa.Float, server_default="0.0"),
            sa.Column("target_non_overlap_score", sa.Float, server_default="0.0"),
            sa.Column("synthetic_lethality_score", sa.Float, server_default="0.0"),
            sa.Column("safety_compatibility_score", sa.Float, server_default="0.0"),
            sa.Column("clinical_precedent_score", sa.Float, server_default="0.0"),
            sa.Column("synergy_score", sa.Float, nullable=False, server_default="0.0"),
            sa.Column("synergy_classification", sa.String(30), server_default="unknown"),
            sa.Column("rationale", sa.Text, nullable=True),
            sa.Column("shared_pathways", JSONB, server_default="[]"),
            sa.Column("complementary_pathways", JSONB, server_default="[]"),
            sa.Column("details", JSONB, server_default="{}"),
            sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_combination_hypotheses_drug_a_id", "combination_hypotheses", ["drug_a_id"])
        op.create_index("ix_combination_hypotheses_drug_b_id", "combination_hypotheses", ["drug_b_id"])
        op.create_index("ix_combination_hypotheses_cancer_type_id", "combination_hypotheses", ["cancer_type_id"])
        op.create_index(
            "ix_combo_hyp_drugs_cancer",
            "combination_hypotheses",
            ["drug_a_id", "drug_b_id", "cancer_type_id"],
            unique=True,
        )
        op.create_index(
            "ix_combo_hyp_synergy_desc",
            "combination_hypotheses",
            [sa.text("synergy_score DESC")],
        )


def downgrade() -> None:
    op.drop_index("ix_combo_hyp_synergy_desc")
    op.drop_index("ix_combo_hyp_drugs_cancer")
    op.drop_index("ix_combination_hypotheses_cancer_type_id")
    op.drop_index("ix_combination_hypotheses_drug_b_id")
    op.drop_index("ix_combination_hypotheses_drug_a_id")
    op.drop_table("combination_hypotheses")
    op.drop_index("ix_gene_dep_effect")
    op.drop_index("ix_gene_dep_gene_lineage")
    op.drop_index("ix_gene_dependencies_lineage")
    op.drop_index("ix_gene_dependencies_gene_symbol")
    op.drop_table("gene_dependencies")
