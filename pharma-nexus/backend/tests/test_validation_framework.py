"""Tests for app.services.validation_framework helper functions.

Tests the pure utility functions — AUC interpretation, calibration
interpretation, weight recommendation generation, row attribute extraction,
ground truth constants, negative controls, temporal cohorts, effect size
interpretation, and robustness interpretation.
"""

import pytest

from app.services.validation_framework import (
    KNOWN_REPURPOSING_CASES,
    KNOWN_REPURPOSING_FAILURES,
    TEMPORAL_COHORTS,
    _generate_weight_recommendation,
    _interpret_auc,
    _interpret_calibration,
    _interpret_effect_size,
    _interpret_robustness,
    getattr_from_row,
)


# ===================================================================
# AUC Interpretation
# ===================================================================

class TestInterpretAUC:
    """Tests for _interpret_auc."""

    def test_excellent(self):
        assert "excellent" in _interpret_auc(0.95)
        assert "excellent" in _interpret_auc(0.90)

    def test_good(self):
        result = _interpret_auc(0.85)
        assert "good" in result

    def test_fair(self):
        result = _interpret_auc(0.75)
        assert "fair" in result

    def test_poor(self):
        result = _interpret_auc(0.65)
        assert "poor" in result

    def test_random(self):
        result = _interpret_auc(0.50)
        assert "no discrimination" in result or "random" in result

    def test_boundary_09(self):
        result = _interpret_auc(0.9)
        assert "excellent" in result

    def test_boundary_08(self):
        result = _interpret_auc(0.8)
        assert "good" in result

    def test_boundary_07(self):
        result = _interpret_auc(0.7)
        assert "fair" in result

    def test_boundary_06(self):
        result = _interpret_auc(0.6)
        assert "poor" in result

    def test_just_below_06(self):
        result = _interpret_auc(0.59)
        assert "random" in result or "no discrimination" in result

    def test_perfect_auc(self):
        result = _interpret_auc(1.0)
        assert "excellent" in result

    def test_zero_auc(self):
        result = _interpret_auc(0.0)
        assert "random" in result or "no discrimination" in result


# ===================================================================
# Calibration Interpretation
# ===================================================================

class TestInterpretCalibration:
    """Tests for _interpret_calibration."""

    def test_well_calibrated(self):
        assert "well calibrated" in _interpret_calibration(0.03)

    def test_moderately_calibrated(self):
        assert "moderately" in _interpret_calibration(0.10)

    def test_poorly_calibrated(self):
        assert "poorly" in _interpret_calibration(0.20)

    def test_very_poorly_calibrated(self):
        result = _interpret_calibration(0.30)
        assert "very poorly" in result

    def test_just_below_005(self):
        assert "well calibrated" in _interpret_calibration(0.049)

    def test_at_005(self):
        """Exactly 0.05 should be 'moderately' (boundary uses strict <)."""
        assert "moderately" in _interpret_calibration(0.05)

    def test_just_below_015(self):
        assert "moderately" in _interpret_calibration(0.149)

    def test_at_015(self):
        """Exactly 0.15 should be 'poorly' (boundary uses strict <)."""
        assert "poorly" in _interpret_calibration(0.15)

    def test_just_below_025(self):
        assert "poorly" in _interpret_calibration(0.249)

    def test_at_025(self):
        """Exactly 0.25 should be 'very poorly' (boundary uses strict <)."""
        assert "very poorly" in _interpret_calibration(0.25)

    def test_zero_brier(self):
        """Perfect calibration."""
        result = _interpret_calibration(0.0)
        assert "well calibrated" in result


# ===================================================================
# Weight Recommendation Generation
# ===================================================================

