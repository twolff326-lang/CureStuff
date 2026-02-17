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
from sqlalchemy.orm import relationship

from app.database import Base


class HypothesisAnalysis(Base):
    """Stores LLM-generated analyses for hypotheses.

    Each hypothesis can have multiple analysis types:
      - narrative: mechanistic narrative explaining the biology
      - critique: devil's advocate critique identifying weaknesses
      - comparative: comparative analysis against similar hypotheses
      - literature_synthesis: synthesis of supporting literature
      - experiment_design: suggested experiments to validate
      - confidence: confidence assessment with precedent analysis
    """

    __tablename__ = "hypothesis_analyses"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hypothesis_id = Column(
        Integer,
        ForeignKey("hypotheses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    analysis_type = Column(String(50), nullable=False, index=True)
    model_used = Column(String(100), nullable=False)
    content = Column(JSONB, nullable=False)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    generation_time_seconds = Column(Float)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    hypothesis = relationship("Hypothesis")

    __table_args__ = (
        Index(
            "ix_hypothesis_analyses_hyp_type",
            "hypothesis_id",
            "analysis_type",
            unique=True,
        ),
    )


class LLMUsageLog(Base):
    """Tracks Claude API usage for cost monitoring and rate limiting."""

    __tablename__ = "llm_usage_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hypothesis_id = Column(
        Integer,
        ForeignKey("hypotheses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    analysis_type = Column(String(50), nullable=False)
    model = Column(String(100), nullable=False)
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    estimated_cost_usd = Column(Float, default=0.0)
    error = Column(Text)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_llm_usage_logs_created_at", "created_at"),
        Index("ix_llm_usage_logs_model", "model"),
    )
