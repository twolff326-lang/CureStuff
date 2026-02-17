"""Add target_disease_associations table and targets.ensembl_gene_id column.

Revision ID: 002
Revises: 001
Create Date: 2026-02-17
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add ensembl_gene_id column to targets
    op.add_column(
        "targets",
        sa.Column("ensembl_gene_id", sa.String(30), nullable=True),
    )
    op.create_index("ix_targets_ensembl_gene_id", "targets", ["ensembl_gene_id"])

    # Create target_disease_associations table
    op.create_table(
        "target_disease_associations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "target_id",
            sa.Integer,
            sa.ForeignKey("targets.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("disease_id", sa.String(50), nullable=False, index=True),
        sa.Column("disease_name", sa.String(500)),
        sa.Column("overall_score", sa.Float),
        sa.Column("genetic_association_score", sa.Float),
        sa.Column("somatic_mutation_score", sa.Float),
        sa.Column("known_drug_score", sa.Float),
        sa.Column("literature_score", sa.Float),
        sa.Column("rna_expression_score", sa.Float),
        sa.Column("animal_model_score", sa.Float),
        sa.Column("source", sa.String(50), nullable=False, server_default="opentargets"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_target_disease_assoc_target_disease",
        "target_disease_associations",
        ["target_id", "disease_id"],
    )


def downgrade() -> None:
    op.drop_table("target_disease_associations")
    op.drop_index("ix_targets_ensembl_gene_id", table_name="targets")
    op.drop_column("targets", "ensembl_gene_id")
