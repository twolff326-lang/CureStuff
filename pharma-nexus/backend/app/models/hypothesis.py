from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base


class Hypothesis(Base):
    __tablename__ = "hypotheses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    drug_id = Column(Integer, ForeignKey("drugs.id", ondelete="CASCADE"), nullable=False, index=True)
    cancer_type_id = Column(
        Integer, ForeignKey("cancer_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title = Column(String(1000), nullable=False)
    summary = Column(Text)
    mechanism_narrative = Column(Text)
    composite_score = Column(Float, nullable=False, default=0.0)
    evidence_strength = Column(String(20), default="speculative")
    pathway_overlap_score = Column(Float)
    expression_correlation_score = Column(Float)
    literature_support_score = Column(Float)
    clinical_evidence_score = Column(Float)
    safety_score = Column(Float)
    novelty_score = Column(Float)
    causal_dependency_score = Column(Float, comment="DepMap CRISPR gene dependency score")
    gnn_link_score = Column(Float, comment="GNN-predicted link probability score (0-100)")
    mutation_context_score = Column(Float, default=0.0, comment="Mutation-conditional vulnerability score")
    polypharmacology_score = Column(Float, default=0.0, comment="Off-target bioassay activity score")
    pharmacological_response_score = Column(Float, default=0.0, comment="PRISM/GDSC drug sensitivity screen score")
    p_value = Column(Float, comment="Permutation-based p-value against null distribution")
    fdr_adjusted_p_value = Column(Float, comment="BH FDR-corrected p-value")
    confidence_interval = Column(JSONB, comment="Bootstrap 95% CI for composite score")
    scoring_method_version = Column(String(50), default="v2_statistical")
    status = Column(String(20), nullable=False, default="generated", index=True)
    critique = Column(JSONB)
    reviewer_notes = Column(Text)

    # Batch tracking — groups hypotheses from the same generation run
    # for reproducibility and cohort-level analysis.
    batch_id = Column(
        String(36), nullable=True, index=True,
        comment="UUID grouping hypotheses from the same generation batch",
    )
    generation_pipeline_run_id = Column(
        Integer, ForeignKey("pipeline_runs.id", ondelete="SET NULL"),
        nullable=True,
        comment="Pipeline run that generated this hypothesis",
    )

    # Per-dimension confidence intervals (bootstrap 95% CI per score)
    dimension_confidence_intervals = Column(
        JSONB, nullable=True,
        comment="Per-dimension bootstrap 95% CIs, e.g. {pathway_overlap: {lower: 40, upper: 65}}",
    )

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    drug = relationship("Drug", back_populates="hypotheses")
    cancer_type = relationship("CancerType", back_populates="hypotheses")
    evidence = relationship(
        "HypothesisEvidence", back_populates="hypothesis", lazy="selectin"
    )

    __table_args__ = (
        CheckConstraint(
            "composite_score >= 0 AND composite_score <= 100",
            name="ck_hypotheses_composite_score",
        ),
        Index("ix_hypotheses_composite_score_desc", composite_score.desc()),
        Index("ix_hypotheses_drug_cancer", "drug_id", "cancer_type_id"),
        Index("ix_hypotheses_batch_id", "batch_id"),
    )


class HypothesisEvidence(Base):
    __tablename__ = "hypothesis_evidence"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hypothesis_id = Column(
        Integer, ForeignKey("hypotheses.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_type = Column(String(50), nullable=False)
    source_type = Column(String(50))
    source_id = Column(String(100))
    description = Column(Text)
    strength = Column(String(20), default="weak")
    confidence = Column(Float, default=0.0)
    raw_data = Column(JSONB, default=dict)

    # Relationships
    hypothesis = relationship("Hypothesis", back_populates="evidence")

    __table_args__ = (
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_hypothesis_evidence_confidence",
        ),
        Index(
            "ix_hypothesis_evidence_raw_data", "raw_data", postgresql_using="gin"
        ),
    )


class ValidationResult(Base):
    """Stores results of validation runs for tracking scoring performance over time."""
    __tablename__ = "validation_results"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_date = Column(DateTime, server_default=func.now(), nullable=False)
    validation_type = Column(String(50), nullable=False)
    scoring_method_version = Column(String(50), nullable=False)
    n_hypotheses = Column(Integer, nullable=False)
    n_ground_truth_matched = Column(Integer)
    roc_auc = Column(Float)
    pr_auc = Column(Float)
    mean_rank_percentile = Column(Float)
    brier_score = Column(Float)
    results = Column(JSONB, nullable=False)
    weights_used = Column(JSONB)

    __table_args__ = (
        Index("ix_validation_results_type_date", "validation_type", "run_date"),
    )
