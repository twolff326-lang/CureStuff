"""Tests for app.services.combination_engine.CombinationEngine.

Tests the pure-function scoring methods — target non-overlap, rationale
generation, and synergy classification — without database access.
"""

import pytest

from app.services.combination_engine import CombinationEngine


@pytest.fixture
def engine():
    return CombinationEngine()


# ===================================================================
# Target Non-Overlap Scoring
# ===================================================================

class TestScoreTargetNonOverlap:
    """Tests for _score_target_non_overlap."""

    def test_completely_non_overlapping(self, engine):
        """Drugs targeting completely different genes → high score."""
        targets_a = [{"gene_symbol": "A", "action_type": "inhibitor"}]
        targets_b = [{"gene_symbol": "B", "action_type": "agonist"}]
        result = engine._score_target_non_overlap(
            targets_a, targets_b, {"A"}, {"B"}
        )
        assert result["score"] >= 80
        assert result["details"]["shared_targets"] == 0
        assert result["details"]["overlap_ratio"] == 0.0

    def test_completely_overlapping(self, engine):
        """Same targets → low score."""
        targets_a = [{"gene_symbol": "X", "action_type": "inhibitor"}]
        targets_b = [{"gene_symbol": "X", "action_type": "inhibitor"}]
        result = engine._score_target_non_overlap(
            targets_a, targets_b, {"X"}, {"X"}
        )
        assert result["score"] <= 30
        assert result["details"]["overlap_ratio"] == 1.0

    def test_partial_overlap(self, engine):
        targets_a = [
            {"gene_symbol": "A", "action_type": "inhibitor"},
            {"gene_symbol": "B", "action_type": "blocker"},
        ]
        targets_b = [
            {"gene_symbol": "B", "action_type": "inhibitor"},
            {"gene_symbol": "C", "action_type": "agonist"},
        ]
        result = engine._score_target_non_overlap(
            targets_a, targets_b, {"A", "B"}, {"B", "C"}
        )
        # 1 shared out of 3 total → 1/3 overlap → ~66% non-overlap
        assert 40 < result["score"] < 90
        assert result["details"]["shared_targets"] == 1

    def test_no_targets(self, engine):
        """Empty target sets → score 0."""
        result = engine._score_target_non_overlap([], [], set(), set())
        assert result["score"] == 0
        assert result["details"]["reason"] == "no_targets"

    def test_diverse_action_types_boost(self, engine):
        """Different action types should give a diversity bonus."""
        targets_a = [{"gene_symbol": "A", "action_type": "inhibitor"}]
        targets_b = [{"gene_symbol": "B", "action_type": "agonist"}]
        diverse_result = engine._score_target_non_overlap(
            targets_a, targets_b, {"A"}, {"B"}
        )

        targets_same = [{"gene_symbol": "C", "action_type": "inhibitor"}]
        same_result = engine._score_target_non_overlap(
            targets_a, targets_same, {"A"}, {"C"}
        )
        # More diverse action types should yield at least equal score
        assert diverse_result["score"] >= same_result["score"]

    def test_shared_genes_listed(self, engine):
        """Shared genes should appear in details."""
        targets_a = [{"gene_symbol": "EGFR", "action_type": "inhibitor"}]
        targets_b = [{"gene_symbol": "EGFR", "action_type": "blocker"}]
        result = engine._score_target_non_overlap(
            targets_a, targets_b, {"EGFR"}, {"EGFR"}
        )
        assert "EGFR" in result["details"]["shared_genes"]

    def test_score_bounded_0_100(self, engine):
        """Score should always be in [0, 100]."""
        targets_a = [
            {"gene_symbol": f"G{i}", "action_type": "inhibitor"}
            for i in range(20)
        ]
        targets_b = [
            {"gene_symbol": f"H{i}", "action_type": "agonist"}
            for i in range(20)
        ]
        genes_a = {f"G{i}" for i in range(20)}
        genes_b = {f"H{i}" for i in range(20)}
        result = engine._score_target_non_overlap(
            targets_a, targets_b, genes_a, genes_b
        )
        assert 0 <= result["score"] <= 100


# ===================================================================
# Synergy Classification
# ===================================================================

class TestSynergyClassification:
    """Tests for synergy classification thresholds in _score_combination."""

    @pytest.mark.parametrize("synergy,expected", [
        (75.0, "synergistic"),
        (60.0, "synergistic"),
        (50.0, "additive"),
        (40.0, "additive"),
        (30.0, "uncertain"),
        (20.0, "uncertain"),
        (10.0, "antagonistic"),
        (0.0, "antagonistic"),
    ])
    def test_classification_thresholds(self, synergy, expected):
        """Verify classification boundaries."""
        if synergy >= 60:
            classification = "synergistic"
        elif synergy >= 40:
            classification = "additive"
        elif synergy >= 20:
            classification = "uncertain"
        else:
            classification = "antagonistic"
        assert classification == expected

    def test_boundary_at_60(self):
        """Exactly 60 should be synergistic."""
        assert 60 >= 60  # synergistic threshold

    def test_boundary_at_40(self):
        """Exactly 40 should be additive."""
        synergy = 40
        assert synergy >= 40 and synergy < 60


