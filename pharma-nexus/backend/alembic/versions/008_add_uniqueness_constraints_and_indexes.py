"""Add uniqueness constraints and missing indexes to junction tables.

Prevents duplicate records in DrugTarget, LiteratureDrug, LiteratureTarget,
LiteratureCancer, TrialDrug, and HypothesisEvidence. Adds missing indexes
on HypothesisEvidence.source_id and GeneExpression(cancer_type_id, is_tumor).

Revision ID: 008
Revises: 007
Create Date: 2026-02-17 00:00:00.000000
"""

from alembic import op

# revision identifiers
revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Deduplicate existing data before adding constraints ---

    # DrugTarget: keep lowest id per (drug_id, target_id)
    op.execute(
        """
        DELETE FROM drug_targets
        WHERE id NOT IN (
            SELECT MIN(id) FROM drug_targets GROUP BY drug_id, target_id
        )
        """
    )

    # LiteratureDrug: keep lowest id per (literature_id, drug_id)
    op.execute(
        """
        DELETE FROM literature_drugs
        WHERE id NOT IN (
            SELECT MIN(id) FROM literature_drugs GROUP BY literature_id, drug_id
        )
        """
    )

    # LiteratureTarget: keep lowest id per (literature_id, target_id)
    op.execute(
        """
        DELETE FROM literature_targets
        WHERE id NOT IN (
            SELECT MIN(id) FROM literature_targets GROUP BY literature_id, target_id
        )
        """
    )

    # LiteratureCancer: keep lowest id per (literature_id, cancer_type_id)
    op.execute(
        """
        DELETE FROM literature_cancers
        WHERE id NOT IN (
            SELECT MIN(id) FROM literature_cancers GROUP BY literature_id, cancer_type_id
        )
        """
    )

    # TrialDrug: keep lowest id per (trial_id, drug_id)
    op.execute(
        """
        DELETE FROM trial_drugs
        WHERE id NOT IN (
            SELECT MIN(id) FROM trial_drugs GROUP BY trial_id, drug_id
        )
        """
    )

    # HypothesisEvidence: keep lowest id per (hypothesis_id, evidence_type, source_id)
    op.execute(
        """
        DELETE FROM hypothesis_evidence
        WHERE id NOT IN (
            SELECT MIN(id) FROM hypothesis_evidence
            GROUP BY hypothesis_id, evidence_type, source_id
        )
        """
    )

    # --- Add uniqueness constraints ---

    op.create_unique_constraint(
        "uq_drug_targets_drug_target", "drug_targets", ["drug_id", "target_id"]
    )
    op.create_unique_constraint(
        "uq_literature_drugs_lit_drug", "literature_drugs", ["literature_id", "drug_id"]
    )
    op.create_unique_constraint(
        "uq_literature_targets_lit_target",
        "literature_targets",
        ["literature_id", "target_id"],
    )
    op.create_unique_constraint(
        "uq_literature_cancers_lit_cancer",
        "literature_cancers",
        ["literature_id", "cancer_type_id"],
    )
    op.create_unique_constraint(
        "uq_trial_drugs_trial_drug", "trial_drugs", ["trial_id", "drug_id"]
    )
    op.create_unique_constraint(
        "uq_hypothesis_evidence_hyp_type_source",
        "hypothesis_evidence",
        ["hypothesis_id", "evidence_type", "source_id"],
    )

    # --- Add missing indexes ---

    op.create_index(
        "ix_hypothesis_evidence_source_id",
        "hypothesis_evidence",
        ["source_id"],
    )
    op.create_index(
        "ix_gene_expression_cancer_tumor",
        "gene_expression",
        ["cancer_type_id", "is_tumor"],
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index("ix_gene_expression_cancer_tumor", table_name="gene_expression")
    op.drop_index("ix_hypothesis_evidence_source_id", table_name="hypothesis_evidence")

    # Drop uniqueness constraints
    op.drop_constraint(
        "uq_hypothesis_evidence_hyp_type_source", "hypothesis_evidence", type_="unique"
    )
    op.drop_constraint("uq_trial_drugs_trial_drug", "trial_drugs", type_="unique")
    op.drop_constraint(
        "uq_literature_cancers_lit_cancer", "literature_cancers", type_="unique"
    )
    op.drop_constraint(
        "uq_literature_targets_lit_target", "literature_targets", type_="unique"
    )
    op.drop_constraint(
        "uq_literature_drugs_lit_drug", "literature_drugs", type_="unique"
    )
    op.drop_constraint(
        "uq_drug_targets_drug_target", "drug_targets", type_="unique"
    )
