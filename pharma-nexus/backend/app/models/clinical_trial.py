from datetime import date

from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base


class ClinicalTrial(Base):
    __tablename__ = "clinical_trials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    nct_id = Column(String(20), unique=True, nullable=False, index=True)
    title = Column(Text, nullable=False)
    status = Column(String(50), index=True)
    phase = Column(String(20))
    cancer_type_id = Column(
        Integer, ForeignKey("cancer_types.id", ondelete="SET NULL"), nullable=True, index=True
    )
    conditions = Column(JSONB, default=list)
    interventions = Column(JSONB, default=list)
    enrollment = Column(Integer)
    start_date = Column(Date)
    completion_date = Column(Date)
    results_summary = Column(Text)
    source_url = Column(Text)

    # Relationships
    cancer_type = relationship("CancerType")
    trial_drugs = relationship(
        "TrialDrug", back_populates="trial", lazy="selectin"
    )

    __table_args__ = (
        Index("ix_clinical_trials_conditions", "conditions", postgresql_using="gin"),
        Index(
            "ix_clinical_trials_interventions", "interventions", postgresql_using="gin"
        ),
    )
