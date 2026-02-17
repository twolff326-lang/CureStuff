"""Integration tests for the validation and benchmarking system.

Tests the metric computation, weight calibration algorithms, and
database-backed validation flows (seeding, matching, outcomes).
"""

import numpy as np
import pytest

from app.services.scoring_config import DIMENSIONS
from app.services.validation import GROUND_TRUTH_CASES, ValidationService


# =====================================================================
# Metric computation tests (no DB needed)
# =====================================================================


class TestComputeMetrics:
    """Test the _compute_metrics helper directly."""

    def setup_method(self):
        self.svc = ValidationService()

    def test_perfect_separation(self):
        """When successes score high and failures low, precision@5 = 1.0."""
        cases = [
            {"outcome": "success", "composite_score": 80},
            {"outcome": "success", "composite_score": 75},
            {"outcome": "success", "composite_score": 70},
            {"outcome": "success", "composite_score": 65},
            {"outcome": "success", "composite_score": 60},
            {"outcome": "failure", "composite_score": 20},
            {"outcome": "failure", "composite_score": 15},
            {"outcome": "failure", "composite_score": 10},
        ]
        metrics = self.svc._compute_metrics(cases)
        assert metrics["precision@5"] == 1.0
        assert metrics["separation"]["correctly_ordered"] == 1.0
        assert metrics["separation"]["gap"] > 0

    def test_inverted_scores_give_low_precision(self):
        """When failures score higher than successes, precision should drop."""
        cases = [
            {"outcome": "failure", "composite_score": 90},
            {"outcome": "failure", "composite_score": 85},
            {"outcome": "failure", "composite_score": 80},
            {"outcome": "failure", "composite_score": 75},
            {"outcome": "failure", "composite_score": 70},
            {"outcome": "success", "composite_score": 20},
            {"outcome": "success", "composite_score": 15},
        ]
        metrics = self.svc._compute_metrics(cases)
        assert metrics["precision@5"] == 0.0
        assert metrics["separation"]["gap"] < 0

    def test_no_failures_means_no_separation(self):
        """Without failures, separation metric should be None."""
        cases = [
            {"outcome": "success", "composite_score": 80},
            {"outcome": "success", "composite_score": 60},
            {"outcome": "ongoing", "composite_score": 50},
        ]
        metrics = self.svc._compute_metrics(cases)
        assert metrics["negatives"] == 0
        assert metrics["separation"] is None

    def test_empty_cases_returns_error(self):
        metrics = self.svc._compute_metrics([])
        assert "error" in metrics

    def test_cases_with_errors_skipped(self):
        """Cases with 'error' key instead of 'composite_score' should be excluded."""
        cases = [
            {"outcome": "success", "composite_score": 80},
            {"outcome": "failure", "error": "scoring failed"},
            {"outcome": "success", "composite_score": 60},
        ]
        metrics = self.svc._compute_metrics(cases)
        assert metrics["total_scored"] == 2

    def test_outcome_distribution_stats(self):
        """Score distribution should contain mean/min/max per outcome."""
        cases = [
            {"outcome": "success", "composite_score": 80},
            {"outcome": "success", "composite_score": 60},
            {"outcome": "failure", "composite_score": 30},
            {"outcome": "ongoing", "composite_score": 50},
        ]
        metrics = self.svc._compute_metrics(cases)
        dist = metrics["score_distribution_by_outcome"]

        assert dist["success"]["mean"] == 70.0
        assert dist["success"]["min"] == 60.0
        assert dist["success"]["max"] == 80.0
        assert dist["failure"]["mean"] == 30.0
        assert dist["ongoing"]["count"] == 1

    def test_partial_counted_as_positive(self):
        """Partial outcomes should count as positives for precision."""
        cases = [
            {"outcome": "partial", "composite_score": 80},
            {"outcome": "failure", "composite_score": 20},
        ]
        metrics = self.svc._compute_metrics(cases)
        assert metrics["positives"] == 1
        assert metrics["negatives"] == 1

    def test_precision_at_k_skips_large_k(self):
        """precision@k shouldn't be computed when k > total cases."""
        cases = [
            {"outcome": "success", "composite_score": 80},
            {"outcome": "failure", "composite_score": 20},
        ]
        metrics = self.svc._compute_metrics(cases)
        assert "precision@5" not in metrics
        assert "precision@10" not in metrics


# =====================================================================
# Calibration algorithm tests (no DB needed)
# =====================================================================


