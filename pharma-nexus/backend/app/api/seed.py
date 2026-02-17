from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.quickstart_seed import seed_quickstart

router = APIRouter()


@router.post("/seed")
async def run_seed(
    force: bool = Query(False, description="Re-seed even if data already exists"),
    db: AsyncSession = Depends(get_db),
):
    """Load quick-start demo data (15 drugs, 8 cancers, 20 hypotheses)."""
    return await seed_quickstart(db, force=force)
