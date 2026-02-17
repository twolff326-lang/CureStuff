from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.ingestion_log import IngestionLog
from app.tasks.celery_app import celery_app

router = APIRouter()

VALID_SOURCES = {"drugbank", "pubchem", "chembl", "all_drugs"}


class IngestionRequest(BaseModel):
    source: str
    xml_path: str | None = None


@router.post("/start")
async def start_ingestion(request: IngestionRequest):
    """Kick off a data ingestion Celery task.

    Body: {"source": "drugbank"|"pubchem"|"chembl"|"all_drugs", "xml_path": "..."}
    Returns: {"task_id": "...", "status": "queued"}
    """
    source = request.source.lower()
    if source not in VALID_SOURCES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid source '{source}'. Must be one of: {', '.join(sorted(VALID_SOURCES))}",
        )

    task_map = {
        "drugbank": "app.tasks.ingest.ingest_drugbank",
        "pubchem": "app.tasks.ingest.ingest_pubchem",
        "chembl": "app.tasks.ingest.ingest_chembl",
        "all_drugs": "app.tasks.ingest.ingest_all_drugs",
    }

    task_name = task_map[source]
    kwargs = {}
    if source in ("drugbank", "all_drugs") and request.xml_path:
        kwargs["xml_path"] = request.xml_path

    task = celery_app.send_task(task_name, kwargs=kwargs)
    return {"task_id": task.id, "status": "queued", "source": source}


@router.get("/status/{task_id}")
async def get_ingestion_status(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get status of a running ingestion task.

    Returns Celery task state plus matching ingestion_log entry.
    """
    result = celery_app.AsyncResult(task_id)
    task_state = result.state
    task_result = None

    if result.ready():
        try:
            task_result = result.result
        except Exception:
            task_result = {"error": str(result.result)}

    # Try to find matching ingestion log (most recent for the task)
    log_entry = None
    log_result = await db.execute(
        select(IngestionLog)
        .order_by(IngestionLog.started_at.desc())
        .limit(1)
    )
    log = log_result.scalar_one_or_none()
    if log:
        log_entry = {
            "id": log.id,
            "source": log.source,
            "task_type": log.task_type,
            "status": log.status,
            "records_processed": log.records_processed,
            "errors": log.errors,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        }

    return {
        "task_id": task_id,
        "task_state": task_state,
        "task_result": task_result,
        "ingestion_log": log_entry,
    }


@router.get("/logs")
async def get_ingestion_logs(
    source: str | None = Query(None, description="Filter by source name"),
    status: str | None = Query(None, description="Filter by status"),
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of all ingestion runs, sorted by started_at desc."""
    query = select(IngestionLog)
    count_query = select(func.count(IngestionLog.id))

    if source:
        query = query.where(IngestionLog.source == source)
        count_query = count_query.where(IngestionLog.source == source)
    if status:
        query = query.where(IngestionLog.status == status)
        count_query = count_query.where(IngestionLog.status == status)

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar()

    # Get paginated results
    offset = (page - 1) * per_page
    query = query.order_by(IngestionLog.started_at.desc()).offset(offset).limit(per_page)
    result = await db.execute(query)
    logs = result.scalars().all()

    return {
        "logs": [
            {
                "id": log.id,
                "source": log.source,
                "task_type": log.task_type,
                "status": log.status,
                "records_processed": log.records_processed,
                "errors": log.errors,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
            }
            for log in logs
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }
