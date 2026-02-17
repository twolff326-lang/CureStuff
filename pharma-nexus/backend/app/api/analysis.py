"""Gene expression analysis API routes.

Endpoints:
  - GET  /expression/{cancer_type_id}/top-genes       Top over/underexpressed genes
  - GET  /expression/{cancer_type_id}/differential     Full DE results (paginated)
  - GET  /expression/drug/{drug_id}/cancer/{cid}       Drug-cancer expression score
  - GET  /pathway-activity/{cancer_type_id}            All pathway activities for a cancer
  - GET  /pathway-activity/{cancer_type_id}/{pid}      Single pathway activity detail
  - GET  /coexpression/{gene}/{cancer_type_id}         Co-expression network
  - GET  /synthetic-lethality/drug/{did}/cancer/{cid}  Synthetic lethality assessment
  - POST /compute/differential-expression              Trigger DE computation
  - POST /compute/pathway-activity                     Trigger pathway activity computation
  - POST /compute/expression-scores                    Trigger drug expression scoring
  - POST /run                                          Trigger full analysis pipeline
  - GET  /status/{task_id}                             Check analysis task status
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.cancer_type import CancerMolecularProfile
from app.services.expression_analyzer import ExpressionAnalyzer
from app.tasks.celery_app import celery_app

router = APIRouter()

_analyzer: ExpressionAnalyzer | None = None


def _get_analyzer() -> ExpressionAnalyzer:
    global _analyzer
    if _analyzer is None:
        _analyzer = ExpressionAnalyzer()
    return _analyzer


# ------------------------------------------------------------------
# Expression queries
# ------------------------------------------------------------------


@router.get("/expression/{cancer_type_id}/top-genes")
async def get_top_genes(
    cancer_type_id: int,
    direction: str = Query("both", description="up, down, or both"),
    limit: int = Query(50, ge=1, le=500),
    min_zscore: float = Query(0.0, description="Minimum absolute z-score"),
    min_frequency: float = Query(0.0, description="Minimum frequency percent"),
    db: AsyncSession = Depends(get_db),
):
    """Top overexpressed or underexpressed genes in a cancer type."""
    query = select(CancerMolecularProfile).where(
        CancerMolecularProfile.cancer_type_id == cancer_type_id,
    )

    if direction == "up":
        query = query.where(
            CancerMolecularProfile.alteration_type == "overexpression"
        )
    elif direction == "down":
        query = query.where(
            CancerMolecularProfile.alteration_type == "underexpression"
        )
    else:
        query = query.where(
            CancerMolecularProfile.alteration_type.in_(
                ["overexpression", "underexpression"]
            )
        )

    if min_zscore > 0:
        query = query.where(
            func.abs(CancerMolecularProfile.expression_zscore) >= min_zscore
        )
    if min_frequency > 0:
        query = query.where(
            CancerMolecularProfile.frequency_percent >= min_frequency
        )

    # Sort by absolute z-score descending
    query = query.order_by(
        func.abs(CancerMolecularProfile.expression_zscore).desc().nulls_last()
    ).limit(limit)

    result = await db.execute(query)
    profiles = result.scalars().all()

    return {
        "cancer_type_id": cancer_type_id,
        "direction": direction,
        "genes": [
            {
                "gene_symbol": p.gene_symbol,
                "alteration_type": p.alteration_type,
                "frequency_percent": p.frequency_percent,
                "median_expression": p.median_expression,
                "expression_zscore": p.expression_zscore,
                "source": p.source,
            }
            for p in profiles
        ],
        "count": len(profiles),
    }


@router.get("/expression/{cancer_type_id}/differential")
async def get_differential_expression(
    cancer_type_id: int,
    significant_only: bool = Query(False, description="Only significant results"),
    sort_by: str = Query("zscore", description="zscore or frequency"),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Full differential expression results for a cancer type (paginated)."""
    query = select(CancerMolecularProfile).where(
        CancerMolecularProfile.cancer_type_id == cancer_type_id,
        CancerMolecularProfile.alteration_type.in_(
            ["overexpression", "underexpression"]
        ),
    )
    count_query = select(func.count(CancerMolecularProfile.id)).where(
        CancerMolecularProfile.cancer_type_id == cancer_type_id,
        CancerMolecularProfile.alteration_type.in_(
            ["overexpression", "underexpression"]
        ),
    )

    if significant_only:
        query = query.where(CancerMolecularProfile.source == "differential_expression")
        count_query = count_query.where(
            CancerMolecularProfile.source == "differential_expression"
        )

    if sort_by == "frequency":
        query = query.order_by(
            CancerMolecularProfile.frequency_percent.desc().nulls_last()
        )
    else:
        query = query.order_by(
            func.abs(CancerMolecularProfile.expression_zscore).desc().nulls_last()
        )

    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0

    offset = (page - 1) * per_page
    query = query.offset(offset).limit(per_page)
    result = await db.execute(query)
    profiles = result.scalars().all()

    return {
        "cancer_type_id": cancer_type_id,
        "results": [
            {
                "gene_symbol": p.gene_symbol,
                "alteration_type": p.alteration_type,
                "frequency_percent": p.frequency_percent,
                "median_expression": p.median_expression,
                "expression_zscore": p.expression_zscore,
                "source": p.source,
            }
            for p in profiles
        ],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ------------------------------------------------------------------
# Drug expression scoring
# ------------------------------------------------------------------


@router.get("/expression/drug/{drug_id}/cancer/{cancer_type_id}")
async def get_drug_expression_score(
    drug_id: int,
    cancer_type_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Expression compatibility score for a drug-cancer pair."""
    analyzer = _get_analyzer()
    return await analyzer.score_target_expression(drug_id, cancer_type_id, db)


# ------------------------------------------------------------------
# Pathway activity
# ------------------------------------------------------------------


@router.get("/pathway-activity/{cancer_type_id}")
async def get_pathway_activities(
    cancer_type_id: int,
    direction: str | None = Query(None, description="activated, suppressed, or neutral"),
    min_score: int = Query(0, ge=0, le=100),
    sort_by: str = Query("activity_score", description="activity_score or mean_zscore"),
    limit: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """All pathway activity scores for a cancer type.

    Reads from the expression_score_cache table.
    """
    from app.models.expression_cache import ExpressionScoreCache

    query = select(ExpressionScoreCache).where(
        ExpressionScoreCache.cache_type == "pathway_activity",
        ExpressionScoreCache.entity_id_2 == cancer_type_id,
    )

    if min_score > 0:
        query = query.where(ExpressionScoreCache.score >= min_score)

    query = query.order_by(ExpressionScoreCache.score.desc()).limit(limit)

    result = await db.execute(query)
    entries = result.scalars().all()

    activities = []
    for entry in entries:
        details = entry.details or {}
        entry_direction = details.get("direction", "unknown")
        if direction and entry_direction != direction:
            continue
        activities.append(details)

    return {
        "cancer_type_id": cancer_type_id,
        "pathways": activities,
        "count": len(activities),
    }


@router.get("/pathway-activity/{cancer_type_id}/{pathway_id}")
async def get_pathway_activity_detail(
    cancer_type_id: int,
    pathway_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Detailed pathway activity for a specific pathway in a cancer type."""
    analyzer = _get_analyzer()
    return await analyzer.compute_pathway_activity(
        cancer_type_id, pathway_id, db
    )


# ------------------------------------------------------------------
# Co-expression
# ------------------------------------------------------------------


@router.get("/coexpression/{gene_symbol}/{cancer_type_id}")
async def get_coexpression_network(
    gene_symbol: str,
    cancer_type_id: int,
    threshold: float = Query(0.7, ge=0.3, le=1.0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Co-expression network for a gene in a cancer type."""
    analyzer = _get_analyzer()
    correlations = await analyzer.find_coexpressed_genes(
        gene_symbol, cancer_type_id, db,
        threshold=threshold, top_n=limit,
    )
    return {
        "gene_symbol": gene_symbol,
        "cancer_type_id": cancer_type_id,
        "threshold": threshold,
        "correlations": correlations,
        "count": len(correlations),
    }


# ------------------------------------------------------------------
# Synthetic lethality
# ------------------------------------------------------------------


@router.get("/synthetic-lethality/drug/{drug_id}/cancer/{cancer_type_id}")
async def get_synthetic_lethality(
    drug_id: int,
    cancer_type_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Synthetic lethality potential assessment for a drug-cancer pair."""
    analyzer = _get_analyzer()
    return await analyzer.check_synthetic_lethality_potential(
        drug_id, cancer_type_id, db
    )


# ------------------------------------------------------------------
# Compute triggers (dispatch Celery tasks)
# ------------------------------------------------------------------


class ComputeRequest(BaseModel):
    cancer_type_id: int | None = None


@router.post("/compute/differential-expression")
async def trigger_differential_expression(request: ComputeRequest):
    """Trigger differential expression computation for all or one cancer type."""
    kwargs = {}
    if request.cancer_type_id:
        kwargs["cancer_type_id"] = request.cancer_type_id

    task = celery_app.send_task(
        "app.tasks.analyze.compute_all_differential_expression",
        kwargs=kwargs,
    )
    return {"task_id": task.id, "status": "queued"}


@router.post("/compute/pathway-activity")
async def trigger_pathway_activity(request: ComputeRequest):
    """Trigger pathway activity computation."""
    kwargs = {}
    if request.cancer_type_id:
        kwargs["cancer_type_id"] = request.cancer_type_id

    task = celery_app.send_task(
        "app.tasks.analyze.compute_pathway_activities",
        kwargs=kwargs,
    )
    return {"task_id": task.id, "status": "queued"}


@router.post("/compute/expression-scores")
async def trigger_expression_scores(request: ComputeRequest):
    """Trigger drug expression score computation."""
    kwargs = {}
    if request.cancer_type_id:
        kwargs["cancer_type_id"] = request.cancer_type_id

    task = celery_app.send_task(
        "app.tasks.analyze.compute_drug_expression_scores",
        kwargs=kwargs,
    )
    return {"task_id": task.id, "status": "queued"}


@router.post("/run")
async def trigger_full_analysis(request: ComputeRequest):
    """Trigger full expression analysis pipeline (DE + scoring + pathway activity)."""
    kwargs = {}
    if request.cancer_type_id:
        kwargs["cancer_type_id"] = request.cancer_type_id

    task = celery_app.send_task(
        "app.tasks.analyze.run_full_expression_analysis",
        kwargs=kwargs,
    )
    return {"task_id": task.id, "status": "queued"}


@router.get("/status/{task_id}")
async def get_analysis_status(task_id: str):
    """Check the status of a running analysis task."""
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
