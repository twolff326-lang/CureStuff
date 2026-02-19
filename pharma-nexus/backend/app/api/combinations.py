"""Drug Combination API routes.

Endpoints:
  - GET  /                              Paginated combination listing
  - GET  /top                           Top synergistic combinations
  - GET  /stats                         Combination statistics
  - GET  /cancer/{cancer_type_id}       Combinations for a cancer type
  - GET  /{combo_id}                    Full combination detail
  - POST /generate/cancer/{cancer_id}   Trigger combination generation
  - POST /generate/all                  Trigger generation for all cancer types
  - GET  /status/{task_id}              Check generation task status
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cancer_type import CancerType
from app.models.drug import Drug
from app.models.gene_dependency import CombinationHypothesis
from app.tasks.celery_app import celery_app

router = APIRouter()


# ------------------------------------------------------------------
# Request/response models
# ------------------------------------------------------------------


class GenerateCombinationsRequest(BaseModel):
    min_single_score: float = 30.0
    max_pairs: int = 200


# ------------------------------------------------------------------
# Combination queries
# ------------------------------------------------------------------


@router.get("/top")
async def get_top_combinations(
    limit: int = Query(20, ge=1, le=100),
    min_synergy: float = Query(0.0, ge=0, le=100),
    synergy_classification: str | None = Query(None),
    cancer_type_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Top synergistic drug combinations by synergy score."""
    query = select(CombinationHypothesis).where(
        CombinationHypothesis.synergy_score >= min_synergy
    )

    if synergy_classification:
        query = query.where(
            CombinationHypothesis.synergy_classification == synergy_classification
        )
    if cancer_type_id:
        query = query.where(
            CombinationHypothesis.cancer_type_id == cancer_type_id
        )

    query = query.order_by(
        CombinationHypothesis.synergy_score.desc()
    ).limit(limit)

    result = await db.execute(query)
    combos = result.scalars().all()

    serialized = []
    for c in combos:
        serialized.append(await _serialize_combination(c, db))

    return {"combinations": serialized, "count": len(serialized)}


@router.get("/stats")
async def get_combination_stats(db: AsyncSession = Depends(get_db)):
    """Combination hypothesis statistics."""
    total_result = await db.execute(
        select(func.count(CombinationHypothesis.id))
    )
    total = total_result.scalar() or 0

    # By classification
    class_result = await db.execute(
        select(
            CombinationHypothesis.synergy_classification,
            func.count(CombinationHypothesis.id),
        ).group_by(CombinationHypothesis.synergy_classification)
    )
    by_classification = {row[0]: row[1] for row in class_result.all()}

    # Average scores
    avg_result = await db.execute(
        select(
            func.avg(CombinationHypothesis.synergy_score),
            func.avg(CombinationHypothesis.pathway_complementarity_score),
            func.avg(CombinationHypothesis.target_non_overlap_score),
            func.avg(CombinationHypothesis.synthetic_lethality_score),
            func.avg(CombinationHypothesis.safety_compatibility_score),
            func.avg(CombinationHypothesis.clinical_precedent_score),
        )
    )
    avg_row = avg_result.first()

    return {
        "total_combinations": total,
        "by_classification": by_classification,
        "average_scores": {
            "synergy": round(avg_row[0] or 0, 1),
            "pathway_complementarity": round(avg_row[1] or 0, 1),
            "target_non_overlap": round(avg_row[2] or 0, 1),
            "synthetic_lethality": round(avg_row[3] or 0, 1),
            "safety_compatibility": round(avg_row[4] or 0, 1),
            "clinical_precedent": round(avg_row[5] or 0, 1),
        } if avg_row and avg_row[0] else {},
    }


