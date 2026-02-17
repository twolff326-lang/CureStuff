"""Validation service for benchmarking and calibrating the scoring system.

Provides:
  - Ground truth seeding with known drug repurposing cases
  - Benchmark scoring of ground truth against current system
  - Precision/recall metrics at various thresholds
  - Weight calibration via logistic regression against outcomes
  - Outcome recording for hypothesis feedback loop
"""

import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drug import Drug
from app.models.cancer_type import CancerType
from app.models.hypothesis import Hypothesis
from app.models.validation import HypothesisOutcome, ValidationCase
from app.services.evidence_scorer import EvidenceScorer
from app.services.scoring_config import DIMENSIONS, ScoringConfig

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Ground truth: known drug repurposing cases for oncology
# ------------------------------------------------------------------

GROUND_TRUTH_CASES: list[dict[str, Any]] = [
    # === SUCCESSES (FDA-approved repurposings) ===
    {
        "drug_name": "Thalidomide",
        "drug_drugbank_id": "DB01041",
        "cancer_name": "Multiple Myeloma",
        "cancer_tcga_code": None,
        "outcome": "success",
        "outcome_detail": "FDA approved 2006 for newly diagnosed MM in combo with dexamethasone. "
        "Originally sedative, withdrawn for teratogenicity, repurposed via anti-angiogenic mechanism.",
        "fda_approved": True,
        "approval_year": 2006,
        "max_trial_phase": "Phase 4",
        "reference_pmids": ["12538462", "17105813"],
        "source": "curated",
    },
    {
        "drug_name": "Lenalidomide",
        "drug_drugbank_id": "DB00480",
        "cancer_name": "Multiple Myeloma",
        "cancer_tcga_code": None,
        "outcome": "success",
        "outcome_detail": "FDA approved 2005. Thalidomide analogue with improved safety profile. "
        "Standard of care for newly diagnosed and relapsed/refractory MM.",
        "fda_approved": True,
        "approval_year": 2005,
        "max_trial_phase": "Phase 4",
        "reference_pmids": ["17482179", "25482145"],
        "source": "curated",
    },
    {
        "drug_name": "Methotrexate",
        "drug_drugbank_id": "DB00563",
        "cancer_name": "Breast Cancer",
        "cancer_tcga_code": "BRCA",
        "outcome": "success",
        "outcome_detail": "Originally for leukemia (1947), repurposed as part of CMF regimen for "
        "breast cancer. Also widely used for rheumatoid arthritis.",
        "fda_approved": True,
        "approval_year": 1971,
        "max_trial_phase": "Phase 4",
        "reference_pmids": ["386228"],
        "source": "curated",
    },
    {
        "drug_name": "Rituximab",
        "drug_drugbank_id": "DB00073",
        "cancer_name": "Diffuse Large B-Cell Lymphoma",
        "cancer_tcga_code": "DLBC",
        "outcome": "success",
        "outcome_detail": "Anti-CD20 antibody. Originally approved for follicular lymphoma (1997), "
        "R-CHOP became standard of care for DLBCL.",
        "fda_approved": True,
        "approval_year": 1997,
        "max_trial_phase": "Phase 4",
        "reference_pmids": ["12075054", "18048386"],
        "source": "curated",
    },
    {
        "drug_name": "Imatinib",
        "drug_drugbank_id": "DB00619",
        "cancer_name": "Gastrointestinal Stromal Tumor",
        "cancer_tcga_code": None,
        "outcome": "success",
        "outcome_detail": "Originally for CML (BCR-ABL), repurposed for GIST via KIT inhibition. "
        "FDA approved for GIST 2002. Landmark case of molecular-target repurposing.",
        "fda_approved": True,
        "approval_year": 2002,
        "max_trial_phase": "Phase 4",
        "reference_pmids": ["11856794", "12181401"],
        "source": "curated",
    },
    {
        "drug_name": "Celecoxib",
        "drug_drugbank_id": "DB00482",
        "cancer_name": "Colorectal Cancer",
        "cancer_tcga_code": "COAD",
        "outcome": "success",
        "outcome_detail": "COX-2 inhibitor approved for FAP polyp reduction (2000). "
        "Strong evidence for CRC chemoprevention. Anti-inflammatory mechanism.",
        "fda_approved": True,
        "approval_year": 2000,
        "max_trial_phase": "Phase 3",
        "reference_pmids": ["10963602", "16377592"],
        "source": "curated",
    },
    {
        "drug_name": "Raloxifene",
        "drug_drugbank_id": "DB00481",
        "cancer_name": "Breast Cancer",
        "cancer_tcga_code": "BRCA",
        "outcome": "success",
        "outcome_detail": "SERM originally for osteoporosis. FDA approved for breast cancer risk "
        "reduction in postmenopausal women (2007). STAR trial vs tamoxifen.",
        "fda_approved": True,
        "approval_year": 2007,
        "max_trial_phase": "Phase 4",
        "reference_pmids": ["16760442", "19546404"],
        "source": "curated",
    },
    {
        "drug_name": "Bevacizumab",
        "drug_drugbank_id": "DB00112",
        "cancer_name": "Glioblastoma",
        "cancer_tcga_code": "GBM",
        "outcome": "success",
        "outcome_detail": "Anti-VEGF antibody. Originally for CRC (2004). FDA approved for "
        "recurrent GBM (2009) based on response rate data.",
        "fda_approved": True,
        "approval_year": 2009,
        "max_trial_phase": "Phase 3",
        "reference_pmids": ["19188680", "24101040"],
        "source": "curated",
    },
    {
        "drug_name": "Everolimus",
        "drug_drugbank_id": "DB01590",
        "cancer_name": "Breast Cancer",
        "cancer_tcga_code": "BRCA",
        "outcome": "success",
        "outcome_detail": "mTOR inhibitor. Originally immunosuppressant for transplant. "
        "FDA approved for HR+/HER2- advanced breast cancer (2012) with exemestane.",
        "fda_approved": True,
        "approval_year": 2012,
        "max_trial_phase": "Phase 3",
        "reference_pmids": ["22149876"],
        "source": "curated",
    },
    {
        "drug_name": "Niraparib",
        "drug_drugbank_id": "DB12793",
        "cancer_name": "Ovarian Cancer",
        "cancer_tcga_code": "OV",
        "outcome": "success",
        "outcome_detail": "PARP inhibitor. FDA approved for maintenance therapy in recurrent "
        "ovarian cancer (2017). Works via synthetic lethality in BRCA-mutated tumors.",
        "fda_approved": True,
        "approval_year": 2017,
        "max_trial_phase": "Phase 3",
        "reference_pmids": ["27717299"],
        "source": "curated",
    },
    {
        "drug_name": "Tamoxifen",
        "drug_drugbank_id": "DB00675",
        "cancer_name": "Breast Cancer",
        "cancer_tcga_code": "BRCA",
        "outcome": "success",
        "outcome_detail": "Originally developed as contraceptive. Repurposed as first targeted "
        "therapy for ER+ breast cancer. FDA approved 1977.",
        "fda_approved": True,
        "approval_year": 1977,
        "max_trial_phase": "Phase 4",
        "reference_pmids": ["280153", "9440735"],
        "source": "curated",
    },
    {
        "drug_name": "All-trans Retinoic Acid",
        "drug_drugbank_id": "DB00755",
        "cancer_name": "Acute Promyelocytic Leukemia",
        "cancer_tcga_code": "LAML",
        "outcome": "success",
        "outcome_detail": "Vitamin A derivative. Repurposed for APL differentiation therapy. "
        "Changed APL from most fatal to most curable leukemia.",
        "fda_approved": True,
        "approval_year": 1995,
        "max_trial_phase": "Phase 4",
        "reference_pmids": ["1850498", "21385065"],
        "source": "curated",
    },
    # === PROMISING / UNDER INVESTIGATION ===
    {
        "drug_name": "Metformin",
        "drug_drugbank_id": "DB00331",
        "cancer_name": "Breast Cancer",
        "cancer_tcga_code": "BRCA",
        "outcome": "ongoing",
        "outcome_detail": "Diabetes drug. Epidemiological evidence of reduced cancer risk. "
        "Multiple Phase 2/3 trials ongoing. AMPK/mTOR pathway mechanism.",
        "fda_approved": False,
        "max_trial_phase": "Phase 3",
        "reference_pmids": ["20543250", "26014232"],
        "source": "curated",
    },
    {
        "drug_name": "Aspirin",
        "drug_drugbank_id": "DB00945",
        "cancer_name": "Colorectal Cancer",
        "cancer_tcga_code": "COAD",
        "outcome": "ongoing",
        "outcome_detail": "Strong epidemiological evidence for CRC risk reduction. "
        "COX-dependent and independent anti-tumor mechanisms. USPSTF recommends for CRC prevention.",
        "fda_approved": False,
        "max_trial_phase": "Phase 3",
        "reference_pmids": ["23345087", "26314551"],
        "source": "curated",
    },
    {
        "drug_name": "Propranolol",
        "drug_drugbank_id": "DB00571",
        "cancer_name": "Breast Cancer",
        "cancer_tcga_code": "BRCA",
        "outcome": "ongoing",
        "outcome_detail": "Beta-blocker. Retrospective studies show reduced breast cancer "
        "recurrence and mortality. Beta-adrenergic signaling promotes metastasis.",
        "fda_approved": False,
        "max_trial_phase": "Phase 2",
        "reference_pmids": ["21263100", "27867044"],
        "source": "curated",
    },
    {
        "drug_name": "Chloroquine",
        "drug_drugbank_id": "DB00608",
        "cancer_name": "Glioblastoma",
        "cancer_tcga_code": "GBM",
        "outcome": "ongoing",
        "outcome_detail": "Antimalarial. Autophagy inhibitor. Phase 2 trials showed improved "
        "survival when combined with standard therapy.",
        "fda_approved": False,
        "max_trial_phase": "Phase 2",
        "reference_pmids": ["16818686", "24867226"],
        "source": "curated",
    },
    {
        "drug_name": "Disulfiram",
        "drug_drugbank_id": "DB00822",
        "cancer_name": "Glioblastoma",
        "cancer_tcga_code": "GBM",
        "outcome": "ongoing",
        "outcome_detail": "Alcohol aversion drug. With copper, shows anti-tumor activity. "
        "ALDH inhibition, proteasome inhibition. Phase 2 trials ongoing.",
        "fda_approved": False,
        "max_trial_phase": "Phase 2",
        "reference_pmids": ["28321153", "31601496"],
        "source": "curated",
    },
    # === FAILURES (promising but failed in trials) ===
    {
        "drug_name": "Bevacizumab",
        "drug_drugbank_id": "DB00112",
        "cancer_name": "Breast Cancer",
        "cancer_tcga_code": "BRCA",
        "outcome": "failure",
        "outcome_detail": "Anti-VEGF initially got accelerated approval for metastatic breast "
        "cancer (2008) but FDA revoked it (2011) after confirmatory trials showed no OS benefit.",
        "fda_approved": False,
        "max_trial_phase": "Phase 3",
        "reference_pmids": ["21532565", "22149888"],
        "source": "curated",
    },
    {
        "drug_name": "Sorafenib",
        "drug_drugbank_id": "DB00398",
        "cancer_name": "Breast Cancer",
        "cancer_tcga_code": "BRCA",
        "outcome": "failure",
        "outcome_detail": "Multi-kinase inhibitor approved for RCC and HCC. Multiple Phase 3 "
        "trials in breast cancer failed to show survival benefit.",
        "fda_approved": False,
        "max_trial_phase": "Phase 3",
        "reference_pmids": ["24166332"],
        "source": "curated",
    },
    {
        "drug_name": "Cimetidine",
        "drug_drugbank_id": "DB00501",
        "cancer_name": "Colorectal Cancer",
        "cancer_tcga_code": "COAD",
        "outcome": "failure",
        "outcome_detail": "H2-receptor antagonist. Early studies suggested benefit in CRC "
        "but larger controlled trials failed to confirm. Promising preclinical, failed clinical.",
        "fda_approved": False,
        "max_trial_phase": "Phase 3",
        "reference_pmids": ["11932907", "15696091"],
        "source": "curated",
    },
    {
        "drug_name": "Lovastatin",
        "drug_drugbank_id": "DB00227",
        "cancer_name": "Breast Cancer",
        "cancer_tcga_code": "BRCA",
        "outcome": "failure",
        "outcome_detail": "Statin. Preclinical anti-cancer effects via mevalonate pathway. "
        "Clinical trials showed no significant benefit in breast cancer outcomes.",
        "fda_approved": False,
        "max_trial_phase": "Phase 2",
        "reference_pmids": ["17579213", "22198402"],
        "source": "curated",
    },
    {
        "drug_name": "Nelfinavir",
        "drug_drugbank_id": "DB00220",
        "cancer_name": "Lung Adenocarcinoma",
        "cancer_tcga_code": "LUAD",
        "outcome": "failure",
        "outcome_detail": "HIV protease inhibitor. Showed anti-tumor activity preclinically "
        "via AKT/PI3K inhibition. Phase 2 trials in NSCLC did not show benefit.",
        "fda_approved": False,
        "max_trial_phase": "Phase 2",
        "reference_pmids": ["17325028", "24862461"],
        "source": "curated",
    },
    {
        "drug_name": "Mebendazole",
        "drug_drugbank_id": "DB00643",
        "cancer_name": "Glioblastoma",
        "cancer_tcga_code": "GBM",
        "outcome": "partial",
        "outcome_detail": "Anthelmintic. Case reports of response in GBM. Phase 1 trials show "
        "safety but efficacy unproven. Tubulin binding mechanism.",
        "fda_approved": False,
        "max_trial_phase": "Phase 1",
        "reference_pmids": ["25031039", "28122488"],
        "source": "curated",
    },
]


