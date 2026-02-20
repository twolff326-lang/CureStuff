"""Tests for app.services.expression_analyzer.ExpressionAnalyzer.

Tests the pure scoring methods — action-expression compatibility,
binding affinity weights, synthetic lethality scoring, and explanation
generation — without database access.
"""

import pytest

from app.services.expression_analyzer import ExpressionAnalyzer


@pytest.fixture
def analyzer():
    return ExpressionAnalyzer()


# ===================================================================
# Action-Expression Compatibility
# ===================================================================

class TestActionExpressionCompatibility:
    """Tests for _compute_action_expression_compatibility."""

    # --- Inhibitors ---

    def test_inhibitor_overexpressed_high(self, analyzer):
        """Inhibitor + strongly overexpressed target = high compatibility."""
        result = analyzer._compute_action_expression_compatibility(
            action_type="inhibitor",
            expression_zscore=3.0,
            frequency_overexpressed=40,
            frequency_underexpressed=0,
        )
        assert result["label"] == "high"
        assert result["score"] >= 60

    def test_inhibitor_mildly_overexpressed_moderate(self, analyzer):
        """Inhibitor + mildly overexpressed = moderate."""
        result = analyzer._compute_action_expression_compatibility(
            action_type="inhibitor",
            expression_zscore=1.5,
            frequency_overexpressed=15,
            frequency_underexpressed=0,
        )
        assert result["label"] == "moderate"
        assert 30 <= result["score"] <= 70

    def test_inhibitor_underexpressed_poor(self, analyzer):
        """Inhibitor + underexpressed target = poor match."""
        result = analyzer._compute_action_expression_compatibility(
            action_type="inhibitor",
            expression_zscore=-2.0,
            frequency_overexpressed=0,
            frequency_underexpressed=30,
        )
        assert result["label"] == "poor"
        assert result["score"] <= 15

    def test_inhibitor_normal_expression_low(self, analyzer):
        """Inhibitor + normal expression = low/uncertain."""
        result = analyzer._compute_action_expression_compatibility(
            action_type="inhibitor",
            expression_zscore=0.2,
            frequency_overexpressed=3,
            frequency_underexpressed=5,
        )
        assert result["label"] == "low"
        assert result["score"] == 25

    # --- Agonists ---

    def test_agonist_underexpressed_high(self, analyzer):
        """Agonist + strongly underexpressed target = high compatibility."""
        result = analyzer._compute_action_expression_compatibility(
            action_type="agonist",
            expression_zscore=-3.0,
            frequency_overexpressed=0,
            frequency_underexpressed=40,
        )
        assert result["label"] == "high"
        assert result["score"] >= 60

    def test_agonist_mildly_underexpressed_moderate(self, analyzer):
        result = analyzer._compute_action_expression_compatibility(
            action_type="agonist",
            expression_zscore=-1.5,
            frequency_overexpressed=0,
            frequency_underexpressed=15,
        )
        assert result["label"] == "moderate"

    def test_agonist_overexpressed_poor(self, analyzer):
        """Agonist + already overexpressed = poor (harmful to activate more)."""
        result = analyzer._compute_action_expression_compatibility(
            action_type="agonist",
            expression_zscore=3.0,
            frequency_overexpressed=40,
            frequency_underexpressed=0,
        )
        assert result["label"] == "poor"
        assert result["score"] <= 10

    def test_agonist_normal_low(self, analyzer):
        result = analyzer._compute_action_expression_compatibility(
            action_type="agonist",
            expression_zscore=0.0,
            frequency_overexpressed=5,
            frequency_underexpressed=5,
        )
        assert result["label"] == "low"

    # --- Unknown action type ---

    def test_unknown_action_with_deviation(self, analyzer):
        result = analyzer._compute_action_expression_compatibility(
            action_type="substrate",
            expression_zscore=2.5,
            frequency_overexpressed=30,
            frequency_underexpressed=0,
        )
        assert result["label"] == "unknown"
        assert result["score"] > 15

    def test_unknown_action_no_deviation(self, analyzer):
        result = analyzer._compute_action_expression_compatibility(
            action_type="unknown",
            expression_zscore=0.3,
            frequency_overexpressed=0,
            frequency_underexpressed=0,
        )
        assert result["label"] == "unknown"
        assert result["score"] == 15

    # --- Edge cases ---

    def test_none_action_type(self, analyzer):
        result = analyzer._compute_action_expression_compatibility(
            action_type=None,
            expression_zscore=0,
            frequency_overexpressed=0,
            frequency_underexpressed=0,
        )
        assert result["label"] == "unknown"

    def test_score_always_bounded(self, analyzer):
        """Score should always be in [0, 100] regardless of inputs."""
        extremes = [
            ("inhibitor", 10.0, 100, 0),
            ("inhibitor", -10.0, 0, 100),
            ("agonist", 10.0, 100, 0),
            ("agonist", -10.0, 0, 100),
        ]
        for action, z, fo, fu in extremes:
            result = analyzer._compute_action_expression_compatibility(
                action, z, fo, fu
            )
            assert 0 <= result["score"] <= 100

    def test_antagonist_treated_as_inhibitory(self, analyzer):
        """'antagonist' should behave like an inhibitor."""
        result = analyzer._compute_action_expression_compatibility(
            action_type="antagonist",
            expression_zscore=3.0,
            frequency_overexpressed=40,
            frequency_underexpressed=0,
        )
        assert result["label"] == "high"

    def test_activator_treated_as_agonist(self, analyzer):
        """'activator' should behave like an agonist."""
        result = analyzer._compute_action_expression_compatibility(
            action_type="activator",
            expression_zscore=-3.0,
            frequency_overexpressed=0,
            frequency_underexpressed=40,
        )
        assert result["label"] == "high"

    def test_explanation_always_present(self, analyzer):
        result = analyzer._compute_action_expression_compatibility(
            "inhibitor", 2.5, 30, 0
        )
        assert len(result["explanation"]) > 10


