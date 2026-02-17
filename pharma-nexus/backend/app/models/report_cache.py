"""Report cache model for tracking generated reports.

Reports are expensive to generate (multiple DB queries + PDF rendering).
This table caches metadata about generated reports so we can serve them
without regenerating, and invalidate when underlying data changes.
"""

from sqlalchemy import Column, DateTime, Index, Integer, String, func

from app.database import Base


class ReportCache(Base):
    """Track generated reports for caching and retrieval."""

    __tablename__ = "report_cache"

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_type = Column(
        String(50), nullable=False
    )  # hypothesis, cancer_summary, novel_discoveries, executive_summary
    entity_id = Column(Integer, nullable=True)  # hypothesis_id or cancer_type_id
    file_path = Column(String(500), nullable=False)
    file_size_bytes = Column(Integer)
    generated_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_report_cache_lookup", "report_type", "entity_id"),
    )
