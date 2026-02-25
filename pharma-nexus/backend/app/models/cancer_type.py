from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CancerType(Base):
    __tablename__ = "cancer_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    tcga_code: Mapped[str | None] = mapped_column(String(20), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    tissue: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    mutations: Mapped[list["Mutation"]] = relationship(
        "Mutation", back_populates="cancer_type", lazy="selectin"
    )
    hypotheses: Mapped[list["Hypothesis"]] = relationship(
        "Hypothesis", back_populates="cancer_type", lazy="selectin"
    )
