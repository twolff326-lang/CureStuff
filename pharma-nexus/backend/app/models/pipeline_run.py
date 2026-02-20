from sqlalchemy import Column, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class PipelineRun(Base):
    """Tracks a full repurposing pipeline execution across multiple phases."""

    __tablename__ = "pipeline_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    status = Column(String(50), nullable=False, default="running", index=True)
    current_phase = Column(String(100), nullable=True)
    phases = Column(JSONB, nullable=False, default=list)
    config = Column(JSONB, nullable=False, default=dict)
    started_at = Column(DateTime, server_default=func.now(), nullable=False)
    completed_at = Column(DateTime, nullable=True)
