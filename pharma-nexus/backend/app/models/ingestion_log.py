from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class IngestionLog(Base):
    __tablename__ = "ingestion_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(100), nullable=False, index=True)
    task_type = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False, default="started", index=True)
    records_processed = Column(Integer, default=0)
    total_expected = Column(Integer, nullable=True)
    errors = Column(JSONB, default=list)
    started_at = Column(DateTime, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime)

    __table_args__ = (
        Index("ix_ingestion_logs_errors", "errors", postgresql_using="gin"),
    )
