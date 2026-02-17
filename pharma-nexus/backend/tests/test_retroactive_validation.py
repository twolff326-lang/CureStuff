"""Tests for retroactive validation ('time machine' test).

Tests evidence gathering, predictability scoring, and metric computation
against in-memory test data.
"""

import pytest
import pytest_asyncio
from datetime import date

from app.models.cancer_type import CancerType, CancerMolecularProfile
from app.models.drug import Drug, DrugTarget, LiteratureDrug
from app.models.literature import Literature, LiteratureCancer
from app.models.mutation import Mutation
from app.models.target import Target
from app.services.retroactive_validation import RetroactiveValidator


# =====================================================================
# Fixtures
# =====================================================================


@pytest_asyncio.fixture
async def retro_data(db):
    """Set up a scenario mimicking a known repurposing case.

    Simulates Imatinib/GIST-like case: a drug with known targets that
    overlap with cancer mutations.
    """
    # Cancer: a test analog of GIST
    cancer = CancerType(
        id=300, tcga_code="RTST", name="Retro Test Cancer", tissue="GI"
    )
    db.add(cancer)

    # Target: like KIT
    target = Target(
        id=300, uniprot_id="R_UNI_A", gene_symbol="RKIT",
        gene_name="Retro KIT analog"
    )
    db.add(target)

    # Drug: like Imatinib
    drug = Drug(
        id=300, drugbank_id="DB_R001", name="RetroMatinib",
        status="approved", mechanism_of_action="KIT inhibitor"
    )
    db.add(drug)

    # Drug-target link
    dt = DrugTarget(drug_id=300, target_id=300, action_type="inhibitor", source="test")
    db.add(dt)

    # Mutation: RKIT mutated in cancer
    mut = Mutation(
        cancer_type_id=300, gene_symbol="RKIT",
        mutation_type="gain_of_function", frequency_percent=85.0,
        source="test",
    )
    db.add(mut)

    # Expression: RKIT overexpressed
    expr = CancerMolecularProfile(
        cancer_type_id=300, gene_symbol="RKIT",
        alteration_type="overexpression", expression_zscore=4.2,
        source="test",
    )
    db.add(expr)

    # Pre-discovery paper (published before "approval")
    paper_early = Literature(
        id=300, pmid="R0001",
        title="RKIT mutations in GI tumors",
        abstract="RKIT gain-of-function mutations drive tumor growth.",
        pub_date=date(1998, 6, 1),
        journal="Cancer Research",
    )
    db.add(paper_early)
    db.add(LiteratureCancer(literature_id=300, cancer_type_id=300))

    # Post-discovery paper
    paper_late = Literature(
        id=301, pmid="R0002",
        title="RetroMatinib efficacy in RKIT-mutant tumors",
        abstract="RetroMatinib showed dramatic responses in patients.",
        pub_date=date(2005, 3, 1),
        journal="NEJM",
    )
    db.add(paper_late)
    db.add(LiteratureDrug(literature_id=301, drug_id=300))
    db.add(LiteratureCancer(literature_id=301, cancer_type_id=300))

    # Co-mention paper (pre-discovery)
    paper_co = Literature(
        id=302, pmid="R0003",
        title="KIT inhibitors for GI cancers",
        abstract="RetroMatinib and similar KIT inhibitors may help in retro test cancer.",
        pub_date=date(1999, 1, 1),
        journal="JCO",
    )
    db.add(paper_co)
    db.add(LiteratureDrug(literature_id=302, drug_id=300))
    db.add(LiteratureCancer(literature_id=302, cancer_type_id=300))

    await db.flush()
    return {"cancer": cancer, "drug": drug, "target": target}


# =====================================================================
# Evidence Gathering
# =====================================================================


class TestEvidenceGathering:
    @pytest.mark.asyncio
    async def test_gathers_drug_targets(self, db, retro_data):
        validator = RetroactiveValidator()
        evidence = await validator._gather_evidence(300, 300, None, db)

        assert evidence["drug_targets"]["count"] == 1
        assert "RKIT" in evidence["drug_targets"]["genes"]

    @pytest.mark.asyncio
    async def test_gathers_mutations(self, db, retro_data):
        validator = RetroactiveValidator()
        evidence = await validator._gather_evidence(300, 300, None, db)

        assert evidence["target_mutations"]["count"] == 1
        assert evidence["target_mutations"]["genes"][0]["gene"] == "RKIT"

    @pytest.mark.asyncio
    async def test_gathers_expression(self, db, retro_data):
        validator = RetroactiveValidator()
        evidence = await validator._gather_evidence(300, 300, None, db)

        assert evidence["expression_changes"]["total"] >= 1
        assert len(evidence["expression_changes"]["dysregulated"]) >= 1

    @pytest.mark.asyncio
    async def test_time_filter_excludes_late_papers(self, db, retro_data):
        validator = RetroactiveValidator()
        cutoff = date(2000, 1, 1)  # Before the 2005 paper

        evidence = await validator._gather_evidence(300, 300, cutoff, db)

        # Co-mention should count the 1999 paper but not the 2005 paper
        assert evidence["literature_co_mentions"]["count"] == 1
        assert evidence["literature_co_mentions"]["time_filtered"] is True

    @pytest.mark.asyncio
    async def test_no_time_filter_includes_all(self, db, retro_data):
        validator = RetroactiveValidator()

        evidence = await validator._gather_evidence(300, 300, None, db)

        # Without filter, both co-mention papers count
        assert evidence["literature_co_mentions"]["count"] == 2


