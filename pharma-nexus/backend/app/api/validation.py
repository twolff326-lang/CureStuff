"""Validation API routes for scientific rigor assessment.

Endpoints:
  - GET  /retrospective          Run retrospective validation against known repurposing cases
  - GET  /ablation               Run leave-one-out ablation study on scoring dimensions
  - GET  /calibration            Assess score calibration (predicted vs observed probability)
  - GET  /null-distribution      Generate null distribution and per-hypothesis p-values
  - GET  /ground-truth           View known repurposing cases matched to database
  - GET  /summary                Full validation summary with all metrics
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.validation_framework import ValidationFramework

router = APIRouter()


@router.get("/ground-truth")
async def get_ground_truth(db: AsyncSession = Depends(get_db)):
    """View known drug repurposing cases and how many match our database.

    This is the foundation of validation — curated from FDA approvals,
    RepoDB, and landmark repurposing papers.
    """
    framework = ValidationFramework()
    return await framework.build_ground_truth(db)


@router.get("/retrospective")
async def run_retrospective_validation(db: AsyncSession = Depends(get_db)):
    """Run full retrospective validation.

    Tests whether our scoring system would have identified known drug
    repurposing successes. Returns:
      - Rank recovery: where do known successes rank among all hypotheses?
      - ROC-AUC: discrimination between true positives and negatives
      - PR-AUC: precision-recall (better for imbalanced data)
      - Enrichment at score thresholds
      - Mann-Whitney U test for score separation
    """
    framework = ValidationFramework()
    return await framework.run_retrospective_validation(db)


@router.get("/ablation")
async def run_ablation_study(db: AsyncSession = Depends(get_db)):
    """Run leave-one-dimension-out ablation study.

    For each of the 6 scoring dimensions, measures the impact on ROC-AUC
    when that dimension is removed. Answers: "Which dimensions actually
    contribute to predictive performance?"

    Returns importance ranking and weight recommendations.
    """
    framework = ValidationFramework()
    return await framework.run_ablation_study(db)


@router.get("/calibration")
async def run_calibration_analysis(
    n_bins: int = Query(10, ge=3, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Assess score calibration.

    Answers: "Does a composite score of 70 correspond to ~70% probability
    of being a true repurposing case?"

    Returns calibration curve, Brier score, and expected calibration error.
    """
    framework = ValidationFramework()
    return await framework.run_calibration_analysis(db, n_bins=n_bins)


@router.get("/null-distribution")
async def get_null_distribution(
    n_permutations: int = Query(1000, ge=100, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """Generate null distribution of composite scores.

    Randomly shuffles dimension scores across hypotheses to establish
    what scores look like under the null hypothesis (no real biological
    connection). Each hypothesis gets a p-value against this null.

    Returns null distribution statistics and per-hypothesis p-values.
    """
    framework = ValidationFramework()
    return await framework.generate_null_distribution(
        db, n_permutations=n_permutations
    )


@router.get("/summary")
async def get_validation_summary(db: AsyncSession = Depends(get_db)):
    """Comprehensive validation summary combining all analyses.

    This is the endpoint you show to reviewers. It combines:
      - Ground truth coverage
      - Retrospective validation metrics (ROC-AUC, PR-AUC, rank recovery)
      - Ablation study results (dimension importance)
      - Calibration assessment
      - Statistical interpretation
    """
    framework = ValidationFramework()

    results = {}

    # Run all analyses
    results["ground_truth"] = await framework.build_ground_truth(db)
    results["retrospective"] = await framework.run_retrospective_validation(db)
    results["ablation"] = await framework.run_ablation_study(db)
    results["calibration"] = await framework.run_calibration_analysis(db)

    # Generate overall assessment
    retro = results["retrospective"]
    metrics = retro.get("metrics", {})
    roc = metrics.get("roc", {})
    ablation = results["ablation"]

    assessment = []
    auc_val = roc.get("auc", 0)
    if auc_val >= 0.8:
        assessment.append(f"STRONG: ROC-AUC of {auc_val:.3f} indicates good discrimination between true and false repurposing candidates.")
    elif auc_val >= 0.7:
        assessment.append(f"FAIR: ROC-AUC of {auc_val:.3f} indicates moderate discrimination. Consider optimizing dimension weights.")
    elif auc_val > 0:
        assessment.append(f"WEAK: ROC-AUC of {auc_val:.3f} indicates limited discrimination. Scoring model needs significant improvement.")

    rank_recovery = metrics.get("rank_recovery", {})
    mean_percentile = rank_recovery.get("mean_percentile", 0)
    if mean_percentile >= 80:
        assessment.append(f"Known repurposing cases rank in the top {100-mean_percentile:.0f}% on average — strong rank recovery.")
    elif mean_percentile >= 60:
        assessment.append(f"Known repurposing cases rank in the top {100-mean_percentile:.0f}% on average — moderate rank recovery.")

    if isinstance(ablation, dict) and "importance_ranking" in ablation:
        top_dim = ablation["importance_ranking"][0] if ablation["importance_ranking"] else None
        if top_dim:
            assessment.append(
                f"Most important dimension: {top_dim['dimension']} "
                f"(removing it drops AUC by {top_dim['auc_drop']:.4f})."
            )

    results["overall_assessment"] = " ".join(assessment) if assessment else "Insufficient data for assessment."

    return results
