"""Models for hypothesis validation and outcome tracking.

ValidationCase: Known drug repurposing successes/failures used as ground truth.
HypothesisOutcome: User-recorded outcomes for generated hypotheses.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class ValidationCase(Base):
    """Known drug repurposing case for benchmarking the scoring system.

    Each row represents a historically documented case where a drug was
    successfully (or unsuccessfully) repurposed for a cancer indication.
    These cases serve as ground truth to calibrate scoring weights and
    measure system precision.
    """

    __tablename__ = "validation_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    drug_name: Mapped[str] = mapped_column(String(500), nullable=False)
    drug_drugbank_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cancer_name: Mapped[str] = mapped_column(String(300), nullable=False)
    cancer_tcga_code: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Outcome
    outcome: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # "success", "failure", "partial", "ongoing"
    outcome_detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Approval/evidence info
    fda_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_trial_phase: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reference_pmids: Mapped[list | None] = mapped_column(JSONB, default=list)
    reference_nct_ids: Mapped[list | None] = mapped_column(JSONB, default=list)

    # Linked IDs (populated when matched to database records)
    matched_drug_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("drugs.id", ondelete="SET NULL"), nullable=True
    )
    matched_cancer_type_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("cancer_types.id", ondelete="SET NULL"), nullable=True
    )
    matched_hypothesis_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("hypotheses.id", ondelete="SET NULL"), nullable=True
    )

    # Scoring snapshot (populated during benchmark runs)
    predicted_composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_strength: Mapped[str | None] = mapped_column(String(20), nullable=True)
    dimension_scores_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    source: Mapped[str] = mapped_column(
        String(100), nullable=False, default="curated"
    )  # "curated", "literature", "fda"

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_validation_cases_outcome", "outcome"),
        Index("ix_validation_cases_drug_cancer", "drug_drugbank_id", "cancer_tcga_code"),
    )


class HypothesisOutcome(Base):
    """User-recorded outcome for a generated hypothesis.

    Allows researchers to mark hypotheses as validated, invalidated,
    or under investigation — creating a feedback loop for weight calibration.
    """

    __tablename__ = "hypothesis_outcomes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    hypothesis_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("hypotheses.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # Outcome tracking
    outcome: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # "validated", "invalidated", "promising", "inconclusive"
    outcome_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Score snapshot at time of review
    composite_score_at_review: Mapped[float | None] = mapped_column(Float, nullable=True)
    dimension_scores_at_review: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Reviewer info
    reviewer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Evidence supporting the outcome
    supporting_evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    hypothesis = relationship("Hypothesis", backref="outcome_record")

    __table_args__ = (
        Index("ix_hypothesis_outcomes_outcome", "outcome"),
    )