# ===================================================================
# Binding Affinity to Weight
# ===================================================================

class TestAffinityToWeight:
    """Tests for _affinity_to_weight."""

    def test_none_affinity(self, analyzer):
        assert analyzer._affinity_to_weight(None) == 0.2

    def test_sub_nanomolar(self, analyzer):
        """< 1 nM = picomolar binding = strongest."""
        assert analyzer._affinity_to_weight(0.5) == 1.0

    def test_single_digit_nm(self, analyzer):
        assert analyzer._affinity_to_weight(5) == 0.9

    def test_double_digit_nm(self, analyzer):
        assert analyzer._affinity_to_weight(50) == 0.7

    def test_triple_digit_nm(self, analyzer):
        assert analyzer._affinity_to_weight(500) == 0.5

    def test_micromolar(self, analyzer):
        """1000-10000 nM = micromolar = weak."""
        assert analyzer._affinity_to_weight(5000) == 0.3

    def test_very_weak(self, analyzer):
        """> 10000 nM = very weak binding."""
        assert analyzer._affinity_to_weight(50000) == 0.2

    def test_boundary_1nm(self, analyzer):
        assert analyzer._affinity_to_weight(1) == 0.9

    def test_boundary_10nm(self, analyzer):
        assert analyzer._affinity_to_weight(10) == 0.7

    def test_boundary_100nm(self, analyzer):
        assert analyzer._affinity_to_weight(100) == 0.5

    def test_boundary_1000nm(self, analyzer):
        assert analyzer._affinity_to_weight(1000) == 0.3

    def test_boundary_10000nm(self, analyzer):
        assert analyzer._affinity_to_weight(10000) == 0.2

    def test_monotonically_decreasing(self, analyzer):
        """Higher affinity (nM) should give lower or equal weight."""
        vals = [0.1, 1, 10, 100, 1000, 10000, 100000]
        weights = [analyzer._affinity_to_weight(v) for v in vals]
        for i in range(len(weights) - 1):
            assert weights[i] >= weights[i + 1]


# ===================================================================
# Synthetic Lethality Potential Scoring
# ===================================================================

