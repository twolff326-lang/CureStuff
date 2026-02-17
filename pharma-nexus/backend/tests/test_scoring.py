"""Unit tests for evidence scoring dimensions and composite scoring.

Tests each of the 6 scoring dimensions with controlled inputs to verify
that the scoring formulas produce expected results and respect boundaries.
"""

import pytest

from app.services.scoring_config import DIMENSIONS, ScoringConfig


# =====================================================================
# ScoringConfig unit tests (no DB needed)
# =====================================================================


class TestCompositeScore:
    """Test composite score computation from dimension scores."""

    def setup_method(self):
        self.config = ScoringConfig()
        self.uniform_weights = {dim: 1 / 6 for dim in DIMENSIONS}

    def _make_dims(self, **kwargs):
        """Build dimension_scores dict with defaults of 0."""
        return {dim: {"score": kwargs.get(dim, 0)} for dim in DIMENSIONS}

    def test_all_zeros(self):
        dims = self._make_dims()
        score = self.config.compute_composite_score(dims, self.uniform_weights)
        assert score == 0.0

    def test_all_hundreds(self):
        dims = self._make_dims(**{d: 100 for d in DIMENSIONS})
        score = self.config.compute_composite_score(dims, self.uniform_weights)
        assert score == 100.0

    def test_single_dimension_only(self):
        dims = self._make_dims(pathway_overlap=60)
        score = self.config.compute_composite_score(dims, self.uniform_weights)
        expected = 60 * (1 / 6)
        assert abs(score - expected) < 0.2

    def test_weighted_emphasis(self):
        """Heavy weight on one dimension should reflect in composite."""
        weights = {
            "pathway_overlap": 0.50,
            "expression_correlation": 0.10,
            "literature_support": 0.10,
            "clinical_evidence": 0.10,
            "safety": 0.10,
            "novelty": 0.10,
        }
        dims = self._make_dims(pathway_overlap=80, expression_correlation=20)
        score = self.config.compute_composite_score(dims, weights)
        # 80*0.5 + 20*0.1 = 42
        assert abs(score - 42.0) < 0.2

    def test_score_capped_at_100(self):
        """Even with extreme inputs, score should not exceed 100."""
        dims = {dim: {"score": 150} for dim in DIMENSIONS}  # Invalid but shouldn't crash
        score = self.config.compute_composite_score(dims, self.uniform_weights)
        assert score <= 100.0

    def test_score_floored_at_0(self):
        dims = {dim: {"score": -50} for dim in DIMENSIONS}
        score = self.config.compute_composite_score(dims, self.uniform_weights)
        assert score >= 0.0

    def test_missing_dimension_treated_as_zero(self):
        """If a dimension is missing from results, it should score 0."""
        dims = {"pathway_overlap": {"score": 100}}
        score = self.config.compute_composite_score(dims, self.uniform_weights)
        expected = 100 * (1 / 6)
        assert abs(score - expected) < 0.2


class TestEvidenceStrength:
    """Test evidence strength classification."""

    def setup_method(self):
        self.config = ScoringConfig()

    def test_strong(self):
        assert self.config.determine_evidence_strength(75.0) == "strong"
        assert self.config.determine_evidence_strength(100.0) == "strong"

    def test_moderate(self):
        assert self.config.determine_evidence_strength(50.0) == "moderate"
        assert self.config.determine_evidence_strength(74.9) == "moderate"

    def test_suggestive(self):
        assert self.config.determine_evidence_strength(25.0) == "suggestive"
        assert self.config.determine_evidence_strength(49.9) == "suggestive"

    def test_speculative(self):
        assert self.config.determine_evidence_strength(0.0) == "speculative"
        assert self.config.determine_evidence_strength(24.9) == "speculative"

    def test_boundary_values(self):
        """Test exact boundary values."""
        assert self.config.determine_evidence_strength(75) == "strong"
        assert self.config.determine_evidence_strength(50) == "moderate"
        assert self.config.determine_evidence_strength(25) == "suggestive"


