"""Add unique constraints to drug_targets, mutations, pathway_targets

Revision ID: 002_unique_constraints
Revises: 001_initial
Create Date: 2026-02-25

"""
from alembic import op

revision = "002_unique_constraints"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_drug_target_affinity", "drug_targets",
        ["drug_id", "target_id", "affinity_type"],
    )
    op.create_unique_constraint(
        "uq_mutation_cancer_gene", "mutations",
        ["cancer_type_id", "gene_symbol"],
    )
    op.create_unique_constraint(
        "uq_pathway_gene", "pathway_targets",
        ["pathway_id", "gene_symbol"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_pathway_gene", "pathway_targets", type_="unique")
    op.drop_constraint("uq_mutation_cancer_gene", "mutations", type_="unique")
    op.drop_constraint("uq_drug_target_affinity", "drug_targets", type_="unique")
