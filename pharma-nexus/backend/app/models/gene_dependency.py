"""Gene dependency model — CRISPR/RNAi screen data from DepMap.

Stores gene-level dependency scores for cancer cell lines, indicating
which genes are essential for cancer cell survival. Used by the Causal
Dependency scoring dimension to distinguish driver genes (functionally
required) from passenger genes (mutated but dispensable).

Source: DepMap (https://depmap.org/portal/)
  - CRISPR (Chronos): genome-wide loss-of-function screens
  - Gene effect scores: negative = essential, ~0 = dispensable
  - Common essentials: median effect < -0.5 across most lines
"""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class GeneDependency(Base):
    """Per-gene dependency score aggregated across cell lines of a cancer lineage.

    Each row represents how essential a gene is for survival of cancer cells
    of a given lineage (e.g., breast, lung, colon). A strongly negative
    gene_effect means the cancer cells die when the gene is knocked out.
    """
    __tablename__ = "gene_dependencies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gene_symbol = Column(String(50), nullable=False, index=True)
    depmap_id = Column(String(50), comment="DepMap gene ID (e.g., BRAF (673))")
    lineage = Column(String(100), nullable=False, index=True,
                     comment="Cancer lineage from DepMap (e.g., breast, lung)")
    gene_effect = Column(Float, nullable=False,
                         comment="Chronos gene effect (median across lineage cell lines). Negative = essential.")
    num_cell_lines = Column(Integer,
                            comment="Number of cell lines in this lineage used to compute the score")
    dependency_probability = Column(Float,
                                    comment="Probability that gene is a dependency (0-1)")
    is_common_essential = Column(Integer, default=0,
                                 comment="1 if gene is commonly essential across all lineages")
    is_strongly_selective = Column(Integer, default=0,
                                   comment="1 if gene is selectively essential in this lineage but not others")
    selectivity_score = Column(Float,
                               comment="How selective the dependency is to this lineage vs others")
    source = Column(String(50), nullable=False, default="depmap")
    dataset_version = Column(String(50), comment="DepMap release (e.g., 24Q4)")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_gene_dep_gene_lineage", "gene_symbol", "lineage", unique=True),
        Index("ix_gene_dep_effect", "gene_effect"),
    )


class CombinationHypothesis(Base):
    """Drug combination hypothesis — predicts synergistic drug pairs for a cancer type.

    Built on top of single-drug hypotheses: if Drug A and Drug B each have
    repurposing evidence for a cancer, this table stores the predicted synergy
    of using them together, based on pathway complementarity, synthetic
    lethality patterns, and non-overlapping toxicity.
    """
    __tablename__ = "combination_hypotheses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    drug_a_id = Column(Integer, nullable=False, index=True)
    drug_b_id = Column(Integer, nullable=False, index=True)
    cancer_type_id = Column(Integer, nullable=False, index=True)

    # Link back to the individual hypotheses
    hypothesis_a_id = Column(Integer, comment="Hypothesis ID for drug A + cancer")
    hypothesis_b_id = Column(Integer, comment="Hypothesis ID for drug B + cancer")

    # Synergy scoring (each 0-100)
    pathway_complementarity_score = Column(Float, default=0.0,
                                           comment="Do the drugs hit different/complementary pathways?")
    target_non_overlap_score = Column(Float, default=0.0,
                                      comment="How distinct are their molecular targets?")
    synthetic_lethality_score = Column(Float, default=0.0,
                                       comment="Do their targets form a synthetic lethal pair?")
    safety_compatibility_score = Column(Float, default=0.0,
                                        comment="Non-overlapping toxicity profiles")
    clinical_precedent_score = Column(Float, default=0.0,
                                      comment="Existing combination trial evidence")

    # Composite
    synergy_score = Column(Float, nullable=False, default=0.0,
                           comment="Weighted composite synergy prediction (0-100)")
    synergy_classification = Column(String(30), default="unknown",
                                    comment="synergistic, additive, antagonistic, unknown")

    # Context
    rationale = Column(Text, comment="Human-readable explanation of predicted synergy")
    shared_pathways = Column(JSONB, default=list,
                             comment="Pathways where both drugs have activity")
    complementary_pathways = Column(JSONB, default=list,
                                    comment="Pathways uniquely covered by each drug")
    details = Column(JSONB, default=dict, comment="Full scoring breakdown")

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_combo_hyp_drugs_cancer", "drug_a_id", "drug_b_id", "cancer_type_id", unique=True),
        Index("ix_combo_hyp_synergy_desc", synergy_score.desc()),
    )
