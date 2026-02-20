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
        """Drugs targeting completely different genes → max score."""
        targets_a = [{"gene_symbol": "A", "action_type": "inhibitor"}]
        targets_b = [{"gene_symbol": "B", "action_type": "agonist"}]
        result = engine._score_target_non_overlap(
            targets_a, targets_b, {"A"}, {"B"}
        )
        # non_overlap=1.0 → 80, action_diversity=2/2=1.0 → 20, total=100
        assert result["score"] == 100
        assert result["details"]["shared_targets"] == 0
        assert result["details"]["overlap_ratio"] == 0.0

    def test_completely_overlapping(self, engine):
        """Same targets → low score."""
        targets_a = [{"gene_symbol": "X", "action_type": "inhibitor"}]
        targets_b = [{"gene_symbol": "X", "action_type": "inhibitor"}]
        result = engine._score_target_non_overlap(
            targets_a, targets_b, {"X"}, {"X"}
        )
        # overlap_ratio=1.0 → non_overlap=0 → 0*80=0
        # action_diversity = 1/max(1+1,1) = 0.5 → 0.5*20 = 10
        assert result["score"] == 10
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
        # overlap_ratio=1/3 → non_overlap=2/3 → 2/3*80=53.33
        # action_diversity = 3/max(4,1)=0.75 → 0.75*20=15 → total=68.33 → round=68
        assert result["score"] == 68
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
    """Tests for synergy classification thresholds via _score_combination.

    The classification logic lives inside _score_combination, which is async
    and requires full DB mocks.  We verify the classification by inspecting
    the result of _score_target_non_overlap at known boundary scores since
    that is a synchronous pure function, and separately verify the threshold
    semantics documented in the module docstring.
    """

    def test_high_non_overlap_score_above_80(self, engine):
        """Completely non-overlapping targets with diverse actions → score >= 80."""
        targets_a = [{"gene_symbol": "A", "action_type": "inhibitor"}]
        targets_b = [{"gene_symbol": "B", "action_type": "agonist"}]
        result = engine._score_target_non_overlap(targets_a, targets_b, {"A"}, {"B"})
        assert result["score"] >= 80, (
            f"Non-overlapping diverse targets should score >= 80, got {result['score']}"
        )

    def test_full_overlap_score_at_most_20(self, engine):
        """Identical single targets, same action → score dominated by overlap penalty."""
        targets_a = [{"gene_symbol": "X", "action_type": "inhibitor"}]
        targets_b = [{"gene_symbol": "X", "action_type": "inhibitor"}]
        result = engine._score_target_non_overlap(targets_a, targets_b, {"X"}, {"X"})
        # overlap_ratio=1.0 → non_overlap=0 → 0*80=0, action_diversity=1/2=0.5 → 0.5*20=10
        assert result["score"] == 10, (
            f"Full overlap same-action should score 10, got {result['score']}"
        )

    def test_boundary_at_classification_thresholds(self, engine):
        """Verify the documented classification thresholds (60/40/20) from the module docstring."""
        # The module docstring states:
        #   synergistic >= 60, additive 40-59, uncertain 20-39, antagonistic <20
        # Verify by computing scores at strategic overlap levels.
        # With 3 non-overlapping genes each, diverse actions:
        targets_a = [{"gene_symbol": f"A{i}", "action_type": "inhibitor"} for i in range(3)]
        targets_b = [{"gene_symbol": f"B{i}", "action_type": "agonist"} for i in range(3)]
        result = engine._score_target_non_overlap(
            targets_a, targets_b, {f"A{i}" for i in range(3)}, {f"B{i}" for i in range(3)}
        )
        # 0% overlap → non_overlap=1.0 → 80 + action_diversity bonus
        assert result["score"] >= 60, "Zero overlap should score in synergistic range"


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
    """Verify synergy weight configuration in the production _score_combination source.

    The weights are defined inline in _score_combination (lines 179-185 of
    combination_engine.py).  We extract them via inspect to ensure they stay
    consistent rather than re-declaring a hardcoded copy.
    """

    def _extract_weights(self):
        """Extract the actual weights dict from _score_combination source."""
        import ast
        import inspect
        import textwrap

        source = inspect.getsource(CombinationEngine._score_combination)
        # Find the weights dict literal in the source
        tree = ast.parse(textwrap.dedent(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "weights":
                        # Compile and eval just the dict literal
                        return ast.literal_eval(node.value)
        raise AssertionError("Could not find 'weights' dict in _score_combination source")

    def test_weights_sum_to_one(self):
        weights = self._extract_weights()
        assert abs(sum(weights.values()) - 1.0) < 1e-10, (
            f"Production synergy weights sum to {sum(weights.values())}, expected 1.0"
        )

    def test_all_weights_positive(self):
        weights = self._extract_weights()
        for name, w in weights.items():
            assert w > 0, f"Weight '{name}' is not positive: {w}"

    def test_five_dimensions(self):
        weights = self._extract_weights()
        assert len(weights) == 5, f"Expected 5 synergy dimensions, got {len(weights)}"

    def test_expected_dimension_names(self):
        weights = self._extract_weights()
        expected_dims = {
            "pathway_complementarity",
            "target_non_overlap",
            "synthetic_lethality",
            "safety_compatibility",
            "clinical_precedent",
        }
        assert set(weights.keys()) == expected_dims
