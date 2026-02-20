"""Retrospective validation framework for drug repurposing hypothesis scoring.

This module provides the scientific rigor that transforms Pharma Nexus from
a tool into a validated method. It answers the fundamental question:

    "Does our scoring actually predict real-world repurposing outcomes?"

Components:
  1. Ground Truth Assembly — Known successful drug repurposing cases from
     RepoDB, FDA approval history, and literature-confirmed cases.
  2. Retrospective Validation — Rank recovery analysis: given known successes,
     where does our scoring rank them among all candidates?
  3. ROC/AUC Metrics — Area under the receiver operating characteristic curve
     measuring discrimination between true positives and negatives.
  4. Ablation Studies — Leave-one-dimension-out analysis to quantify each
     scoring dimension's contribution to predictive performance.
  5. Calibration Analysis — Does a score of 70 correspond to ~70% likelihood
     of being a true positive?

References:
  - Brown & Patel (2017). Drug repurposing: A review of current approaches.
  - Pushpakom et al. (2019). Drug repurposing: progress, challenges and
    recommendations. Nature Reviews Drug Discovery.
"""

import logging
from typing import Any

import numpy as np
from scipy import stats as scipy_stats
from sklearn.calibration import calibration_curve
from sklearn.metrics import (
    auc,
    average_precision_score,
    precision_recall_curve,
    roc_auc_score,
    roc_curve,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerType
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import Drug, DrugTarget, TrialDrug
from app.models.hypothesis import Hypothesis
from app.models.target import Target
from app.services.scoring_config import DIMENSIONS, ScoringConfig

logger = logging.getLogger(__name__)


# ===================================================================
# GROUND TRUTH: Known drug repurposing successes
# ===================================================================

# Curated from FDA approvals, RepoDB, and landmark repurposing papers.
# Format: (drug_name_fragment, cancer_name_fragment, evidence_level)
# evidence_level: "fda_approved" | "phase3_success" | "phase2_positive" | "preclinical_validated"
KNOWN_REPURPOSING_CASES = [
    # FDA-approved repurposed cancer drugs
    ("thalidomide", "multiple myeloma", "fda_approved"),
    ("lenalidomide", "multiple myeloma", "fda_approved"),
    ("methotrexate", "breast", "fda_approved"),
    ("aspirin", "colorectal", "phase3_success"),
    ("celecoxib", "colorectal", "phase2_positive"),
    ("metformin", "breast", "phase2_positive"),
    ("metformin", "colorectal", "phase2_positive"),
    ("metformin", "endometrial", "phase2_positive"),
    ("propranolol", "melanoma", "phase2_positive"),
    ("itraconazole", "lung", "phase2_positive"),
    ("itraconazole", "prostate", "phase2_positive"),
    ("disulfiram", "glioblastoma", "preclinical_validated"),
    ("chloroquine", "glioblastoma", "phase2_positive"),
    ("valproic acid", "leukemia", "phase2_positive"),
    ("all-trans retinoic acid", "acute promyelocytic leukemia", "fda_approved"),
    ("rapamycin", "renal cell", "fda_approved"),
    ("everolimus", "breast", "fda_approved"),
    ("everolimus", "renal cell", "fda_approved"),
    ("nivolumab", "melanoma", "fda_approved"),
    ("pembrolizumab", "lung", "fda_approved"),
    ("rituximab", "lymphoma", "fda_approved"),
    ("bevacizumab", "colorectal", "fda_approved"),
    ("bevacizumab", "lung", "fda_approved"),
    ("bevacizumab", "glioblastoma", "fda_approved"),
    ("sorafenib", "hepatocellular", "fda_approved"),
    ("sorafenib", "renal cell", "fda_approved"),
    ("imatinib", "gastrointestinal stromal", "fda_approved"),
    ("tamoxifen", "breast", "fda_approved"),
    ("zoledronic acid", "breast", "phase3_success"),
    ("statins", "colorectal", "preclinical_validated"),
    ("mebendazole", "glioblastoma", "preclinical_validated"),
    ("niclosamide", "colorectal", "preclinical_validated"),
    ("auranofin", "leukemia", "preclinical_validated"),
    ("nelfinavir", "cervical", "phase2_positive"),
    ("ritonavir", "kaposi sarcoma", "phase2_positive"),
    ("vorinostat", "cutaneous t-cell lymphoma", "fda_approved"),
    ("arsenic trioxide", "acute promyelocytic leukemia", "fda_approved"),
    ("dexamethasone", "multiple myeloma", "fda_approved"),
    ("bortezomib", "mantle cell lymphoma", "fda_approved"),
]


class ValidationFramework:
    """Retrospective validation and performance assessment for the scoring system.

    Answers: "If we had used Pharma Nexus in the past, would it have
    identified the drug repurposing successes we now know about?"
    """

    def __init__(self):
        self.config = ScoringConfig()

    # ==================================================================
    # Ground Truth Assembly
    # ==================================================================

    async def build_ground_truth(
        self, db: AsyncSession
    ) -> dict[str, Any]:
        """Match known repurposing cases to drug-cancer pairs in our database.

        Returns:
            dict with matched_cases, unmatched_cases, coverage statistics
        """
        matched = []
        unmatched = []

        for drug_fragment, cancer_fragment, evidence_level in KNOWN_REPURPOSING_CASES:
            # Find matching drug
            drug_result = await db.execute(
                select(Drug.id, Drug.name).where(
                    Drug.name.ilike(f"%{drug_fragment}%")
                ).limit(1)
            )
            drug_row = drug_result.first()

            # Find matching cancer type
            cancer_result = await db.execute(
                select(CancerType.id, CancerType.name).where(
                    CancerType.name.ilike(f"%{cancer_fragment}%")
                ).limit(1)
            )
            cancer_row = cancer_result.first()

            if drug_row and cancer_row:
                matched.append({
                    "drug_id": drug_row[0],
                    "drug_name": drug_row[1],
                    "cancer_type_id": cancer_row[0],
                    "cancer_name": cancer_row[1],
                    "evidence_level": evidence_level,
                    "ground_truth_label": 1,
                })
            else:
                unmatched.append({
                    "drug_fragment": drug_fragment,
                    "cancer_fragment": cancer_fragment,
                    "evidence_level": evidence_level,
                    "drug_found": drug_row is not None,
                    "cancer_found": cancer_row is not None,
                })

        # Evidence level weights for weighted metrics
        level_weights = {
            "fda_approved": 1.0,
            "phase3_success": 0.8,
            "phase2_positive": 0.5,
            "preclinical_validated": 0.3,
        }

        for case in matched:
            case["weight"] = level_weights.get(case["evidence_level"], 0.3)

        return {
            "matched_cases": matched,
            "unmatched_cases": unmatched,
            "n_matched": len(matched),
            "n_unmatched": len(unmatched),
            "coverage": len(matched) / len(KNOWN_REPURPOSING_CASES) if KNOWN_REPURPOSING_CASES else 0,
            "by_evidence_level": {
                level: sum(1 for c in matched if c["evidence_level"] == level)
                for level in level_weights
            },
        }

    # ==================================================================
    # Retrospective Validation
    # ==================================================================

    async def run_retrospective_validation(
        self, db: AsyncSession
    ) -> dict[str, Any]:
        """Full retrospective validation pipeline.

        1. Build ground truth from known repurposing cases
        2. Get all scored hypotheses from the database
        3. Label each hypothesis as positive (known success) or negative
        4. Compute rank recovery, ROC-AUC, PR-AUC
        5. Return detailed performance metrics

        Returns:
            dict with comprehensive validation results
        """
        # Step 1: Ground truth
        ground_truth = await self.build_ground_truth(db)
        positive_pairs = {
            (c["drug_id"], c["cancer_type_id"])
            for c in ground_truth["matched_cases"]
        }

        if not positive_pairs:
            return {
                "error": "No ground truth cases matched database entries",
                "ground_truth": ground_truth,
            }

        # Step 2: Get all hypotheses
        result = await db.execute(
            select(Hypothesis).order_by(Hypothesis.composite_score.desc())
        )
        all_hypotheses = result.scalars().all()

        if not all_hypotheses:
            return {
                "error": "No hypotheses scored yet",
                "ground_truth": ground_truth,
            }

        # Step 3: Label
        labels = []
        scores = []
        ranks = []
        positive_ranks = []
        case_details = []

        for rank, hyp in enumerate(all_hypotheses, 1):
            pair = (hyp.drug_id, hyp.cancer_type_id)
            is_positive = pair in positive_pairs
            labels.append(1 if is_positive else 0)
            scores.append(hyp.composite_score)
            ranks.append(rank)

            if is_positive:
                positive_ranks.append(rank)
                # Find the ground truth case for weight
                case = next(
                    (c for c in ground_truth["matched_cases"]
                     if c["drug_id"] == hyp.drug_id
                     and c["cancer_type_id"] == hyp.cancer_type_id),
                    None,
                )
                case_details.append({
                    "drug_id": hyp.drug_id,
                    "cancer_type_id": hyp.cancer_type_id,
                    "title": hyp.title,
                    "composite_score": hyp.composite_score,
                    "rank": rank,
                    "percentile": round((1 - rank / len(all_hypotheses)) * 100, 1),
                    "evidence_level": case["evidence_level"] if case else "unknown",
                    "weight": case["weight"] if case else 0.3,
                    "dimension_scores": {
                        "pathway_overlap": hyp.pathway_overlap_score,
                        "expression_correlation": hyp.expression_correlation_score,
                        "literature_support": hyp.literature_support_score,
                        "clinical_evidence": hyp.clinical_evidence_score,
                        "safety": hyp.safety_score,
                        "novelty": hyp.novelty_score,
                    },
                })

        # Step 4: Compute metrics
        labels_arr = np.array(labels)
        scores_arr = np.array(scores)
        n_total = len(all_hypotheses)
        n_positive = int(labels_arr.sum())

        metrics = {}

        # Rank recovery metrics
        if positive_ranks:
            metrics["rank_recovery"] = {
                "mean_rank": round(float(np.mean(positive_ranks)), 1),
                "median_rank": round(float(np.median(positive_ranks)), 1),
                "mean_percentile": round(
                    float(np.mean([1 - r / n_total for r in positive_ranks])) * 100, 1
                ),
                "top_1pct": sum(1 for r in positive_ranks if r <= n_total * 0.01),
                "top_5pct": sum(1 for r in positive_ranks if r <= n_total * 0.05),
                "top_10pct": sum(1 for r in positive_ranks if r <= n_total * 0.10),
                "top_25pct": sum(1 for r in positive_ranks if r <= n_total * 0.25),
                "n_positive": n_positive,
                "n_total": n_total,
            }

            # Mann-Whitney U test: are positive scores significantly higher?
            pos_scores = scores_arr[labels_arr == 1]
            neg_scores = scores_arr[labels_arr == 0]
            if len(pos_scores) > 0 and len(neg_scores) > 0:
                u_stat, u_pval = scipy_stats.mannwhitneyu(
                    pos_scores, neg_scores, alternative="greater"
                )
                metrics["rank_recovery"]["mann_whitney_u"] = float(u_stat)
                metrics["rank_recovery"]["mann_whitney_p"] = float(u_pval)
                metrics["rank_recovery"]["pos_mean_score"] = round(float(pos_scores.mean()), 2)
                metrics["rank_recovery"]["neg_mean_score"] = round(float(neg_scores.mean()), 2)

        # ROC-AUC
        if n_positive > 0 and n_positive < n_total:
            try:
                fpr, tpr, thresholds = roc_curve(labels_arr, scores_arr)
                roc_auc = float(auc(fpr, tpr))
                metrics["roc"] = {
                    "auc": round(roc_auc, 4),
                    "interpretation": _interpret_auc(roc_auc),
                    "fpr": fpr.tolist()[:50],  # subsample for API response size
                    "tpr": tpr.tolist()[:50],
                }
            except Exception as e:
                logger.warning("ROC-AUC computation failed: %s", e)

            # PR-AUC (more informative for imbalanced data)
            try:
                precision, recall, _ = precision_recall_curve(labels_arr, scores_arr)
                pr_auc = float(average_precision_score(labels_arr, scores_arr))
                metrics["precision_recall"] = {
                    "auc": round(pr_auc, 4),
                    "baseline": round(n_positive / n_total, 4),
                    "lift": round(pr_auc / (n_positive / n_total), 2) if n_positive > 0 else 0,
                }
            except Exception as e:
                logger.warning("PR-AUC computation failed: %s", e)

        # Enrichment at various thresholds
        thresholds = [25, 50, 75]
        enrichment = {}
        for thresh in thresholds:
            above = labels_arr[scores_arr >= thresh]
            if len(above) > 0:
                precision_at_thresh = float(above.sum()) / len(above)
                baseline = n_positive / n_total
                enrichment[f"score_ge_{thresh}"] = {
                    "n_hypotheses": int(len(above)),
                    "n_true_positives": int(above.sum()),
                    "precision": round(precision_at_thresh, 4),
                    "enrichment_fold": round(
                        precision_at_thresh / baseline, 2
                    ) if baseline > 0 else 0.0,
                }
        metrics["enrichment_at_thresholds"] = enrichment

        return {
            "ground_truth_summary": {
                "n_matched": ground_truth["n_matched"],
                "n_unmatched": ground_truth["n_unmatched"],
                "coverage": ground_truth["coverage"],
                "by_evidence_level": ground_truth["by_evidence_level"],
            },
            "dataset": {
                "n_hypotheses": n_total,
                "n_positive": n_positive,
                "n_negative": n_total - n_positive,
                "positive_rate": round(n_positive / n_total, 6) if n_total > 0 else 0,
            },
            "metrics": metrics,
            "case_details": sorted(case_details, key=lambda x: x["rank"]),
        }

    # ==================================================================
    # Ablation Studies
    # ==================================================================

    async def run_ablation_study(
        self, db: AsyncSession
    ) -> dict[str, Any]:
        """Leave-one-dimension-out ablation study.

        For each of the 6 scoring dimensions, re-compute composite scores
        with that dimension zeroed out and measure the impact on ROC-AUC.

        Answers: "Which dimensions actually matter for prediction?"

        Returns:
            dict with per-dimension ablation results and importance ranking
        """
        ground_truth = await self.build_ground_truth(db)
        positive_pairs = {
            (c["drug_id"], c["cancer_type_id"])
            for c in ground_truth["matched_cases"]
        }

        if not positive_pairs:
            return {"error": "No ground truth cases matched"}

        # Get all hypotheses with dimension scores
        result = await db.execute(select(Hypothesis))
        all_hypotheses = result.scalars().all()

        if len(all_hypotheses) < 10:
            return {"error": "Too few hypotheses for meaningful ablation"}

        # Get active weights
        weights = await self.config.get_active_weights(db)

        # Labels
        labels = np.array([
            1 if (h.drug_id, h.cancer_type_id) in positive_pairs else 0
            for h in all_hypotheses
        ])

        n_positive = int(labels.sum())
        if n_positive == 0 or n_positive == len(labels):
            return {"error": "Need both positive and negative cases for ablation"}

        # Baseline: full model AUC
        baseline_scores = np.array([h.composite_score for h in all_hypotheses])
        baseline_auc = float(roc_auc_score(labels, baseline_scores))

        # Ablation for each dimension
        ablation_results = {}
        for dim in DIMENSIONS:
            # Recompute composite with this dimension zeroed out
            ablated_weights = weights.copy()
            removed_weight = ablated_weights.pop(dim)

            # Redistribute removed weight proportionally
            remaining_total = sum(ablated_weights.values())
            if remaining_total > 0:
                for k in ablated_weights:
                    ablated_weights[k] *= 1.0 / remaining_total
            else:
                continue

            ablated_scores = []
            for h in all_hypotheses:
                dim_scores = {
                    "pathway_overlap": {"score": h.pathway_overlap_score or 0},
                    "expression_correlation": {"score": h.expression_correlation_score or 0},
                    "literature_support": {"score": h.literature_support_score or 0},
                    "clinical_evidence": {"score": h.clinical_evidence_score or 0},
                    "safety": {"score": h.safety_score or 0},
                    "novelty": {"score": h.novelty_score or 0},
                }
                # Zero out the ablated dimension
                dim_scores[dim] = {"score": 0}
                score = self.config.compute_composite_score(dim_scores, ablated_weights)
                ablated_scores.append(score)

            ablated_arr = np.array(ablated_scores)
            ablated_auc = float(roc_auc_score(labels, ablated_arr))

            auc_drop = baseline_auc - ablated_auc
            ablation_results[dim] = {
                "ablated_auc": round(ablated_auc, 4),
                "auc_drop": round(auc_drop, 4),
                "relative_importance": round(
                    auc_drop / baseline_auc * 100, 2
                ) if baseline_auc > 0 else 0.0,
                "original_weight": weights[dim],
            }

        # Rank dimensions by importance (AUC drop)
        importance_ranking = sorted(
            ablation_results.items(),
            key=lambda x: x[1]["auc_drop"],
            reverse=True,
        )

        return {
            "baseline_auc": round(baseline_auc, 4),
            "baseline_interpretation": _interpret_auc(baseline_auc),
            "n_hypotheses": len(all_hypotheses),
            "n_positive": n_positive,
            "ablation_results": ablation_results,
            "importance_ranking": [
                {"dimension": dim, **data}
                for dim, data in importance_ranking
            ],
            "recommendation": _generate_weight_recommendation(
                ablation_results, weights
            ),
        }

    # ==================================================================
    # Calibration Analysis
    # ==================================================================

    async def run_calibration_analysis(
        self, db: AsyncSession, n_bins: int = 10
    ) -> dict[str, Any]:
        """Assess score calibration: does a score of X mean X% probability?

        Returns:
            dict with calibration curve data, Brier score, expected calibration error
        """
        ground_truth = await self.build_ground_truth(db)
        positive_pairs = {
            (c["drug_id"], c["cancer_type_id"])
            for c in ground_truth["matched_cases"]
        }

        if not positive_pairs:
            return {"error": "No ground truth cases matched"}

        result = await db.execute(select(Hypothesis))
        all_hypotheses = result.scalars().all()

        labels = np.array([
            1 if (h.drug_id, h.cancer_type_id) in positive_pairs else 0
            for h in all_hypotheses
        ])
        scores = np.array([h.composite_score / 100.0 for h in all_hypotheses])

        n_positive = int(labels.sum())
        if n_positive < 2:
            return {"error": "Need at least 2 positive cases for calibration"}

        # Calibration curve
        try:
            prob_true, prob_pred = calibration_curve(
                labels, scores, n_bins=n_bins, strategy="uniform"
            )
        except Exception:
            prob_true = np.array([])
            prob_pred = np.array([])

        # Brier score (mean squared error of probability estimates)
        brier = float(np.mean((scores - labels) ** 2))

        # Expected Calibration Error
        ece = 0.0
        if len(prob_true) > 0:
            bin_sizes = np.histogram(scores, bins=n_bins, range=(0, 1))[0]
            total = bin_sizes.sum()
            for i in range(len(prob_true)):
                if i < len(bin_sizes) and total > 0:
                    weight = bin_sizes[i] / total
                    ece += weight * abs(prob_true[i] - prob_pred[i])

        return {
            "n_hypotheses": len(all_hypotheses),
            "n_positive": n_positive,
            "brier_score": round(brier, 6),
            "expected_calibration_error": round(float(ece), 4),
            "interpretation": _interpret_calibration(brier),
            "calibration_curve": {
                "predicted_probability": prob_pred.tolist(),
                "observed_frequency": prob_true.tolist(),
            },
        }

    # ==================================================================
    # Null Distribution Generation
    # ==================================================================

    async def generate_null_distribution(
        self,
        db: AsyncSession,
        n_permutations: int = 1000,
        seed: int = 42,
    ) -> dict[str, Any]:
        """Generate null distribution of composite scores via random drug-cancer pairing.

        Randomly shuffles drug-cancer assignments and recomputes scores to
        establish what scores look like under the null hypothesis (no real
        biological connection).

        Returns:
            dict with null distribution statistics and per-hypothesis p-values
        """
        result = await db.execute(
            select(
                Hypothesis.id,
                Hypothesis.composite_score,
                Hypothesis.pathway_overlap_score,
                Hypothesis.expression_correlation_score,
                Hypothesis.literature_support_score,
                Hypothesis.clinical_evidence_score,
                Hypothesis.safety_score,
                Hypothesis.novelty_score,
                Hypothesis.causal_dependency_score,
                Hypothesis.gnn_link_score,
                Hypothesis.mutation_context_score,
                Hypothesis.polypharmacology_score,
            )
        )
        hypotheses = result.all()

        if len(hypotheses) < 10:
            return {"error": "Too few hypotheses for null distribution"}

        weights = await self.config.get_active_weights(db)

        # Extract dimension score vectors
        dim_vectors = {
            dim: np.array([
                getattr_from_row(h, dim) for h in hypotheses
            ])
            for dim in DIMENSIONS
        }

        rng = np.random.default_rng(seed)
        null_scores = np.zeros(n_permutations)

        for i in range(n_permutations):
            # Randomly sample one dimension score from each dimension independently
            composite = 0.0
            for dim in DIMENSIONS:
                idx = rng.integers(0, len(hypotheses))
                composite += weights[dim] * dim_vectors[dim][idx]
            null_scores[i] = min(composite, 100.0)

        # Compute per-hypothesis p-values
        observed_scores = np.array([h[1] for h in hypotheses])
        p_values = []
        for obs in observed_scores:
            p = float(np.sum(null_scores >= obs) / n_permutations)
            p = max(p, 1.0 / (n_permutations + 1))
            p_values.append(round(p, 6))

        return {
            "null_distribution": {
                "mean": round(float(null_scores.mean()), 2),
                "std": round(float(null_scores.std()), 2),
                "median": round(float(np.median(null_scores)), 2),
                "percentiles": {
                    "5th": round(float(np.percentile(null_scores, 5)), 2),
                    "25th": round(float(np.percentile(null_scores, 25)), 2),
                    "75th": round(float(np.percentile(null_scores, 75)), 2),
                    "95th": round(float(np.percentile(null_scores, 95)), 2),
                    "99th": round(float(np.percentile(null_scores, 99)), 2),
                },
                "n_permutations": n_permutations,
            },
            "hypothesis_p_values": [
                {"hypothesis_id": h[0], "score": h[1], "p_value": pv}
                for h, pv in zip(hypotheses, p_values)
            ],
            "significance_summary": {
                "n_significant_005": sum(1 for p in p_values if p < 0.05),
                "n_significant_001": sum(1 for p in p_values if p < 0.01),
                "n_total": len(p_values),
            },
        }


# ===================================================================
# Helpers
# ===================================================================


def getattr_from_row(row, dim: str) -> float:
    """Extract dimension score from a SQLAlchemy row tuple."""
    dim_index = {
        "pathway_overlap": 2,
        "expression_correlation": 3,
        "literature_support": 4,
        "clinical_evidence": 5,
        "safety": 6,
        "novelty": 7,
        "causal_dependency": 8,
        "gnn_link": 9,
        "mutation_context": 10,
        "polypharmacology": 11,
    }
    idx = dim_index.get(dim)
    if idx is None:
        return 0.0
    val = row[idx]
    return float(val) if val is not None else 0.0


def _interpret_auc(auc_value: float) -> str:
    """Interpret ROC-AUC value with standard thresholds."""
    if auc_value >= 0.9:
        return "excellent discrimination"
    if auc_value >= 0.8:
        return "good discrimination"
    if auc_value >= 0.7:
        return "fair discrimination"
    if auc_value >= 0.6:
        return "poor discrimination"
    return "no discrimination (random)"


def _interpret_calibration(brier: float) -> str:
    """Interpret Brier score."""
    if brier < 0.05:
        return "well calibrated"
    if brier < 0.15:
        return "moderately calibrated"
    if brier < 0.25:
        return "poorly calibrated"
    return "very poorly calibrated (scores do not reflect probabilities)"


def _generate_weight_recommendation(
    ablation_results: dict[str, dict],
    current_weights: dict[str, float],
) -> str:
    """Generate a human-readable recommendation based on ablation results."""
    important = [
        (dim, data["auc_drop"])
        for dim, data in ablation_results.items()
        if data["auc_drop"] > 0.01
    ]
    important.sort(key=lambda x: x[1], reverse=True)

    harmful = [
        (dim, data["auc_drop"])
        for dim, data in ablation_results.items()
        if data["auc_drop"] < -0.01
    ]

    parts = []
    if important:
        top = important[0]
        parts.append(
            f"Most important dimension: {top[0]} (removing it drops AUC by {top[1]:.4f}). "
            f"Consider increasing its weight from {current_weights.get(top[0], 0):.2f}."
        )

    if harmful:
        worst = harmful[0]
        parts.append(
            f"Potentially harmful dimension: {worst[0]} (removing it IMPROVES AUC by "
            f"{abs(worst[1]):.4f}). Consider reducing its weight or investigating data quality."
        )

    if not important and not harmful:
        parts.append(
            "No single dimension dramatically affects performance. "
            "Consider whether the scoring model captures the right signals."
        )

    return " ".join(parts)