class ValidationService:
    """Benchmarks the scoring system against known drug repurposing cases
    and provides tools for weight calibration.
    """

    def __init__(self):
        self.scorer = EvidenceScorer()
        self.config = ScoringConfig()

    # ------------------------------------------------------------------
    # Ground truth seeding
    # ------------------------------------------------------------------

    async def seed_ground_truth(
        self, db: AsyncSession, replace: bool = False
    ) -> dict[str, Any]:
        """Load curated ground truth cases into the database.

        Args:
            replace: If True, delete existing curated cases before inserting.
        """
        if replace:
            await db.execute(
                ValidationCase.__table__.delete().where(
                    ValidationCase.source == "curated"
                )
            )

        inserted = 0
        skipped = 0
        for case in GROUND_TRUTH_CASES:
            # Check if already exists
            existing = await db.execute(
                select(ValidationCase).where(
                    ValidationCase.drug_name == case["drug_name"],
                    ValidationCase.cancer_name == case["cancer_name"],
                    ValidationCase.source == "curated",
                )
            )
            if existing.scalar_one_or_none():
                skipped += 1
                continue

            vc = ValidationCase(**case)
            db.add(vc)
            inserted += 1

        await db.flush()
        return {
            "inserted": inserted,
            "skipped": skipped,
            "total_cases": len(GROUND_TRUTH_CASES),
        }

    async def match_cases_to_db(self, db: AsyncSession) -> dict[str, Any]:
        """Match validation cases to existing Drug and CancerType records."""
        result = await db.execute(select(ValidationCase))
        cases = result.scalars().all()

        matched = 0
        unmatched = 0
        for case in cases:
            # Match drug
            if case.drug_drugbank_id and not case.matched_drug_id:
                drug_result = await db.execute(
                    select(Drug.id).where(
                        Drug.drugbank_id == case.drug_drugbank_id
                    )
                )
                drug_id = drug_result.scalar_one_or_none()
                if drug_id:
                    case.matched_drug_id = drug_id

            # Match cancer type
            if case.cancer_tcga_code and not case.matched_cancer_type_id:
                ct_result = await db.execute(
                    select(CancerType.id).where(
                        CancerType.tcga_code == case.cancer_tcga_code
                    )
                )
                ct_id = ct_result.scalar_one_or_none()
                if ct_id:
                    case.matched_cancer_type_id = ct_id

            # Try name-based matching as fallback
            if not case.matched_drug_id:
                drug_result = await db.execute(
                    select(Drug.id).where(
                        func.lower(Drug.name) == case.drug_name.lower()
                    )
                )
                drug_id = drug_result.scalar_one_or_none()
                if drug_id:
                    case.matched_drug_id = drug_id

            if not case.matched_cancer_type_id:
                ct_result = await db.execute(
                    select(CancerType.id).where(
                        func.lower(CancerType.name).contains(
                            case.cancer_name.lower()
                        )
                    )
                )
                ct_id = ct_result.scalar_one_or_none()
                if ct_id:
                    case.matched_cancer_type_id = ct_id

            # Match hypothesis if both drug and cancer matched
            if case.matched_drug_id and case.matched_cancer_type_id:
                hyp_result = await db.execute(
                    select(Hypothesis.id).where(
                        Hypothesis.drug_id == case.matched_drug_id,
                        Hypothesis.cancer_type_id == case.matched_cancer_type_id,
                    )
                )
                hyp_id = hyp_result.scalar_one_or_none()
                if hyp_id:
                    case.matched_hypothesis_id = hyp_id

            if case.matched_drug_id and case.matched_cancer_type_id:
                matched += 1
            else:
                unmatched += 1

        await db.flush()
        return {"matched": matched, "unmatched": unmatched, "total": len(cases)}

    # ------------------------------------------------------------------
    # Benchmark scoring
    # ------------------------------------------------------------------

    async def run_benchmark(
        self,
        db: AsyncSession,
        weights: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Score all matched validation cases and compute precision metrics.

        Returns scoring results and precision@k for various thresholds.
        """
        if weights is None:
            weights = await self.config.get_active_weights(db)

        # Get all validation cases with matched drug + cancer
        result = await db.execute(
            select(ValidationCase).where(
                ValidationCase.matched_drug_id.isnot(None),
                ValidationCase.matched_cancer_type_id.isnot(None),
            )
        )
        cases = result.scalars().all()

        if not cases:
            return {
                "error": "No matched validation cases. Run match_cases_to_db first.",
                "cases_scored": 0,
            }

        scored_cases = []
        for case in cases:
            try:
                dim_scores = await self.scorer.score_all_dimensions(
                    case.matched_drug_id, case.matched_cancer_type_id, db
                )
                composite = self.config.compute_composite_score(dim_scores, weights)
                strength = self.config.determine_evidence_strength(composite)

                # Update the case with latest scores
                case.predicted_composite_score = composite
                case.predicted_strength = strength
                case.dimension_scores_snapshot = {
                    dim: {"score": d["score"]} for dim, d in dim_scores.items()
                }

                scored_cases.append({
                    "id": case.id,
                    "drug_name": case.drug_name,
                    "cancer_name": case.cancer_name,
                    "outcome": case.outcome,
                    "fda_approved": case.fda_approved,
                    "composite_score": composite,
                    "predicted_strength": strength,
                    "dimension_scores": {
                        dim: d["score"] for dim, d in dim_scores.items()
                    },
                })
            except Exception as e:
                logger.error(
                    "Failed to score validation case %d (%s / %s): %s",
                    case.id, case.drug_name, case.cancer_name, e,
                )
                scored_cases.append({
                    "id": case.id,
                    "drug_name": case.drug_name,
                    "cancer_name": case.cancer_name,
                    "outcome": case.outcome,
                    "error": str(e),
                })

        await db.flush()

        # Compute metrics
        metrics = self._compute_metrics(scored_cases)

        return {
            "cases_scored": len(scored_cases),
            "weights_used": weights,
            "metrics": metrics,
            "cases": scored_cases,
        }

    def _compute_metrics(
        self, scored_cases: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Compute precision, recall, and calibration metrics."""
        # Filter to cases with scores (no errors)
        valid = [c for c in scored_cases if "composite_score" in c]
        if not valid:
            return {"error": "No valid scored cases"}

        # Binary labels: success/ongoing = positive, failure = negative
        positives = [c for c in valid if c["outcome"] in ("success", "ongoing", "partial")]
        negatives = [c for c in valid if c["outcome"] == "failure"]

        # Sort by predicted score descending
        valid_sorted = sorted(valid, key=lambda x: x["composite_score"], reverse=True)

        # Precision@k
        precision_at_k = {}
        for k in [5, 10, 15, 20]:
            if k > len(valid_sorted):
                continue
            top_k = valid_sorted[:k]
            true_pos_in_k = sum(
                1 for c in top_k if c["outcome"] in ("success", "ongoing", "partial")
            )
            precision_at_k[f"precision@{k}"] = round(true_pos_in_k / k, 3)

        # Score distribution by outcome
        outcome_scores: dict[str, list[float]] = {}
        for c in valid:
            outcome_scores.setdefault(c["outcome"], []).append(c["composite_score"])

        score_stats = {}
        for outcome, scores in outcome_scores.items():
            score_stats[outcome] = {
                "count": len(scores),
                "mean": round(sum(scores) / len(scores), 1),
                "min": round(min(scores), 1),
                "max": round(max(scores), 1),
            }

        # Separation quality: can the system distinguish successes from failures?
        separation = None
        if positives and negatives:
            pos_scores = [c["composite_score"] for c in positives]
            neg_scores = [c["composite_score"] for c in negatives]
            pos_mean = sum(pos_scores) / len(pos_scores)
            neg_mean = sum(neg_scores) / len(neg_scores)
            separation = {
                "positive_mean_score": round(pos_mean, 1),
                "negative_mean_score": round(neg_mean, 1),
                "gap": round(pos_mean - neg_mean, 1),
                "correctly_ordered": round(
                    sum(1 for p in pos_scores for n in neg_scores if p > n)
                    / max(len(pos_scores) * len(neg_scores), 1),
                    3,
                ),
            }

        return {
            "total_scored": len(valid),
            "positives": len(positives),
            "negatives": len(negatives),
            **precision_at_k,
            "score_distribution_by_outcome": score_stats,
            "separation": separation,
        }

    # ------------------------------------------------------------------
    # Weight calibration
    # ------------------------------------------------------------------

    async def calibrate_weights(
        self,
        db: AsyncSession,
        method: str = "logistic",
    ) -> dict[str, Any]:
        """Optimize scoring weights using validation outcomes.

        Uses scored validation cases or hypothesis outcomes to find weights
        that best separate successes from failures.

        Args:
            method: "logistic" (logistic regression) or "grid" (grid search)
        """
        # Collect labeled data from validation cases
        result = await db.execute(
            select(ValidationCase).where(
                ValidationCase.matched_drug_id.isnot(None),
                ValidationCase.matched_cancer_type_id.isnot(None),
                ValidationCase.dimension_scores_snapshot.isnot(None),
            )
        )
        cases = result.scalars().all()

        # Also collect from hypothesis outcomes
        outcome_result = await db.execute(
            select(HypothesisOutcome, Hypothesis)
            .join(Hypothesis, HypothesisOutcome.hypothesis_id == Hypothesis.id)
        )
        outcome_rows = outcome_result.all()

        # Build feature matrix and labels
        features = []
        labels = []

        for case in cases:
            snapshot = case.dimension_scores_snapshot or {}
            if len(snapshot) < 6:
                continue
            row = [snapshot.get(dim, {}).get("score", 0) for dim in DIMENSIONS]
            features.append(row)
            labels.append(1 if case.outcome in ("success", "ongoing", "partial") else 0)

        for outcome, hyp in outcome_rows:
            row = [
                hyp.pathway_overlap_score or 0,
                hyp.expression_correlation_score or 0,
                hyp.literature_support_score or 0,
                hyp.clinical_evidence_score or 0,
                hyp.safety_score or 0,
                hyp.novelty_score or 0,
            ]
            features.append(row)
            labels.append(
                1 if outcome.outcome in ("validated", "promising") else 0
            )

        if len(features) < 10:
            return {
                "error": f"Need at least 10 labeled cases for calibration, have {len(features)}",
                "cases_available": len(features),
            }

        X = np.array(features)
        y = np.array(labels)

        if method == "logistic":
            return self._calibrate_logistic(X, y)
        elif method == "grid":
            return self._calibrate_grid(X, y)
        else:
            return {"error": f"Unknown calibration method: {method}"}

    def _calibrate_logistic(
        self, X: np.ndarray, y: np.ndarray
    ) -> dict[str, Any]:
        """Fit logistic regression to find optimal dimension weights."""
        # Normalize features to 0-1
        X_norm = X / 100.0

        # Simple gradient descent logistic regression (no sklearn dependency)
        n_features = X_norm.shape[1]
        weights = np.ones(n_features) / n_features  # Start uniform
        lr = 0.1
        n_iter = 1000

        for _ in range(n_iter):
            # Forward pass
            z = X_norm @ weights
            pred = 1 / (1 + np.exp(-z))
            pred = np.clip(pred, 1e-7, 1 - 1e-7)

            # Gradient
            grad = X_norm.T @ (pred - y) / len(y)
            weights -= lr * grad

            # Project weights to valid range (positive, sum to 1)
            weights = np.maximum(weights, 0.01)
            weights = weights / weights.sum()

        # Compute accuracy with optimized weights
        composite = X_norm @ weights * 100
        predictions = composite >= 50  # threshold at 50
        accuracy = np.mean(predictions == y)

        # Compute accuracy with current (uniform-ish) weights
        current = np.array([0.20, 0.20, 0.20, 0.15, 0.10, 0.15])
        current_composite = X_norm @ current * 100
        current_predictions = current_composite >= 50
        current_accuracy = np.mean(current_predictions == y)

        optimized = {dim: round(float(w), 4) for dim, w in zip(DIMENSIONS, weights)}

        return {
            "method": "logistic_regression",
            "samples": len(y),
            "positives": int(y.sum()),
            "negatives": int(len(y) - y.sum()),
            "optimized_weights": optimized,
            "optimized_accuracy": round(float(accuracy), 3),
            "current_accuracy": round(float(current_accuracy), 3),
            "improvement": round(float(accuracy - current_accuracy), 3),
        }

    def _calibrate_grid(
        self, X: np.ndarray, y: np.ndarray
    ) -> dict[str, Any]:
        """Grid search over weight combinations to maximize separation."""
        X_norm = X / 100.0
        best_score = -1.0
        best_weights = None

        # Coarse grid: sample weight combinations
        steps = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35]
        tried = 0

        for w0 in steps:
            for w1 in steps:
                for w2 in steps:
                    remainder = 1.0 - w0 - w1 - w2
                    if remainder < 0.05:
                        continue
                    # Split remainder roughly among remaining 3
                    for w3_frac in [0.3, 0.4, 0.5]:
                        w3 = round(remainder * w3_frac, 2)
                        for w4_frac in [0.3, 0.5]:
                            w4 = round((remainder - w3) * w4_frac, 2)
                            w5 = round(remainder - w3 - w4, 2)
                            if w3 < 0.01 or w4 < 0.01 or w5 < 0.01:
                                continue

                            w = np.array([w0, w1, w2, w3, w4, w5])
                            composite = X_norm @ w * 100

                            # Score: accuracy at threshold 50
                            predictions = composite >= 50
                            accuracy = np.mean(predictions == y)

                            # Also consider separation of means
                            pos_mean = composite[y == 1].mean() if y.sum() > 0 else 0
                            neg_mean = composite[y == 0].mean() if (1 - y).sum() > 0 else 0
                            sep = (pos_mean - neg_mean) / 100

                            combined = accuracy * 0.7 + sep * 0.3

                            if combined > best_score:
                                best_score = combined
                                best_weights = w.copy()
                            tried += 1

        if best_weights is None:
            return {"error": "Grid search found no valid weights"}

        optimized = {
            dim: round(float(w), 4) for dim, w in zip(DIMENSIONS, best_weights)
        }

        return {
            "method": "grid_search",
            "samples": len(y),
            "combinations_tried": tried,
            "optimized_weights": optimized,
            "best_combined_score": round(float(best_score), 3),
        }

    # ------------------------------------------------------------------
    # Sensitivity analysis
    # ------------------------------------------------------------------

    async def sensitivity_analysis(
        self,
        db: AsyncSession,
        perturbation: float = 0.05,
    ) -> dict[str, Any]:
        """Measure how sensitive the top-k ranking is to weight perturbations.

        For each dimension, increase weight by `perturbation` (decrease others
        proportionally) and measure how much the top-20 ranking changes.
        """
        base_weights = await self.config.get_active_weights(db)

        # Get top 50 hypotheses
        hyp_result = await db.execute(
            select(Hypothesis)
            .order_by(Hypothesis.composite_score.desc())
            .limit(50)
        )
        hypotheses = hyp_result.scalars().all()
        if len(hypotheses) < 10:
            return {"error": "Need at least 10 hypotheses for sensitivity analysis"}

        # Base ranking
        base_order = [h.id for h in hypotheses[:20]]

        results = {}
        for dim in DIMENSIONS:
            # Perturb this dimension up
            perturbed = base_weights.copy()
            perturbed[dim] = min(perturbed[dim] + perturbation, 1.0)

            # Renormalize remaining weights
            remaining = 1.0 - perturbed[dim]
            other_sum = sum(v for k, v in base_weights.items() if k != dim)
            if other_sum > 0:
                for k in perturbed:
                    if k != dim:
                        perturbed[k] = base_weights[k] * remaining / other_sum

            # Rescore hypotheses with perturbed weights
            rescored = []
            for h in hypotheses:
                dim_scores = {
                    "pathway_overlap": {"score": h.pathway_overlap_score or 0},
                    "expression_correlation": {"score": h.expression_correlation_score or 0},
                    "literature_support": {"score": h.literature_support_score or 0},
                    "clinical_evidence": {"score": h.clinical_evidence_score or 0},
                    "safety": {"score": h.safety_score or 0},
                    "novelty": {"score": h.novelty_score or 0},
                }
                new_composite = self.config.compute_composite_score(dim_scores, perturbed)
                rescored.append((h.id, new_composite))

            rescored.sort(key=lambda x: x[1], reverse=True)
            new_order = [r[0] for r in rescored[:20]]

            # Measure ranking change (Kendall tau distance approximation)
            changed_positions = sum(
                1 for i, hid in enumerate(base_order) if hid not in new_order or new_order.index(hid) != i
            )

            results[dim] = {
                "weight_change": f"{base_weights[dim]:.2f} -> {perturbed[dim]:.2f}",
                "positions_changed_in_top20": changed_positions,
                "percent_disruption": round(changed_positions / len(base_order) * 100, 1),
            }

        return {
            "perturbation_amount": perturbation,
            "base_weights": base_weights,
            "dimension_sensitivity": results,
        }

    # ------------------------------------------------------------------
    # Outcome tracking
    # ------------------------------------------------------------------

    async def record_outcome(
        self,
        hypothesis_id: int,
        outcome: str,
        db: AsyncSession,
        notes: str | None = None,
        reviewer: str | None = None,
        supporting_evidence: dict | None = None,
    ) -> dict[str, Any]:
        """Record an outcome for a hypothesis (feedback loop)."""
        valid_outcomes = ("validated", "invalidated", "promising", "inconclusive")
        if outcome not in valid_outcomes:
            raise ValueError(f"Outcome must be one of {valid_outcomes}")

        # Get hypothesis
        hyp_result = await db.execute(
            select(Hypothesis).where(Hypothesis.id == hypothesis_id)
        )
        hyp = hyp_result.scalar_one_or_none()
        if not hyp:
            raise ValueError(f"Hypothesis {hypothesis_id} not found")

        # Check for existing outcome
        existing = await db.execute(
            select(HypothesisOutcome).where(
                HypothesisOutcome.hypothesis_id == hypothesis_id
            )
        )
        record = existing.scalar_one_or_none()

        dim_snapshot = {
            "pathway_overlap": hyp.pathway_overlap_score,
            "expression_correlation": hyp.expression_correlation_score,
            "literature_support": hyp.literature_support_score,
            "clinical_evidence": hyp.clinical_evidence_score,
            "safety": hyp.safety_score,
            "novelty": hyp.novelty_score,
        }

        if record:
            record.outcome = outcome
            record.outcome_notes = notes
            record.reviewer = reviewer
            record.reviewed_at = datetime.now(timezone.utc)
            record.composite_score_at_review = hyp.composite_score
            record.dimension_scores_at_review = dim_snapshot
            record.supporting_evidence = supporting_evidence
        else:
            record = HypothesisOutcome(
                hypothesis_id=hypothesis_id,
                outcome=outcome,
                outcome_notes=notes,
                reviewer=reviewer,
                composite_score_at_review=hyp.composite_score,
                dimension_scores_at_review=dim_snapshot,
                supporting_evidence=supporting_evidence,
            )
            db.add(record)

        await db.flush()

        return {
            "hypothesis_id": hypothesis_id,
            "outcome": outcome,
            "composite_score": hyp.composite_score,
            "recorded_at": record.reviewed_at.isoformat(),
        }

    async def get_outcome_summary(self, db: AsyncSession) -> dict[str, Any]:
        """Summary statistics for recorded outcomes."""
        result = await db.execute(
            select(
                HypothesisOutcome.outcome,
                func.count(HypothesisOutcome.id),
                func.avg(HypothesisOutcome.composite_score_at_review),
            ).group_by(HypothesisOutcome.outcome)
        )
        rows = result.all()

        summary = {}
        total = 0
        for outcome, count, avg_score in rows:
            summary[outcome] = {
                "count": count,
                "avg_composite_score": round(float(avg_score), 1) if avg_score else None,
            }
            total += count

        return {"total_outcomes": total, "by_outcome": summary}
