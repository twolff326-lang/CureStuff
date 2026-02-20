"""Tests for the Tallula Algorithm — Stochastic Resonance Ensemble Discovery.

Covers all 5 phases:
  Phase 1: Lens generation (Dirichlet sampling, dropout, renormalization)
  Phase 2: Ensemble scoring (per-lens composite scores)
  Phase 3: Distribution analysis (ubiquity, resonance, fragility)
  Phase 4: Discovery classification (robust/resonant/fragile/moderate/weak)
  Phase 5: Resonance decomposition (activation dimension identification)
  Full pipeline: TallulaEngine.run() end-to-end
"""

import numpy as np
import pytest

from app.services.tallula import (
    DISCOVERY_CLASSES,
    TallulaEngine,
    classify_discovery,
    compute_fragility,
    compute_resonance,
    compute_score_distribution_stats,
    compute_ubiquity,
    decompose_resonance,
    ensemble_score_all,
    generate_lenses,
    score_hypothesis_under_lenses,
)
from app.services.scoring_config import DIMENSIONS


# ===================================================================
# Phase 1 — Lens Generation
# ===================================================================


class TestGenerateLenses:
    def test_correct_count(self):
        lenses = generate_lenses(n_lenses=50, seed=1)
        assert len(lenses) == 50

    def test_lens_structure(self):
        lenses = generate_lenses(n_lenses=5, seed=1)
        for lens in lenses:
            assert "weights" in lens
            assert "mask" in lens
            assert "n_active" in lens
            assert "lens_id" in lens
            assert len(lens["weights"]) == len(DIMENSIONS)
            assert len(lens["mask"]) == len(DIMENSIONS)

    def test_weights_sum_to_one(self):
        lenses = generate_lenses(n_lenses=100, seed=42)
        for lens in lenses:
            total = sum(lens["weights"].values())
            assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, expected 1.0"

    def test_masked_dimensions_have_zero_weight(self):
        lenses = generate_lenses(n_lenses=100, seed=42)
        for lens in lenses:
            for dim in DIMENSIONS:
                if not lens["mask"][dim]:
                    assert lens["weights"][dim] == 0.0

    def test_minimum_two_active_dimensions(self):
        # Even with high dropout, at least 2 survive
        lenses = generate_lenses(n_lenses=200, dropout_rate=0.8, seed=99)
        for lens in lenses:
            assert lens["n_active"] >= 2

    def test_reproducibility_with_seed(self):
        a = generate_lenses(n_lenses=20, seed=123)
        b = generate_lenses(n_lenses=20, seed=123)
        for la, lb in zip(a, b):
            for dim in DIMENSIONS:
                assert la["weights"][dim] == lb["weights"][dim]

    def test_different_seeds_produce_different_lenses(self):
        a = generate_lenses(n_lenses=10, seed=1)
        b = generate_lenses(n_lenses=10, seed=2)
        # At least one lens should differ
        any_different = False
        for la, lb in zip(a, b):
            for dim in DIMENSIONS:
                if abs(la["weights"][dim] - lb["weights"][dim]) > 1e-6:
                    any_different = True
                    break
        assert any_different

    def test_low_alpha_creates_sparse_weights(self):
        lenses = generate_lenses(n_lenses=100, dirichlet_alpha=0.1, seed=42)
        # With low alpha, weights should be spikier (higher max weight)
        max_weights = [max(l["weights"].values()) for l in lenses]
        avg_max = np.mean(max_weights)
        assert avg_max > 0.3, f"Low alpha should produce spiky weights, got avg max {avg_max}"

    def test_high_alpha_creates_uniform_weights(self):
        lenses = generate_lenses(n_lenses=100, dirichlet_alpha=100.0, dropout_rate=0.0, seed=42)
        # With high alpha and no dropout, weights should be near uniform
        expected = 1.0 / len(DIMENSIONS)
        for lens in lenses:
            for dim in DIMENSIONS:
                assert abs(lens["weights"][dim] - expected) < 0.05


# ===================================================================
# Phase 2 — Ensemble Scoring
# ===================================================================


