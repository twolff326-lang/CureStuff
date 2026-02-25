from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class DrugTarget(Base):
    __tablename__ = "drug_targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    drug_id: Mapped[int] = mapped_column(Integer, ForeignKey("drugs.id", ondelete="CASCADE"), index=True)
    target_id: Mapped[int] = mapped_column(Integer, ForeignKey("targets.id", ondelete="CASCADE"), index=True)
    action_type: Mapped[str | None] = mapped_column(String(50))
    binding_affinity: Mapped[float | None] = mapped_column(Float)
    affinity_type: Mapped[str | None] = mapped_column(String(20))  # IC50, Ki, Kd, EC50
    affinity_units: Mapped[str | None] = mapped_column(String(20))  # nM, uM
    source: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    drug: Mapped["Drug"] = relationship("Drug", back_populates="targets")
    target: Mapped["Target"] = relationship("Target", back_populates="drug_targets")
