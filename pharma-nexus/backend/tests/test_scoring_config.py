"""Tests for ScoringConfig — weight validation, composite scoring, and strength mapping.

Tests the pure-logic methods that don't require database access,
plus async methods using a mocked session.
"""

import pytest

from app.services.scoring_config import (
    DEFAULT_WEIGHTS,
    DIMENSIONS,
    ScoringConfig,
)
from tests.conftest import MockResult, MockSession

# Reusable instance
config = ScoringConfig()


# ===================================================================
# _validate_weights
# ===================================================================


class TestValidateWeights:
    def test_valid_default_weights(self):
        """Default weights pass validation."""
        ScoringConfig._validate_weights(DEFAULT_WEIGHTS)

    def test_valid_custom_weights(self):
        """Custom weights summing to 1.0 pass validation."""
        w = {
            "pathway_overlap": 0.30,
            "expression_correlation": 0.10,
            "literature_support": 0.10,
            "clinical_evidence": 0.20,
            "safety": 0.20,
            "novelty": 0.10,
        }
        ScoringConfig._validate_weights(w)

    def test_missing_dimension_raises(self):
        """Missing a dimension raises ValueError."""
        w = {k: v for k, v in DEFAULT_WEIGHTS.items() if k != "novelty"}
        with pytest.raises(ValueError, match="Missing weight dimensions"):
            ScoringConfig._validate_weights(w)

    def test_extra_dimension_raises(self):
        """Extra unknown dimension raises ValueError."""
        w = {**DEFAULT_WEIGHTS, "unknown_dim": 0.05}
        with pytest.raises(ValueError, match="Unknown weight dimensions"):
            ScoringConfig._validate_weights(w)

    def test_weight_above_one_raises(self):
        """Weight > 1.0 raises ValueError."""
        w = {**DEFAULT_WEIGHTS, "novelty": 1.5}
        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            ScoringConfig._validate_weights(w)

    def test_negative_weight_raises(self):
        """Negative weight raises ValueError."""
        w = {**DEFAULT_WEIGHTS, "safety": -0.1}
        with pytest.raises(ValueError, match="must be between 0.0 and 1.0"):
            ScoringConfig._validate_weights(w)

    def test_weights_not_summing_to_one_raises(self):
        """Weights summing far from 1.0 raises ValueError."""
        w = {dim: 0.05 for dim in DIMENSIONS}  # sums to 0.5
        with pytest.raises(ValueError, match="must sum to 1.0"):
            ScoringConfig._validate_weights(w)

    def test_weights_within_tolerance(self):
        """Weights summing to 1.005 pass (within 0.01 tolerance)."""
        w = {
            "pathway_overlap": 0.201,
            "expression_correlation": 0.201,
            "literature_support": 0.201,
            "clinical_evidence": 0.151,
            "safety": 0.101,
            "novelty": 0.15,
        }
        # Sum = 1.005, within 0.01 tolerance
        ScoringConfig._validate_weights(w)

    def test_all_zero_except_one_raises(self):
        """All-zero-except-one doesn't sum to 1.0 unless one=1.0."""
        w = {dim: 0.0 for dim in DIMENSIONS}
        w["novelty"] = 0.5
        with pytest.raises(ValueError, match="must sum to 1.0"):
            ScoringConfig._validate_weights(w)

    def test_single_dimension_at_one(self):
        """Single dimension at 1.0, rest at 0.0 is valid."""
        w = {dim: 0.0 for dim in DIMENSIONS}
        w["pathway_overlap"] = 1.0
        ScoringConfig._validate_weights(w)


# ===================================================================
# compute_composite_score
# ===================================================================