class TestEnsembleScoring:
    def _make_dim_scores(self, **overrides):
        scores = {dim: 50.0 for dim in DIMENSIONS}
        scores.update(overrides)
        return scores

    def test_uniform_weights_produce_mean(self):
        """With uniform weights and uniform scores, composite = score."""
        dim_scores = self._make_dim_scores()
        lenses = generate_lenses(n_lenses=50, dirichlet_alpha=100.0, dropout_rate=0.0, seed=1)
        result = score_hypothesis_under_lenses(dim_scores, lenses)
        assert len(result) == 50
        for score in result:
            assert abs(score - 50.0) < 2.0  # Near 50 with high alpha

    def test_scores_clamped_to_0_100(self):
        dim_scores = {dim: 0.0 for dim in DIMENSIONS}
        lenses = generate_lenses(n_lenses=10, seed=1)
        result = score_hypothesis_under_lenses(dim_scores, lenses)
        assert all(0.0 <= s <= 100.0 for s in result)

    def test_high_single_dimension_creates_variance(self):
        """A hypothesis with one very high dimension should have high
        variance across lenses (depending on how much weight that dim gets)."""
        dim_scores = {dim: 10.0 for dim in DIMENSIONS}
        dim_scores["novelty"] = 95.0
        lenses = generate_lenses(n_lenses=200, seed=42)
        result = score_hypothesis_under_lenses(dim_scores, lenses)
        assert np.std(result) > 5.0, "Expected significant variance from uneven dimension scores"

    def test_ensemble_score_all(self):
        hypotheses = [
            {
                "hypothesis_id": 1,
                "dimension_scores": self._make_dim_scores(novelty=90.0),
            },
            {
                "hypothesis_id": 2,
                "dimension_scores": self._make_dim_scores(safety=80.0),
            },
        ]
        lenses = generate_lenses(n_lenses=20, seed=1)
        result = ensemble_score_all(hypotheses, lenses)
        assert 1 in result
        assert 2 in result
        assert len(result[1]) == 20
        assert len(result[2]) == 20


# ===================================================================
# Phase 3 — Distribution Analysis
# ===================================================================


class TestUbiquity:
    def test_all_high_scores(self):
        scores = np.array([90.0] * 100)
        u = compute_ubiquity(scores, threshold_percentile=50.0)
        assert u == 1.0

    def test_all_low_scores(self):
        scores = np.array([10.0] * 100)
        global_scores = np.concatenate([scores, np.full(100, 80.0)])
        u = compute_ubiquity(scores, global_scores=global_scores)
        assert u < 0.1

    def test_mixed_scores(self):
        scores = np.concatenate([np.full(50, 80.0), np.full(50, 20.0)])
        u = compute_ubiquity(scores, threshold_percentile=50.0)
        assert 0.3 <= u <= 0.7


class TestResonance:
    def test_uniform_scores_low_resonance(self):
        scores = np.full(100, 50.0)
        r = compute_resonance(scores)
        assert r == 1.0  # max == median

    def test_peaked_scores_high_resonance(self):
        scores = np.concatenate([np.full(90, 10.0), np.full(10, 80.0)])
        r = compute_resonance(scores)
        assert r > 3.0

    def test_all_zeros_handled(self):
        scores = np.zeros(50)
        r = compute_resonance(scores)
        assert r >= 0.0  # Should not crash


class TestFragility:
    def test_balanced_scores_low_fragility(self):
        dim_scores = {dim: 50.0 for dim in DIMENSIONS}
        result = compute_fragility(dim_scores, 50.0)
        assert result["fragility_index"] < 0.3

    def test_single_high_dimension_high_fragility(self):
        dim_scores = {dim: 0.0 for dim in DIMENSIONS}
        dim_scores["novelty"] = 100.0
        result = compute_fragility(dim_scores, 100.0 / len(DIMENSIONS))
        assert result["fragility_index"] > 0.0
        assert result["critical_dimension"] == "novelty"

    def test_zero_baseline(self):
        dim_scores = {dim: 0.0 for dim in DIMENSIONS}
        result = compute_fragility(dim_scores, 0.0)
        assert result["fragility_index"] == 0.0

    def test_returns_all_dimensions(self):
        dim_scores = {dim: 50.0 for dim in DIMENSIONS}
        result = compute_fragility(dim_scores, 50.0)
        for dim in DIMENSIONS:
            assert dim in result["ablation_impacts"]