class TestScoreSLPotential:
    """Tests for _score_sl_potential."""

    def test_strong_interaction_many_pathways(self, analyzer):
        """High STRING score + many shared pathways = high SL score."""
        score = analyzer._score_sl_potential(
            interaction_score=950,
            shared_pathways=[
                {"name": "DNA repair"},
                {"name": "Homologous recombination"},
                {"name": "Cell cycle"},
            ],
        )
        assert score >= 75

    def test_no_interaction_no_pathways(self, analyzer):
        score = analyzer._score_sl_potential(
            interaction_score=0,
            shared_pathways=[],
        )
        assert score == 0

    def test_moderate_interaction(self, analyzer):
        score = analyzer._score_sl_potential(
            interaction_score=700,
            shared_pathways=[{"name": "MAPK signaling"}],
        )
        assert 30 <= score <= 60

    def test_weak_interaction(self, analyzer):
        score = analyzer._score_sl_potential(
            interaction_score=400,
            shared_pathways=[],
        )
        assert score == 10

    def test_dna_repair_bonus(self, analyzer):
        """DNA repair pathways get a bonus."""
        without_bonus = analyzer._score_sl_potential(
            interaction_score=700,
            shared_pathways=[{"name": "MAPK signaling"}],
        )
        with_bonus = analyzer._score_sl_potential(
            interaction_score=700,
            shared_pathways=[{"name": "DNA repair pathway"}],
        )
        assert with_bonus > without_bonus

    def test_homologous_recombination_bonus(self, analyzer):
        score = analyzer._score_sl_potential(
            interaction_score=500,
            shared_pathways=[{"name": "Homologous recombination repair"}],
        )
        # Should include the DNA repair bonus
        assert score > 30

    def test_capped_at_100(self, analyzer):
        score = analyzer._score_sl_potential(
            interaction_score=999,
            shared_pathways=[
                {"name": "DNA repair"},
                {"name": "Mismatch repair"},
                {"name": "Base excision repair"},
                {"name": "Nucleotide excision repair"},
            ],
        )
        assert score <= 100

    def test_below_400_no_interaction_score(self, analyzer):
        score = analyzer._score_sl_potential(
            interaction_score=300,
            shared_pathways=[],
        )
        assert score == 0


# ===================================================================
# Expression Explanation Generation
# ===================================================================

class TestGenerateExpressionExplanation:
    """Tests for _generate_expression_explanation."""

    def test_no_targets(self, analyzer):
        result = analyzer._generate_expression_explanation([])
        assert "no drug targets" in result.lower()

    def test_all_favorable(self, analyzer):
        targets = [
            {"compatibility": "high"},
            {"compatibility": "moderate"},
        ]
        result = analyzer._generate_expression_explanation(targets)
        assert "all" in result.lower()
        assert "2" in result

    def test_none_favorable(self, analyzer):
        targets = [
            {"compatibility": "poor"},
            {"compatibility": "low"},
        ]
        result = analyzer._generate_expression_explanation(targets)
        assert "none" in result.lower()

    def test_mixed_favorable(self, analyzer):
        targets = [
            {"compatibility": "high"},
            {"compatibility": "poor"},
            {"compatibility": "low"},
        ]
        result = analyzer._generate_expression_explanation(targets)
        assert "1 of 3" in result


# ===================================================================
# Pathway Drug Relevance
# ===================================================================

class TestPathwayDrugRelevance:
    """Tests for _pathway_drug_relevance."""

    def test_activated_pathway(self, analyzer):
        result = analyzer._pathway_drug_relevance(
            {"direction": "activated"},
            ["BRAF", "MEK1"],
        )
        assert "hyperactivated" in result.lower()
        assert "BRAF" in result

    def test_suppressed_pathway(self, analyzer):
        result = analyzer._pathway_drug_relevance(
            {"direction": "suppressed"},
            ["TP53"],
        )
        assert "suppressed" in result.lower()

    def test_neutral_pathway(self, analyzer):
        result = analyzer._pathway_drug_relevance(
            {"direction": "neutral"},
            ["CDK4"],
        )
        assert "CDK4" in result

    def test_many_targets_truncated(self, analyzer):
        result = analyzer._pathway_drug_relevance(
            {"direction": "activated"},
            ["A", "B", "C", "D", "E"],
        )
        assert "+2 more" in result


# ===================================================================
# Synthetic Lethality Explanation
# ===================================================================

class TestGenerateSLExplanation:
    """Tests for _generate_sl_explanation."""

    def test_no_pairs(self, analyzer):
        result = analyzer._generate_sl_explanation([])
        assert "no synthetic lethality" in result.lower()

    def test_high_confidence_pair(self, analyzer):
        pairs = [{
            "drug_target": "PARP1",
            "cancer_lost_gene": "BRCA1",
            "cancer_loss_frequency": 25.0,
            "confidence": "high",
            "sl_score": 85,
        }]
        result = analyzer._generate_sl_explanation(pairs)
        assert "PARP1" in result
        assert "BRCA1" in result
        assert "25" in result

    def test_moderate_confidence_pair(self, analyzer):
        pairs = [{
            "drug_target": "ATR",
            "cancer_lost_gene": "ATM",
            "cancer_loss_frequency": 10.0,
            "confidence": "moderate",
            "sl_score": 55,
        }]
        result = analyzer._generate_sl_explanation(pairs)
        assert "ATR" in result
        assert "moderate" in result
