"""Tests for HypothesisEngine — summary generation, rescoring, and integration.

Tests the pure-logic methods and the async orchestration with mocked DB.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.hypothesis_engine import HypothesisEngine
from app.services.scoring_config import DEFAULT_WEIGHTS, DIMENSIONS, ScoringConfig
from tests.conftest import (
    MockResult,
    MockSession,
    make_hypothesis,
)

engine = HypothesisEngine()


# ===================================================================
# _generate_summary
# ===================================================================


class TestGenerateSummary:
    def test_single_strategy(self):
        """Summary includes single discovery strategy."""
        dim_scores = {dim: {"score": 50} for dim in DIMENSIONS}
        summary = engine._generate_summary(
            "Aspirin", "Breast Cancer",
            ["direct_target"],
            dim_scores, 50.0,
        )
        assert "Aspirin" in summary
        assert "Breast Cancer" in summary
        assert "direct target overlap" in summary
        assert "50/100" in summary

    def test_multiple_strategies(self):
        """Summary lists multiple strategies."""
        dim_scores = {dim: {"score": 60} for dim in DIMENSIONS}
        summary = engine._generate_summary(
            "Imatinib", "CML",
            ["direct_target", "pathway_mediated", "literature_seeded"],
            dim_scores, 65.0,
        )
        assert "direct target overlap" in summary
        assert "pathway-mediated connection" in summary
        assert "literature evidence" in summary

    def test_highlights_top_scoring_dimension(self):
        """Summary highlights the highest-scoring dimension."""
        dim_scores = {
            "pathway_overlap": {"score": 30},
            "expression_correlation": {"score": 90},
            "literature_support": {"score": 20},
            "clinical_evidence": {"score": 10},
            "safety": {"score": 40},
            "novelty": {"score": 50},
        }
        summary = engine._generate_summary(
            "DrugX", "CancerY",
            ["expression_driven"],
            dim_scores, 45.0,
        )
        assert "expression correlation" in summary
        assert "90/100" in summary

    def test_all_zero_scores(self):
        """Summary handles all-zero dimension scores gracefully."""
        dim_scores = {dim: {"score": 0} for dim in DIMENSIONS}
        summary = engine._generate_summary(
            "DrugA", "CancerB",
            ["analog_discovery"],
            dim_scores, 0.0,
        )
        assert "DrugA" in summary
        assert "CancerB" in summary
        assert "speculative" in summary

    def test_evidence_strength_in_summary(self):
        """Summary includes evidence strength label."""
        dim_scores = {dim: {"score": 80} for dim in DIMENSIONS}
        summary = engine._generate_summary(
            "DrugC", "CancerD",
            ["direct_target"],
            dim_scores, 80.0,
        )
        assert "strong" in summary

    def test_all_six_strategy_names(self):
        """All 6 strategy names can be rendered."""
        dim_scores = {dim: {"score": 50} for dim in DIMENSIONS}
        all_strategies = [
            "direct_target", "pathway_mediated", "interaction_network",
            "expression_driven", "literature_seeded", "analog_discovery",
        ]
        summary = engine._generate_summary(
            "Drug", "Cancer", all_strategies, dim_scores, 50.0,
        )
        assert "direct target overlap" in summary
        assert "pathway-mediated connection" in summary
        assert "protein interaction network proximity" in summary
        assert "expression-action compatibility" in summary
        assert "literature evidence" in summary
        assert "structural analog similarity" in summary


# ===================================================================
# rescore_all
# ===================================================================


class TestRescoreAll:
    @pytest.mark.asyncio
    async def test_rescore_empty_db(self):
        """Rescoring with no hypotheses returns 0/0."""
        db = MockSession()
        # get_active_weights call
        db.queue_result(MockResult(scalar_value=None))
        # select all hypotheses
        db.queue_result(MockResult(rows=[]))

        result = await engine.rescore_all(db)
        assert result["rescored"] == 0
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_rescore_updates_scores(self):
        """Rescoring updates composite_score and evidence_strength."""
        h = make_hypothesis(
            pathway_overlap_score=60,
            expression_correlation_score=40,
            literature_support_score=80,
            clinical_evidence_score=20,
            safety_score=50,
            novelty_score=70,
            causal_dependency_score=0,
            gnn_link_score=0,
            mutation_context_score=0,
            polypharmacology_score=0,
        )

        db = MockSession()
        # get_active_weights
        db.queue_result(MockResult(scalar_value=None))
        # select all hypotheses
        db.queue_result(MockResult(rows=[h]))

        result = await engine.rescore_all(db)
        assert result["rescored"] == 1
        assert result["total"] == 1

        # Verify the composite was recomputed with default weights (11 dims)
        expected = (
            60 * 0.12 + 40 * 0.12 + 80 * 0.10
            + 20 * 0.09 + 50 * 0.06 + 70 * 0.09
        )
        # 7.2 + 4.8 + 8.0 + 1.8 + 3.0 + 6.3 = 31.1
        assert h.composite_score == 31.1
        assert h.evidence_strength == "suggestive"

    @pytest.mark.asyncio
    async def test_rescore_with_custom_weights(self):
        """Rescoring with custom novelty-focused weights."""
        h = make_hypothesis(
            pathway_overlap_score=20,
            expression_correlation_score=20,
            literature_support_score=20,
            clinical_evidence_score=20,
            safety_score=20,
            novelty_score=100,
        )

        novelty_weights = {
            "pathway_overlap": 0.10,
            "expression_correlation": 0.10,
            "literature_support": 0.10,
            "clinical_evidence": 0.10,
            "safety": 0.10,
            "novelty": 0.50,
        }

        db = MockSession()
        # select all hypotheses (weights provided directly, no DB call for weights)
        db.queue_result(MockResult(rows=[h]))

        result = await engine.rescore_all(db, weights=novelty_weights)
        assert result["rescored"] == 1

        # 20*0.1*4 + 20*0.1 + 100*0.5 = 8 + 2 + 50 = 60
        expected = 20 * 0.10 * 5 + 100 * 0.50  # 10 + 50 = 60
        assert h.composite_score == 60.0
        assert h.evidence_strength == "moderate"

    @pytest.mark.asyncio
    async def test_rescore_handles_none_scores(self):
        """Rescoring handles hypotheses with None dimension scores."""
        h = make_hypothesis(
            pathway_overlap_score=None,
            expression_correlation_score=None,
            literature_support_score=None,
            clinical_evidence_score=None,
            safety_score=None,
            novelty_score=None,
            causal_dependency_score=None,
            gnn_link_score=None,
            mutation_context_score=None,
            polypharmacology_score=None,
            pharmacological_response_score=None,
        )

        db = MockSession()
        db.queue_result(MockResult(scalar_value=None))
        db.queue_result(MockResult(rows=[h]))

        result = await engine.rescore_all(db)
        assert result["rescored"] == 1
        assert h.composite_score == 0.0
        assert h.evidence_strength == "speculative"

    @pytest.mark.asyncio
    async def test_rescore_multiple_hypotheses(self):
        """Rescoring handles multiple hypotheses."""
        h1 = make_hypothesis(id=1, pathway_overlap_score=80, expression_correlation_score=80,
                             literature_support_score=80, clinical_evidence_score=80,
                             safety_score=80, novelty_score=80,
                             causal_dependency_score=80, gnn_link_score=80,
                             mutation_context_score=80, polypharmacology_score=80,
                             pharmacological_response_score=80)
        h2 = make_hypothesis(id=2, pathway_overlap_score=10, expression_correlation_score=10,
                             literature_support_score=10, clinical_evidence_score=10,
                             safety_score=10, novelty_score=10,
                             causal_dependency_score=10, gnn_link_score=10,
                             mutation_context_score=10, polypharmacology_score=10,
                             pharmacological_response_score=10)

        db = MockSession()
        db.queue_result(MockResult(scalar_value=None))
        db.queue_result(MockResult(rows=[h1, h2]))

        result = await engine.rescore_all(db)
        assert result["rescored"] == 2

        assert h1.composite_score == 80.0
        assert h1.evidence_strength == "strong"

        assert h2.composite_score == 10.0
        assert h2.evidence_strength == "speculative"


# ===================================================================
# ScoringConfig integration with HypothesisEngine
# ===================================================================


class TestScoringConfigIntegration:
    """Tests that verify ScoringConfig and HypothesisEngine work together."""

    def test_engine_has_scorer_and_config(self):
        """Engine initializes with EvidenceScorer and ScoringConfig."""
        e = HypothesisEngine()
        assert e.scorer is not None
        assert e.config is not None
        assert isinstance(e.config, ScoringConfig)

    def test_all_strength_categories_reachable(self):
        """Verify all 4 strength categories are reachable via scoring."""
        cfg = ScoringConfig()

        test_cases = [
            (80, "strong"),
            (60, "moderate"),
            (30, "suggestive"),
            (10, "speculative"),
        ]
        for score, expected in test_cases:
            assert cfg.determine_evidence_strength(score) == expected

    def test_composite_with_extreme_weights(self):
        """Only novelty weighted = novelty score drives composite."""
        cfg = ScoringConfig()
        weights = {dim: 0.0 for dim in DIMENSIONS}
        weights["novelty"] = 1.0

        dim_scores = {
            "pathway_overlap": {"score": 100},
            "expression_correlation": {"score": 100},
            "literature_support": {"score": 100},
            "clinical_evidence": {"score": 100},
            "safety": {"score": 100},
            "novelty": {"score": 25},
        }

        composite = cfg.compute_composite_score(dim_scores, weights)
        assert composite == 25.0

    def test_default_weights_symmetry(self):
        """Default weights treat pathway and expression equally."""
        assert DEFAULT_WEIGHTS["pathway_overlap"] == DEFAULT_WEIGHTS["expression_correlation"]

    def test_default_weights_clinical_and_novelty(self):
        """Clinical and novelty have same weight in defaults."""
        assert DEFAULT_WEIGHTS["clinical_evidence"] == DEFAULT_WEIGHTS["novelty"]

    def test_default_weights_sum_to_one(self):
        """Default weights must sum to exactly 1.0."""
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9


# ===================================================================
# Edge cases
# ===================================================================


class TestEdgeCases:
    def test_summary_with_empty_strategies(self):
        """Summary with empty strategies list still works."""
        dim_scores = {dim: {"score": 50} for dim in DIMENSIONS}
        summary = engine._generate_summary(
            "DrugX", "CancerY", [], dim_scores, 50.0,
        )
        assert "DrugX" in summary
        assert "Identified via: " in summary

    def test_summary_with_unknown_strategy(self):
        """Summary with unknown strategy falls back to raw name."""
        dim_scores = {dim: {"score": 50} for dim in DIMENSIONS}
        summary = engine._generate_summary(
            "DrugX", "CancerY",
            ["future_strategy"],
            dim_scores, 50.0,
        )
        assert "future_strategy" in summary

    def test_composite_score_boundary_values(self):
        """Test composite at exact boundary values for strength mapping."""
        cfg = ScoringConfig()

        # Exactly at boundaries
        assert cfg.determine_evidence_strength(75.0) == "strong"
        assert cfg.determine_evidence_strength(74.99) == "moderate"
        assert cfg.determine_evidence_strength(50.0) == "moderate"
        assert cfg.determine_evidence_strength(49.99) == "suggestive"
        assert cfg.determine_evidence_strength(25.0) == "suggestive"
        assert cfg.determine_evidence_strength(24.99) == "speculative"
        assert cfg.determine_evidence_strength(0.0) == "speculative"
