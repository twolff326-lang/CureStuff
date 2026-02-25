from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gene_symbol: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    uniprot_id: Mapped[str | None] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    drug_targets: Mapped[list["DrugTarget"]] = relationship(
        "DrugTarget", back_populates="target", lazy="selectin"
    )
