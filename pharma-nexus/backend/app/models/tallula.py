"""Tallula Algorithm discovery results model.

Stores the output of each Tallula Algorithm run — the classified
discoveries, their metrics, and resonance decomposition data.
"""

from sqlalchemy import (
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

from app.database import Base


class TallulaRun(Base):
    """Metadata for a single Tallula Algorithm execution."""
    __tablename__ = "tallula_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    n_lenses = Column(Integer, nullable=False)
    dropout_rate = Column(Float, nullable=False)
    dirichlet_alpha = Column(Float, nullable=False)
    seed = Column(Integer)
    n_hypotheses_input = Column(Integer, nullable=False)
    cancer_type_id = Column(
        Integer, ForeignKey("cancer_types.id", ondelete="SET NULL"),
        nullable=True,
        comment="If set, this run was scoped to a single cancer type",
    )

    # Summary counts
    n_resonant = Column(Integer, nullable=False, default=0)
    n_robust = Column(Integer, nullable=False, default=0)
    n_fragile = Column(Integer, nullable=False, default=0)
    n_moderate = Column(Integer, nullable=False, default=0)
    n_weak = Column(Integer, nullable=False, default=0)

    # Full parameter and summary JSON
    parameters = Column(JSONB, nullable=False, default=dict)
    summary = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_tallula_runs_created", "created_at"),
    )


class TallulaDiscovery(Base):
    """A single discovery produced by the Tallula Algorithm.

    Each row represents one hypothesis analyzed through the stochastic
    lens ensemble, classified as robust/resonant/fragile/moderate/weak.
    """
    __tablename__ = "tallula_discoveries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        Integer, ForeignKey("tallula_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    hypothesis_id = Column(
        Integer, ForeignKey("hypotheses.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    drug_id = Column(Integer, nullable=False, index=True)
    cancer_type_id = Column(Integer, nullable=False, index=True)

    # Classification
    discovery_class = Column(
        String(20), nullable=False, index=True,
        comment="robust, resonant, fragile, moderate, weak",
    )
    deterministic_score = Column(
        Float, nullable=False,
        comment="Original composite score from standard scoring",
    )

    # Tallula metrics
    ubiquity = Column(Float, nullable=False, comment="Fraction of lenses where this scored well (0-1)")
    resonance = Column(Float, nullable=False, comment="Max/median score ratio across lenses")
    fragility_index = Column(Float, nullable=False, comment="Max fractional score drop from ablation")
    critical_dimension = Column(String(50), comment="Dimension whose removal hurts most")

    # Score distribution across lenses
    score_mean = Column(Float)
    score_median = Column(Float)
    score_std = Column(Float)
    score_max = Column(Float)
    score_min = Column(Float)

    # Resonance decomposition (the WHY)
    resonance_profile = Column(JSONB, comment="Activation dimensions and narrative")
    ablation_impacts = Column(JSONB, comment="Per-dimension ablation impact")

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_tallula_disc_class_score", "discovery_class", score_max.desc()),
        Index("ix_tallula_disc_resonance", resonance.desc()),
        Index("ix_tallula_disc_run_class", "run_id", "discovery_class"),
    )