class TestDistributionStats:
    def test_basic_stats(self):
        scores = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        stats = compute_score_distribution_stats(scores)
        assert stats["mean"] == 30.0
        assert stats["median"] == 30.0
        assert stats["min"] == 10.0
        assert stats["max"] == 50.0

    def test_empty_array(self):
        stats = compute_score_distribution_stats(np.array([]))
        assert stats == {}


# ===================================================================
# Phase 4 — Discovery Classification
# ===================================================================


class TestClassifyDiscovery:
    def test_weak(self):
        assert classify_discovery(
            ubiquity=0.5, resonance=1.0, fragility_index=0.1, mean_score=10.0
        ) == "weak"

    def test_fragile(self):
        assert classify_discovery(
            ubiquity=0.5, resonance=1.0, fragility_index=0.6, mean_score=50.0
        ) == "fragile"

    def test_robust(self):
        assert classify_discovery(
            ubiquity=0.8, resonance=1.5, fragility_index=0.1, mean_score=60.0
        ) == "robust"

    def test_resonant(self):
        assert classify_discovery(
            ubiquity=0.1, resonance=4.0, fragility_index=0.2, mean_score=40.0
        ) == "resonant"

    def test_moderate(self):
        assert classify_discovery(
            ubiquity=0.3, resonance=1.5, fragility_index=0.2, mean_score=40.0
        ) == "moderate"

    def test_all_classes_documented(self):
        for cls in ["robust", "resonant", "fragile", "moderate", "weak"]:
            assert cls in DISCOVERY_CLASSES


# ===================================================================
# Phase 5 — Resonance Decomposition
# ===================================================================


class TestResonanceDecomposition:
    def test_identifies_activation_dimension(self):
        """A hypothesis with one dominant dimension should have that
        dimension identified as the top activator."""
        dim_scores = {dim: 10.0 for dim in DIMENSIONS}
        dim_scores["causal_dependency"] = 95.0

        lenses = generate_lenses(n_lenses=200, seed=42)
        lens_scores = score_hypothesis_under_lenses(dim_scores, lenses)

        result = decompose_resonance(dim_scores, lenses, lens_scores)
        assert "activation_dimensions" in result
        assert "narrative" in result
        assert len(result["activation_dimensions"]) == len(DIMENSIONS)

        # The top activator should be causal_dependency
        top_activator = result["activation_dimensions"][0]["dimension"]
        assert top_activator == "causal_dependency", (
            f"Expected causal_dependency as top activator, got {top_activator}"
        )

    def test_narrative_is_nonempty(self):
        dim_scores = {dim: 50.0 for dim in DIMENSIONS}
        lenses = generate_lenses(n_lenses=50, seed=1)
        lens_scores = score_hypothesis_under_lenses(dim_scores, lenses)
        result = decompose_resonance(dim_scores, lenses, lens_scores)
        assert isinstance(result["narrative"], str)
        assert len(result["narrative"]) > 0


# ===================================================================
# Full Pipeline — TallulaEngine
# ===================================================================


