"""Tests for novelty and dosing plausibility checks.

Tests the PubMed novelty checker (with mocked Entrez calls) and the
dosing plausibility checker (against in-memory test data).
"""

from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

from app.models.cancer_type import CancerType
from app.models.drug import Drug, DrugTarget
from app.models.evidence import Bioassay
from app.models.target import Target
from app.services.novelty_plausibility import (
    DosingPlausibilityChecker,
    NoveltyChecker,
)


# =====================================================================
# Fixtures
# =====================================================================


@pytest_asyncio.fixture
async def dosing_data(db):
    """Set up drugs, targets, and bioassay data for dosing tests."""
    cancer = CancerType(
        id=500, tcga_code="DOSE", name="Dosing Test Cancer", tissue="Test"
    )
    db.add(cancer)

    targets = [
        Target(id=500, uniprot_id="DOS_A", gene_symbol="DOSA", gene_name="Dose Gene A"),
        Target(id=501, uniprot_id="DOS_B", gene_symbol="DOSB", gene_name="Dose Gene B"),
    ]
    db.add_all(targets)

    # Drug with strong affinity (approved, should be highly plausible)
    drug_strong = Drug(
        id=500, drugbank_id="DB_DOS1", name="StrongAffinityDrug",
        status="approved", mechanism_of_action="Targets DOSA with high affinity",
    )
    # Drug with weak affinity (experimental, should be implausible)
    drug_weak = Drug(
        id=501, drugbank_id="DB_DOS2", name="WeakAffinityDrug",
        status="experimental", mechanism_of_action="Targets DOSB weakly",
    )
    # Drug with no affinity data
    drug_nodata = Drug(
        id=502, drugbank_id="DB_DOS3", name="NoDataDrug",
        status="approved", mechanism_of_action="Unknown target",
    )
    db.add_all([drug_strong, drug_weak, drug_nodata])

    # Drug-target links with binding affinity
    db.add(DrugTarget(
        drug_id=500, target_id=500, action_type="inhibitor",
        binding_affinity_nm=50.0, source="test",
    ))
    db.add(DrugTarget(
        drug_id=501, target_id=501, action_type="inhibitor",
        binding_affinity_nm=50000.0, source="test",
    ))

    # Bioassay data: StrongAffinityDrug has low IC50 (good)
    db.add(Bioassay(
        drug_id=500, target_id=500,
        activity_type="IC50", activity_value=100.0, activity_unit="nM",
        source="test",
    ))
    db.add(Bioassay(
        drug_id=500, target_id=500,
        activity_type="Ki", activity_value=75.0, activity_unit="nM",
        source="test",
    ))

    # Bioassay data: WeakAffinityDrug has high IC50 (bad)
    db.add(Bioassay(
        drug_id=501, target_id=501,
        activity_type="IC50", activity_value=100.0, activity_unit="um",
        source="test",
    ))

    await db.flush()

    return {
        "cancer": cancer,
        "drug_strong": drug_strong,
        "drug_weak": drug_weak,
        "drug_nodata": drug_nodata,
    }


# =====================================================================
# NoveltyChecker — PubMed search (mocked)
# =====================================================================