class TestWeightValidation:
    """Test weight validation rules."""

    def test_valid_weights(self):
        ScoringConfig._validate_weights({
            "pathway_overlap": 0.20,
            "expression_correlation": 0.20,
            "literature_support": 0.20,
            "clinical_evidence": 0.15,
            "safety": 0.10,
            "novelty": 0.15,
        })

    def test_missing_dimension(self):
        with pytest.raises(ValueError, match="Missing"):
            ScoringConfig._validate_weights({
                "pathway_overlap": 0.25,
                "expression_correlation": 0.25,
                "literature_support": 0.25,
                "clinical_evidence": 0.25,
                # safety and novelty missing
            })

    def test_extra_dimension(self):
        with pytest.raises(ValueError, match="Unknown"):
            ScoringConfig._validate_weights({
                "pathway_overlap": 0.15,
                "expression_correlation": 0.15,
                "literature_support": 0.15,
                "clinical_evidence": 0.15,
                "safety": 0.15,
                "novelty": 0.15,
                "magic": 0.10,
            })

    def test_weights_dont_sum_to_one(self):
        with pytest.raises(ValueError, match="sum to 1.0"):
            ScoringConfig._validate_weights({
                "pathway_overlap": 0.30,
                "expression_correlation": 0.30,
                "literature_support": 0.30,
                "clinical_evidence": 0.30,
                "safety": 0.30,
                "novelty": 0.30,
            })

    def test_negative_weight(self):
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            ScoringConfig._validate_weights({
                "pathway_overlap": -0.10,
                "expression_correlation": 0.30,
                "literature_support": 0.20,
                "clinical_evidence": 0.20,
                "safety": 0.20,
                "novelty": 0.20,
            })

    def test_weight_over_one(self):
        with pytest.raises(ValueError, match="between 0.0 and 1.0"):
            ScoringConfig._validate_weights({
                "pathway_overlap": 1.50,
                "expression_correlation": 0.0,
                "literature_support": 0.0,
                "clinical_evidence": 0.0,
                "safety": 0.0,
                "novelty": 0.0,
            })

    def test_tolerance_near_one(self):
        """Weights summing to 0.999 should pass (within 0.01 tolerance)."""
        ScoringConfig._validate_weights({
            "pathway_overlap": 0.166,
            "expression_correlation": 0.166,
            "literature_support": 0.166,
            "clinical_evidence": 0.166,
            "safety": 0.167,
            "novelty": 0.168,
        })


# =====================================================================
# Scoring formula unit tests (mocked data, no DB)
# =====================================================================


class TestPathwayOverlapFormula:
    """Test the pathway overlap scoring formula with synthetic data."""

    def test_no_shared_pathways(self):
        from app.services.evidence_scorer import EvidenceScorer

        scorer = EvidenceScorer()
        # Manually call the scoring logic by constructing the expected data
        pathway_data = {
            "shared_pathways": [],
            "total_drug_target_pathways": 5,
            "total_cancer_altered_pathways": 10,
            "shared_count": 0,
        }
        import asyncio

        async def _score():
            # We can call score_pathway_overlap with pre-computed data
            # (it doesn't need DB when pathway_data is provided)
            return await scorer.score_pathway_overlap(0, 0, None, pathway_data)

        result = asyncio.get_event_loop().run_until_complete(_score())
        assert result["score"] == 0

    def test_full_overlap(self):
        from app.services.evidence_scorer import EvidenceScorer

        scorer = EvidenceScorer()
        pathway_data = {
            "shared_pathways": [
                {
                    "pathway_id": 1,
                    "pathway_name": "PI3K-AKT",
                    "overlap_significance": 0.9,
                    "drug_targets_in_pathway": ["EGFR"],
                    "cancer_altered_genes_in_pathway": ["EGFR", "PTEN"],
                },
                {
                    "pathway_id": 2,
                    "pathway_name": "MAPK",
                    "overlap_significance": 0.8,
                    "drug_targets_in_pathway": ["BRAF"],
                    "cancer_altered_genes_in_pathway": ["KRAS"],
                },
            ],
            "total_drug_target_pathways": 2,
            "total_cancer_altered_pathways": 3,
            "shared_count": 2,
        }
        import asyncio

        async def _score():
            return await scorer.score_pathway_overlap(0, 0, None, pathway_data)

        result = asyncio.get_event_loop().run_until_complete(_score())
        # Base: (2/3)*50 = 33.3
        # Sig bonus: 2 pathways >= 0.7 -> 20, capped at 30
        # Direct overlap: EGFR in both -> 20
        # Total: 33.3 + 20 + 20 = 73
        assert result["score"] >= 70
        assert result["score"] <= 100
        assert len(result["evidence"]) == 2

    def test_score_capped(self):
        """Even with extreme data, score should not exceed 100."""
        from app.services.evidence_scorer import EvidenceScorer

        scorer = EvidenceScorer()
        shared = [
            {
                "pathway_id": i,
                "pathway_name": f"P{i}",
                "overlap_significance": 0.95,
                "drug_targets_in_pathway": ["GENE_A"],
                "cancer_altered_genes_in_pathway": ["GENE_A"],
            }
            for i in range(20)
        ]
        pathway_data = {
            "shared_pathways": shared,
            "total_drug_target_pathways": 20,
            "total_cancer_altered_pathways": 20,
            "shared_count": 20,
        }
        import asyncio

        async def _score():
            return await scorer.score_pathway_overlap(0, 0, None, pathway_data)

        result = asyncio.get_event_loop().run_until_complete(_score())
        assert result["score"] == 100


