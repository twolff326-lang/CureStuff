"""LLM-powered analysis API routes.

Endpoints:
  - POST /hypothesis/{id}/narrative          Generate mechanistic narrative
  - POST /hypothesis/{id}/critique           Generate devil's advocate critique
  - POST /hypothesis/{id}/comparative        Generate comparative analysis
  - POST /hypothesis/{id}/literature         Generate literature synthesis
  - POST /hypothesis/{id}/experiments        Generate experiment design
  - POST /hypothesis/{id}/confidence         Generate confidence assessment
  - POST /hypothesis/{id}/full               Generate all 6 analyses
  - GET  /hypothesis/{id}/analyses           List all analyses for a hypothesis
  - POST /batch/narratives                   Batch narrative generation
  - POST /batch/full                         Batch full analysis generation
  - POST /batch/comparative                  Batch comparative analysis
  - GET  /usage                              LLM usage statistics
  - GET  /status/{task_id}                   Check task status
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.hypothesis import Hypothesis
from app.models.llm_analysis import HypothesisAnalysis
from app.services.llm_analyst import ANALYSIS_TYPES, LLMAnalyst
from app.tasks.celery_app import celery_app

router = APIRouter()

_analyst: LLMAnalyst | None = None


def _get_analyst() -> LLMAnalyst:
    global _analyst
    if _analyst is None:
        _analyst = LLMAnalyst()
    return _analyst


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------


class BatchNarrativeRequest(BaseModel):
    min_score: float = 0.0
    limit: int = 100


class BatchFullRequest(BaseModel):
    min_score: float = 50.0
    limit: int = 20


class BatchComparativeRequest(BaseModel):
    cancer_type_id: int | None = None
    min_score: float = 30.0


class ComparativeRequest(BaseModel):
    compare_ids: list[int] | None = None


# ------------------------------------------------------------------
# Individual analysis endpoints
# ------------------------------------------------------------------


@router.post("/hypothesis/{hypothesis_id}/narrative")
async def generate_narrative(
    hypothesis_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Generate a mechanistic narrative for a hypothesis.

    Uses Claude Opus for high-scoring hypotheses (>= 70), Sonnet otherwise.
    """
    await _verify_hypothesis(hypothesis_id, db)
    analyst = _get_analyst()
    result = await analyst.generate_narrative(hypothesis_id, db)
    return result


