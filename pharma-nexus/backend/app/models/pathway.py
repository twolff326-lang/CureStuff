from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Pathway(Base):
    __tablename__ = "pathways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source_id: Mapped[str | None] = mapped_column(String(50))  # Reactome/KEGG ID
    source: Mapped[str] = mapped_column(String(50), default="reactome")
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    targets: Mapped[list["PathwayTarget"]] = relationship(
        "PathwayTarget", back_populates="pathway"
    )
