from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class ExpressionScoreCache(Base):
    __tablename__ = "expression_score_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_type = Column(String(50), nullable=False)  # drug_expression | pathway_activity | differential_expression
    entity_id_1 = Column(Integer, nullable=False)  # drug_id or pathway_id or cancer_type_id
    entity_id_2 = Column(Integer, nullable=False)  # cancer_type_id
    score = Column(Float)
    details = Column(JSONB)
    computed_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index(
            "ix_cache_lookup",
            "cache_type",
            "entity_id_1",
            "entity_id_2",
            unique=True,
        ),
    )