@router.post("/hypothesis/{hypothesis_id}/critique")
async def generate_critique(
    hypothesis_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Generate a devil's advocate critique identifying weaknesses."""
    await _verify_hypothesis(hypothesis_id, db)
    analyst = _get_analyst()
    result = await analyst.generate_critique(hypothesis_id, db)
    return result


@router.post("/hypothesis/{hypothesis_id}/comparative")
async def generate_comparative(
    hypothesis_id: int,
    request: ComparativeRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Generate a comparative analysis against similar hypotheses.

    Optionally provide compare_ids to specify which hypotheses to compare against.
    If omitted, auto-selects top 5 from same cancer type.
    """
    await _verify_hypothesis(hypothesis_id, db)
    analyst = _get_analyst()
    compare_ids = request.compare_ids if request else None
    result = await analyst.generate_comparative_analysis(
        hypothesis_id, db, compare_ids=compare_ids
    )
    return result


@router.post("/hypothesis/{hypothesis_id}/literature")
async def generate_literature_synthesis(
    hypothesis_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Synthesize relevant literature into a coherent evidence narrative."""
    await _verify_hypothesis(hypothesis_id, db)
    analyst = _get_analyst()
    result = await analyst.synthesize_literature(hypothesis_id, db)
    return result


@router.post("/hypothesis/{hypothesis_id}/experiments")
async def generate_experiment_design(
    hypothesis_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Design validation experiments for the hypothesis.

    Uses temperature=0.1 for slightly more creative suggestions.
    """
    await _verify_hypothesis(hypothesis_id, db)
    analyst = _get_analyst()
    result = await analyst.design_experiments(hypothesis_id, db)
    return result


@router.post("/hypothesis/{hypothesis_id}/confidence")
async def generate_confidence_assessment(
    hypothesis_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Assess confidence with precedent analysis.

    Incorporates prior narrative and critique analyses if available.
    """
    await _verify_hypothesis(hypothesis_id, db)
    analyst = _get_analyst()
    result = await analyst.assess_confidence(hypothesis_id, db)
    return result


@router.post("/hypothesis/{hypothesis_id}/full")
async def generate_full_analysis(
    hypothesis_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Generate all 6 analysis types for a hypothesis.

    Runs narrative + critique first, then remaining 4 in parallel.
    This is the most comprehensive but also most expensive operation.
    """
    await _verify_hypothesis(hypothesis_id, db)
    analyst = _get_analyst()
    result = await analyst.generate_full_analysis(hypothesis_id, db)
    return result


# ------------------------------------------------------------------
# Read analyses
# ------------------------------------------------------------------


@router.get("/hypothesis/{hypothesis_id}/analyses")
async def list_analyses(
    hypothesis_id: int,
    analysis_type: str | None = Query(None, description="Filter by type"),
    db: AsyncSession = Depends(get_db),
):
    """List all LLM analyses for a hypothesis."""
    await _verify_hypothesis(hypothesis_id, db)

    query = select(HypothesisAnalysis).where(
        HypothesisAnalysis.hypothesis_id == hypothesis_id
    )
    if analysis_type:
        if analysis_type not in ANALYSIS_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid analysis_type. Must be one of: {', '.join(ANALYSIS_TYPES)}",
            )
        query = query.where(HypothesisAnalysis.analysis_type == analysis_type)

    query = query.order_by(HypothesisAnalysis.created_at.desc())
    result = await db.execute(query)
    analyses = result.scalars().all()

    return {
        "hypothesis_id": hypothesis_id,
        "analyses": [
            {
                "id": a.id,
                "analysis_type": a.analysis_type,
                "model_used": a.model_used,
                "content": a.content,
                "input_tokens": a.input_tokens,
                "output_tokens": a.output_tokens,
                "generation_time_seconds": a.generation_time_seconds,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "updated_at": a.updated_at.isoformat() if a.updated_at else None,
            }
            for a in analyses
        ],
        "count": len(analyses),
    }


# ------------------------------------------------------------------
# Batch endpoints (dispatch Celery tasks)
# ------------------------------------------------------------------


@router.post("/batch/narratives")
async def trigger_batch_narratives(request: BatchNarrativeRequest):
    """Trigger batch narrative generation for top hypotheses.

    Dispatches to Celery for background processing.
    """
    task = celery_app.send_task(
        "app.tasks.analyze.generate_narratives_batch",
        kwargs={"min_score": request.min_score, "limit": request.limit},
    )
    return {"task_id": task.id, "status": "queued", "operation": "batch_narratives"}


@router.post("/batch/full")
async def trigger_batch_full_analysis(request: BatchFullRequest):
    """Trigger full analysis generation (all 6 types) for top hypotheses.

    Dispatches to Celery for background processing.
    Each hypothesis generates 6 Claude API calls.
    """
    task = celery_app.send_task(
        "app.tasks.analyze.generate_full_analysis_batch",
        kwargs={"min_score": request.min_score, "limit": request.limit},
    )
    return {"task_id": task.id, "status": "queued", "operation": "batch_full_analysis"}


@router.post("/batch/comparative")
async def trigger_batch_comparative(request: BatchComparativeRequest):
    """Trigger comparative analysis generation grouped by cancer type.

    Dispatches to Celery for background processing.
    """
    kwargs: dict = {"min_score": request.min_score}
    if request.cancer_type_id:
        kwargs["cancer_type_id"] = request.cancer_type_id

    task = celery_app.send_task(
        "app.tasks.analyze.generate_comparative_analyses",
        kwargs=kwargs,
    )
    return {
        "task_id": task.id,
        "status": "queued",
        "operation": "batch_comparative",
    }


# ------------------------------------------------------------------
# Usage stats and task status
# ------------------------------------------------------------------


@router.get("/usage")
async def get_usage_stats(
    days: int = Query(30, ge=1, le=365, description="Number of days to report"),
    db: AsyncSession = Depends(get_db),
):
    """Get LLM usage statistics: calls, tokens, costs, by model and type."""
    analyst = _get_analyst()
    return await analyst.get_usage_stats(db, days=days)


@router.get("/status/{task_id}")
async def get_task_status(task_id: str):
    """Check the status of a running LLM analysis task."""
    result = celery_app.AsyncResult(task_id)
    task_state = result.state
    task_result = None
    progress = None

    if task_state == "PROGRESS" and isinstance(result.info, dict):
        progress = result.info
    elif result.ready():
        try:
            task_result = result.result
        except Exception:
            task_result = {"error": str(result.result)}

    return {
        "task_id": task_id,
        "status": task_state,
        "result": task_result,
        "progress": progress,
    }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _verify_hypothesis(hypothesis_id: int, db: AsyncSession) -> Hypothesis:
    """Verify a hypothesis exists and return it."""
    result = await db.execute(
        select(Hypothesis).where(Hypothesis.id == hypothesis_id)
    )
    hypothesis = result.scalar_one_or_none()
    if not hypothesis:
        raise HTTPException(
            status_code=404, detail=f"Hypothesis {hypothesis_id} not found"
        )
    return hypothesis
