from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.hypothesis import Hypothesis
from app.models.drug import Drug
from app.models.cancer_type import CancerType

router = APIRouter()


@router.get("")
async def list_hypotheses(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    min_score: float = Query(0, ge=0, le=100),
    strategy: str = Query(None),
    sort_by: str = Query("composite_score"),
    db: AsyncSession = Depends(get_db),
):
    query = select(Hypothesis).options(
        selectinload(Hypothesis.drug),
        selectinload(Hypothesis.cancer_type),
    )
    count_query = select(func.count(Hypothesis.id))

    if min_score > 0:
        query = query.where(Hypothesis.composite_score >= min_score)
        count_query = count_query.where(Hypothesis.composite_score >= min_score)
    if strategy:
        query = query.where(Hypothesis.strategy == strategy)
        count_query = count_query.where(Hypothesis.strategy == strategy)

    total = (await db.execute(count_query)).scalar()

    sort_col = getattr(Hypothesis, sort_by, Hypothesis.composite_score)
    result = await db.execute(
        query.order_by(sort_col.desc()).offset((page - 1) * per_page).limit(per_page)
    )
    hypotheses = result.scalars().all()

    return {
        "items": [_serialize_hypothesis(h) for h in hypotheses],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/top")
async def top_hypotheses(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Hypothesis)
        .options(
            selectinload(Hypothesis.drug),
            selectinload(Hypothesis.cancer_type),
        )
        .order_by(Hypothesis.composite_score.desc())
        .limit(limit)
    )
    return [_serialize_hypothesis(h) for h in result.scalars().all()]


@router.get("/stats")
async def hypothesis_stats(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count(Hypothesis.id)))).scalar()
    avg_score = (await db.execute(select(func.avg(Hypothesis.composite_score)))).scalar()

    strategy_counts = (
        await db.execute(
            select(Hypothesis.strategy, func.count(Hypothesis.id))
            .group_by(Hypothesis.strategy)
        )
    ).all()

    return {
        "total": total,
        "average_score": round(avg_score or 0, 2),
        "by_strategy": {s: c for s, c in strategy_counts if s},
    }


@router.get("/{hypothesis_id}")
async def get_hypothesis(hypothesis_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Hypothesis)
        .options(
            selectinload(Hypothesis.drug),
            selectinload(Hypothesis.cancer_type),
        )
        .where(Hypothesis.id == hypothesis_id)
    )
    h = result.scalar_one_or_none()
    if not h:
        raise HTTPException(status_code=404, detail="Hypothesis not found")
    return _serialize_hypothesis(h)


def _serialize_hypothesis(h: Hypothesis) -> dict:
    return {
        "id": h.id,
        "drug": {"id": h.drug.id, "name": h.drug.name} if h.drug else None,
        "cancer_type": {"id": h.cancer_type.id, "name": h.cancer_type.name} if h.cancer_type else None,
        "composite_score": round(h.composite_score, 2),
        "target_binding_score": round(h.target_binding_score, 2),
        "pathway_overlap_score": round(h.pathway_overlap_score, 2),
        "clinical_evidence_score": round(h.clinical_evidence_score, 2),
        "evidence_summary": h.evidence_summary,
        "strategy": h.strategy,
        "status": h.status,
        "created_at": h.created_at.isoformat() if h.created_at else None,
    }