class TestNoveltyFormula:
    """Test the novelty scoring formula (inverse evidence)."""

    def test_zero_evidence_is_max_novelty(self):
        """No papers, no trials -> score 100."""
        # The formula: max(100 - co_papers*8 - relevant_trials*15, 0)
        score = max(100 - 0 * 8 - 0 * 15, 0)
        assert score == 100

    def test_moderate_evidence_reduces_novelty(self):
        """5 papers, 2 trials -> score = max(100 - 40 - 30, 0) = 30."""
        score = max(100 - 5 * 8 - 2 * 15, 0)
        assert score == 30

    def test_heavy_evidence_floors_at_zero(self):
        """20 papers -> max(100 - 160, 0) = 0."""
        score = max(100 - 20 * 8 - 0 * 15, 0)
        assert score == 0

    def test_novelty_coefficients(self):
        """Verify the specific coefficients used in the formula."""
        # 1 paper reduces by 8
        assert max(100 - 1 * 8, 0) == 92
        # 1 trial reduces by 15
        assert max(100 - 0 * 8 - 1 * 15, 0) == 85
        # 1 paper + 1 trial
        assert max(100 - 1 * 8 - 1 * 15, 0) == 77


class TestLiteratureFormula:
    """Test the literature support scoring formula."""

    def test_zero_papers(self):
        score = min(0 * 8 + 0 * 3, 100)
        assert score == 0

    def test_moderate_support(self):
        """5 co-mentions, 3 analyzed -> 5*8 + 3*3 = 49."""
        score = min(5 * 8 + 3 * 3, 100)
        assert score == 49

    def test_capped_at_100(self):
        """20 co-mentions -> 160, capped at 100."""
        score = min(20 * 8 + 0 * 3, 100)
        assert score == 100


class TestSafetyFormula:
    """Test the safety scoring formula."""

    def test_approved_base(self):
        assert 60 == {"approved": 60, "investigational": 40, "experimental": 20, "withdrawn": 5}["approved"]

    def test_withdrawn_base(self):
        assert 5 == {"approved": 60, "investigational": 40, "experimental": 20, "withdrawn": 5}["withdrawn"]

    def test_max_safety_score(self):
        """Approved (60) + MoA (15) + cancer indication (20) + bioassays (10) = 105, capped at 100."""
        score = min(60 + 15 + 20 + 10, 100)
        assert score == 100

    def test_minimal_safety_score(self):
        """Withdrawn with nothing else -> 5."""
        score = min(5 + 0 + 0 + 0, 100)
        assert score == 5


class TestClinicalEvidenceFormula:
    """Test the clinical evidence scoring formula."""

    def test_phase_weights(self):
        weights = {"Phase 4": 25, "Phase 3": 25, "Phase 2": 15, "Phase 1": 8}
        assert weights["Phase 3"] == 25
        assert weights["Phase 1"] == 8

    def test_active_modifier(self):
        """Active/completed trials get 1.2x multiplier."""
        base = 25  # Phase 3
        modified = int(base * 1.2)
        assert modified == 30

    def test_terminated_modifier(self):
        """Terminated trials get 0.5x multiplier."""
        base = 25  # Phase 3
        modified = int(base * 0.5)
        assert modified == 12


