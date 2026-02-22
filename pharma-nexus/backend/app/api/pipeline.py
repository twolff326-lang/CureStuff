"""API routes for the unified repurposing pipeline.

Endpoints:
  POST /api/pipeline/run       — Launch a full pipeline run
  GET  /api/pipeline/status/{id} — Get status of a pipeline run
  GET  /api/pipeline/runs      — List recent pipeline runs
  GET  /api/pipeline/phases    — List available pipeline phases
"""

import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select, func, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.database import get_db, async_session_factory, engine
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

    # Acquire an advisory lock to serialize pipeline creation.
    # FOR UPDATE alone is insufficient: when no running pipeline exists,
    # the lock targets zero rows, so concurrent requests both pass.
    # pg_advisory_xact_lock is released automatically when the
    # transaction commits or rolls back.
    _PIPELINE_LOCK_KEY = 73019  # arbitrary constant
    await db.execute(text(f"SELECT pg_advisory_xact_lock({_PIPELINE_LOCK_KEY})"))

    existing = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.status == "running")
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

    # Store the Celery task ID so the cancel endpoint can revoke it.
    # If this second commit fails, the run and task are already live —
    # return success anyway since only SIGTERM revocation is lost
    # (the orchestrator's DB status check still works for cancellation).
    try:
        run.config = {**config, "_celery_task_id": task.id}
        flag_modified(run, "config")
        await db.commit()
    except Exception as exc:
        logger.warning(
            "Pipeline run %d: task ID storage failed (%s). "
            "Cancel via SIGTERM will not work for this run.",
            run_id, exc,
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


@router.post("/cancel")
async def cancel_current_pipeline():
    """Cancel the currently-running pipeline (no run_id needed)."""
    return await _safe_cancel(run_id=None)


@router.post("/cancel/{run_id}")
async def cancel_pipeline_run(run_id: int):
    """Cancel a running pipeline by ID."""
    return await _safe_cancel(run_id=run_id)


async def _safe_cancel(run_id: int | None) -> JSONResponse:
    """Cancel via raw SQL — no ORM, no FOR UPDATE, single atomic UPDATE.

    Uses engine.connect() directly (not even a Session) so there is
    nothing that can interfere: no dependency injection, no ORM identity
    map, no expire-on-commit, no middleware exception chain.
    """
    try:
        async with engine.connect() as conn:
            # Single atomic UPDATE — cancels the run and all pending/running
            # phases in one statement.  Returns the updated row so we know
            # what happened.
            if run_id is not None:
                where_clause = "id = :rid AND status = 'running'"
                params = {"rid": run_id}
            else:
                # Pick the single running row (if any).
                where_clause = "id = (SELECT id FROM pipeline_runs WHERE status = 'running' LIMIT 1)"
                params = {}

            result = await conn.execute(
                text(f"""
                    UPDATE pipeline_runs
                    SET status = 'cancelled',
                        current_phase = NULL,
                        completed_at = NOW(),
                        phases = (
                            SELECT COALESCE(jsonb_agg(
                                CASE WHEN elem->>'status' IN ('pending','running')
                                     THEN jsonb_set(elem, '{{status}}', '"cancelled"')
                                     ELSE elem
                                END
                            ), '[]'::jsonb)
                            FROM jsonb_array_elements(phases) AS elem
                        )
                    WHERE {where_clause}
                    RETURNING id, config
                """),
                params,
            )
            row = result.mappings().first()
            await conn.commit()

        if not row:
            return JSONResponse(
                status_code=404,
                content={"detail": "No running pipeline found (or already cancelled)"},
            )

        cancelled_id = row["id"]
        task_id = (row["config"] or {}).get("_celery_task_id")

        # Best-effort Celery revoke (non-blocking).
        if task_id:
            try:
                await asyncio.to_thread(
                    celery_app.control.revoke, task_id, terminate=True, signal="SIGTERM",
                )
            except Exception:
                pass

        logger.info("Pipeline run %s cancelled", cancelled_id)
        return JSONResponse(
            status_code=200,
            content={"run_id": cancelled_id, "status": "cancelled"},
        )

    except Exception as exc:
        logger.exception("Cancel failed for run_id=%s", run_id)
        return JSONResponse(
            status_code=500,
            content={"detail": f"Cancel failed: {type(exc).__name__}: {exc}"},
        )


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
