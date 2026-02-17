from sqlalchemy import (
    Boolean,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Bioassay(Base):
    __tablename__ = "bioassays"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pubchem_aid = Column(String(50), index=True)
    target_id = Column(Integer, ForeignKey("targets.id", ondelete="SET NULL"), index=True)
    drug_id = Column(Integer, ForeignKey("drugs.id", ondelete="SET NULL"), index=True)
    activity_type = Column(String(20))
    activity_value = Column(Float)
    activity_unit = Column(String(20))
    activity_outcome = Column(String(20))
    source = Column(String(50), nullable=False)

    target = relationship("Target")
    drug = relationship("Drug")

    __table_args__ = (
        Index("ix_bioassays_drug_target", "drug_id", "target_id"),
    )


class GeneExpression(Base):
    __tablename__ = "gene_expression"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cancer_type_id = Column(
        Integer, ForeignKey("cancer_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gene_symbol = Column(String(50), nullable=False, index=True)
    sample_id = Column(String(100), nullable=False)
    expression_value = Column(Float)
    expression_log2 = Column(Float)
    is_tumor = Column(Boolean, default=True)
    source = Column(String(50), nullable=False)

    cancer_type = relationship("CancerType")

    __table_args__ = (
        Index("ix_gene_expression_cancer_gene", "cancer_type_id", "gene_symbol"),
        Index("ix_gene_expression_cancer_tumor", "cancer_type_id", "is_tumor"),
    )
