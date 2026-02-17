from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.database import Base


class Mutation(Base):
    __tablename__ = "mutations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cancer_type_id = Column(
        Integer, ForeignKey("cancer_types.id", ondelete="CASCADE"), nullable=False, index=True
    )
    gene_symbol = Column(String(50), nullable=False, index=True)
    mutation_type = Column(String(50), nullable=False)
    protein_change = Column(String(100))
    genomic_position = Column(String(100))
    frequency_percent = Column(Float)
    functional_impact = Column(String(20), default="unknown")
    cosmic_id = Column(String(50), index=True)
    source = Column(String(50), nullable=False)

    # Relationships
    cancer_type = relationship("CancerType", back_populates="mutations")

    __table_args__ = (
        CheckConstraint(
            "frequency_percent IS NULL OR (frequency_percent >= 0 AND frequency_percent <= 100)",
            name="ck_mutations_frequency",
        ),
        Index("ix_mutations_cancer_gene", "cancer_type_id", "gene_symbol"),
    )