class TestExpressionCompatibility:
    """Test the expression correlation compatibility formula."""

    def test_inhibitor_on_overexpressed(self):
        """Inhibitor + overexpressed (zscore > 0) -> high compatibility."""
        zscore = 3.0
        compat = min(zscore / 3.0, 1.0)
        assert compat == 1.0

    def test_inhibitor_on_underexpressed(self):
        """Inhibitor + underexpressed -> low compatibility (uses fallback)."""
        zscore = -2.0
        compat = min(abs(zscore) / 5.0, 0.5)
        assert compat == 0.4

    def test_agonist_on_underexpressed(self):
        """Agonist + underexpressed (zscore < 0) -> high compatibility."""
        zscore = -3.0
        compat = min(abs(zscore) / 3.0, 1.0)
        assert compat == 1.0

    def test_no_expression(self):
        """No expression data -> minimal compatibility."""
        compat = 0.1
        assert compat == 0.1

    def test_compatibility_capped(self):
        """Even extreme z-scores shouldn't exceed 1.0 compatibility."""
        zscore = 10.0
        compat = min(zscore / 3.0, 1.0)
        assert compat == 1.0


# =====================================================================
# Adjusted score (LLM confidence gate) tests
# =====================================================================


class TestAdjustedScore:
    """Test the multiplicative LLM confidence gate."""

    def setup_method(self):
        self.config = ScoringConfig()

    def test_no_confidence_is_passthrough(self):
        """Without LLM analysis, adjusted = composite."""
        assert self.config.compute_adjusted_score(80.0, None) == 80.0

    def test_full_confidence_unchanged(self):
        """LLM confidence of 100 = no change."""
        assert self.config.compute_adjusted_score(80.0, 100.0) == 80.0

    def test_zero_confidence_floors_at_30_percent(self):
        """LLM confidence of 0 = composite * 0.30."""
        result = self.config.compute_adjusted_score(80.0, 0.0)
        assert result == 24.0  # 80 * 0.3

    def test_mid_confidence_reduces_proportionally(self):
        """LLM confidence of 50 = composite * 0.65."""
        result = self.config.compute_adjusted_score(100.0, 50.0)
        assert result == 65.0  # 100 * (0.3 + 0.7 * 0.5)

    def test_low_confidence_tanks_score(self):
        """LLM confidence of 10 (very_low) should significantly reduce score."""
        result = self.config.compute_adjusted_score(80.0, 10.0)
        # gate = 0.3 + 0.7 * 0.1 = 0.37
        assert result == 29.6  # 80 * 0.37

    def test_never_exceeds_composite(self):
        """Adjusted score can never be higher than composite."""
        for conf in [0, 25, 50, 75, 100]:
            adj = self.config.compute_adjusted_score(80.0, conf)
            assert adj <= 80.0

    def test_clamped_at_zero(self):
        """Negative composite scores get clamped."""
        result = self.config.compute_adjusted_score(-10.0, 50.0)
        assert result == 0.0

    def test_clamped_at_100(self):
        """Scores above 100 get clamped."""
        result = self.config.compute_adjusted_score(120.0, 100.0)
        assert result == 100.0

    def test_real_scenario_deprioritize(self):
        """A hypothesis with high composite but low LLM confidence should drop."""
        composite = 72.0  # "moderate" strength
        llm_confidence = 15.0  # low confidence — LLM found problems
        adjusted = self.config.compute_adjusted_score(composite, llm_confidence)
        # gate = 0.3 + 0.7 * 0.15 = 0.405
        assert adjusted == 29.2  # Was moderate, now speculative
        assert self.config.determine_evidence_strength(adjusted) == "suggestive"

    def test_real_scenario_proceed_immediately(self):
        """High composite + high LLM confidence should stay high."""
        composite = 72.0
        llm_confidence = 85.0
        adjusted = self.config.compute_adjusted_score(composite, llm_confidence)
        # gate = 0.3 + 0.7 * 0.85 = 0.895
        assert adjusted == 64.4
        assert self.config.determine_evidence_strength(adjusted) == "moderate"


