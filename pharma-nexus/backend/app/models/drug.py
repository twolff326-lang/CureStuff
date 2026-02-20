from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base


class Drug(Base):
    __tablename__ = "drugs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    drugbank_id = Column(String(20), unique=True, nullable=False, index=True)
    name = Column(String(500), nullable=False, index=True)
    generic_name = Column(String(500))
    description = Column(Text)
    mechanism_of_action = Column(Text)
    pharmacodynamics = Column(Text)
    indication = Column(Text)
    status = Column(String(50), nullable=False, default="approved", index=True)
    molecular_formula = Column(String(200))
    smiles = Column(Text)
    inchi_key = Column(String(100), index=True)
    cas_number = Column(String(50))
    categories = Column(JSONB, default=dict)
    mechanism_embedding = Column(Vector(384))
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    drug_targets = relationship("DrugTarget", back_populates="drug", lazy="selectin")
    hypotheses = relationship("Hypothesis", back_populates="drug", lazy="selectin")

    __table_args__ = (
        Index("ix_drugs_categories", "categories", postgresql_using="gin"),
    )


class DrugTarget(Base):
    __tablename__ = "drug_targets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    drug_id = Column(Integer, ForeignKey("drugs.id", ondelete="CASCADE"), nullable=False, index=True)
    target_id = Column(Integer, ForeignKey("targets.id", ondelete="CASCADE"), nullable=False, index=True)
    action_type = Column(String(100))
    known_action = Column(Boolean, default=False)
    binding_affinity_nm = Column(Float)
    source = Column(String(50), nullable=False)
    references = Column(JSONB, default=list)

    # Relationships
    drug = relationship("Drug", back_populates="drug_targets")
    target = relationship("Target", back_populates="drug_targets")

    __table_args__ = (
        UniqueConstraint("drug_id", "target_id", name="uq_drug_targets_drug_target"),
        Index("ix_drug_targets_drug_target", "drug_id", "target_id"),
        Index("ix_drug_targets_references", "references", postgresql_using="gin"),
    )


class LiteratureDrug(Base):
    __tablename__ = "literature_drugs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    literature_id = Column(
        Integer, ForeignKey("literature.id", ondelete="CASCADE"), nullable=False, index=True
    )
    drug_id = Column(Integer, ForeignKey("drugs.id", ondelete="CASCADE"), nullable=False, index=True)
    mention_type = Column(String(50), default="passing")

    literature = relationship("Literature", back_populates="literature_drugs")
    drug = relationship("Drug")

    __table_args__ = (
        UniqueConstraint("literature_id", "drug_id", name="uq_literature_drugs_lit_drug"),
        Index("ix_literature_drugs_lit_drug", "literature_id", "drug_id"),
    )


class TrialDrug(Base):
    __tablename__ = "trial_drugs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trial_id = Column(
        Integer, ForeignKey("clinical_trials.id", ondelete="CASCADE"), nullable=False, index=True
    )
    drug_id = Column(Integer, ForeignKey("drugs.id", ondelete="CASCADE"), nullable=False, index=True)

    trial = relationship("ClinicalTrial", back_populates="trial_drugs")
    drug = relationship("Drug")

    __table_args__ = (
        UniqueConstraint("trial_id", "drug_id", name="uq_trial_drugs_trial_drug"),
        Index("ix_trial_drugs_trial_drug", "trial_id", "drug_id"),
    )
