from datetime import datetime

from pgvector.sqlalchemy import Vector
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
from sqlalchemy.orm import relationship

from app.database import Base


class Target(Base):
    __tablename__ = "targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uniprot_id = Column(String(20), unique=True, nullable=False, index=True)
    gene_symbol = Column(String(50), nullable=False, index=True)
    gene_name = Column(String(500))
    organism = Column(String(200), default="Homo sapiens")
    function_description = Column(Text)
    subcellular_location = Column(Text)
    protein_class = Column(String(200))
    ensembl_gene_id = Column(String(30), index=True)
    embedding = Column(Vector(384))
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    drug_targets = relationship("DrugTarget", back_populates="target", lazy="selectin")
    pathway_targets = relationship(
        "PathwayTarget", back_populates="target", lazy="selectin"
    )


class ProteinInteraction(Base):
    __tablename__ = "protein_interactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    protein_a_uniprot = Column(String(20), nullable=False, index=True)
    protein_b_uniprot = Column(String(20), nullable=False, index=True)
    interaction_score = Column(Float)
    experimental_score = Column(Float)
    database_score = Column(Float)
    textmining_score = Column(Float)
    source = Column(String(50), nullable=False)

    __table_args__ = (
        Index(
            "ix_protein_interactions_pair",
            "protein_a_uniprot",
            "protein_b_uniprot",
        ),
    )