class TestNoveltyChecker:
    @pytest.mark.asyncio
    async def test_novel_combination(self):
        """A drug-cancer pair with zero papers should be classified as novel."""
        checker = NoveltyChecker()

        mock_search = MagicMock()
        mock_search.return_value = MagicMock()
        mock_read = MagicMock(return_value={"Count": "0", "IdList": []})

        with patch("app.services.novelty_plausibility.Entrez.esearch", mock_search), \
             patch("app.services.novelty_plausibility.Entrez.read", mock_read):
            result = await checker.check_pubmed_novelty("FakeDrug123", "RareCancer456")

        assert result["is_novel"] is True
        assert result["novelty_tier"] == "novel"
        assert result["total_evidence"] == 0

    @pytest.mark.asyncio
    async def test_understudied_combination(self):
        """A drug-cancer pair with 3 papers should be understudied."""
        checker = NoveltyChecker()

        call_count = 0

        def mock_esearch(**kwargs):
            return MagicMock()

        def mock_read(handle):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: title/abstract search
                return {"Count": "3", "IdList": ["111", "222", "333"]}
            else:
                # Second call: MeSH search
                return {"Count": "2", "IdList": []}

        mock_fetch = MagicMock()
        mock_fetch.return_value = MagicMock()
        mock_fetch.return_value.read.return_value = (
            "PMID- 111\n"
            "TI  - Paper about FakeDrug\n"
            "DP  - 2023\n"
            "\n"
            "PMID- 222\n"
            "TI  - Another paper\n"
            "DP  - 2022\n"
            "\n"
            "PMID- 333\n"
            "TI  - Third paper\n"
            "DP  - 2024\n"
        )

        with patch("app.services.novelty_plausibility.Entrez.esearch", mock_esearch), \
             patch("app.services.novelty_plausibility.Entrez.read", mock_read), \
             patch("app.services.novelty_plausibility.Entrez.efetch", mock_fetch):
            result = await checker.check_pubmed_novelty("FakeDrug", "SomeCancer")

        assert result["is_novel"] is True
        assert result["novelty_tier"] == "understudied"
        assert result["paper_count"] == 3
        assert len(result["top_papers"]) == 3

    @pytest.mark.asyncio
    async def test_well_studied_combination(self):
        """A drug-cancer pair with 50 papers should be well_studied."""
        checker = NoveltyChecker()

        call_count = 0

        def mock_esearch(**kwargs):
            return MagicMock()

        def mock_read(handle):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"Count": "50", "IdList": ["1", "2", "3", "4", "5"]}
            else:
                return {"Count": "45", "IdList": []}

        mock_fetch = MagicMock()
        mock_fetch.return_value = MagicMock()
        mock_fetch.return_value.read.return_value = (
            "PMID- 1\nTI  - Paper 1\nDP  - 2020\n\n"
        )

        with patch("app.services.novelty_plausibility.Entrez.esearch", mock_esearch), \
             patch("app.services.novelty_plausibility.Entrez.read", mock_read), \
             patch("app.services.novelty_plausibility.Entrez.efetch", mock_fetch):
            result = await checker.check_pubmed_novelty("Tamoxifen", "Breast Cancer")

        assert result["is_novel"] is False
        assert result["novelty_tier"] == "well_studied"
        assert result["total_evidence"] == 50

    @pytest.mark.asyncio
    async def test_known_combination(self):
        """A drug-cancer pair with 15 papers should be known."""
        checker = NoveltyChecker()

        call_count = 0

        def mock_esearch(**kwargs):
            return MagicMock()

        def mock_read(handle):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"Count": "15", "IdList": ["1"]}
            else:
                return {"Count": "10", "IdList": []}

        mock_fetch = MagicMock()
        mock_fetch.return_value = MagicMock()
        mock_fetch.return_value.read.return_value = "PMID- 1\nTI  - Paper\nDP  - 2021\n"

        with patch("app.services.novelty_plausibility.Entrez.esearch", mock_esearch), \
             patch("app.services.novelty_plausibility.Entrez.read", mock_read), \
             patch("app.services.novelty_plausibility.Entrez.efetch", mock_fetch):
            result = await checker.check_pubmed_novelty("SomeDrug", "SomeCancer")

        assert result["is_novel"] is False
        assert result["novelty_tier"] == "known"

    @pytest.mark.asyncio
    async def test_pubmed_failure_graceful(self):
        """PubMed failure should return check_failed, not crash."""
        checker = NoveltyChecker()

        with patch(
            "app.services.novelty_plausibility.Entrez.esearch",
            side_effect=Exception("Network error"),
        ):
            result = await checker.check_pubmed_novelty("AnyDrug", "AnyCancer")

        assert result["novelty_tier"] == "check_failed"
        assert result["is_novel"] is None
        assert "error" in result

    @pytest.mark.asyncio
    async def test_result_contains_search_query(self):
        """Result should include the PubMed query for reproducibility."""
        checker = NoveltyChecker()

        def mock_esearch(**kwargs):
            return MagicMock()

        def mock_read(handle):
            return {"Count": "0", "IdList": []}

        with patch("app.services.novelty_plausibility.Entrez.esearch", mock_esearch), \
             patch("app.services.novelty_plausibility.Entrez.read", mock_read):
            result = await checker.check_pubmed_novelty("TestDrug", "TestCancer")

        assert "search_query" in result
        assert "TestDrug" in result["search_query"]
        assert "TestCancer" in result["search_query"]

    @pytest.mark.asyncio
    async def test_mesh_count_used_when_higher(self):
        """Should use MeSH count when it's higher than title/abstract count."""
        checker = NoveltyChecker()

        call_count = 0

        def mock_esearch(**kwargs):
            return MagicMock()

        def mock_read(handle):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"Count": "2", "IdList": ["1", "2"]}
            else:
                # MeSH count is higher
                return {"Count": "25", "IdList": []}

        mock_fetch = MagicMock()
        mock_fetch.return_value = MagicMock()
        mock_fetch.return_value.read.return_value = "PMID- 1\nTI  - Paper\nDP  - 2021\n"

        with patch("app.services.novelty_plausibility.Entrez.esearch", mock_esearch), \
             patch("app.services.novelty_plausibility.Entrez.read", mock_read), \
             patch("app.services.novelty_plausibility.Entrez.efetch", mock_fetch):
            result = await checker.check_pubmed_novelty("SomeDrug", "SomeCancer")

        # total_evidence should use the higher MeSH count
        assert result["total_evidence"] == 25
        assert result["novelty_tier"] == "well_studied"


