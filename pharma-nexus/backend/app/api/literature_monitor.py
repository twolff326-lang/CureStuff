"""Literature monitoring API routes.

Endpoints:
  - GET  /alerts           List literature alerts
  - PUT  /alerts/{id}/read Mark an alert as read
  - GET  /history/{hypothesis_id}  Score change history for a hypothesis
  - POST /check            Manually trigger a literature check
  - POST /monitor/{hypothesis_id}  Enable monitoring for a hypothesis
  - DELETE /monitor/{hypothesis_id}  Disable monitoring
  - GET  /monitor/status   List all monitored hypotheses
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.literature_alert import LiteratureAlert, MonitoringConfig, ScoreHistory
from app.services.literature_monitor import LiteratureMonitor

logger = logging.getLogger(__name__)
router = APIRouter()


class MonitorRequest(BaseModel):
    alert_threshold: float = Field(5.0, ge=1.0, le=50.0)
    check_interval_hours: int = Field(168, ge=1, le=720)


class ManualCheckRequest(BaseModel):
    days_back: int = Field(7, ge=1, le=90)
    max_hypotheses: int = Field(100, ge=1, le=500)


@router.get("/alerts", summary="List literature alerts")
async def list_alerts(
    unread_only: bool = Query(True),
    severity: str | None = Query(None, pattern="^(info|notable|significant|critical)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get literature alerts, optionally filtered by read status and severity."""
    monitor = LiteratureMonitor(db)
    return await monitor.get_alerts(
        unread_only=unread_only,
        severity=severity,
        limit=limit,
        offset=offset,
    )


@router.put("/alerts/{alert_id}/read", summary="Mark alert as read")
async def mark_alert_read(
    alert_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Mark a literature alert as read."""
    monitor = LiteratureMonitor(db)
    success = await monitor.mark_alert_read(alert_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
    return {"alert_id": alert_id, "is_read": True}


@router.get("/history/{hypothesis_id}", summary="Score change history")
async def score_history(
    hypothesis_id: int,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Get the score change history for a hypothesis (newest first)."""
    monitor = LiteratureMonitor(db)
    history = await monitor.get_score_history(hypothesis_id, limit)
    return {
        "hypothesis_id": hypothesis_id,
        "total": len(history),
        "history": history,
    }


@router.post("/check", summary="Manually trigger literature check")
async def manual_check(
    request: ManualCheckRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Manually trigger a PubMed check for all monitored hypotheses.

    This is the same check that runs automatically via Celery Beat,
    but triggered on-demand.
    """
    monitor = LiteratureMonitor(db)
    result = await monitor.check_for_new_literature(
        days_back=request.days_back,
        max_hypotheses=request.max_hypotheses,
    )
    return result


@router.post("/monitor/{hypothesis_id}", summary="Enable monitoring")
async def enable_monitoring(
    hypothesis_id: int,
    request: MonitorRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Enable literature monitoring for a specific hypothesis."""
    monitor = LiteratureMonitor(db)
    threshold = request.alert_threshold if request else 5.0
    interval = request.check_interval_hours if request else 168
    return await monitor.enable_monitoring(
        hypothesis_id,
        alert_threshold=threshold,
        check_interval_hours=interval,
    )


@router.delete("/monitor/{hypothesis_id}", summary="Disable monitoring")
async def disable_monitoring(
    hypothesis_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Disable literature monitoring for a hypothesis."""
    monitor = LiteratureMonitor(db)
    success = await monitor.disable_monitoring(hypothesis_id)
    if not success:
        raise HTTPException(status_code=404, detail=f"No monitoring config for hypothesis {hypothesis_id}")
    return {"hypothesis_id": hypothesis_id, "is_active": False}


@router.get("/monitor/status", summary="List monitored hypotheses")
async def monitoring_status(
    active_only: bool = Query(True),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List all hypotheses with monitoring configuration."""
    query = select(MonitoringConfig)
    if active_only:
        query = query.where(MonitoringConfig.is_active == 1)
    query = query.order_by(MonitoringConfig.created_at.desc())

    result = await db.execute(query)
    configs = result.scalars().all()

    total_alerts = await db.execute(
        select(func.count(LiteratureAlert.id)).where(LiteratureAlert.is_read == 0)
    )
    unread_count = total_alerts.scalar() or 0

    return {
        "total_monitored": len(configs),
        "unread_alerts": unread_count,
        "configs": [
            {
                "id": c.id,
                "hypothesis_id": c.hypothesis_id,
                "is_active": c.is_active == 1,
                "alert_threshold": c.alert_threshold,
                "check_interval_hours": c.check_interval_hours,
                "last_checked_at": c.last_checked_at.isoformat() if c.last_checked_at else None,
            }
            for c in configs
        ],
    }
