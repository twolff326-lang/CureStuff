from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


@router.post("/run")
async def trigger_analysis(db: AsyncSession = Depends(get_db)):
    """Trigger a full analysis run (dispatches Celery tasks)."""
    # Implementation in future prompt
    return {"status": "queued", "task_id": None}


@router.get("/status/{task_id}")
async def get_analysis_status(task_id: str):
    """Check the status of a running analysis task."""
    # Implementation in future prompt
    return {"task_id": task_id, "status": "pending"}
