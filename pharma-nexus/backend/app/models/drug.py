from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Drug(Base):
    __tablename__ = "drugs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    generic_name: Mapped[str | None] = mapped_column(String(255))
    drugbank_id: Mapped[str | None] = mapped_column(String(20), unique=True)
    pubchem_cid: Mapped[int | None] = mapped_column(Integer, unique=True)
    chembl_id: Mapped[str | None] = mapped_column(String(30), unique=True)
    smiles: Mapped[str | None] = mapped_column(Text)
    molecular_formula: Mapped[str | None] = mapped_column(String(255))
    molecular_weight: Mapped[float | None] = mapped_column(Float)
    mechanism_of_action: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="approved", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    targets: Mapped[list["DrugTarget"]] = relationship(
        "DrugTarget", back_populates="drug", lazy="selectin"
    )
    hypotheses: Mapped[list["Hypothesis"]] = relationship(
        "Hypothesis", back_populates="drug", lazy="selectin"
    )