class TestCalibrationLogistic:
    """Test logistic regression weight calibration."""

    def setup_method(self):
        self.svc = ValidationService()

    def test_separable_data_gives_high_accuracy(self):
        """Perfectly separable data should yield accuracy near 1.0."""
        X = np.array([
            [90, 50, 50, 50, 50, 50],
            [85, 45, 55, 48, 52, 50],
            [80, 50, 50, 50, 50, 50],
            [75, 55, 45, 52, 48, 50],
            [70, 50, 50, 50, 50, 50],
            [10, 50, 50, 50, 50, 50],
            [15, 55, 45, 48, 52, 50],
            [20, 50, 50, 50, 50, 50],
            [25, 45, 55, 52, 48, 50],
            [30, 50, 50, 50, 50, 50],
        ])
        y = np.array([1, 1, 1, 1, 1, 0, 0, 0, 0, 0])

        result = self.svc._calibrate_logistic(X, y)

        assert result["optimized_accuracy"] >= 0.8
        # The separating dimension should get highest weight
        assert result["optimized_weights"]["pathway_overlap"] > 0.2

    def test_output_structure(self):
        """Verify all expected fields are returned."""
        np.random.seed(42)
        X = np.random.randn(12, 6) * 10 + 50
        y = np.array([1] * 6 + [0] * 6)

        result = self.svc._calibrate_logistic(X, y)

        assert result["method"] == "logistic_regression"
        assert result["samples"] == 12
        assert result["positives"] == 6
        assert result["negatives"] == 6
        assert len(result["optimized_weights"]) == 6
        assert "optimized_accuracy" in result
        assert "current_accuracy" in result
        assert "improvement" in result

    def test_weights_sum_to_one(self):
        """Optimized weights must sum to 1.0."""
        np.random.seed(0)
        X = np.random.randn(15, 6) * 20 + 50
        y = np.array([1] * 8 + [0] * 7)

        result = self.svc._calibrate_logistic(X, y)
        total = sum(result["optimized_weights"].values())
        assert abs(total - 1.0) < 0.01

    def test_all_weights_positive(self):
        """No weight should be negative or zero."""
        np.random.seed(1)
        X = np.random.randn(15, 6) * 20 + 50
        y = np.array([1] * 8 + [0] * 7)

        result = self.svc._calibrate_logistic(X, y)
        for w in result["optimized_weights"].values():
            assert w > 0

    def test_all_dimensions_present(self):
        """All 6 dimensions must appear in optimized weights."""
        np.random.seed(2)
        X = np.random.randn(10, 6) * 10 + 50
        y = np.array([1] * 5 + [0] * 5)

        result = self.svc._calibrate_logistic(X, y)
        for dim in DIMENSIONS:
            assert dim in result["optimized_weights"]


class TestCalibrationGrid:
    """Test grid search weight calibration."""

    def setup_method(self):
        self.svc = ValidationService()

    def test_grid_output_structure(self):
        np.random.seed(42)
        X = np.random.randn(12, 6) * 10 + 50
        y = np.array([1] * 6 + [0] * 6)

        result = self.svc._calibrate_grid(X, y)

        assert result["method"] == "grid_search"
        assert len(result["optimized_weights"]) == 6
        assert result["combinations_tried"] > 0
        assert "best_combined_score" in result

    def test_grid_weights_sum_near_one(self):
        np.random.seed(0)
        X = np.random.randn(12, 6) * 10 + 50
        y = np.array([1] * 6 + [0] * 6)

        result = self.svc._calibrate_grid(X, y)
        total = sum(result["optimized_weights"].values())
        assert abs(total - 1.0) < 0.05

    def test_grid_explores_many_combinations(self):
        X = np.random.randn(12, 6) * 10 + 50
        y = np.array([1] * 6 + [0] * 6)

        result = self.svc._calibrate_grid(X, y)
        assert result["combinations_tried"] > 100


# =====================================================================
# Database integration tests
# =====================================================================


class TestSeedGroundTruth:
    """Test ground truth seeding operations."""

    @pytest.mark.asyncio
    async def test_seed_inserts_all(self, db):
        svc = ValidationService()
        result = await svc.seed_ground_truth(db)

        assert result["inserted"] == len(GROUND_TRUTH_CASES)
        assert result["skipped"] == 0
        assert result["total_cases"] == len(GROUND_TRUTH_CASES)

    @pytest.mark.asyncio
    async def test_seed_idempotent(self, db):
        """Re-seeding should skip existing cases."""
        svc = ValidationService()
        await svc.seed_ground_truth(db)
        result = await svc.seed_ground_truth(db)

        assert result["inserted"] == 0
        assert result["skipped"] == len(GROUND_TRUTH_CASES)

    @pytest.mark.asyncio
    async def test_seed_replace(self, db):
        """Replace mode should delete and re-insert."""
        svc = ValidationService()
        await svc.seed_ground_truth(db)
        result = await svc.seed_ground_truth(db, replace=True)

        assert result["inserted"] == len(GROUND_TRUTH_CASES)
        assert result["skipped"] == 0


