from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Hypothesis(Base):
    __tablename__ = "hypotheses"
    __table_args__ = (
        UniqueConstraint("drug_id", "cancer_type_id", name="uq_hypothesis_drug_cancer"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    drug_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("drugs.id", ondelete="CASCADE"), index=True
    )
    cancer_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cancer_types.id", ondelete="CASCADE"), index=True
    )
    composite_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    target_binding_score: Mapped[float] = mapped_column(Float, default=0.0)
    pathway_overlap_score: Mapped[float] = mapped_column(Float, default=0.0)
    clinical_evidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    evidence_summary: Mapped[str | None] = mapped_column(Text)
    strategy: Mapped[str | None] = mapped_column(String(50))  # direct_target, pathway, literature
    status: Mapped[str] = mapped_column(String(30), default="generated", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    drug: Mapped["Drug"] = relationship("Drug", back_populates="hypotheses")
    cancer_type: Mapped["CancerType"] = relationship("CancerType", back_populates="hypotheses")