class TestGenerateWeightRecommendation:
    """Tests for _generate_weight_recommendation."""

    def test_most_important_dimension(self):
        ablation = {
            "pathway_overlap": {"auc_drop": 0.05},
            "expression_correlation": {"auc_drop": 0.02},
            "novelty": {"auc_drop": 0.001},
        }
        weights = {
            "pathway_overlap": 0.20,
            "expression_correlation": 0.15,
            "novelty": 0.15,
        }
        result = _generate_weight_recommendation(ablation, weights)
        assert "pathway_overlap" in result
        assert "increasing" in result.lower() or "consider" in result.lower()

    def test_harmful_dimension(self):
        ablation = {
            "pathway_overlap": {"auc_drop": 0.03},
            "novelty": {"auc_drop": -0.05},
        }
        weights = {"pathway_overlap": 0.20, "novelty": 0.15}
        result = _generate_weight_recommendation(ablation, weights)
        assert "novelty" in result
        assert "harmful" in result.lower() or "improves" in result.lower()

    def test_no_significant_dimensions(self):
        ablation = {
            "pathway_overlap": {"auc_drop": 0.005},
            "expression_correlation": {"auc_drop": -0.003},
        }
        weights = {"pathway_overlap": 0.20, "expression_correlation": 0.15}
        result = _generate_weight_recommendation(ablation, weights)
        assert "no single dimension" in result.lower()

    def test_both_important_and_harmful(self):
        ablation = {
            "pathway_overlap": {"auc_drop": 0.08},
            "novelty": {"auc_drop": -0.04},
        }
        weights = {"pathway_overlap": 0.25, "novelty": 0.10}
        result = _generate_weight_recommendation(ablation, weights)
        assert "pathway_overlap" in result
        assert "novelty" in result


# ===================================================================
# Row Attribute Extraction
# ===================================================================

class TestGetAttrFromRow:
    """Tests for getattr_from_row."""

    def test_pathway_overlap(self):
        # Row tuple: (id, composite, pathway, expression, lit, clinical, safety, novelty, causal, gnn, mut, poly, pharma)
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 0.0, 0.0, 0.0, 0.0)
        assert getattr_from_row(row, "pathway_overlap") == 65.0

    def test_expression_correlation(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 0.0, 0.0, 0.0, 0.0)
        assert getattr_from_row(row, "expression_correlation") == 70.0

    def test_literature_support(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 0.0, 0.0, 0.0, 0.0)
        assert getattr_from_row(row, "literature_support") == 55.0

    def test_clinical_evidence(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 0.0, 0.0, 0.0, 0.0)
        assert getattr_from_row(row, "clinical_evidence") == 40.0

    def test_safety(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 0.0, 0.0, 0.0, 0.0)
        assert getattr_from_row(row, "safety") == 85.0

    def test_novelty(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 0.0, 0.0, 0.0, 0.0)
        assert getattr_from_row(row, "novelty") == 90.0

    def test_causal_dependency(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 0.0, 0.0, 0.0, 0.0)
        assert getattr_from_row(row, "causal_dependency") == 30.0

    def test_gnn_link(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 15.0, 0.0, 0.0, 0.0)
        assert getattr_from_row(row, "gnn_link") == 15.0

    def test_mutation_context(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 15.0, 20.0, 0.0, 0.0)
        assert getattr_from_row(row, "mutation_context") == 20.0

    def test_polypharmacology(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 15.0, 20.0, 10.0, 0.0)
        assert getattr_from_row(row, "polypharmacology") == 10.0

    def test_pharmacological_response(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 15.0, 20.0, 10.0, 45.0)
        assert getattr_from_row(row, "pharmacological_response") == 45.0

    def test_unknown_dimension(self):
        row = (1, 80.0, 65.0, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 0.0, 0.0, 0.0, 0.0)
        assert getattr_from_row(row, "nonexistent_dimension") == 0.0

    def test_none_value_returns_zero(self):
        row = (1, 80.0, None, 70.0, 55.0, 40.0, 85.0, 90.0, 30.0, 0.0, 0.0, 0.0, 0.0)
        assert getattr_from_row(row, "pathway_overlap") == 0.0


# ===================================================================
# Ground Truth Constants
# ===================================================================