class TestMatchCases:
    """Test matching validation cases to database records."""

    @pytest.mark.asyncio
    async def test_match_empty_db(self, db):
        """With no drugs/cancers in DB, nothing should match."""
        svc = ValidationService()
        await svc.seed_ground_truth(db)
        result = await svc.match_cases_to_db(db)

        assert result["matched"] == 0
        assert result["unmatched"] > 0

    @pytest.mark.asyncio
    async def test_match_finds_drug(self, db):
        """When a drug exists, it should be matched by drugbank_id."""
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug

        drug = Drug(drugbank_id="DB00675", name="Tamoxifen", status="approved")
        cancer = CancerType(tcga_code="BRCA", name="Breast Cancer")
        db.add(drug)
        db.add(cancer)
        await db.flush()

        svc = ValidationService()
        await svc.seed_ground_truth(db)
        result = await svc.match_cases_to_db(db)

        # At least Tamoxifen/BRCA should match
        assert result["matched"] >= 1


class TestRecordOutcome:
    """Test outcome recording for hypotheses."""

    @pytest.mark.asyncio
    async def test_record_valid_outcome(self, db):
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.models.hypothesis import Hypothesis

        drug = Drug(drugbank_id="DB99999", name="TestDrug", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="TEST", name="Test Cancer")
        db.add(cancer)
        await db.flush()

        hyp = Hypothesis(
            drug_id=drug.id,
            cancer_type_id=cancer.id,
            title="Test hypothesis",
            composite_score=65.0,
            pathway_overlap_score=70,
            expression_correlation_score=60,
            literature_support_score=50,
            clinical_evidence_score=40,
            safety_score=80,
            novelty_score=90,
        )
        db.add(hyp)
        await db.flush()

        svc = ValidationService()
        result = await svc.record_outcome(
            hypothesis_id=hyp.id,
            outcome="validated",
            db=db,
            notes="Strong preclinical + clinical evidence",
            reviewer="Dr. Test",
        )

        assert result["outcome"] == "validated"
        assert result["composite_score"] == 65.0
        assert "recorded_at" in result

    @pytest.mark.asyncio
    async def test_record_invalid_outcome_raises(self, db):
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.models.hypothesis import Hypothesis

        drug = Drug(drugbank_id="DB88888", name="TestDrug2", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="TST2", name="Test Cancer 2")
        db.add(cancer)
        await db.flush()

        hyp = Hypothesis(
            drug_id=drug.id,
            cancer_type_id=cancer.id,
            title="Test hyp",
            composite_score=50.0,
            pathway_overlap_score=50,
            expression_correlation_score=50,
            literature_support_score=50,
            clinical_evidence_score=50,
            safety_score=50,
            novelty_score=50,
        )
        db.add(hyp)
        await db.flush()

        svc = ValidationService()
        with pytest.raises(ValueError, match="must be one of"):
            await svc.record_outcome(hyp.id, "garbage", db)

    @pytest.mark.asyncio
    async def test_record_nonexistent_hypothesis_raises(self, db):
        svc = ValidationService()
        with pytest.raises(ValueError, match="not found"):
            await svc.record_outcome(99999, "validated", db)

    @pytest.mark.asyncio
    async def test_outcome_upsert(self, db):
        """Recording a second outcome for same hypothesis should update."""
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.models.hypothesis import Hypothesis

        drug = Drug(drugbank_id="DB77777", name="TestDrug3", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="TST3", name="Test Cancer 3")
        db.add(cancer)
        await db.flush()

        hyp = Hypothesis(
            drug_id=drug.id,
            cancer_type_id=cancer.id,
            title="Upsert test",
            composite_score=55.0,
            pathway_overlap_score=50,
            expression_correlation_score=50,
            literature_support_score=50,
            clinical_evidence_score=50,
            safety_score=50,
            novelty_score=50,
        )
        db.add(hyp)
        await db.flush()

        svc = ValidationService()
        await svc.record_outcome(hyp.id, "promising", db)
        result = await svc.record_outcome(hyp.id, "validated", db)

        assert result["outcome"] == "validated"


