"""LLM-generated discovery proposals.

When the LLM reads batches of papers about a cancer type and reasons
about drug mechanisms, it proposes novel drug-cancer connections that
no structured database captures. These proposals are stored here and,
if they pass quality filters, feed into the hypothesis engine as
Strategy 7: llm_synthesis.
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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base


class LLMDiscoveryProposal(Base):
    __tablename__ = "llm_discovery_proposals"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # What the LLM proposed
    drug_id = Column(
        Integer,
        ForeignKey("drugs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cancer_type_id = Column(
        Integer,
        ForeignKey("cancer_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The LLM's reasoning
    mechanism_rationale = Column(Text, nullable=False)
    key_papers = Column(JSONB, default=list)       # PMIDs that support the connection
    inferred_pathways = Column(JSONB, default=list) # pathway names the LLM identified
    transitive_chain = Column(JSONB, default=list)  # the A→B→C reasoning chain
    confidence = Column(Float, nullable=False)       # LLM self-assessed 0-1
    novelty_reasoning = Column(Text)                 # why this is novel

    # Quality & status
    status = Column(
        String(20),
        nullable=False,
        default="proposed",
        index=True,
    )  # proposed → accepted → hypothesis_created | rejected
    hypothesis_id = Column(
        Integer,
        ForeignKey("hypotheses.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Provenance
    model_used = Column(String(100), nullable=False)
    input_tokens = Column(Integer)
    output_tokens = Column(Integer)
    batch_id = Column(String(50), index=True)  # groups proposals from one run

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    drug = relationship("Drug")
    cancer_type = relationship("CancerType")
    hypothesis = relationship("Hypothesis")

    __table_args__ = (
        UniqueConstraint(
            "drug_id",
            "cancer_type_id",
            "batch_id",
            name="uq_discovery_proposals_drug_cancer_batch",
        ),
        Index("ix_discovery_proposals_status", "status"),
        Index(
            "ix_discovery_proposals_confidence_desc",
            confidence.desc(),
        ),
    )
