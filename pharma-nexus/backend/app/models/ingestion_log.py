from datetime import datetime

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


class IngestionLog(Base):
    __tablename__ = "ingestion_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(100), nullable=False, index=True)
    task_type = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False, default="started", index=True)
    records_processed = Column(Integer, default=0)
    total_expected = Column(Integer, nullable=True)
    errors = Column(JSONB, default=list)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at = Column(DateTime(timezone=True))

    # Data source versioning — tracks exactly which version of external data
    # was used so results are reproducible for publication.
    data_source_version = Column(
        String(200), nullable=True,
        comment="External data source version (e.g. ChEMBL v33, PubChem 2025-02-20)",
    )
    data_downloaded_at = Column(
        DateTime(timezone=True), nullable=True,
        comment="Timestamp when the external data was actually retrieved",
    )
    api_url = Column(
        String(500), nullable=True,
        comment="Exact API URL or endpoint used for this ingestion",
    )
    records_filtered = Column(
        Integer, nullable=True,
        comment="Number of records excluded by quality/relevance filters",
    )
    checksum = Column(
        String(128), nullable=True,
        comment="SHA-256 hash of ingested data for integrity verification",
    )
    duration_seconds = Column(
        Float, nullable=True,
        comment="Wall-clock ingestion duration in seconds",
    )
    checkpoint = Column(
        JSONB, nullable=True,
        comment="Resume checkpoint for long-running connectors (phase, offset, etc.)",
    )
    report = Column(
        JSONB, nullable=True,
        comment="Comprehensive execution report: phases, HTTP stats, data quality, timeline, warnings",
    )

    __table_args__ = (
        Index("ix_ingestion_logs_errors", "errors", postgresql_using="gin"),
    )
