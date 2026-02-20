"""Literature monitoring and alert models.

Supports real-time literature monitoring with score change tracking
and automated alerts when new evidence shifts hypothesis scores.
"""

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class ScoreHistory(Base):
    """Tracks hypothesis score changes over time.

    Each row is a snapshot: when a hypothesis score changes by any amount
    (from re-scoring, new evidence, etc.), a record is created with both
    the old and new score and the trigger that caused the change.
    """
    __tablename__ = "score_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hypothesis_id = Column(
        Integer, ForeignKey("hypotheses.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    old_composite_score = Column(Float, nullable=False)
    new_composite_score = Column(Float, nullable=False)
    score_delta = Column(Float, nullable=False, comment="new - old")
    trigger = Column(
        String(50), nullable=False,
        comment="What caused the change: new_literature, rescore, new_trial, gnn_update",
    )
    trigger_details = Column(JSONB, default=dict, comment="E.g. {pmid: '12345', title: '...'}")

    # Per-dimension snapshot at time of change
    dimension_scores = Column(JSONB, comment="All 8 dimension scores at time of change")

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_score_history_hyp_date", "hypothesis_id", "created_at"),
        Index("ix_score_history_delta", score_delta.desc()),
    )


class LiteratureAlert(Base):
    """Alert generated when new literature significantly impacts a hypothesis.

    Created by the literature monitoring service when:
      - A new PubMed paper co-mentions a tracked drug-cancer pair
      - The resulting score change exceeds the alert threshold (default: 5 pts)
    """
    __tablename__ = "literature_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hypothesis_id = Column(
        Integer, ForeignKey("hypotheses.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    alert_type = Column(
        String(50), nullable=False,
        comment="new_paper, new_trial_result, score_increase, score_decrease",
    )
    severity = Column(
        String(20), nullable=False, default="info",
        comment="info, notable, significant, critical",
    )
    title = Column(String(500), nullable=False)
    description = Column(Text)
    score_delta = Column(Float, comment="Score change that triggered this alert")

    # Source information
    source_pmid = Column(String(20), comment="PubMed ID if triggered by a paper")
    source_nct_id = Column(String(20), comment="NCT ID if triggered by a trial")
    source_data = Column(JSONB, default=dict)

    is_read = Column(Integer, default=0, comment="0=unread, 1=read")
    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_lit_alert_unread", "is_read", "created_at"),
        Index("ix_lit_alert_severity", "severity", "created_at"),
        Index("ix_lit_alert_hyp", "hypothesis_id"),
    )


class MonitoringConfig(Base):
    """Configuration for the literature monitoring scheduler.

    Stores which drug-cancer pairs to actively monitor and how often.
    """
    __tablename__ = "monitoring_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    hypothesis_id = Column(
        Integer, ForeignKey("hypotheses.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    is_active = Column(Integer, nullable=False, default=1)
    alert_threshold = Column(
        Float, nullable=False, default=5.0,
        comment="Minimum score delta to trigger an alert",
    )
    last_checked_at = Column(DateTime)
    check_interval_hours = Column(Integer, nullable=False, default=168, comment="Default: weekly")

    created_at = Column(DateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_monitoring_active", "is_active", "last_checked_at"),
    )