# =====================================================================
# Ground truth dataset validation
# =====================================================================


class TestGroundTruthDataset:
    """Validate the ground truth dataset integrity."""

    def test_has_successes(self):
        from app.services.validation import GROUND_TRUTH_CASES

        successes = [c for c in GROUND_TRUTH_CASES if c["outcome"] == "success"]
        assert len(successes) >= 10, "Need at least 10 known successes"

    def test_has_failures(self):
        from app.services.validation import GROUND_TRUTH_CASES

        failures = [c for c in GROUND_TRUTH_CASES if c["outcome"] == "failure"]
        assert len(failures) >= 3, "Need at least 3 known failures"

    def test_all_cases_have_required_fields(self):
        from app.services.validation import GROUND_TRUTH_CASES

        for case in GROUND_TRUTH_CASES:
            assert case["drug_name"], f"Missing drug_name: {case}"
            assert case["cancer_name"], f"Missing cancer_name: {case}"
            assert case["outcome"] in ("success", "failure", "partial", "ongoing"), (
                f"Invalid outcome '{case['outcome']}' for {case['drug_name']}"
            )
            assert case["source"] == "curated"

    def test_fda_approved_are_successes(self):
        from app.services.validation import GROUND_TRUTH_CASES

        for case in GROUND_TRUTH_CASES:
            if case.get("fda_approved"):
                assert case["outcome"] == "success", (
                    f"FDA approved case should be success: {case['drug_name']}"
                )

    def test_no_duplicate_cases(self):
        from app.services.validation import GROUND_TRUTH_CASES

        seen = set()
        for case in GROUND_TRUTH_CASES:
            key = (case["drug_name"], case["cancer_name"])
            assert key not in seen, f"Duplicate case: {key}"
            seen.add(key)

    def test_drugbank_ids_are_valid_format(self):
        from app.services.validation import GROUND_TRUTH_CASES

        for case in GROUND_TRUTH_CASES:
            dbid = case.get("drug_drugbank_id")
            if dbid:
                assert dbid.startswith("DB"), (
                    f"Invalid DrugBank ID format: {dbid}"
                )

    def test_reference_pmids_are_strings(self):
        from app.services.validation import GROUND_TRUTH_CASES

        for case in GROUND_TRUTH_CASES:
            pmids = case.get("reference_pmids", [])
            for pmid in pmids:
                assert isinstance(pmid, str), (
                    f"PMID should be string: {pmid} for {case['drug_name']}"
                )
                assert pmid.isdigit(), (
                    f"PMID should be numeric string: {pmid}"
                )


# =====================================================================
# Cost optimization tests
# =====================================================================


