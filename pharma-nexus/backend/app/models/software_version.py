"""Software version registry for publication reproducibility.

Captures the exact package versions used during each pipeline run
so that results can be traced back to specific software states.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class SoftwareVersion(Base):
    """Snapshot of key package versions captured at pipeline execution time."""

    __tablename__ = "software_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id = Column(
        Integer,
        ForeignKey("pipeline_runs.id", ondelete="CASCADE"),
        nullable=True,
        comment="Pipeline run this snapshot belongs to (NULL for manual captures)",
    )
    snapshot_label = Column(
        String(200), nullable=False,
        comment="Human label, e.g. 'pipeline_run_42' or 'manual_2025-02-22'",
    )
    python_version = Column(String(50), nullable=False)
    packages = Column(
        JSONB, nullable=False,
        comment="Dict of package_name -> version for all key dependencies",
    )
    platform_info = Column(
        JSONB, nullable=True,
        comment="OS, architecture, CPU info",
    )
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_software_versions_run", "pipeline_run_id"),
        Index("ix_software_versions_created", "created_at"),
    )
