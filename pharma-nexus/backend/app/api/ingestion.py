import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, async_session
from app.models.ingestion_log import IngestionLog

router = APIRouter()

VALID_SOURCES = ["pubchem", "chembl", "cancer_seed", "pathways", "hypotheses"]


class IngestionRequest(BaseModel):
    source: str


@router.post("/start")
async def start_ingestion(
    request: IngestionRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    if request.source not in VALID_SOURCES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid source. Valid sources: {VALID_SOURCES}",
        )

    # Check if already running
    result = await db.execute(
        select(IngestionLog).where(
            IngestionLog.source == request.source,
            IngestionLog.status == "running",
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"{request.source} ingestion already running")

    log = IngestionLog(
        source=request.source,
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)

    background_tasks.add_task(_run_ingestion, request.source, log.id)

    return {"id": log.id, "source": request.source, "status": "running"}


@router.get("/status")
async def list_ingestion_status(
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IngestionLog).order_by(IngestionLog.created_at.desc()).limit(20)
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "source": log.source,
            "status": log.status,
            "records_processed": log.records_processed,
            "total_expected": log.total_expected,
            "error_message": log.error_message,
            "started_at": log.started_at.isoformat() if log.started_at else None,
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
        }
        for log in logs
    ]


@router.get("/status/{log_id}")
async def get_ingestion_status(
    log_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(IngestionLog).where(IngestionLog.id == log_id)
    )
    log = result.scalar_one_or_none()
    if not log:
        raise HTTPException(status_code=404, detail="Ingestion log not found")

    return {
        "id": log.id,
        "source": log.source,
        "status": log.status,
        "records_processed": log.records_processed,
        "total_expected": log.total_expected,
        "error_message": log.error_message,
        "started_at": log.started_at.isoformat() if log.started_at else None,
        "completed_at": log.completed_at.isoformat() if log.completed_at else None,
    }


async def _run_ingestion(source: str, log_id: int):
    """Run ingestion in background. Each source has its own connector."""
    try:
        async with async_session() as db:
            if source == "pubchem":
                from app.services.ingestion.pubchem import PubChemConnector
                connector = PubChemConnector(db, log_id)
            elif source == "chembl":
                from app.services.ingestion.chembl import ChEMBLConnector
                connector = ChEMBLConnector(db, log_id)
            elif source == "cancer_seed":
                from app.services.ingestion.cancer_seed import CancerSeedConnector
                connector = CancerSeedConnector(db, log_id)
            elif source == "pathways":
                from app.services.ingestion.pathways import PathwayConnector
                connector = PathwayConnector(db, log_id)
            elif source == "hypotheses":
                from app.services.hypothesis_engine import HypothesisEngine
                engine = HypothesisEngine(db, log_id)
                await engine.generate_all()
                return
            else:
                raise ValueError(f"Unknown source: {source}")

            await connector.run()
    except Exception as e:
        async with async_session() as db:
            result = await db.execute(
                select(IngestionLog).where(IngestionLog.id == log_id)
            )
            log = result.scalar_one_or_none()
            if log:
                log.status = "failed"
                log.error_message = str(e)[:2000]
                log.completed_at = datetime.now(timezone.utc)
                await db.commit()