class TestKnownRepurposingCases:
    """Validate the KNOWN_REPURPOSING_CASES constant."""

    def test_has_entries(self):
        assert len(KNOWN_REPURPOSING_CASES) > 100

    def test_tuple_structure(self):
        """Each entry should be (drug_fragment, cancer_fragment, evidence_level)."""
        for entry in KNOWN_REPURPOSING_CASES:
            assert len(entry) == 3
            drug, cancer, level = entry
            assert isinstance(drug, str)
            assert isinstance(cancer, str)
            assert isinstance(level, str)

    def test_valid_evidence_levels(self):
        valid = {"fda_approved", "phase3_success", "phase2_positive", "preclinical_validated"}
        for _, _, level in KNOWN_REPURPOSING_CASES:
            assert level in valid, f"Invalid evidence level: {level}"

    def test_has_fda_approved(self):
        fda = [c for c in KNOWN_REPURPOSING_CASES if c[2] == "fda_approved"]
        assert len(fda) >= 50

    def test_has_phase3(self):
        p3 = [c for c in KNOWN_REPURPOSING_CASES if c[2] == "phase3_success"]
        assert len(p3) >= 3

    def test_has_phase2(self):
        p2 = [c for c in KNOWN_REPURPOSING_CASES if c[2] == "phase2_positive"]
        assert len(p2) >= 20

    def test_has_preclinical(self):
        pre = [c for c in KNOWN_REPURPOSING_CASES if c[2] == "preclinical_validated"]
        assert len(pre) >= 15

    def test_no_empty_strings(self):
        for drug, cancer, level in KNOWN_REPURPOSING_CASES:
            assert drug.strip() != ""
            assert cancer.strip() != ""
            assert level.strip() != ""

    def test_well_known_cases_present(self):
        """Check that landmark repurposing cases are in the ground truth."""
        drugs = {c[0] for c in KNOWN_REPURPOSING_CASES}
        assert "thalidomide" in drugs
        assert "imatinib" in drugs
        assert "metformin" in drugs
        assert "aspirin" in drugs

    def test_no_duplicates(self):
        seen = set()
        for entry in KNOWN_REPURPOSING_CASES:
            assert entry not in seen, f"Duplicate: {entry}"
            seen.add(entry)


# ===================================================================
# Negative Controls Constants
# ===================================================================

class TestKnownRepurposingFailures:
    """Validate the KNOWN_REPURPOSING_FAILURES constant."""

    def test_has_entries(self):
        assert len(KNOWN_REPURPOSING_FAILURES) >= 22

    def test_tuple_structure(self):
        """Each entry should be (drug_fragment, cancer_fragment, failure_stage, reason)."""
        for entry in KNOWN_REPURPOSING_FAILURES:
            assert len(entry) == 4
            drug, cancer, stage, reason = entry
            assert isinstance(drug, str)
            assert isinstance(cancer, str)
            assert isinstance(stage, str)
            assert isinstance(reason, str)

    def test_valid_failure_stages(self):
        valid = {"phase3_failed", "phase2_failed", "withdrawn"}
        for _, _, stage, _ in KNOWN_REPURPOSING_FAILURES:
            assert stage in valid, f"Invalid failure stage: {stage}"

    def test_has_phase3_failures(self):
        p3 = [c for c in KNOWN_REPURPOSING_FAILURES if c[2] == "phase3_failed"]
        assert len(p3) >= 5

    def test_has_phase2_failures(self):
        p2 = [c for c in KNOWN_REPURPOSING_FAILURES if c[2] == "phase2_failed"]
        assert len(p2) >= 10

    def test_has_withdrawn(self):
        wd = [c for c in KNOWN_REPURPOSING_FAILURES if c[2] == "withdrawn"]
        assert len(wd) >= 2

    def test_no_empty_strings(self):
        for drug, cancer, stage, reason in KNOWN_REPURPOSING_FAILURES:
            assert drug.strip() != ""
            assert cancer.strip() != ""
            assert stage.strip() != ""
            assert reason.strip() != ""

    def test_reasons_are_informative(self):
        """Each failure should have a meaningful reason."""
        for _, _, _, reason in KNOWN_REPURPOSING_FAILURES:
            assert len(reason) >= 10, f"Reason too short: {reason}"

    def test_no_duplicates(self):
        seen = set()
        for entry in KNOWN_REPURPOSING_FAILURES:
            key = (entry[0], entry[1], entry[2])
            assert key not in seen, f"Duplicate: {key}"
            seen.add(key)

    def test_well_known_failures_present(self):
        """Check that notable trial failures are in the dataset."""
        drugs_cancers = {(c[0], c[1]) for c in KNOWN_REPURPOSING_FAILURES}
        # Bevacizumab breast cancer approval was famously withdrawn by FDA
        assert ("bevacizumab", "breast") in drugs_cancers

    def test_no_conflict_with_positive_ground_truth(self):
        """No drug-cancer pair should appear in both positive and negative datasets.

        The fragment-based DB matching can't distinguish clinical settings
        (e.g., adjuvant vs metastatic), so the same drug+cancer fragment
        in both lists would create a DB-level contradiction.
        """
        positive_pairs = {(c[0], c[1]) for c in KNOWN_REPURPOSING_CASES}
        negative_pairs = {(c[0], c[1]) for c in KNOWN_REPURPOSING_FAILURES}
        overlap = positive_pairs & negative_pairs
        assert len(overlap) == 0, (
            f"Drug-cancer pairs in both positive and negative sets: {overlap}"
        )