class TestTallulaEngine:
    def _make_hypotheses(self):
        """Create a mix of hypothesis types that should produce
        different discovery classifications."""
        return [
            # Should be ROBUST: high across all dimensions
            {
                "hypothesis_id": 1,
                "drug_id": 10,
                "cancer_type_id": 1,
                "title": "Well-supported drug for Breast Cancer",
                "composite_score": 75.0,
                "dimension_scores": {dim: 75.0 for dim in DIMENSIONS},
            },
            # Should be RESONANT: one very high dim, rest low
            {
                "hypothesis_id": 2,
                "drug_id": 20,
                "cancer_type_id": 2,
                "title": "Hidden gem for Lung Cancer",
                "composite_score": 25.0,
                "dimension_scores": {
                    "pathway_overlap": 5.0,
                    "expression_correlation": 5.0,
                    "literature_support": 2.0,
                    "clinical_evidence": 0.0,
                    "safety": 5.0,
                    "novelty": 98.0,
                    "causal_dependency": 90.0,
                    "gnn_link": 0.0,
                    "mutation_context": 0.0,
                    "polypharmacology": 0.0,
                },
            },
            # Should be WEAK: low everywhere
            {
                "hypothesis_id": 3,
                "drug_id": 30,
                "cancer_type_id": 3,
                "title": "No signal drug for Colon Cancer",
                "composite_score": 5.0,
                "dimension_scores": {dim: 5.0 for dim in DIMENSIONS},
            },
            # MODERATE: middling scores
            {
                "hypothesis_id": 4,
                "drug_id": 40,
                "cancer_type_id": 1,
                "title": "Moderate drug for Breast Cancer",
                "composite_score": 40.0,
                "dimension_scores": {dim: 40.0 for dim in DIMENSIONS},
            },
        ]

    def test_returns_all_hypotheses(self):
        engine = TallulaEngine()
        hypotheses = self._make_hypotheses()
        result = engine.run(hypotheses, n_lenses=100, seed=42)
        assert len(result["discoveries"]) == len(hypotheses)

    def test_result_structure(self):
        engine = TallulaEngine()
        result = engine.run(self._make_hypotheses(), n_lenses=100, seed=42)
        assert "algorithm" in result
        assert result["algorithm"] == "tallula"
        assert "version" in result
        assert "parameters" in result
        assert "summary" in result
        assert "discoveries" in result

    def test_discovery_structure(self):
        engine = TallulaEngine()
        result = engine.run(self._make_hypotheses(), n_lenses=100, seed=42)
        for disc in result["discoveries"]:
            assert "hypothesis_id" in disc
            assert "discovery_class" in disc
            assert disc["discovery_class"] in DISCOVERY_CLASSES
            assert "tallula_metrics" in disc
            assert "ubiquity" in disc["tallula_metrics"]
            assert "resonance" in disc["tallula_metrics"]
            assert "fragility_index" in disc["tallula_metrics"]
            assert "score_distribution" in disc

    def test_robust_hypothesis_classified_correctly(self):
        engine = TallulaEngine()
        result = engine.run(self._make_hypotheses(), n_lenses=200, seed=42)
        h1 = next(d for d in result["discoveries"] if d["hypothesis_id"] == 1)
        assert h1["discovery_class"] == "robust", (
            f"High-across-all-dims hypothesis should be robust, got {h1['discovery_class']}"
        )

    def test_weak_hypothesis_classified_correctly(self):
        engine = TallulaEngine()
        result = engine.run(self._make_hypotheses(), n_lenses=200, seed=42)
        h3 = next(d for d in result["discoveries"] if d["hypothesis_id"] == 3)
        assert h3["discovery_class"] == "weak", (
            f"Low-everywhere hypothesis should be weak, got {h3['discovery_class']}"
        )

    def test_resonant_sorted_first(self):
        engine = TallulaEngine()
        result = engine.run(self._make_hypotheses(), n_lenses=200, seed=42)
        classes = [d["discovery_class"] for d in result["discoveries"]]
        # Resonant should appear before weak
        if "resonant" in classes and "weak" in classes:
            assert classes.index("resonant") < classes.index("weak")

    def test_summary_counts(self):
        engine = TallulaEngine()
        result = engine.run(self._make_hypotheses(), n_lenses=100, seed=42)
        counts = result["summary"]["class_counts"]
        total = sum(counts.values())
        assert total == len(self._make_hypotheses())

    def test_top_n_filter(self):
        engine = TallulaEngine()
        hypotheses = self._make_hypotheses()
        result = engine.run(hypotheses, n_lenses=100, seed=42, top_n=1)
        # Should have at most 1 per class
        from collections import Counter
        class_counts = Counter(d["discovery_class"] for d in result["discoveries"])
        for count in class_counts.values():
            assert count <= 1

    def test_empty_input(self):
        engine = TallulaEngine()
        result = engine.run([], n_lenses=50)
        assert "error" in result

    def test_reproducibility(self):
        engine = TallulaEngine()
        hyps = self._make_hypotheses()
        a = engine.run(hyps, n_lenses=100, seed=7)
        b = engine.run(hyps, n_lenses=100, seed=7)
        for da, db in zip(a["discoveries"], b["discoveries"]):
            assert da["hypothesis_id"] == db["hypothesis_id"]
            assert da["discovery_class"] == db["discovery_class"]
            assert da["tallula_metrics"]["resonance"] == db["tallula_metrics"]["resonance"]
