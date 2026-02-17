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
    status = Column(String(20), nullable=False, default="generated", index=True)
    reviewer_notes = Column(Text)
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