@router.get("/cancer/{cancer_type_id}")
async def get_combinations_for_cancer(
    cancer_type_id: int,
    limit: int = Query(50, ge=1, le=200),
    min_synergy: float = Query(0.0, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get all combinations for a specific cancer type."""
    query = (
        select(CombinationHypothesis)
        .where(
            CombinationHypothesis.cancer_type_id == cancer_type_id,
            CombinationHypothesis.synergy_score >= min_synergy,
        )
        .order_by(CombinationHypothesis.synergy_score.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    combos = result.scalars().all()

    serialized = []
    for c in combos:
        serialized.append(await _serialize_combination(c, db))

    return {"combinations": serialized, "count": len(serialized), "cancer_type_id": cancer_type_id}


# ------------------------------------------------------------------
# Generation triggers
# ------------------------------------------------------------------


@router.post("/generate/cancer/{cancer_type_id}")
async def trigger_combination_generation(
    cancer_type_id: int,
    request: GenerateCombinationsRequest | None = None,
):
    """Trigger combination generation for a cancer type."""
    min_score = request.min_single_score if request else 30.0
    max_pairs = request.max_pairs if request else 200
    task = celery_app.send_task(
        "app.tasks.generate.generate_combinations_for_cancer",
        kwargs={
            "cancer_type_id": cancer_type_id,
            "min_single_score": min_score,
            "max_pairs": max_pairs,
        },
    )
    return {"task_id": task.id, "status": "queued", "cancer_type_id": cancer_type_id}


@router.post("/generate/all")
async def trigger_combination_generation_all(
    request: GenerateCombinationsRequest | None = None,
):
    """Trigger combination generation for all cancer types."""
    min_score = request.min_single_score if request else 30.0
    max_pairs = request.max_pairs if request else 200
    task = celery_app.send_task(
        "app.tasks.generate.generate_all_combinations",
        kwargs={"min_single_score": min_score, "max_pairs": max_pairs},
    )
    return {"task_id": task.id, "status": "queued"}


@router.get("/status/{task_id}")
async def get_generation_status(task_id: str):
    """Check status of a combination generation task."""
    result = celery_app.AsyncResult(task_id)
    task_result = None
    if result.ready():
        try:
            task_result = result.result
        except Exception:
            task_result = {"error": str(result.result)}
    return {"task_id": task_id, "status": result.state, "result": task_result}


# ------------------------------------------------------------------
# Combination detail (must be after specific routes)
# ------------------------------------------------------------------


@router.get("/{combo_id}")
async def get_combination_detail(
    combo_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get full combination detail."""
    result = await db.execute(
        select(CombinationHypothesis).where(
            CombinationHypothesis.id == combo_id
        )
    )
    combo = result.scalar_one_or_none()
    if not combo:
        raise HTTPException(status_code=404, detail="Combination not found")

    data = await _serialize_combination(combo, db)
    data["rationale"] = combo.rationale
    data["shared_pathways"] = combo.shared_pathways
    data["complementary_pathways"] = combo.complementary_pathways
    data["details"] = combo.details
    return data


@router.get("/")
async def list_combinations(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    min_synergy: float = Query(0.0, ge=0, le=100),
    synergy_classification: str | None = Query(None),
    cancer_type_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of combination hypotheses."""
    query = select(CombinationHypothesis).where(
        CombinationHypothesis.synergy_score >= min_synergy
    )
    count_query = select(func.count(CombinationHypothesis.id)).where(
        CombinationHypothesis.synergy_score >= min_synergy
    )

    if synergy_classification:
        query = query.where(
            CombinationHypothesis.synergy_classification == synergy_classification
        )
        count_query = count_query.where(
            CombinationHypothesis.synergy_classification == synergy_classification
        )
    if cancer_type_id:
        query = query.where(
            CombinationHypothesis.cancer_type_id == cancer_type_id
        )
        count_query = count_query.where(
            CombinationHypothesis.cancer_type_id == cancer_type_id
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * per_page
    query = query.order_by(
        CombinationHypothesis.synergy_score.desc()
    ).offset(offset).limit(per_page)

    result = await db.execute(query)
    combos = result.scalars().all()

    serialized = []
    for c in combos:
        serialized.append(await _serialize_combination(c, db))

    return {
        "combinations": serialized,
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _serialize_combination(c: CombinationHypothesis, db: AsyncSession) -> dict:
    """Serialize a combination hypothesis with drug and cancer names."""
    drug_a_result = await db.execute(
        select(Drug.name, Drug.drugbank_id).where(Drug.id == c.drug_a_id)
    )
    drug_a = drug_a_result.first()

    drug_b_result = await db.execute(
        select(Drug.name, Drug.drugbank_id).where(Drug.id == c.drug_b_id)
    )
    drug_b = drug_b_result.first()

    cancer_result = await db.execute(
        select(CancerType.name, CancerType.tcga_code).where(
            CancerType.id == c.cancer_type_id
        )
    )
    cancer = cancer_result.first()

    return {
        "id": c.id,
        "drug_a": {
            "id": c.drug_a_id,
            "name": drug_a[0] if drug_a else None,
            "drugbank_id": drug_a[1] if drug_a else None,
        },
        "drug_b": {
            "id": c.drug_b_id,
            "name": drug_b[0] if drug_b else None,
            "drugbank_id": drug_b[1] if drug_b else None,
        },
        "cancer_type": {
            "id": c.cancer_type_id,
            "name": cancer[0] if cancer else None,
            "tcga_code": cancer[1] if cancer else None,
        },
        "synergy_score": c.synergy_score,
        "synergy_classification": c.synergy_classification,
        "dimension_scores": {
            "pathway_complementarity": c.pathway_complementarity_score,
            "target_non_overlap": c.target_non_overlap_score,
            "synthetic_lethality": c.synthetic_lethality_score,
            "safety_compatibility": c.safety_compatibility_score,
            "clinical_precedent": c.clinical_precedent_score,
        },
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }
