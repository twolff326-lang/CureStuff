"""API routes for the unified repurposing pipeline.

Endpoints:
  POST /api/pipeline/run       — Launch a full pipeline run
  GET  /api/pipeline/status/{id} — Get status of a pipeline run
  GET  /api/pipeline/runs      — List recent pipeline runs
  GET  /api/pipeline/phases    — List available pipeline phases
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.pipeline_run import PipelineRun
from app.tasks.celery_app import celery_app
from app.tasks.pipeline import PIPELINE_PHASES

logger = logging.getLogger(__name__)

router = APIRouter()


class PipelineRunRequest(BaseModel):
    phases: list[str] | None = None
    min_score: float | None = None
    llm_min_score: float | None = None
    llm_limit: int | None = None


@router.get("/phases")
async def list_phases():
    """Return all available pipeline phases with their defaults."""
    return {
        "phases": [
            {
                "key": key,
                "label": label,
                "default_enabled": default,
            }
            for key, label, _, default in PIPELINE_PHASES
        ]
    }


@router.post("/run")
async def start_pipeline(
    request: PipelineRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """Launch a full repurposing pipeline run.

    Creates a PipelineRun row, initializes all phase statuses,
    then dispatches the Celery orchestrator task.
    """
    # Determine which phases to run
    if request.phases:
        valid_keys = {key for key, _, _, _ in PIPELINE_PHASES}
        invalid = set(request.phases) - valid_keys
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid phase keys: {', '.join(sorted(invalid))}. "
                       f"Valid: {', '.join(sorted(valid_keys))}",
            )
        enabled_phases = request.phases
    else:
        enabled_phases = [key for key, _, _, default in PIPELINE_PHASES if default]

    # Check no pipeline is already running (use FOR UPDATE to prevent race)
    existing = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.status == "running")
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=409,
            detail="A pipeline is already running. Wait for it to finish or cancel it first.",
        )

    # Build phase status list
    phase_label_map = {key: label for key, label, _, _ in PIPELINE_PHASES}
    phases_json = [
        {
            "key": key,
            "label": phase_label_map[key],
            "status": "pending",
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
        }
        for key in enabled_phases
    ]

    config = {}
    if request.min_score is not None:
        config["min_score"] = request.min_score
    if request.llm_min_score is not None:
        config["llm_min_score"] = request.llm_min_score
    if request.llm_limit is not None:
        config["llm_limit"] = request.llm_limit

    # Create the run record (within the same transaction as the lock)
    run = PipelineRun(
        status="running",
        current_phase=None,
        phases=phases_json,
        config=config,
    )
    db.add(run)
    await db.flush()
    run_id = run.id
    await db.commit()

    # Dispatch the Celery orchestrator
    task = celery_app.send_task(
        "app.tasks.pipeline.run_full_pipeline",
        kwargs={
            "run_id": run_id,
            "enabled_phases": enabled_phases,
            "config": config,
        },
    )

    logger.info("Pipeline run %d started (task=%s, phases=%s)", run_id, task.id, enabled_phases)

    return {
        "run_id": run_id,
        "task_id": task.id,
        "status": "running",
        "phases": phases_json,
    }


@router.get("/status/{run_id}")
async def get_pipeline_status(
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get the current status of a pipeline run including per-phase progress."""
    result = await db.execute(
        select(PipelineRun).where(PipelineRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found")

    return {
        "id": run.id,
        "status": run.status,
        "current_phase": run.current_phase,
        "phases": run.phases,
        "config": run.config,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
    }


@router.post("/cancel/{run_id}")
async def cancel_pipeline_run(
    run_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Cancel a running pipeline by ID.

    Marks the run and any pending phases as cancelled so a new run can start.
    """
    result = await db.execute(
        select(PipelineRun).where(PipelineRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Pipeline run not found")

    return await _cancel_run(run, db)


@router.post("/cancel")
async def cancel_current_pipeline(
    db: AsyncSession = Depends(get_db),
):
    """Cancel the currently-running pipeline (no run_id needed).

    Finds whichever pipeline run has status='running' and cancels it.
    """
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.status == "running")
        .limit(1)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(
            status_code=404,
            detail="No running pipeline found",
        )

    return await _cancel_run(run, db)


async def _cancel_run(run: PipelineRun, db: AsyncSession):
    """Shared logic to cancel a pipeline run."""
    if run.status not in ("running",):
        raise HTTPException(
            status_code=409,
            detail=f"Pipeline run {run.id} is already '{run.status}'",
        )

    # Mark pending/running phases as cancelled
    phases = run.phases or []
    for p in phases:
        if p.get("status") in ("pending", "running"):
            p["status"] = "cancelled"

    run.status = "cancelled"
    run.current_phase = None
    run.completed_at = datetime.now(timezone.utc)
    run.phases = phases
    await db.commit()

    logger.info("Pipeline run %d cancelled", run.id)
    return {"run_id": run.id, "status": "cancelled"}


@router.get("/runs")
async def list_pipeline_runs(
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """List recent pipeline runs, newest first."""
    total_result = await db.execute(select(func.count(PipelineRun.id)))
    total = total_result.scalar() or 0

    offset = (page - 1) * per_page
    result = await db.execute(
        select(PipelineRun)
        .order_by(PipelineRun.started_at.desc())
        .offset(offset)
        .limit(per_page)
    )
    runs = result.scalars().all()

    return {
        "runs": [
            {
                "id": r.id,
                "status": r.status,
                "current_phase": r.current_phase,
                "phases": r.phases,
                "config": r.config,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            }
            for r in runs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
