from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cancer_type import CancerType
from app.models.mutation import Mutation

router = APIRouter()


@router.get("")
async def list_cancer_types(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    search: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    query = select(CancerType)
    count_query = select(func.count(CancerType.id))

    if search:
        query = query.where(CancerType.name.ilike(f"%{search}%"))
        count_query = count_query.where(CancerType.name.ilike(f"%{search}%"))

    total = (await db.execute(count_query)).scalar()
    result = await db.execute(
        query.order_by(CancerType.name).offset((page - 1) * per_page).limit(per_page)
    )
    cancer_types = result.scalars().all()

    return {
        "items": [
            {
                "id": ct.id,
                "name": ct.name,
                "tcga_code": ct.tcga_code,
                "description": ct.description,
                "tissue": ct.tissue,
            }
            for ct in cancer_types
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/count")
async def cancer_type_count(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(CancerType.id)))).scalar()
    return {"count": total}


@router.get("/{cancer_type_id}")
async def get_cancer_type(cancer_type_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(CancerType).where(CancerType.id == cancer_type_id)
    )
    ct = result.scalar_one_or_none()
    if not ct:
        raise HTTPException(status_code=404, detail="Cancer type not found")

    mutations_result = await db.execute(
        select(Mutation)
        .where(Mutation.cancer_type_id == cancer_type_id)
        .order_by(Mutation.frequency.desc())
    )
    mutations = mutations_result.scalars().all()

    return {
        "id": ct.id,
        "name": ct.name,
        "tcga_code": ct.tcga_code,
        "description": ct.description,
        "tissue": ct.tissue,
        "mutations": [
            {
                "gene_symbol": m.gene_symbol,
                "mutation_type": m.mutation_type,
                "frequency": m.frequency,
            }
            for m in mutations
        ],
    }