class TestOutcomeSummary:
    """Test outcome summary statistics."""

    @pytest.mark.asyncio
    async def test_summary_groups_by_outcome(self, db):
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.models.hypothesis import Hypothesis

        drug = Drug(drugbank_id="DB66666", name="SummaryDrug", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="SUM", name="Summary Cancer")
        db.add(cancer)
        await db.flush()

        svc = ValidationService()
        outcomes = ["validated", "validated", "invalidated", "promising"]

        for i, outcome in enumerate(outcomes):
            hyp = Hypothesis(
                drug_id=drug.id,
                cancer_type_id=cancer.id,
                title=f"Summary hyp {i}",
                composite_score=40.0 + i * 15,
                pathway_overlap_score=50,
                expression_correlation_score=50,
                literature_support_score=50,
                clinical_evidence_score=50,
                safety_score=50,
                novelty_score=50,
            )
            db.add(hyp)
            await db.flush()
            await svc.record_outcome(hyp.id, outcome, db)

        summary = await svc.get_outcome_summary(db)

        assert summary["total_outcomes"] == 4
        assert summary["by_outcome"]["validated"]["count"] == 2
        assert summary["by_outcome"]["invalidated"]["count"] == 1
        assert summary["by_outcome"]["promising"]["count"] == 1

    @pytest.mark.asyncio
    async def test_empty_summary(self, db):
        svc = ValidationService()
        summary = await svc.get_outcome_summary(db)
        assert summary["total_outcomes"] == 0


# =====================================================================
# LLM Confidence Feedback Loop tests
# =====================================================================


