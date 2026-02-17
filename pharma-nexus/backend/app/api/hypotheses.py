"""Hypothesis API routes.

Endpoints:
  - GET  /                               Paginated hypothesis listing with filters
  - GET  /top                            Top hypotheses by composite score
  - GET  /novel                          Novel discovery endpoint (high novelty + score)
  - GET  /stats                          Hypothesis statistics
  - GET  /scoring/weights                List scoring presets
  - PUT  /scoring/weights/active         Set active scoring preset
  - POST /scoring/weights                Create custom preset
  - POST /generate/cancer/{cancer_id}    Trigger generation for a cancer type
  - POST /generate/drug/{drug_id}        Trigger generation for a drug
  - POST /generate/all                   Trigger generation for all
  - POST /rescore                        Trigger rescoring
  - GET  /status/{task_id}               Check generation task status
  - GET  /{hypothesis_id}                Full hypothesis detail with evidence
  - GET  /{hypothesis_id}/evidence       Evidence breakdown for a hypothesis
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cancer_type import CancerType
from app.models.drug import Drug
from app.models.hypothesis import Hypothesis, HypothesisEvidence
from app.tasks.celery_app import celery_app

router = APIRouter()


# ------------------------------------------------------------------
# Request/response models
# ------------------------------------------------------------------


class GenerateRequest(BaseModel):
    min_score: float = 15.0


class RescoreRequest(BaseModel):
    preset_name: str | None = None


class WeightPresetRequest(BaseModel):
    name: str
    weights: dict[str, float]
    description: str | None = None
    set_default: bool = False


class SetActivePresetRequest(BaseModel):
    name: str


# ------------------------------------------------------------------
# Hypothesis queries
# ------------------------------------------------------------------


@router.get("/top")
async def get_top_hypotheses(
    limit: int = Query(20, ge=1, le=100),
    min_score: float = Query(0.0, ge=0, le=100),
    evidence_strength: str | None = Query(None, description="strong, moderate, suggestive, speculative"),
    cancer_type_id: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Top hypotheses by composite score."""
    query = select(Hypothesis).where(
        Hypothesis.composite_score >= min_score
    )

    if evidence_strength:
        query = query.where(Hypothesis.evidence_strength == evidence_strength)
    if cancer_type_id:
        query = query.where(Hypothesis.cancer_type_id == cancer_type_id)

    query = query.order_by(Hypothesis.composite_score.desc()).limit(limit)
    result = await db.execute(query)
    hypotheses = result.scalars().all()

    return {
        "hypotheses": [_serialize_hypothesis(h) for h in hypotheses],
        "count": len(hypotheses),
    }