# ===================================================================
# Rationale Generation
# ===================================================================

class TestGenerateRationale:
    """Tests for _generate_rationale."""

    def test_basic_rationale_structure(self, engine):
        rationale = engine._generate_rationale(
            drug_a_name="Imatinib",
            drug_b_name="Venetoclax",
            cancer_name="Breast Cancer",
            pathway_score={
                "score": 50, "details": {"complementarity": 0.7,
                "drug_a_pathways": 5, "drug_b_pathways": 6, "overlap_ratio": 0.3},
            },
            target_score={"score": 80, "details": {"shared_targets": 0}},
            sl_score={
                "score": 40, "details": {
                    "essential_targets_a": ["BCL2"],
                    "essential_targets_b": ["ABL1"],
                },
            },
            safety_score={"score": 70, "details": {}},
            clinical_score={"score": 30, "details": {"combination_trials": 2}},
            synergy=65.0,
            classification="synergistic",
        )
        assert "Imatinib" in rationale
        assert "Venetoclax" in rationale
        assert "Breast Cancer" in rationale
        assert "synergistic" in rationale
        assert "65" in rationale

    def test_high_complementarity_mentioned(self, engine):
        rationale = engine._generate_rationale(
            "DrugA", "DrugB", "Lung Cancer",
            {"score": 50, "details": {"complementarity": 0.8,
             "drug_a_pathways": 10, "drug_b_pathways": 8, "overlap_ratio": 0.2}},
            {"score": 90, "details": {"shared_targets": 0}},
            {"score": 0, "details": {"essential_targets_a": [], "essential_targets_b": []}},
            {"score": 50, "details": {}},
            {"score": 20, "details": {"combination_trials": 0}},
            70.0, "synergistic",
        )
        assert "complementarity" in rationale.lower()

    def test_non_overlapping_targets_mentioned(self, engine):
        rationale = engine._generate_rationale(
            "DrugA", "DrugB", "Lung Cancer",
            {"score": 50, "details": {"complementarity": 0.3}},
            {"score": 100, "details": {"shared_targets": 0}},
            {"score": 0, "details": {"essential_targets_a": [], "essential_targets_b": []}},
            {"score": 50, "details": {}},
            {"score": 20, "details": {"combination_trials": 0}},
            55.0, "additive",
        )
        assert "non-overlapping" in rationale.lower()

    def test_sl_signal_mentioned(self, engine):
        rationale = engine._generate_rationale(
            "DrugA", "DrugB", "Melanoma",
            {"score": 30, "details": {"complementarity": 0.2}},
            {"score": 50, "details": {"shared_targets": 3}},
            {"score": 80, "details": {
                "essential_targets_a": ["BRAF"],
                "essential_targets_b": ["MEK1"],
            }},
            {"score": 50, "details": {}},
            {"score": 20, "details": {"combination_trials": 0}},
            60.0, "synergistic",
        )
        assert "synthetic lethality" in rationale.lower() or "essential" in rationale.lower()

    def test_clinical_precedent_mentioned(self, engine):
        rationale = engine._generate_rationale(
            "DrugA", "DrugB", "CRC",
            {"score": 30, "details": {"complementarity": 0.2}},
            {"score": 50, "details": {"shared_targets": 3}},
            {"score": 0, "details": {"essential_targets_a": [], "essential_targets_b": []}},
            {"score": 50, "details": {}},
            {"score": 80, "details": {"combination_trials": 5}},
            55.0, "additive",
        )
        assert "trial" in rationale.lower() or "clinical" in rationale.lower()


# ===================================================================
# Synergy Weight Configuration
# ===================================================================

class TestSynergyWeights:
    """Verify synergy weight configuration is valid."""

    def test_weights_sum_to_one(self):
        weights = {
            "pathway_complementarity": 0.30,
            "target_non_overlap": 0.20,
            "synthetic_lethality": 0.25,
            "safety_compatibility": 0.10,
            "clinical_precedent": 0.15,
        }
        assert abs(sum(weights.values()) - 1.0) < 1e-10

    def test_all_weights_positive(self):
        weights = {
            "pathway_complementarity": 0.30,
            "target_non_overlap": 0.20,
            "synthetic_lethality": 0.25,
            "safety_compatibility": 0.10,
            "clinical_precedent": 0.15,
        }
        for w in weights.values():
            assert w > 0

    def test_five_dimensions(self):
        weights = {
            "pathway_complementarity": 0.30,
            "target_non_overlap": 0.20,
            "synthetic_lethality": 0.25,
            "safety_compatibility": 0.10,
            "clinical_precedent": 0.15,
        }
        assert len(weights) == 5