class TestConfidenceFeedbackLoop:
    """Test that LLM confidence flows back into hypothesis scoring."""

    def _make_hypothesis(self, db, drug, cancer, composite=70.0):
        from app.models.hypothesis import Hypothesis

        hyp = Hypothesis(
            drug_id=drug.id,
            cancer_type_id=cancer.id,
            title=f"Feedback test ({composite})",
            composite_score=composite,
            adjusted_score=composite,  # Starts as passthrough
            pathway_overlap_score=70,
            expression_correlation_score=60,
            literature_support_score=80,
            clinical_evidence_score=50,
            safety_score=65,
            novelty_score=55,
        )
        db.add(hyp)
        return hyp

    @pytest.mark.asyncio
    async def test_confidence_writes_to_hypothesis(self, db):
        """After apply, llm_confidence_score should be set on the hypothesis."""
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.services.llm_analyst import LLMAnalyst

        drug = Drug(drugbank_id="DB11111", name="FeedbackDrug", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="FB1", name="Feedback Cancer")
        db.add(cancer)
        await db.flush()

        hyp = self._make_hypothesis(db, drug, cancer, composite=72.0)
        await db.flush()

        analyst = LLMAnalyst.__new__(LLMAnalyst)  # Skip __init__ (no API key needed)
        confidence_content = {
            "overall_confidence": 85,
            "recommendation": "proceed_with_caution",
        }
        await analyst._apply_confidence_to_hypothesis(hyp, confidence_content, db)

        assert hyp.llm_confidence_score == 85.0
        assert hyp.llm_recommendation == "proceed_with_caution"
        assert hyp.adjusted_score is not None
        assert hyp.adjusted_score < hyp.composite_score  # Gate compresses

    @pytest.mark.asyncio
    async def test_high_confidence_preserves_ranking(self, db):
        """High LLM confidence should barely change the adjusted score."""
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.services.llm_analyst import LLMAnalyst

        drug = Drug(drugbank_id="DB22222", name="HighConfDrug", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="HC1", name="HighConf Cancer")
        db.add(cancer)
        await db.flush()

        hyp = self._make_hypothesis(db, drug, cancer, composite=80.0)
        await db.flush()

        analyst = LLMAnalyst.__new__(LLMAnalyst)
        await analyst._apply_confidence_to_hypothesis(
            hyp,
            {"overall_confidence": 95, "recommendation": "proceed_immediately"},
            db,
        )

        # gate = 0.3 + 0.7 * 0.95 = 0.965 → adjusted = 80 * 0.965 = 77.2
        assert hyp.adjusted_score == 77.2

    @pytest.mark.asyncio
    async def test_low_confidence_tanks_ranking(self, db):
        """Low LLM confidence should dramatically reduce adjusted score."""
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.services.llm_analyst import LLMAnalyst

        drug = Drug(drugbank_id="DB33333", name="LowConfDrug", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="LC1", name="LowConf Cancer")
        db.add(cancer)
        await db.flush()

        hyp = self._make_hypothesis(db, drug, cancer, composite=80.0)
        await db.flush()

        analyst = LLMAnalyst.__new__(LLMAnalyst)
        await analyst._apply_confidence_to_hypothesis(
            hyp,
            {"overall_confidence": 10, "recommendation": "deprioritize"},
            db,
        )

        # gate = 0.3 + 0.7 * 0.1 = 0.37 → adjusted = 80 * 0.37 = 29.6
        assert hyp.adjusted_score == 29.6
        assert hyp.llm_recommendation == "deprioritize"

    @pytest.mark.asyncio
    async def test_reranking_with_confidence(self, db):
        """Hypothesis B (lower composite but higher confidence) should outrank
        Hypothesis A (higher composite but low confidence) after LLM analysis."""
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.services.llm_analyst import LLMAnalyst

        drug = Drug(drugbank_id="DB44444", name="RerankDrug", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="RR1", name="Rerank Cancer")
        db.add(cancer)
        await db.flush()

        hyp_a = self._make_hypothesis(db, drug, cancer, composite=80.0)
        hyp_b = self._make_hypothesis(db, drug, cancer, composite=60.0)
        await db.flush()

        analyst = LLMAnalyst.__new__(LLMAnalyst)

        # A: high composite but LLM says "deprioritize"
        await analyst._apply_confidence_to_hypothesis(
            hyp_a, {"overall_confidence": 15, "recommendation": "deprioritize"}, db
        )
        # B: lower composite but LLM says "proceed"
        await analyst._apply_confidence_to_hypothesis(
            hyp_b, {"overall_confidence": 90, "recommendation": "proceed_immediately"}, db
        )

        # B should now outrank A
        assert hyp_b.adjusted_score > hyp_a.adjusted_score

    @pytest.mark.asyncio
    async def test_invalid_confidence_ignored(self, db):
        """Invalid confidence values should be handled gracefully."""
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.services.llm_analyst import LLMAnalyst

        drug = Drug(drugbank_id="DB55555", name="InvalidDrug", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="IV1", name="Invalid Cancer")
        db.add(cancer)
        await db.flush()

        hyp = self._make_hypothesis(db, drug, cancer, composite=70.0)
        await db.flush()

        analyst = LLMAnalyst.__new__(LLMAnalyst)

        # Confidence is a string (bad LLM output)
        await analyst._apply_confidence_to_hypothesis(
            hyp, {"overall_confidence": "not a number"}, db
        )
        # Should fall through to None → passthrough
        assert hyp.llm_confidence_score is None
        assert hyp.adjusted_score == 70.0

    @pytest.mark.asyncio
    async def test_confidence_clamped_to_range(self, db):
        """Confidence values outside 0-100 should be clamped."""
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.services.llm_analyst import LLMAnalyst

        drug = Drug(drugbank_id="DB56666", name="ClampDrug", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="CL1", name="Clamp Cancer")
        db.add(cancer)
        await db.flush()

        hyp = self._make_hypothesis(db, drug, cancer, composite=70.0)
        await db.flush()

        analyst = LLMAnalyst.__new__(LLMAnalyst)

        # LLM returns confidence > 100
        await analyst._apply_confidence_to_hypothesis(
            hyp, {"overall_confidence": 150}, db
        )
        assert hyp.llm_confidence_score == 100.0
        assert hyp.adjusted_score == 70.0  # gate = 1.0

    @pytest.mark.asyncio
    async def test_invalid_recommendation_ignored(self, db):
        """Unknown recommendation values should be set to None."""
        from app.models.cancer_type import CancerType
        from app.models.drug import Drug
        from app.services.llm_analyst import LLMAnalyst

        drug = Drug(drugbank_id="DB57777", name="BadRecDrug", status="approved")
        db.add(drug)
        cancer = CancerType(tcga_code="BR1", name="BadRec Cancer")
        db.add(cancer)
        await db.flush()

        hyp = self._make_hypothesis(db, drug, cancer, composite=70.0)
        await db.flush()

        analyst = LLMAnalyst.__new__(LLMAnalyst)
        await analyst._apply_confidence_to_hypothesis(
            hyp, {"overall_confidence": 80, "recommendation": "yolo"}, db
        )
        assert hyp.llm_recommendation is None
        assert hyp.llm_confidence_score == 80.0
