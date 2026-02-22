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


class ScoringWeight(Base):
    """Stores configurable scoring weights for hypothesis composite scoring.

    Each row represents a named scoring preset (e.g. 'balanced', 'novelty_focused').
    The weights JSONB column holds the 6 dimension weights that must sum to 1.0.
    """

    __tablename__ = "scoring_weights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    description = Column(Text)
    weights = Column(JSONB, nullable=False)
    # weights schema: {
    #   "pathway_overlap": 0.20,
    #   "expression_correlation": 0.20,
    #   "literature_support": 0.20,
    #   "clinical_evidence": 0.15,
    #   "safety": 0.10,
    #   "novelty": 0.15,
    # }
    is_default = Column(Integer, default=0, nullable=False,
                        comment="Integer flag: 1 = active default preset, 0 = inactive")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_scoring_weights_is_default", "is_default"),
    )
