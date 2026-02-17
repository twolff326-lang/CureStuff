from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import relationship

from app.database import Base


class TargetDiseaseAssociation(Base):
    __tablename__ = "target_disease_associations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    target_id = Column(
        Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    disease_id = Column(String(50), nullable=False, index=True)
    disease_name = Column(String(500))
    overall_score = Column(Float)
    genetic_association_score = Column(Float)
    somatic_mutation_score = Column(Float)
    known_drug_score = Column(Float)
    literature_score = Column(Float)
    rna_expression_score = Column(Float)
    animal_model_score = Column(Float)
    source = Column(String(50), default="opentargets", nullable=False)
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    # Relationships
    target = relationship("Target")

    __table_args__ = (
        Index("ix_target_disease_assoc_target_disease", "target_id", "disease_id"),
    )
