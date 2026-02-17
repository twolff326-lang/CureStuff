from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


@router.post("/start/{source}")
async def start_ingestion(source: str, db: AsyncSession = Depends(get_db)):
    """Start data ingestion from a specific source."""
    # Implementation in future prompt
    return {"status": "queued", "source": source, "task_id": None}


@router.get("/logs")
async def get_ingestion_logs(
    skip: int = 0, limit: int = 50, db: AsyncSession = Depends(get_db)
):
    """Get ingestion log history."""
    # Implementation in future prompt
    return {"logs": [], "total": 0, "skip": skip, "limit": limit}
