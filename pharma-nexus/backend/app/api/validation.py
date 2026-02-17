"""API endpoints for validation, benchmarking, and outcome tracking.

Endpoints:
  POST /api/validation/seed          — Seed ground truth cases
  POST /api/validation/match         — Match cases to database records
  POST /api/validation/benchmark     — Run benchmark against ground truth
  POST /api/validation/calibrate     — Optimize scoring weights
  GET  /api/validation/sensitivity   — Weight sensitivity analysis
  GET  /api/validation/cases         — List ground truth cases
  GET  /api/validation/metrics       — Outcome summary metrics
  POST /api/hypotheses/{id}/outcome  — Record hypothesis outcome
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.validation import HypothesisOutcome, ValidationCase
from app.services.validation import ValidationService

router = APIRouter()

_service: ValidationService | None = None


def _get_service() -> ValidationService:
    global _service
    if _service is None:
        _service = ValidationService()
    return _service


# ------------------------------------------------------------------
# Ground truth management
# ------------------------------------------------------------------


@router.post("/seed")
async def seed_ground_truth(
    replace: bool = Query(False, description="Replace existing curated cases"),
    db: AsyncSession = Depends(get_db),
):
    """Seed the database with curated ground truth drug repurposing cases."""
    svc = _get_service()
    result = await svc.seed_ground_truth(db, replace=replace)
    return result


@router.post("/match")
async def match_cases(db: AsyncSession = Depends(get_db)):
    """Match ground truth cases to existing Drug and CancerType records."""
    svc = _get_service()
    result = await svc.match_cases_to_db(db)
    return result


@router.get("/cases")
async def list_cases(
    outcome: str | None = Query(None, description="Filter by outcome"),
    matched_only: bool = Query(False, description="Only show matched cases"),
    db: AsyncSession = Depends(get_db),
):
    """List all ground truth validation cases."""
    query = select(ValidationCase)
    if outcome:
        query = query.where(ValidationCase.outcome == outcome)
    if matched_only:
        query = query.where(
            ValidationCase.matched_drug_id.isnot(None),
            ValidationCase.matched_cancer_type_id.isnot(None),
        )
    query = query.order_by(ValidationCase.outcome, ValidationCase.drug_name)

    result = await db.execute(query)
    cases = result.scalars().all()

    return {
        "total": len(cases),
        "cases": [
            {
                "id": c.id,
                "drug_name": c.drug_name,
                "drug_drugbank_id": c.drug_drugbank_id,
                "cancer_name": c.cancer_name,
                "cancer_tcga_code": c.cancer_tcga_code,
                "outcome": c.outcome,
                "outcome_detail": c.outcome_detail,
                "fda_approved": c.fda_approved,
                "approval_year": c.approval_year,
                "max_trial_phase": c.max_trial_phase,
                "matched_drug_id": c.matched_drug_id,
                "matched_cancer_type_id": c.matched_cancer_type_id,
                "matched_hypothesis_id": c.matched_hypothesis_id,
                "predicted_composite_score": c.predicted_composite_score,
                "predicted_strength": c.predicted_strength,
                "dimension_scores_snapshot": c.dimension_scores_snapshot,
            }
            for c in cases
        ],
    }


# ------------------------------------------------------------------
# Benchmarking
# ------------------------------------------------------------------


@router.post("/benchmark")
async def run_benchmark(
    db: AsyncSession = Depends(get_db),
):
    """Score all matched ground truth cases and compute precision metrics.

    Returns per-case scores, precision@k, and separation between
    successes and failures.
    """
    svc = _get_service()
    result = await svc.run_benchmark(db)
    return result


# ------------------------------------------------------------------
# Calibration
# ------------------------------------------------------------------


class CalibrateRequest(BaseModel):
    method: str = "logistic"


@router.post("/calibrate")
async def calibrate_weights(
    body: CalibrateRequest,
    db: AsyncSession = Depends(get_db),
):
    """Optimize scoring weights using logistic regression or grid search.

    Requires at least 10 labeled cases (ground truth + hypothesis outcomes).
    Returns optimized weights and accuracy comparison.
    """
    svc = _get_service()
    result = await svc.calibrate_weights(db, method=body.method)
    return result


@router.get("/sensitivity")
async def sensitivity_analysis(
    perturbation: float = Query(
        0.05, ge=0.01, le=0.20, description="Weight perturbation amount"
    ),
    db: AsyncSession = Depends(get_db),
):
    """Measure how sensitive the top-20 ranking is to weight changes.

    For each dimension, nudges the weight up by `perturbation` and measures
    how many positions change in the top-20 hypothesis ranking.
    """
    svc = _get_service()
    result = await svc.sensitivity_analysis(db, perturbation=perturbation)
    return result


# ------------------------------------------------------------------
# Outcome tracking
# ------------------------------------------------------------------


class OutcomeRequest(BaseModel):
    outcome: str
    notes: str | None = None
    reviewer: str | None = None
    supporting_evidence: dict | None = None


@router.post("/outcomes/{hypothesis_id}")
async def record_outcome(
    hypothesis_id: int,
    body: OutcomeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Record a researcher's outcome assessment for a hypothesis.

    Valid outcomes: validated, invalidated, promising, inconclusive.
    """
    svc = _get_service()
    result = await svc.record_outcome(
        hypothesis_id=hypothesis_id,
        outcome=body.outcome,
        db=db,
        notes=body.notes,
        reviewer=body.reviewer,
        supporting_evidence=body.supporting_evidence,
    )
    return result


@router.get("/metrics")
async def outcome_metrics(db: AsyncSession = Depends(get_db)):
    """Summary statistics for all recorded hypothesis outcomes."""
    svc = _get_service()
    return await svc.get_outcome_summary(db)