# =====================================================================
# Predictability Scoring
# =====================================================================


class TestPredictabilityScoring:
    def test_high_predictability_case(self):
        """A case with strong target overlap and literature should score high."""
        validator = RetroactiveValidator()
        evidence = {
            "target_mutations": {"count": 3, "genes": []},
            "expression_changes": {"dysregulated": [{"gene": "A"}, {"gene": "B"}]},
            "literature_co_mentions": {"count": 5},
            "cancer_papers_available": 50,
            "drug_papers_available": 30,
        }
        result = validator._score_predictability(evidence)

        assert result["score"] >= 0.5
        assert result["details"]["target_overlap"] > 0
        assert result["details"]["literature_signal"] > 0

    def test_zero_evidence_case(self):
        """A case with no evidence should score near zero."""
        validator = RetroactiveValidator()
        evidence = {
            "target_mutations": {"count": 0, "genes": []},
            "expression_changes": {"dysregulated": []},
            "literature_co_mentions": {"count": 0},
            "cancer_papers_available": 0,
            "drug_papers_available": 0,
        }
        result = validator._score_predictability(evidence)

        assert result["score"] == 0.0

    def test_score_components_are_capped(self):
        """Individual components should not exceed their maximum weight."""
        validator = RetroactiveValidator()
        evidence = {
            "target_mutations": {"count": 100, "genes": []},  # extreme
            "expression_changes": {"dysregulated": [{}] * 50},  # extreme
            "literature_co_mentions": {"count": 100},  # extreme
            "cancer_papers_available": 1000,
            "drug_papers_available": 1000,
        }
        result = validator._score_predictability(evidence)

        assert result["details"]["target_overlap"] <= 0.3
        assert result["details"]["expression_signal"] <= 0.2
        assert result["details"]["literature_signal"] <= 0.3
        assert result["details"]["data_availability"] <= 0.2
        assert result["score"] <= 1.0


# =====================================================================
# Full Evaluation
# =====================================================================


class TestFullEvaluation:
    @pytest.mark.asyncio
    async def test_evaluate_case_in_database(self, db, retro_data):
        validator = RetroactiveValidator()
        case = {
            "drug_name": "RetroMatinib",
            "drug_drugbank_id": "DB_R001",
            "cancer_name": "Retro Test Cancer",
            "cancer_tcga_code": "RTST",
            "outcome": "success",
            "approval_year": 2002,
        }

        result = await validator.evaluate_case(case, db)

        assert result["status"] == "evaluated"
        assert result["drug_id"] == 300
        assert result["cancer_type_id"] == 300
        assert result["cutoff_date"] == "2000-01-01"
        assert result["predictability_score"] > 0

    @pytest.mark.asyncio
    async def test_evaluate_case_not_in_db(self, db, retro_data):
        validator = RetroactiveValidator()
        case = {
            "drug_name": "NonexistentDrug",
            "drug_drugbank_id": "DB_NONE",
            "cancer_name": "Fake Cancer",
            "cancer_tcga_code": "FAKE",
            "outcome": "success",
        }

        result = await validator.evaluate_case(case, db)
        assert result["status"] == "not_in_database"

    @pytest.mark.asyncio
    async def test_run_evaluation_with_custom_cases(self, db, retro_data):
        validator = RetroactiveValidator()
        cases = [
            {
                "drug_name": "RetroMatinib",
                "drug_drugbank_id": "DB_R001",
                "cancer_name": "Retro Test Cancer",
                "cancer_tcga_code": "RTST",
                "outcome": "success",
                "approval_year": 2002,
            },
        ]

        result = await validator.run_full_evaluation(db, cases=cases)

        assert result["cases_evaluated"] == 1
        assert "metrics" in result


# =====================================================================
# Metrics Computation
# =====================================================================


class TestMetrics:
    def test_compute_metrics_basic(self):
        validator = RetroactiveValidator()
        results = [
            {
                "status": "evaluated",
                "outcome": "success",
                "predictability_score": 0.8,
                "would_have_predicted": True,
            },
            {
                "status": "evaluated",
                "outcome": "failure",
                "predictability_score": 0.2,
                "would_have_predicted": False,
            },
        ]

        metrics = validator._compute_metrics(results)

        assert metrics["successes"]["count"] == 1
        assert metrics["successes"]["would_have_predicted"] == 1
        assert metrics["failures"]["count"] == 1
        assert metrics["failures"]["falsely_predicted"] == 0

    def test_separation_metric(self):
        validator = RetroactiveValidator()
        results = [
            {"status": "evaluated", "outcome": "success", "predictability_score": 0.8, "would_have_predicted": True},
            {"status": "evaluated", "outcome": "success", "predictability_score": 0.6, "would_have_predicted": True},
            {"status": "evaluated", "outcome": "failure", "predictability_score": 0.2, "would_have_predicted": False},
            {"status": "evaluated", "outcome": "failure", "predictability_score": 0.1, "would_have_predicted": False},
        ]

        metrics = validator._compute_metrics(results)

        assert metrics["separation"]["score_gap"] > 0
        assert metrics["separation"]["auc_proxy"] == 1.0  # Perfect separation

    def test_no_evaluated_cases(self):
        validator = RetroactiveValidator()
        results = [
            {"status": "not_in_database", "outcome": "success"},
        ]

        metrics = validator._compute_metrics(results)
        assert "error" in metrics