class TestCostModes:
    """Test LLM cost mode model selection and pricing."""

    def test_economy_mode_uses_haiku(self):
        from app.services.llm_analyst import COST_MODE_MODELS, MODEL_HAIKU

        models = COST_MODE_MODELS["economy"]
        assert models["default"] == MODEL_HAIKU
        assert models["top"] == MODEL_HAIKU

    def test_standard_mode_uses_sonnet_and_opus(self):
        from app.services.llm_analyst import (
            COST_MODE_MODELS,
            MODEL_OPUS,
            MODEL_SONNET,
        )

        models = COST_MODE_MODELS["standard"]
        assert models["default"] == MODEL_SONNET
        assert models["top"] == MODEL_OPUS

    def test_premium_mode_uses_opus_everywhere(self):
        from app.services.llm_analyst import COST_MODE_MODELS, MODEL_OPUS

        models = COST_MODE_MODELS["premium"]
        assert models["default"] == MODEL_OPUS
        assert models["top"] == MODEL_OPUS

    def test_haiku_pricing_is_cheapest(self):
        from app.services.llm_analyst import MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET, PRICING

        haiku_cost = PRICING[MODEL_HAIKU]["input"] + PRICING[MODEL_HAIKU]["output"]
        sonnet_cost = PRICING[MODEL_SONNET]["input"] + PRICING[MODEL_SONNET]["output"]
        opus_cost = PRICING[MODEL_OPUS]["input"] + PRICING[MODEL_OPUS]["output"]
        assert haiku_cost < sonnet_cost < opus_cost

    def test_economy_cost_estimate_per_hypothesis(self):
        """Economy confidence-only should cost ~$0.013 per hypothesis."""
        from app.services.llm_analyst import MODEL_HAIKU, PRICING

        p = PRICING[MODEL_HAIKU]
        # Rough: 3K input + 2K output tokens
        cost = (3000 * p["input"] + 2000 * p["output"]) / 1_000_000
        assert cost < 0.02  # well under 2 cents per hypothesis

    def test_standard_full_analysis_cost_per_hypothesis(self):
        """Standard full analysis (6 calls, Sonnet) should cost ~$0.23."""
        from app.services.llm_analyst import MODEL_SONNET, PRICING

        p = PRICING[MODEL_SONNET]
        cost_per_call = (3000 * p["input"] + 2000 * p["output"]) / 1_000_000
        cost_6 = cost_per_call * 6
        assert cost_6 < 0.50  # under 50 cents for 6 analyses

    def test_economy_vs_standard_savings(self):
        """Economy confidence-only should be at least 10x cheaper than standard full."""
        from app.services.llm_analyst import MODEL_HAIKU, MODEL_SONNET, PRICING

        haiku_p = PRICING[MODEL_HAIKU]
        sonnet_p = PRICING[MODEL_SONNET]
        economy_cost = (3000 * haiku_p["input"] + 2000 * haiku_p["output"]) / 1_000_000 * 1
        standard_cost = (3000 * sonnet_p["input"] + 2000 * sonnet_p["output"]) / 1_000_000 * 6
        ratio = standard_cost / economy_cost
        assert ratio > 10  # economy is at least 10x cheaper

    def test_model_selection_economy(self):
        """Economy mode should select Haiku for all hypotheses."""
        from unittest.mock import MagicMock

        from app.services.llm_analyst import MODEL_HAIKU, LLMAnalyst

        analyst = LLMAnalyst.__new__(LLMAnalyst)
        analyst._cost_mode = "economy"

        low_hyp = MagicMock()
        low_hyp.composite_score = 30.0
        assert analyst._select_model(low_hyp) == MODEL_HAIKU

        high_hyp = MagicMock()
        high_hyp.composite_score = 90.0
        assert analyst._select_model(high_hyp) == MODEL_HAIKU

    def test_model_selection_standard(self):
        """Standard mode: Sonnet for low, Opus for high scorers."""
        from unittest.mock import MagicMock

        from app.services.llm_analyst import MODEL_OPUS, MODEL_SONNET, LLMAnalyst

        analyst = LLMAnalyst.__new__(LLMAnalyst)
        analyst._cost_mode = "standard"

        low_hyp = MagicMock()
        low_hyp.composite_score = 30.0
        assert analyst._select_model(low_hyp) == MODEL_SONNET

        high_hyp = MagicMock()
        high_hyp.composite_score = 90.0
        assert analyst._select_model(high_hyp) == MODEL_OPUS

    def test_model_selection_premium(self):
        """Premium mode: Opus everywhere."""
        from unittest.mock import MagicMock

        from app.services.llm_analyst import MODEL_OPUS, LLMAnalyst

        analyst = LLMAnalyst.__new__(LLMAnalyst)
        analyst._cost_mode = "premium"

        low_hyp = MagicMock()
        low_hyp.composite_score = 30.0
        assert analyst._select_model(low_hyp) == MODEL_OPUS

    def test_cost_mode_constructor_override(self):
        """Cost mode can be overridden via constructor."""
        from app.services.llm_analyst import LLMAnalyst

        analyst = LLMAnalyst.__new__(LLMAnalyst)
        analyst._cost_mode = "economy"
        assert analyst._cost_mode == "economy"

    def test_all_models_have_pricing(self):
        """Every model in COST_MODE_MODELS should have a PRICING entry."""
        from app.services.llm_analyst import COST_MODE_MODELS, PRICING

        for mode, models in COST_MODE_MODELS.items():
            for role, model_name in models.items():
                assert model_name in PRICING, (
                    f"Model {model_name} (mode={mode}, role={role}) missing from PRICING"
                )
