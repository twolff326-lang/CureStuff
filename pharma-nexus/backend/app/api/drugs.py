from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

router = APIRouter()


@router.get("/")
async def list_drugs(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List all drugs with pagination."""
    # Implementation in future prompt
    return {"drugs": [], "total": 0, "skip": skip, "limit": limit}


@router.get("/{drug_id}")
async def get_drug(drug_id: int, db: AsyncSession = Depends(get_db)):
    """Get a single drug by ID with its targets and relationships."""
    # Implementation in future prompt
    return {"drug_id": drug_id}