# ===================================================================
# Temporal Cohorts Constants
# ===================================================================

class TestTemporalCohorts:
    """Validate the TEMPORAL_COHORTS constant."""

    def test_has_both_cohorts(self):
        assert "established" in TEMPORAL_COHORTS
        assert "recent" in TEMPORAL_COHORTS

    def test_established_has_entries(self):
        assert len(TEMPORAL_COHORTS["established"]) >= 20

    def test_recent_has_entries(self):
        assert len(TEMPORAL_COHORTS["recent"]) >= 20

    def test_cohort_tuple_structure(self):
        for cohort in ("established", "recent"):
            for entry in TEMPORAL_COHORTS[cohort]:
                assert len(entry) == 3
                drug, cancer, level = entry
                assert isinstance(drug, str)
                assert isinstance(cancer, str)
                assert level == "fda_approved"

    def test_no_overlap_between_cohorts(self):
        """Established and recent cohorts should not share drug-cancer pairs."""
        established = {(e[0], e[1]) for e in TEMPORAL_COHORTS["established"]}
        recent = {(r[0], r[1]) for r in TEMPORAL_COHORTS["recent"]}
        overlap = established & recent
        assert len(overlap) == 0, f"Overlapping pairs: {overlap}"

    def test_all_in_ground_truth(self):
        """All temporal cohort entries should also be in KNOWN_REPURPOSING_CASES."""
        ground_truth_set = set(KNOWN_REPURPOSING_CASES)
        for cohort in ("established", "recent"):
            for entry in TEMPORAL_COHORTS[cohort]:
                assert entry in ground_truth_set, (
                    f"Temporal cohort entry not in ground truth: {entry}"
                )

    def test_no_duplicates_within_cohort(self):
        for cohort in ("established", "recent"):
            seen = set()
            for entry in TEMPORAL_COHORTS[cohort]:
                assert entry not in seen, f"Duplicate in {cohort}: {entry}"
                seen.add(entry)


# ===================================================================
# Effect Size Interpretation
# ===================================================================

class TestInterpretEffectSize:
    """Tests for _interpret_effect_size."""

    def test_very_large(self):
        assert "very large" in _interpret_effect_size(1.5)

    def test_large(self):
        assert "large" in _interpret_effect_size(0.9)

    def test_medium(self):
        assert "medium" in _interpret_effect_size(0.6)

    def test_small(self):
        assert "small" in _interpret_effect_size(0.3)

    def test_negligible(self):
        assert "negligible" in _interpret_effect_size(0.1)

    def test_boundary_12(self):
        assert "very large" in _interpret_effect_size(1.2)

    def test_boundary_08(self):
        assert "large" in _interpret_effect_size(0.8)

    def test_boundary_05(self):
        assert "medium" in _interpret_effect_size(0.5)

    def test_boundary_02(self):
        assert "small" in _interpret_effect_size(0.2)

    def test_just_below_02(self):
        assert "negligible" in _interpret_effect_size(0.19)

    def test_negative_value(self):
        """Negative Cohen's d should still be interpreted by magnitude."""
        assert "large" in _interpret_effect_size(-1.0)

    def test_zero(self):
        assert "negligible" in _interpret_effect_size(0.0)


# ===================================================================
# Robustness Interpretation
# ===================================================================

class TestInterpretRobustness:
    """Tests for _interpret_robustness."""

    def test_highly_robust(self):
        result = _interpret_robustness(0.85, 0.01, 0.75)
        assert "highly robust" in result

    def test_moderately_robust(self):
        result = _interpret_robustness(0.80, 0.03, 0.60)
        assert "moderately robust" in result

    def test_somewhat_fragile(self):
        result = _interpret_robustness(0.75, 0.07, 0.50)
        assert "somewhat fragile" in result

    def test_fragile(self):
        result = _interpret_robustness(0.70, 0.15, 0.30)
        assert "fragile" in result

    def test_high_std_overrides(self):
        """High std should indicate fragility even with good baseline."""
        result = _interpret_robustness(0.90, 0.12, 0.40)
        assert "fragile" in result

    def test_low_min_prevents_highly_robust(self):
        """Low minimum AUC should prevent 'highly robust' classification."""
        result = _interpret_robustness(0.85, 0.01, 0.50)
        assert "highly robust" not in result
