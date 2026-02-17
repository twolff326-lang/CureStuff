from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


@router.get("/")
async def list_hypotheses(
    skip: int = 0,
    limit: int = 20,
    min_score: float = 0.0,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """List hypotheses sorted by composite score descending."""
    # Implementation in future prompt
    return {"hypotheses": [], "total": 0, "skip": skip, "limit": limit}


@router.get("/{hypothesis_id}")
async def get_hypothesis(hypothesis_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single hypothesis with full evidence breakdown."""
    # Implementation in future prompt
    return {"hypothesis_id": hypothesis_id}
