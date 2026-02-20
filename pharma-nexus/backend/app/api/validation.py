"""Validation API routes for scientific rigor assessment.

Endpoints:
  - GET  /retrospective          Run retrospective validation against known repurposing cases
  - GET  /ablation               Run leave-one-out ablation study on scoring dimensions
  - GET  /calibration            Assess score calibration (predicted vs observed probability)
  - GET  /null-distribution      Generate null distribution and per-hypothesis p-values
  - GET  /ground-truth           View known repurposing cases matched to database
  - GET  /negative-controls      View known drug repurposing failures matched to database
  - GET  /positive-negative      Validate using curated positive AND negative controls
  - GET  /temporal               Temporal cross-validation (train pre-2010, test 2010+)
  - GET  /benchmarks             Compare full model against baseline predictors
  - GET  /sensitivity            Assess robustness to scoring weight perturbations
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

    For each of the 11 scoring dimensions, measures the impact on ROC-AUC
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


@router.get("/negative-controls")
async def get_negative_controls(db: AsyncSession = Depends(get_db)):
    """View known drug repurposing failures matched to our database.

    These are curated from terminated Phase 2/3 clinical trials and
    withdrawn indications. Serves as explicit negative controls for
    validation — the system should score these lower than known successes.
    """
    framework = ValidationFramework()
    return await framework.build_negative_controls(db)


@router.get("/positive-negative")
async def run_positive_negative_validation(db: AsyncSession = Depends(get_db)):
    """Validate using both curated positive and negative controls.

    Unlike /retrospective which treats all non-positive as negative,
    this uses explicitly curated failure cases for cleaner discrimination
    metrics including Cohen's d effect size.
    """
    framework = ValidationFramework()
    return await framework.run_positive_negative_validation(db)


@router.get("/temporal")
async def run_temporal_validation(db: AsyncSession = Depends(get_db)):
    """Temporal cross-validation: train on pre-2010, test on 2010+ approvals.

    Simulates prospective prediction: "Would our system have predicted
    recent drug approvals using only knowledge of older ones?"
    """
    framework = ValidationFramework()
    return await framework.run_temporal_validation(db)


@router.get("/benchmarks")
async def run_benchmark_baselines(db: AsyncSession = Depends(get_db)):
    """Compare full multi-dimensional model against baseline predictors.

    Tests: random baseline, single-dimension predictors (each dimension
    alone), and majority-class baseline. Demonstrates that multi-dimensional
    scoring adds value beyond any single signal.
    """
    framework = ValidationFramework()
    return await framework.run_benchmark_baselines(db)


@router.get("/sensitivity")
async def run_sensitivity_analysis(
    n_perturbations: int = Query(200, ge=50, le=2000),
    perturbation_scale: float = Query(0.1, ge=0.01, le=0.5),
    db: AsyncSession = Depends(get_db),
):
    """Assess robustness of scoring results to weight perturbations.

    Randomly perturbs dimension weights and measures ROC-AUC variance.
    A robust model shows low AUC variance, meaning results don't depend
    heavily on exact weight choices.
    """
    framework = ValidationFramework()
    return await framework.run_sensitivity_analysis(
        db,
        n_perturbations=n_perturbations,
        perturbation_scale=perturbation_scale,
    )


@router.get("/summary")
async def get_validation_summary(db: AsyncSession = Depends(get_db)):
    """Comprehensive validation summary combining all analyses.

    This is the endpoint you show to reviewers. It combines:
      - Ground truth coverage (positive and negative controls)
      - Retrospective validation metrics (ROC-AUC, PR-AUC, rank recovery)
      - Positive vs negative control discrimination
      - Temporal cross-validation (prospective simulation)
      - Benchmark baselines (model vs single dimensions)
      - Ablation study results (dimension importance)
      - Calibration assessment
      - Sensitivity analysis (weight robustness)
      - Statistical interpretation
    """
    framework = ValidationFramework()

    results = {}

    # Build ground truth and negative controls once, pass to sub-analyses
    # to avoid redundant DB queries (~262 queries per build_ground_truth call)
    gt = await framework.build_ground_truth(db)
    nc = await framework.build_negative_controls(db)
    results["ground_truth"] = gt
    results["negative_controls"] = nc

    # Run all analyses, passing pre-built ground truth where applicable
    results["retrospective"] = await framework.run_retrospective_validation(db, ground_truth=gt)
    results["positive_negative"] = await framework.run_positive_negative_validation(
        db, ground_truth=gt, negative_controls=nc
    )
    results["temporal"] = await framework.run_temporal_validation(db)
    results["benchmarks"] = await framework.run_benchmark_baselines(db, ground_truth=gt)
    results["ablation"] = await framework.run_ablation_study(db, ground_truth=gt)
    results["calibration"] = await framework.run_calibration_analysis(db, ground_truth=gt)
    results["sensitivity"] = await framework.run_sensitivity_analysis(db, ground_truth=gt)

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

    # Positive vs negative control assessment
    pos_neg = results.get("positive_negative", {})
    pos_neg_metrics = pos_neg.get("metrics", {})
    pos_neg_auc = pos_neg_metrics.get("roc_auc", 0)
    if pos_neg_auc > 0:
        assessment.append(
            f"Positive vs negative control AUC: {pos_neg_auc:.3f}."
        )
        cohens_d = pos_neg_metrics.get("cohens_d")
        if cohens_d is not None:
            assessment.append(
                f"Score separation effect size (Cohen's d): {cohens_d:.2f} "
                f"({pos_neg_metrics.get('effect_size_interpretation', '')})."
            )

    # Temporal validation assessment
    temporal = results.get("temporal", {})
    temporal_metrics = temporal.get("metrics", {})
    prospective_auc = temporal_metrics.get("prospective_roc_auc", 0)
    if prospective_auc > 0:
        assessment.append(
            f"Prospective temporal AUC: {prospective_auc:.3f} — "
            f"{'system would have predicted recent approvals' if prospective_auc >= 0.65 else 'limited prospective power'}."
        )

    # Benchmark comparison
    benchmarks = results.get("benchmarks", {})
    comparison = benchmarks.get("comparison", {})
    if comparison.get("multi_dim_adds_value"):
        assessment.append(
            f"Multi-dimensional model outperforms best single dimension "
            f"({comparison.get('best_single_dimension', '?')}) by "
            f"{comparison.get('full_vs_best_single_dim', 0):.3f} AUC."
        )

    # Sensitivity assessment
    sensitivity = results.get("sensitivity", {})
    robustness = sensitivity.get("robustness", {})
    robustness_interp = robustness.get("interpretation", "")
    if robustness_interp:
        assessment.append(f"Weight robustness: {robustness_interp}.")

    results["overall_assessment"] = " ".join(assessment) if assessment else "Insufficient data for assessment."

    return results
