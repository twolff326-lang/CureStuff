"""Tests for hypothesis engine scoring logic."""
import pytest
from unittest.mock import MagicMock, AsyncMock

from app.services.hypothesis_engine import HypothesisEngine, KNOWN_PAIRS, W_TARGET, W_PATHWAY, W_CLINICAL


def _make_drug(name="Imatinib", status="approved"):
    d = MagicMock()
    d.id = 1
    d.name = name
    d.status = status
    return d


def _make_cancer(name="Chronic Myeloid Leukemia"):
    c = MagicMock()
    c.id = 1
    c.name = name
    return c


def _make_drug_target(affinity=50.0, units="nM", gene_symbol="ABL1"):
    dt = MagicMock()
    dt.drug_id = 1
    dt.target_id = 1
    dt.binding_affinity = affinity
    dt.affinity_units = units
    dt.affinity_type = "IC50"
    dt.target = MagicMock()
    dt.target.gene_symbol = gene_symbol
    return dt


def _make_mutation(gene_symbol="ABL1", frequency=0.5, cancer_type_id=1):
    m = MagicMock()
    m.cancer_type_id = cancer_type_id
    m.gene_symbol = gene_symbol
    m.frequency = frequency
    return m


class TestTargetBindingScoring:
    """Test _score_target_binding method."""

    def setup_method(self):
        self.engine = HypothesisEngine.__new__(HypothesisEngine)

    def test_no_drug_targets_returns_zero(self):
        score = self.engine._score_target_binding([], set(), [])
        assert score == 0.0

    def test_strong_binding_affinity_nM(self):
        """< 10 nM should score 80-100."""
        dt = _make_drug_target(affinity=5.0, units="nM")
        score = self.engine._score_target_binding([dt], set(), [])
        assert 80 <= score <= 100

    def test_moderate_binding_affinity_nM(self):
        """10-100 nM should score 60-80."""
        dt = _make_drug_target(affinity=50.0, units="nM")
        score = self.engine._score_target_binding([dt], set(), [])
        assert 60 <= score <= 80

    def test_weak_binding_affinity_nM(self):
        """100-1000 nM should score 40-60."""
        dt = _make_drug_target(affinity=500.0, units="nM")
        score = self.engine._score_target_binding([dt], set(), [])
        assert 40 <= score <= 60

    def test_uM_to_nM_normalization(self):
        """0.05 uM = 50 nM should score the same as 50 nM."""
        dt_nM = _make_drug_target(affinity=50.0, units="nM")
        dt_uM = _make_drug_target(affinity=0.05, units="uM")
        score_nM = self.engine._score_target_binding([dt_nM], set(), [])
        score_uM = self.engine._score_target_binding([dt_uM], set(), [])
        assert score_nM == score_uM

    def test_no_affinity_data_gives_default(self):
        dt = _make_drug_target(affinity=None, units="nM")
        score = self.engine._score_target_binding([dt], set(), [])
        assert score == 30.0

    def test_direct_overlap_bonus(self):
        """Direct target-mutation overlap should boost score."""
        dt = _make_drug_target(affinity=50.0, units="nM", gene_symbol="ABL1")
        mutation = _make_mutation(gene_symbol="ABL1", frequency=0.5)
        score_no_overlap = self.engine._score_target_binding([dt], set(), [mutation])
        score_with_overlap = self.engine._score_target_binding([dt], {"ABL1"}, [mutation])
        assert score_with_overlap > score_no_overlap

    def test_score_capped_at_100(self):
        """Score should never exceed 100."""
        dt = _make_drug_target(affinity=1.0, units="nM")
        mutations = [_make_mutation("ABL1", 1.0), _make_mutation("BCR", 1.0)]
        score = self.engine._score_target_binding([dt], {"ABL1", "BCR"}, mutations)
        assert score <= 100.0


class TestClinicalEvidenceScoring:
    """Test _score_clinical_evidence method."""

    def setup_method(self):
        self.engine = HypothesisEngine.__new__(HypothesisEngine)

    def test_known_pair_returns_high_score(self):
        drug = _make_drug("Imatinib")
        cancer = _make_cancer("Chronic Myeloid Leukemia")
        score = self.engine._score_clinical_evidence(drug, cancer)
        assert score == 95.0

    def test_unknown_pair_approved_drug(self):
        drug = _make_drug("SomeDrug", status="approved")
        cancer = _make_cancer("Unknown Cancer")
        score = self.engine._score_clinical_evidence(drug, cancer)
        assert score == 15.0

    def test_unknown_pair_unapproved_drug(self):
        drug = _make_drug("SomeDrug", status="experimental")
        cancer = _make_cancer("Unknown Cancer")
        score = self.engine._score_clinical_evidence(drug, cancer)
        assert score == 5.0

    def test_known_pairs_case_insensitive(self):
        """Drug name matching should be case-insensitive."""
        drug = _make_drug("TAMOXIFEN")
        cancer = _make_cancer("Breast Cancer")
        score = self.engine._score_clinical_evidence(drug, cancer)
        assert score == 95.0


class TestWeights:
    """Test scoring weight constants."""

    def test_weights_sum_to_one(self):
        assert abs(W_TARGET + W_PATHWAY + W_CLINICAL - 1.0) < 0.001

    def test_known_pairs_not_empty(self):
        assert len(KNOWN_PAIRS) > 50


class TestSortWhitelist:
    """Test that sort_by whitelist is enforced in hypotheses API."""

    def test_whitelist_contains_expected(self):
        from app.api.hypotheses import ALLOWED_SORTS
        assert "composite_score" in ALLOWED_SORTS
        assert "target_binding_score" in ALLOWED_SORTS
        assert "pathway_overlap_score" in ALLOWED_SORTS
        assert "clinical_evidence_score" in ALLOWED_SORTS
        assert "created_at" in ALLOWED_SORTS

    def test_whitelist_does_not_allow_arbitrary(self):
        from app.api.hypotheses import ALLOWED_SORTS
        assert "__class__" not in ALLOWED_SORTS
        assert "id" not in ALLOWED_SORTS