@router.get("/novel")
async def get_novel_discoveries(
    limit: int = Query(20, ge=1, le=100),
    min_composite: float = Query(25.0, ge=0, le=100),
    min_novelty: float = Query(60.0, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Novel discovery endpoint: high novelty score + reasonable composite score.

    These are the most exciting finds — drug-cancer pairs that are
    biologically plausible but not yet explored in literature/trials.
    """
    query = (
        select(Hypothesis)
        .where(
            Hypothesis.composite_score >= min_composite,
            Hypothesis.novelty_score >= min_novelty,
        )
        .order_by(
            (Hypothesis.novelty_score + Hypothesis.composite_score).desc()
        )
        .limit(limit)
    )
    result = await db.execute(query)
    hypotheses = result.scalars().all()

    return {
        "hypotheses": [_serialize_hypothesis(h) for h in hypotheses],
        "count": len(hypotheses),
        "filters": {
            "min_composite": min_composite,
            "min_novelty": min_novelty,
        },
    }


@router.get("/stats")
async def get_hypothesis_stats(db: AsyncSession = Depends(get_db)):
    """Hypothesis statistics overview."""
    total_result = await db.execute(
        select(func.count(Hypothesis.id))
    )
    total = total_result.scalar() or 0

    # By evidence strength
    strength_result = await db.execute(
        select(
            Hypothesis.evidence_strength,
            func.count(Hypothesis.id),
        ).group_by(Hypothesis.evidence_strength)
    )
    by_strength = {row[0]: row[1] for row in strength_result.all()}

    # By status
    status_result = await db.execute(
        select(
            Hypothesis.status,
            func.count(Hypothesis.id),
        ).group_by(Hypothesis.status)
    )
    by_status = {row[0]: row[1] for row in status_result.all()}

    # Average scores
    avg_result = await db.execute(
        select(
            func.avg(Hypothesis.composite_score),
            func.avg(Hypothesis.pathway_overlap_score),
            func.avg(Hypothesis.expression_correlation_score),
            func.avg(Hypothesis.literature_support_score),
            func.avg(Hypothesis.clinical_evidence_score),
            func.avg(Hypothesis.safety_score),
            func.avg(Hypothesis.novelty_score),
        )
    )
    avg_row = avg_result.first()

    # Top cancer types by hypothesis count
    top_cancers_result = await db.execute(
        select(
            CancerType.name,
            func.count(Hypothesis.id),
        )
        .join(CancerType, Hypothesis.cancer_type_id == CancerType.id)
        .group_by(CancerType.name)
        .order_by(func.count(Hypothesis.id).desc())
        .limit(10)
    )
    top_cancers = [
        {"name": row[0], "count": row[1]}
        for row in top_cancers_result.all()
    ]

    return {
        "total_hypotheses": total,
        "by_evidence_strength": by_strength,
        "by_status": by_status,
        "average_scores": {
            "composite": round(avg_row[0] or 0, 1),
            "pathway_overlap": round(avg_row[1] or 0, 1),
            "expression_correlation": round(avg_row[2] or 0, 1),
            "literature_support": round(avg_row[3] or 0, 1),
            "clinical_evidence": round(avg_row[4] or 0, 1),
            "safety": round(avg_row[5] or 0, 1),
            "novelty": round(avg_row[6] or 0, 1),
        },
        "top_cancer_types": top_cancers,
    }


# ------------------------------------------------------------------
# Scoring weight management
# ------------------------------------------------------------------


@router.get("/scoring/weights")
async def list_scoring_presets(db: AsyncSession = Depends(get_db)):
    """List all available scoring weight presets."""
    from app.services.scoring_config import ScoringConfig

    config = ScoringConfig()
    presets = await config.list_presets(db)
    return {"presets": presets}


@router.put("/scoring/weights/active")
async def set_active_scoring_preset(
    request: SetActivePresetRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set a scoring preset as the active default."""
    from app.services.scoring_config import ScoringConfig

    config = ScoringConfig()
    result = await config.set_active_preset(request.name, db)
    if not result:
        raise HTTPException(status_code=404, detail=f"Preset '{request.name}' not found")
    return result


@router.post("/scoring/weights")
async def create_scoring_preset(
    request: WeightPresetRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create a custom scoring weight preset."""
    from app.services.scoring_config import ScoringConfig

    config = ScoringConfig()
    try:
        result = await config.create_preset(
            name=request.name,
            weights=request.weights,
            description=request.description,
            set_default=request.set_default,
            db=db,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------
# Generation triggers
# ------------------------------------------------------------------


@router.post("/generate/cancer/{cancer_type_id}")
async def trigger_generation_for_cancer(
    cancer_type_id: int,
    request: GenerateRequest | None = None,
):
    """Trigger hypothesis generation for a specific cancer type."""
    min_score = request.min_score if request else 15.0
    task = celery_app.send_task(
        "app.tasks.generate.generate_hypotheses_for_cancer",
        kwargs={"cancer_type_id": cancer_type_id, "min_score": min_score},
    )
    return {"task_id": task.id, "status": "queued", "cancer_type_id": cancer_type_id}


@router.post("/generate/drug/{drug_id}")
async def trigger_generation_for_drug(
    drug_id: int,
    request: GenerateRequest | None = None,
):
    """Trigger hypothesis generation for a specific drug."""
    min_score = request.min_score if request else 15.0
    task = celery_app.send_task(
        "app.tasks.generate.generate_hypotheses_for_drug",
        kwargs={"drug_id": drug_id, "min_score": min_score},
    )
    return {"task_id": task.id, "status": "queued", "drug_id": drug_id}


@router.post("/generate/all")
async def trigger_generation_all(request: GenerateRequest | None = None):
    """Trigger hypothesis generation for all cancer types."""
    min_score = request.min_score if request else 15.0
    task = celery_app.send_task(
        "app.tasks.generate.generate_all_hypotheses",
        kwargs={"min_score": min_score},
    )
    return {"task_id": task.id, "status": "queued"}


@router.post("/rescore")
async def trigger_rescore(request: RescoreRequest | None = None):
    """Trigger rescoring of all hypotheses."""
    preset_name = request.preset_name if request else None
    task = celery_app.send_task(
        "app.tasks.generate.rescore_hypotheses",
        kwargs={"preset_name": preset_name},
    )
    return {"task_id": task.id, "status": "queued", "preset_name": preset_name}


@router.get("/status/{task_id}")
async def get_generation_status(task_id: str):
    """Check the status of a hypothesis generation task."""
    result = celery_app.AsyncResult(task_id)
    task_state = result.state
    task_result = None

    if result.ready():
        try:
            task_result = result.result
        except Exception:
            task_result = {"error": str(result.result)}

    return {
        "task_id": task_id,
        "status": task_state,
        "result": task_result,
    }


# ------------------------------------------------------------------
# Hypothesis detail (must be after other specific routes)
# ------------------------------------------------------------------


@router.get("/{hypothesis_id}")
async def get_hypothesis(
    hypothesis_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Get a single hypothesis with full evidence breakdown."""
    result = await db.execute(
        select(Hypothesis).where(Hypothesis.id == hypothesis_id)
    )
    hypothesis = result.scalar_one_or_none()
    if not hypothesis:
        raise HTTPException(status_code=404, detail="Hypothesis not found")

    # Get drug and cancer info
    drug_result = await db.execute(
        select(Drug.name, Drug.drugbank_id).where(Drug.id == hypothesis.drug_id)
    )
    drug_row = drug_result.first()

    cancer_result = await db.execute(
        select(CancerType.name, CancerType.tcga_code).where(
            CancerType.id == hypothesis.cancer_type_id
        )
    )
    cancer_row = cancer_result.first()

    # Get evidence
    evidence_result = await db.execute(
        select(HypothesisEvidence).where(
            HypothesisEvidence.hypothesis_id == hypothesis_id
        )
    )
    evidence = evidence_result.scalars().all()

    return {
        **_serialize_hypothesis(hypothesis),
        "mechanism_narrative": hypothesis.mechanism_narrative,
        "reviewer_notes": hypothesis.reviewer_notes,
        "drug": {
            "id": hypothesis.drug_id,
            "name": drug_row[0] if drug_row else None,
            "drugbank_id": drug_row[1] if drug_row else None,
        },
        "cancer_type": {
            "id": hypothesis.cancer_type_id,
            "name": cancer_row[0] if cancer_row else None,
            "tcga_code": cancer_row[1] if cancer_row else None,
        },
        "evidence": [
            {
                "id": ev.id,
                "evidence_type": ev.evidence_type,
                "source_type": ev.source_type,
                "source_id": ev.source_id,
                "description": ev.description,
                "strength": ev.strength,
                "confidence": ev.confidence,
                "raw_data": ev.raw_data,
            }
            for ev in evidence
        ],
        "evidence_count": len(evidence),
    }


@router.get("/{hypothesis_id}/evidence")
async def get_hypothesis_evidence(
    hypothesis_id: int,
    evidence_type: str | None = Query(None, description="Filter by evidence type"),
    db: AsyncSession = Depends(get_db),
):
    """Evidence breakdown for a specific hypothesis."""
    # Verify hypothesis exists
    hyp_result = await db.execute(
        select(Hypothesis.id).where(Hypothesis.id == hypothesis_id)
    )
    if not hyp_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Hypothesis not found")

    query = select(HypothesisEvidence).where(
        HypothesisEvidence.hypothesis_id == hypothesis_id
    )
    if evidence_type:
        query = query.where(HypothesisEvidence.evidence_type == evidence_type)

    result = await db.execute(query)
    evidence = result.scalars().all()

    return {
        "hypothesis_id": hypothesis_id,
        "evidence": [
            {
                "id": ev.id,
                "evidence_type": ev.evidence_type,
                "source_type": ev.source_type,
                "source_id": ev.source_id,
                "description": ev.description,
                "strength": ev.strength,
                "confidence": ev.confidence,
                "raw_data": ev.raw_data,
            }
            for ev in evidence
        ],
        "count": len(evidence),
    }


@router.get("/")
async def list_hypotheses(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    min_score: float = Query(0.0, ge=0, le=100),
    status: str | None = Query(None),
    evidence_strength: str | None = Query(None),
    cancer_type_id: int | None = Query(None),
    drug_id: int | None = Query(None),
    sort_by: str = Query("composite_score", description="composite_score, novelty_score, created_at"),
    db: AsyncSession = Depends(get_db),
):
    """Paginated list of hypotheses with filtering and sorting."""
    query = select(Hypothesis).where(Hypothesis.composite_score >= min_score)
    count_query = select(func.count(Hypothesis.id)).where(
        Hypothesis.composite_score >= min_score
    )

    if status:
        query = query.where(Hypothesis.status == status)
        count_query = count_query.where(Hypothesis.status == status)
    if evidence_strength:
        query = query.where(Hypothesis.evidence_strength == evidence_strength)
        count_query = count_query.where(Hypothesis.evidence_strength == evidence_strength)
    if cancer_type_id:
        query = query.where(Hypothesis.cancer_type_id == cancer_type_id)
        count_query = count_query.where(Hypothesis.cancer_type_id == cancer_type_id)
    if drug_id:
        query = query.where(Hypothesis.drug_id == drug_id)
        count_query = count_query.where(Hypothesis.drug_id == drug_id)

    # Sorting
    if sort_by == "novelty_score":
        query = query.order_by(Hypothesis.novelty_score.desc().nulls_last())
    elif sort_by == "created_at":
        query = query.order_by(Hypothesis.created_at.desc())
    else:
        query = query.order_by(Hypothesis.composite_score.desc())

    # Count
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    # Paginate
    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)
    result = await db.execute(query)
    hypotheses = result.scalars().all()

    return {
        "hypotheses": [_serialize_hypothesis(h) for h in hypotheses],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _serialize_hypothesis(h: Hypothesis) -> dict:
    return {
        "id": h.id,
        "drug_id": h.drug_id,
        "cancer_type_id": h.cancer_type_id,
        "title": h.title,
        "summary": h.summary,
        "composite_score": h.composite_score,
        "evidence_strength": h.evidence_strength,
        "dimension_scores": {
            "pathway_overlap": h.pathway_overlap_score,
            "expression_correlation": h.expression_correlation_score,
            "literature_support": h.literature_support_score,
            "clinical_evidence": h.clinical_evidence_score,
            "safety": h.safety_score,
            "novelty": h.novelty_score,
        },
        "status": h.status,
        "created_at": h.created_at.isoformat() if h.created_at else None,
        "updated_at": h.updated_at.isoformat() if h.updated_at else None,
    }
