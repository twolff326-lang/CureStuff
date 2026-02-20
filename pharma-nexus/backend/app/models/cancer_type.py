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
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class CancerType(Base):
    __tablename__ = "cancer_types"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tcga_code = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(300), nullable=False, index=True)
    tissue = Column(String(200))
    organ = Column(String(200))
    subtype = Column(String(200))
    sample_count = Column(Integer)
    description = Column(Text)

    # Relationships
    molecular_profiles = relationship(
        "CancerMolecularProfile", back_populates="cancer_type", lazy="selectin"
    )
    mutations = relationship("Mutation", back_populates="cancer_type", lazy="selectin")
    hypotheses = relationship(
        "Hypothesis", back_populates="cancer_type", lazy="selectin"
    )


class CancerMolecularProfile(Base):
    __tablename__ = "cancer_molecular_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cancer_type_id = Column(
        Integer, ForeignKey("cancer_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gene_symbol = Column(String(50), nullable=False, index=True)
    alteration_type = Column(String(50), nullable=False)
    frequency_percent = Column(Float)
    median_expression = Column(Float)
    expression_zscore = Column(Float)
    source = Column(String(50), nullable=False)

    # Relationships
    cancer_type = relationship("CancerType", back_populates="molecular_profiles")

    __table_args__ = (
        UniqueConstraint(
            "cancer_type_id", "gene_symbol", "alteration_type",
            name="uq_cancer_molecular_profiles_cancer_gene_alt",
        ),
        CheckConstraint(
            "frequency_percent IS NULL OR (frequency_percent >= 0 AND frequency_percent <= 100)",
            name="ck_cancer_molecular_profiles_frequency",
        ),
        Index(
            "ix_cancer_molecular_profiles_cancer_gene",
            "cancer_type_id",
            "gene_symbol",
        ),
    )