# =====================================================================
# DosingPlausibilityChecker
# =====================================================================


class TestDosingPlausibilityChecker:
    @pytest.mark.asyncio
    async def test_highly_plausible_drug(self, db, dosing_data):
        """Drug with IC50=100nM and Cmax=5000nM should be highly plausible."""
        checker = DosingPlausibilityChecker()
        result = await checker.check_dosing_plausibility(500, 500, db)

        assert result["plausible"] is True
        assert result["verdict"] in ("highly_plausible", "plausible")
        assert result["coverage_ratio"] >= 3.0
        assert result["drug_name"] == "StrongAffinityDrug"

    @pytest.mark.asyncio
    async def test_implausible_drug(self, db, dosing_data):
        """Drug with IC50=100µM and Cmax=1000nM should be implausible."""
        checker = DosingPlausibilityChecker()
        result = await checker.check_dosing_plausibility(501, 500, db)

        # IC50 = 100 µM = 100,000 nM, Cmax for experimental = 1000 nM
        assert result["plausible"] is False
        assert result["verdict"] == "implausible"
        assert result["coverage_ratio"] < 1.0

    @pytest.mark.asyncio
    async def test_no_affinity_data(self, db, dosing_data):
        """Drug with no affinity data should return no_affinity_data."""
        checker = DosingPlausibilityChecker()
        result = await checker.check_dosing_plausibility(502, 500, db)

        assert result["plausible"] is None
        assert result["verdict"] == "no_affinity_data"

    @pytest.mark.asyncio
    async def test_drug_not_found(self, db, dosing_data):
        """Non-existent drug should return drug_not_found."""
        checker = DosingPlausibilityChecker()
        result = await checker.check_dosing_plausibility(99999, 500, db)

        assert result["plausible"] is None
        assert result["verdict"] == "drug_not_found"

    @pytest.mark.asyncio
    async def test_returns_affinity_details(self, db, dosing_data):
        """Should return best/median/worst affinity and data point count."""
        checker = DosingPlausibilityChecker()
        result = await checker.check_dosing_plausibility(500, 500, db)

        assert "best_affinity_nm" in result
        assert "median_affinity_nm" in result
        assert "worst_affinity_nm" in result
        assert "affinity_data_points" in result
        assert result["affinity_data_points"] >= 2  # IC50 + Ki + binding_affinity

    @pytest.mark.asyncio
    async def test_estimated_cmax_by_status(self, db, dosing_data):
        """Approved drugs should have higher estimated Cmax than experimental."""
        checker = DosingPlausibilityChecker()

        result_approved = await checker.check_dosing_plausibility(500, 500, db)
        result_experimental = await checker.check_dosing_plausibility(501, 500, db)

        assert result_approved["estimated_cmax_nm"] > result_experimental["estimated_cmax_nm"]

    @pytest.mark.asyncio
    async def test_interpretation_present(self, db, dosing_data):
        """Every result should include a human-readable interpretation."""
        checker = DosingPlausibilityChecker()

        result = await checker.check_dosing_plausibility(500, 500, db)
        assert "interpretation" in result
        assert len(result["interpretation"]) > 10

        result2 = await checker.check_dosing_plausibility(502, 500, db)
        assert "interpretation" in result2


# =====================================================================
# Unit normalization
# =====================================================================


class TestUnitNormalization:
    def test_nanomolar(self):
        assert DosingPlausibilityChecker._normalize_to_nm(100, "nM") == 100
        assert DosingPlausibilityChecker._normalize_to_nm(100, "nanomolar") == 100

    def test_micromolar(self):
        assert DosingPlausibilityChecker._normalize_to_nm(1, "uM") == 1000
        assert DosingPlausibilityChecker._normalize_to_nm(1, "µM") == 1000
        assert DosingPlausibilityChecker._normalize_to_nm(1, "micromolar") == 1000

    def test_millimolar(self):
        assert DosingPlausibilityChecker._normalize_to_nm(1, "mM") == 1_000_000

    def test_picomolar(self):
        assert DosingPlausibilityChecker._normalize_to_nm(1000, "pM") == 1.0

    def test_molar(self):
        assert DosingPlausibilityChecker._normalize_to_nm(1, "M") == 1_000_000_000

    def test_unknown_unit(self):
        assert DosingPlausibilityChecker._normalize_to_nm(100, "ounces") is None

    def test_none_value(self):
        assert DosingPlausibilityChecker._normalize_to_nm(0, "nM") is None
        assert DosingPlausibilityChecker._normalize_to_nm(100, None) is None
