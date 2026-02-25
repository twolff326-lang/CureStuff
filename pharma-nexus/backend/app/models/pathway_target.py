from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PathwayTarget(Base):
    __tablename__ = "pathway_targets"
    __table_args__ = (
        UniqueConstraint("pathway_id", "gene_symbol", name="uq_pathway_gene"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pathway_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pathways.id", ondelete="CASCADE"), index=True
    )
    gene_symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    role: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    pathway: Mapped["Pathway"] = relationship("Pathway", back_populates="targets")
