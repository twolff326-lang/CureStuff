"""Initial schema with all core tables and pgvector extension.

Revision ID: 001
Revises: None
Create Date: 2026-02-16

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # --- drugs ---
    op.create_table(
        "drugs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("drugbank_id", sa.String(20), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("generic_name", sa.String(500), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("mechanism_of_action", sa.Text(), nullable=True),
        sa.Column("pharmacodynamics", sa.Text(), nullable=True),
        sa.Column("indication", sa.Text(), nullable=True),
        sa.Column("status", sa.String(50), nullable=False, server_default="approved"),
        sa.Column("molecular_formula", sa.String(200), nullable=True),
        sa.Column("smiles", sa.Text(), nullable=True),
        sa.Column("inchi_key", sa.String(100), nullable=True),
        sa.Column("cas_number", sa.String(50), nullable=True),
        sa.Column("categories", postgresql.JSONB(), nullable=True, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("drugbank_id"),
    )
    op.create_index("ix_drugs_drugbank_id", "drugs", ["drugbank_id"])
    op.create_index("ix_drugs_name", "drugs", ["name"])
    op.create_index("ix_drugs_status", "drugs", ["status"])
    op.create_index("ix_drugs_inchi_key", "drugs", ["inchi_key"])
    op.create_index("ix_drugs_categories", "drugs", ["categories"], postgresql_using="gin")

    # --- targets ---
    op.create_table(
        "targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uniprot_id", sa.String(20), nullable=False),
        sa.Column("gene_symbol", sa.String(50), nullable=False),
        sa.Column("gene_name", sa.String(500), nullable=True),
        sa.Column("organism", sa.String(200), nullable=True, server_default="Homo sapiens"),
        sa.Column("function_description", sa.Text(), nullable=True),
        sa.Column("subcellular_location", sa.Text(), nullable=True),
        sa.Column("protein_class", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uniprot_id"),
    )
    # Add vector column separately (alembic doesn't natively handle vector type)
    op.execute("ALTER TABLE targets ADD COLUMN embedding vector(384)")
    op.create_index("ix_targets_uniprot_id", "targets", ["uniprot_id"])
    op.create_index("ix_targets_gene_symbol", "targets", ["gene_symbol"])

    # --- drug_targets ---
    op.create_table(
        "drug_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("drug_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("action_type", sa.String(100), nullable=True),
        sa.Column("known_action", sa.Boolean(), nullable=True, server_default="false"),
        sa.Column("binding_affinity_nm", sa.Float(), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("references", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.ForeignKeyConstraint(["drug_id"], ["drugs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_drug_targets_drug_id", "drug_targets", ["drug_id"])
    op.create_index("ix_drug_targets_target_id", "drug_targets", ["target_id"])
    op.create_index("ix_drug_targets_drug_target", "drug_targets", ["drug_id", "target_id"])
    op.create_index("ix_drug_targets_references", "drug_targets", ["references"], postgresql_using="gin")

    # --- pathways ---
    op.create_table(
        "pathways",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(200), nullable=True),
        sa.Column("genes", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("parent_pathway_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["parent_pathway_id"], ["pathways.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pathways_external_id", "pathways", ["external_id"])
    op.create_index("ix_pathways_name", "pathways", ["name"])
    op.create_index("ix_pathways_source_external", "pathways", ["source", "external_id"], unique=True)
    op.create_index("ix_pathways_genes", "pathways", ["genes"], postgresql_using="gin")

    # --- pathway_targets ---
    op.create_table(
        "pathway_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pathway_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(50), nullable=True, server_default="component"),
        sa.ForeignKeyConstraint(["pathway_id"], ["pathways.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pathway_targets_pathway_id", "pathway_targets", ["pathway_id"])
    op.create_index("ix_pathway_targets_target_id", "pathway_targets", ["target_id"])
    op.create_index("ix_pathway_targets_pathway_target", "pathway_targets", ["pathway_id", "target_id"])

    # --- cancer_types ---
    op.create_table(
        "cancer_types",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("tcga_code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("tissue", sa.String(200), nullable=True),
        sa.Column("organ", sa.String(200), nullable=True),
        sa.Column("subtype", sa.String(200), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tcga_code"),
    )
    op.create_index("ix_cancer_types_tcga_code", "cancer_types", ["tcga_code"])
    op.create_index("ix_cancer_types_name", "cancer_types", ["name"])

    # --- cancer_molecular_profiles ---
    op.create_table(
        "cancer_molecular_profiles",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cancer_type_id", sa.Integer(), nullable=False),
        sa.Column("gene_symbol", sa.String(50), nullable=False),
        sa.Column("alteration_type", sa.String(50), nullable=False),
        sa.Column("frequency_percent", sa.Float(), nullable=True),
        sa.Column("median_expression", sa.Float(), nullable=True),
        sa.Column("expression_zscore", sa.Float(), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.ForeignKeyConstraint(["cancer_type_id"], ["cancer_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "frequency_percent IS NULL OR (frequency_percent >= 0 AND frequency_percent <= 100)",
            name="ck_cancer_molecular_profiles_frequency",
        ),
    )
    op.create_index("ix_cancer_molecular_profiles_cancer_type_id", "cancer_molecular_profiles", ["cancer_type_id"])
    op.create_index("ix_cancer_molecular_profiles_gene_symbol", "cancer_molecular_profiles", ["gene_symbol"])
    op.create_index("ix_cancer_molecular_profiles_cancer_gene", "cancer_molecular_profiles", ["cancer_type_id", "gene_symbol"])

    # --- mutations ---
    op.create_table(
        "mutations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cancer_type_id", sa.Integer(), nullable=False),
        sa.Column("gene_symbol", sa.String(50), nullable=False),
        sa.Column("mutation_type", sa.String(50), nullable=False),
        sa.Column("protein_change", sa.String(100), nullable=True),
        sa.Column("genomic_position", sa.String(100), nullable=True),
        sa.Column("frequency_percent", sa.Float(), nullable=True),
        sa.Column("functional_impact", sa.String(20), nullable=True, server_default="unknown"),
        sa.Column("cosmic_id", sa.String(50), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.ForeignKeyConstraint(["cancer_type_id"], ["cancer_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "frequency_percent IS NULL OR (frequency_percent >= 0 AND frequency_percent <= 100)",
            name="ck_mutations_frequency",
        ),
    )
    op.create_index("ix_mutations_cancer_type_id", "mutations", ["cancer_type_id"])
    op.create_index("ix_mutations_gene_symbol", "mutations", ["gene_symbol"])
    op.create_index("ix_mutations_cosmic_id", "mutations", ["cosmic_id"])
    op.create_index("ix_mutations_cancer_gene", "mutations", ["cancer_type_id", "gene_symbol"])

    # --- literature ---
    op.create_table(
        "literature",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pmid", sa.String(20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=True),
        sa.Column("authors", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("journal", sa.String(500), nullable=True),
        sa.Column("pub_date", sa.Date(), nullable=True),
        sa.Column("doi", sa.String(200), nullable=True),
        sa.Column("mesh_terms", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("relevance_tags", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pmid"),
    )
    # Add vector column separately
    op.execute("ALTER TABLE literature ADD COLUMN abstract_embedding vector(384)")
    op.create_index("ix_literature_pmid", "literature", ["pmid"])
    op.create_index("ix_literature_doi", "literature", ["doi"])
    op.create_index("ix_literature_authors", "literature", ["authors"], postgresql_using="gin")
    op.create_index("ix_literature_mesh_terms", "literature", ["mesh_terms"], postgresql_using="gin")
    op.create_index("ix_literature_relevance_tags", "literature", ["relevance_tags"], postgresql_using="gin")

    # --- literature_drugs ---
    op.create_table(
        "literature_drugs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("literature_id", sa.Integer(), nullable=False),
        sa.Column("drug_id", sa.Integer(), nullable=False),
        sa.Column("mention_type", sa.String(50), nullable=True, server_default="passing"),
        sa.ForeignKeyConstraint(["literature_id"], ["literature.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["drug_id"], ["drugs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_literature_drugs_literature_id", "literature_drugs", ["literature_id"])
    op.create_index("ix_literature_drugs_drug_id", "literature_drugs", ["drug_id"])
    op.create_index("ix_literature_drugs_lit_drug", "literature_drugs", ["literature_id", "drug_id"])

    # --- literature_targets ---
    op.create_table(
        "literature_targets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("literature_id", sa.Integer(), nullable=False),
        sa.Column("target_id", sa.Integer(), nullable=False),
        sa.Column("mention_type", sa.String(50), nullable=True, server_default="passing"),
        sa.ForeignKeyConstraint(["literature_id"], ["literature.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_literature_targets_literature_id", "literature_targets", ["literature_id"])
    op.create_index("ix_literature_targets_target_id", "literature_targets", ["target_id"])
    op.create_index("ix_literature_targets_lit_target", "literature_targets", ["literature_id", "target_id"])

    # --- literature_cancers ---
    op.create_table(
        "literature_cancers",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("literature_id", sa.Integer(), nullable=False),
        sa.Column("cancer_type_id", sa.Integer(), nullable=False),
        sa.Column("mention_type", sa.String(50), nullable=True, server_default="passing"),
        sa.ForeignKeyConstraint(["literature_id"], ["literature.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cancer_type_id"], ["cancer_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_literature_cancers_literature_id", "literature_cancers", ["literature_id"])
    op.create_index("ix_literature_cancers_cancer_type_id", "literature_cancers", ["cancer_type_id"])
    op.create_index("ix_literature_cancers_lit_cancer", "literature_cancers", ["literature_id", "cancer_type_id"])

    # --- clinical_trials ---
    op.create_table(
        "clinical_trials",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("nct_id", sa.String(20), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("phase", sa.String(20), nullable=True),
        sa.Column("conditions", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("interventions", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("enrollment", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("completion_date", sa.Date(), nullable=True),
        sa.Column("results_summary", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nct_id"),
    )
    op.create_index("ix_clinical_trials_nct_id", "clinical_trials", ["nct_id"])
    op.create_index("ix_clinical_trials_status", "clinical_trials", ["status"])
    op.create_index("ix_clinical_trials_conditions", "clinical_trials", ["conditions"], postgresql_using="gin")
    op.create_index("ix_clinical_trials_interventions", "clinical_trials", ["interventions"], postgresql_using="gin")

    # --- trial_drugs ---
    op.create_table(
        "trial_drugs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("trial_id", sa.Integer(), nullable=False),
        sa.Column("drug_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["trial_id"], ["clinical_trials.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["drug_id"], ["drugs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trial_drugs_trial_id", "trial_drugs", ["trial_id"])
    op.create_index("ix_trial_drugs_drug_id", "trial_drugs", ["drug_id"])
    op.create_index("ix_trial_drugs_trial_drug", "trial_drugs", ["trial_id", "drug_id"])

    # --- bioassays ---
    op.create_table(
        "bioassays",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pubchem_aid", sa.String(50), nullable=True),
        sa.Column("target_id", sa.Integer(), nullable=True),
        sa.Column("drug_id", sa.Integer(), nullable=True),
        sa.Column("activity_type", sa.String(20), nullable=True),
        sa.Column("activity_value", sa.Float(), nullable=True),
        sa.Column("activity_unit", sa.String(20), nullable=True),
        sa.Column("activity_outcome", sa.String(20), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["drug_id"], ["drugs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bioassays_pubchem_aid", "bioassays", ["pubchem_aid"])
    op.create_index("ix_bioassays_target_id", "bioassays", ["target_id"])
    op.create_index("ix_bioassays_drug_id", "bioassays", ["drug_id"])
    op.create_index("ix_bioassays_drug_target", "bioassays", ["drug_id", "target_id"])

    # --- hypotheses ---
    op.create_table(
        "hypotheses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("drug_id", sa.Integer(), nullable=False),
        sa.Column("cancer_type_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(1000), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("mechanism_narrative", sa.Text(), nullable=True),
        sa.Column("composite_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("evidence_strength", sa.String(20), nullable=True, server_default="speculative"),
        sa.Column("pathway_overlap_score", sa.Float(), nullable=True),
        sa.Column("expression_correlation_score", sa.Float(), nullable=True),
        sa.Column("literature_support_score", sa.Float(), nullable=True),
        sa.Column("clinical_evidence_score", sa.Float(), nullable=True),
        sa.Column("safety_score", sa.Float(), nullable=True),
        sa.Column("novelty_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="generated"),
        sa.Column("reviewer_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["drug_id"], ["drugs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cancer_type_id"], ["cancer_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "composite_score >= 0 AND composite_score <= 100",
            name="ck_hypotheses_composite_score",
        ),
    )
    op.create_index("ix_hypotheses_drug_id", "hypotheses", ["drug_id"])
    op.create_index("ix_hypotheses_cancer_type_id", "hypotheses", ["cancer_type_id"])
    op.create_index("ix_hypotheses_status", "hypotheses", ["status"])
    op.create_index("ix_hypotheses_drug_cancer", "hypotheses", ["drug_id", "cancer_type_id"])
    op.execute("CREATE INDEX ix_hypotheses_composite_score_desc ON hypotheses (composite_score DESC)")

    # --- hypothesis_evidence ---
    op.create_table(
        "hypothesis_evidence",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("hypothesis_id", sa.Integer(), nullable=False),
        sa.Column("evidence_type", sa.String(50), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=True),
        sa.Column("source_id", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("strength", sa.String(20), nullable=True, server_default="weak"),
        sa.Column("confidence", sa.Float(), nullable=True, server_default="0"),
        sa.Column("raw_data", postgresql.JSONB(), nullable=True, server_default="{}"),
        sa.ForeignKeyConstraint(["hypothesis_id"], ["hypotheses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_hypothesis_evidence_confidence",
        ),
    )
    op.create_index("ix_hypothesis_evidence_hypothesis_id", "hypothesis_evidence", ["hypothesis_id"])
    op.create_index("ix_hypothesis_evidence_raw_data", "hypothesis_evidence", ["raw_data"], postgresql_using="gin")

    # --- protein_interactions ---
    op.create_table(
        "protein_interactions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("protein_a_uniprot", sa.String(20), nullable=False),
        sa.Column("protein_b_uniprot", sa.String(20), nullable=False),
        sa.Column("interaction_score", sa.Float(), nullable=True),
        sa.Column("experimental_score", sa.Float(), nullable=True),
        sa.Column("database_score", sa.Float(), nullable=True),
        sa.Column("textmining_score", sa.Float(), nullable=True),
        sa.Column("source", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_protein_interactions_protein_a", "protein_interactions", ["protein_a_uniprot"])
    op.create_index("ix_protein_interactions_protein_b", "protein_interactions", ["protein_b_uniprot"])
    op.create_index("ix_protein_interactions_pair", "protein_interactions", ["protein_a_uniprot", "protein_b_uniprot"])

    # --- gene_expression ---
    op.create_table(
        "gene_expression",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cancer_type_id", sa.Integer(), nullable=False),
        sa.Column("gene_symbol", sa.String(50), nullable=False),
        sa.Column("sample_id", sa.String(100), nullable=False),
        sa.Column("expression_value", sa.Float(), nullable=True),
        sa.Column("expression_log2", sa.Float(), nullable=True),
        sa.Column("is_tumor", sa.Boolean(), nullable=True, server_default="true"),
        sa.Column("source", sa.String(50), nullable=False),
        sa.ForeignKeyConstraint(["cancer_type_id"], ["cancer_types.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_gene_expression_cancer_type_id", "gene_expression", ["cancer_type_id"])
    op.create_index("ix_gene_expression_gene_symbol", "gene_expression", ["gene_symbol"])
    op.create_index("ix_gene_expression_cancer_gene", "gene_expression", ["cancer_type_id", "gene_symbol"])

    # --- ingestion_logs ---
    op.create_table(
        "ingestion_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("task_type", sa.String(100), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="started"),
        sa.Column("records_processed", sa.Integer(), nullable=True, server_default="0"),
        sa.Column("errors", postgresql.JSONB(), nullable=True, server_default="[]"),
        sa.Column("started_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ingestion_logs_source", "ingestion_logs", ["source"])
    op.create_index("ix_ingestion_logs_status", "ingestion_logs", ["status"])
    op.create_index("ix_ingestion_logs_errors", "ingestion_logs", ["errors"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_table("ingestion_logs")
    op.drop_table("gene_expression")
    op.drop_table("protein_interactions")
    op.drop_table("hypothesis_evidence")
    op.drop_table("hypotheses")
    op.drop_table("bioassays")
    op.drop_table("trial_drugs")
    op.drop_table("clinical_trials")
    op.drop_table("literature_cancers")
    op.drop_table("literature_targets")
    op.drop_table("literature_drugs")
    op.drop_table("literature")
    op.drop_table("mutations")
    op.drop_table("cancer_molecular_profiles")
    op.drop_table("cancer_types")
    op.drop_table("pathway_targets")
    op.drop_table("pathways")
    op.drop_table("drug_targets")
    op.drop_table("targets")
    op.drop_table("drugs")
    op.execute("DROP EXTENSION IF EXISTS vector")
