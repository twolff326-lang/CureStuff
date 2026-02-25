from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Mutation(Base):
    __tablename__ = "mutations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cancer_type_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("cancer_types.id", ondelete="CASCADE"), index=True
    )
    gene_symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    mutation_type: Mapped[str | None] = mapped_column(String(50))  # missense, nonsense, amplification, deletion
    frequency: Mapped[float | None] = mapped_column(Float)  # 0.0-1.0
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    cancer_type: Mapped["CancerType"] = relationship("CancerType", back_populates="mutations")