class TestComputeCompositeScore:
    def test_all_zeros(self):
        """All zero dimension scores produce composite 0."""
        scores = {dim: {"score": 0} for dim in DIMENSIONS}
        assert config.compute_composite_score(scores, DEFAULT_WEIGHTS) == 0.0

    def test_all_hundreds(self):
        """All 100 dimension scores produce composite 100."""
        scores = {dim: {"score": 100} for dim in DIMENSIONS}
        assert config.compute_composite_score(scores, DEFAULT_WEIGHTS) == 100.0

    def test_balanced_weights_with_uniform_scores(self):
        """Uniform scores of 50 with default weights = 50."""
        scores = {dim: {"score": 50} for dim in DIMENSIONS}
        assert config.compute_composite_score(scores, DEFAULT_WEIGHTS) == 50.0

    def test_single_dimension_weighted(self):
        """Only pathway_overlap has weight 1.0, score 75 -> composite 75."""
        weights = {dim: 0.0 for dim in DIMENSIONS}
        weights["pathway_overlap"] = 1.0
        scores = {
            "pathway_overlap": {"score": 75},
            "expression_correlation": {"score": 100},
            "literature_support": {"score": 100},
            "clinical_evidence": {"score": 100},
            "safety": {"score": 100},
            "novelty": {"score": 100},
        }
        assert config.compute_composite_score(scores, weights) == 75.0

    def test_mixed_scores_with_default_weights(self):
        """Specific mixed scores with default weights produce expected composite."""
        scores = {dim: {"score": 50} for dim in DIMENSIONS}
        scores["pathway_overlap"]["score"] = 80
        scores["novelty"]["score"] = 90
        result = config.compute_composite_score(scores, DEFAULT_WEIGHTS)
        # 80*0.14 + 50*0.14 + 50*0.12 + 50*0.10 + 50*0.07 + 90*0.10
        # + 50*0.13 + 50*0.0 + 50*0.12 + 50*0.08 = 58.2
        assert result == 58.2

    def test_capped_at_100(self):
        """Composite never exceeds 100 even with abnormal scores."""
        scores = {dim: {"score": 200} for dim in DIMENSIONS}
        assert config.compute_composite_score(scores, DEFAULT_WEIGHTS) == 100.0

    def test_floor_at_zero(self):
        """Composite never goes below 0 even with negative scores."""
        scores = {dim: {"score": -50} for dim in DIMENSIONS}
        assert config.compute_composite_score(scores, DEFAULT_WEIGHTS) == 0.0

    def test_missing_dimension_defaults_to_zero(self):
        """Missing dimension in scores dict treated as score 0."""
        scores = {
            "pathway_overlap": {"score": 100},
            # Missing all others
        }
        result = config.compute_composite_score(scores, DEFAULT_WEIGHTS)
        assert result == 14.0  # 100 * 0.14

    def test_missing_score_key_defaults_to_zero(self):
        """Dimension dict without 'score' key treated as 0."""
        scores = {dim: {"details": "something"} for dim in DIMENSIONS}
        assert config.compute_composite_score(scores, DEFAULT_WEIGHTS) == 0.0

    def test_result_is_rounded(self):
        """Result is rounded to 1 decimal place."""
        scores = {dim: {"score": 33} for dim in DIMENSIONS}
        result = config.compute_composite_score(scores, DEFAULT_WEIGHTS)
        assert result == 33.0  # 33 * 1.0 = 33.0


# ===================================================================
# determine_evidence_strength
# ===================================================================


class TestDetermineEvidenceStrength:
    def test_strong(self):
        assert config.determine_evidence_strength(75) == "strong"

    def test_strong_at_boundary(self):
        assert config.determine_evidence_strength(75.0) == "strong"

    def test_strong_high(self):
        assert config.determine_evidence_strength(100) == "strong"

    def test_moderate(self):
        assert config.determine_evidence_strength(50) == "moderate"

    def test_moderate_at_boundary(self):
        assert config.determine_evidence_strength(50.0) == "moderate"

    def test_moderate_just_below_strong(self):
        assert config.determine_evidence_strength(74.9) == "moderate"

    def test_suggestive(self):
        assert config.determine_evidence_strength(25) == "suggestive"

    def test_suggestive_at_boundary(self):
        assert config.determine_evidence_strength(25.0) == "suggestive"

    def test_suggestive_just_below_moderate(self):
        assert config.determine_evidence_strength(49.9) == "suggestive"

    def test_speculative(self):
        assert config.determine_evidence_strength(10) == "speculative"

    def test_speculative_zero(self):
        assert config.determine_evidence_strength(0) == "speculative"

    def test_speculative_just_below_suggestive(self):
        assert config.determine_evidence_strength(24.9) == "speculative"


# ===================================================================
# get_active_weights (async, mocked DB)
# ===================================================================


class TestGetActiveWeights:
    @pytest.mark.asyncio
    async def test_returns_default_when_no_db_entry(self):
        """Falls back to DEFAULT_WEIGHTS when no active preset in DB."""
        db = MockSession()
        db.queue_result(MockResult(scalar_value=None))
        result = await config.get_active_weights(db)
        assert result == DEFAULT_WEIGHTS

    @pytest.mark.asyncio
    async def test_returns_db_weights_when_available(self):
        """Returns weights from database when an active preset exists."""
        custom = {dim: 1.0 / 6 for dim in DIMENSIONS}
        preset = type("SW", (), {"weights": custom, "is_default": 1})()
        db = MockSession()
        db.queue_result(MockResult(scalar_value=preset))
        result = await config.get_active_weights(db)
        assert result == custom

    @pytest.mark.asyncio
    async def test_returns_default_when_preset_has_no_weights(self):
        """Falls back to defaults if preset exists but has empty weights."""
        preset = type("SW", (), {"weights": None, "is_default": 1})()
        db = MockSession()
        db.queue_result(MockResult(scalar_value=preset))
        result = await config.get_active_weights(db)
        assert result == DEFAULT_WEIGHTS


# ===================================================================
# DIMENSIONS constant
# ===================================================================


class TestDimensions:
    def test_dimension_count(self):
        assert len(DIMENSIONS) == 10

    def test_all_dimensions_in_default_weights(self):
        for dim in DIMENSIONS:
            assert dim in DEFAULT_WEIGHTS

    def test_default_weights_sum_to_one(self):
        assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 0.001
