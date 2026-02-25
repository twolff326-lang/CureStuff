"""Initial simplified schema - 9 tables

Revision ID: 001_initial
Revises:
Create Date: 2026-02-25

"""
from alembic import op
import sqlalchemy as sa

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drugs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, index=True),
        sa.Column("generic_name", sa.String(255)),
        sa.Column("drugbank_id", sa.String(20), unique=True),
        sa.Column("pubchem_cid", sa.Integer(), unique=True),
        sa.Column("chembl_id", sa.String(30), unique=True),
        sa.Column("smiles", sa.Text()),
        sa.Column("molecular_formula", sa.String(255)),
        sa.Column("molecular_weight", sa.Float()),
        sa.Column("mechanism_of_action", sa.Text()),
        sa.Column("status", sa.String(50), server_default="approved", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gene_symbol", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("uniprot_id", sa.String(20)),
        sa.Column("name", sa.String(255)),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "drug_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("drug_id", sa.Integer(), sa.ForeignKey("drugs.id", ondelete="CASCADE"), index=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("targets.id", ondelete="CASCADE"), index=True),
        sa.Column("action_type", sa.String(50)),
        sa.Column("binding_affinity", sa.Float()),
        sa.Column("affinity_type", sa.String(20)),
        sa.Column("affinity_units", sa.String(20)),
        sa.Column("source", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "cancer_types",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True, index=True),
        sa.Column("tcga_code", sa.String(20), unique=True),
        sa.Column("description", sa.Text()),
        sa.Column("tissue", sa.String(100)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "mutations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cancer_type_id", sa.Integer(), sa.ForeignKey("cancer_types.id", ondelete="CASCADE"), index=True),
        sa.Column("gene_symbol", sa.String(50), nullable=False, index=True),
        sa.Column("mutation_type", sa.String(50)),
        sa.Column("frequency", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "pathways",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, index=True),
        sa.Column("source_id", sa.String(50)),
        sa.Column("source", sa.String(50), server_default="reactome"),
        sa.Column("description", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "pathway_targets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pathway_id", sa.Integer(), sa.ForeignKey("pathways.id", ondelete="CASCADE"), index=True),
        sa.Column("gene_symbol", sa.String(50), nullable=False, index=True),
        sa.Column("role", sa.String(50)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "hypotheses",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("drug_id", sa.Integer(), sa.ForeignKey("drugs.id", ondelete="CASCADE"), index=True),
        sa.Column("cancer_type_id", sa.Integer(), sa.ForeignKey("cancer_types.id", ondelete="CASCADE"), index=True),
        sa.Column("composite_score", sa.Float(), server_default="0"),
        sa.Column("target_binding_score", sa.Float(), server_default="0"),
        sa.Column("pathway_overlap_score", sa.Float(), server_default="0"),
        sa.Column("clinical_evidence_score", sa.Float(), server_default="0"),
        sa.Column("evidence_summary", sa.Text()),
        sa.Column("strategy", sa.String(50)),
        sa.Column("status", sa.String(30), server_default="generated", index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("drug_id", "cancer_type_id", name="uq_hypothesis_drug_cancer"),
    )

    op.create_table(
        "ingestion_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(50), nullable=False, index=True),
        sa.Column("status", sa.String(30), server_default="pending"),
        sa.Column("records_processed", sa.Integer(), server_default="0"),
        sa.Column("total_expected", sa.Integer(), server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("ingestion_logs")
    op.drop_table("hypotheses")
    op.drop_table("pathway_targets")
    op.drop_table("pathways")
    op.drop_table("mutations")
    op.drop_table("cancer_types")
    op.drop_table("drug_targets")
    op.drop_table("targets")
    op.drop_table("drugs")
