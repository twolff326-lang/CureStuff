from datetime import datetime, date

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import relationship

from app.database import Base


class Literature(Base):
    __tablename__ = "literature"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pmid = Column(String(20), unique=True, nullable=False, index=True)
    title = Column(Text, nullable=False)
    abstract = Column(Text)
    authors = Column(JSONB, default=list)
    journal = Column(String(500))
    pub_date = Column(Date)
    doi = Column(String(200), index=True)
    mesh_terms = Column(JSONB, default=list)
    abstract_embedding = Column(Vector(384))
    relevance_tags = Column(JSONB, default=list)
    extracted_findings = Column(JSONB, nullable=True)
    analysis_status = Column(String(20), server_default="pending", nullable=False)
    search_vector = Column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('english', coalesce(title, '')), 'A') || "
            "setweight(to_tsvector('english', coalesce(abstract, '')), 'B')",
            persisted=True,
        ),
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    literature_drugs = relationship(
        "LiteratureDrug", back_populates="literature", lazy="selectin"
    )
    literature_targets = relationship(
        "LiteratureTarget", back_populates="literature", lazy="selectin"
    )
    literature_cancers = relationship(
        "LiteratureCancer", back_populates="literature", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_literature_authors", "authors", postgresql_using="gin"),
        Index("ix_literature_mesh_terms", "mesh_terms", postgresql_using="gin"),
        Index("ix_literature_relevance_tags", "relevance_tags", postgresql_using="gin"),
        Index("ix_literature_search_vector", "search_vector", postgresql_using="gin"),
    )


class LiteratureTarget(Base):
    __tablename__ = "literature_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    literature_id = Column(
        Integer, ForeignKey("literature.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id = Column(
        Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mention_type = Column(String(50), default="passing")

    literature = relationship("Literature", back_populates="literature_targets")
    target = relationship("Target")

    __table_args__ = (
        UniqueConstraint("literature_id", "target_id", name="uq_literature_targets_lit_target"),
        Index("ix_literature_targets_lit_target", "literature_id", "target_id"),
    )


class LiteratureCancer(Base):
    __tablename__ = "literature_cancers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    literature_id = Column(
        Integer, ForeignKey("literature.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cancer_type_id = Column(
        Integer, ForeignKey("cancer_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mention_type = Column(String(50), default="passing")

    literature = relationship("Literature", back_populates="literature_cancers")
    cancer_type = relationship("CancerType")

    __table_args__ = (
        UniqueConstraint("literature_id", "cancer_type_id", name="uq_literature_cancers_lit_cancer"),
        Index("ix_literature_cancers_lit_cancer", "literature_id", "cancer_type_id"),
    )
