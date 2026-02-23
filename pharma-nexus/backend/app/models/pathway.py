from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
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


class Pathway(Base):
    __tablename__ = "pathways"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(50), nullable=False)
    external_id = Column(String(100), nullable=False, index=True)
    name = Column(String(500), nullable=False, index=True)
    description = Column(Text)
    category = Column(String(200))
    genes = Column(JSONB, default=list)
    parent_pathway_id = Column(
        Integer, ForeignKey("pathways.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    parent_pathway = relationship("Pathway", remote_side="Pathway.id", lazy="selectin")
    pathway_targets = relationship(
        "PathwayTarget", back_populates="pathway", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_pathways_source_external", "source", "external_id", unique=True),
        Index("ix_pathways_genes", "genes", postgresql_using="gin"),
    )


class PathwayTarget(Base):
    __tablename__ = "pathway_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pathway_id = Column(
        Integer, ForeignKey("pathways.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id = Column(
        Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role = Column(String(50), default="component")

    # Relationships
    pathway = relationship("Pathway", back_populates="pathway_targets")
    target = relationship("Target", back_populates="pathway_targets")

    __table_args__ = (
        Index("ix_pathway_targets_pathway_target", "pathway_id", "target_id", unique=True),
    )
