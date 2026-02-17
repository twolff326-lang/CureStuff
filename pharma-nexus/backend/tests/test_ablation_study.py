"""Tests for ablation study framework.

Tests each baseline method and the comparison framework against
in-memory test data.
"""

import pytest
import pytest_asyncio

from app.models.cancer_type import CancerType, CancerMolecularProfile
from app.models.drug import Drug, DrugTarget, LiteratureDrug
from app.models.literature import Literature, LiteratureCancer
from app.models.mutation import Mutation
from app.models.pathway import Pathway, PathwayTarget
from app.models.target import Target
from app.services.ablation_study import AblationStudy


# =====================================================================
# Fixtures
# =====================================================================


@pytest_asyncio.fixture
async def ablation_data(db):
    """Set up drugs and cancer with various evidence connections."""
    # Cancer type with a known TCGA code from ground truth (BRCA)
    cancer = CancerType(
        id=400, tcga_code="ABLT", name="Ablation Test Cancer", tissue="Test"
    )
    db.add(cancer)

    # Targets
    targets = [
        Target(id=400, uniprot_id="ABL_A", gene_symbol="ABLA", gene_name="Abl Gene A"),
        Target(id=401, uniprot_id="ABL_B", gene_symbol="ABLB", gene_name="Abl Gene B"),
        Target(id=402, uniprot_id="ABL_C", gene_symbol="ABLC", gene_name="Abl Gene C"),
    ]
    db.add_all(targets)

    # Pathway
    pathway = Pathway(
        id=400, source="test", external_id="ABL_PW",
        name="Ablation Pathway", category="Test"
    )
    db.add(pathway)
    db.add(PathwayTarget(pathway_id=400, target_id=400))
    db.add(PathwayTarget(pathway_id=400, target_id=402))

    # Drugs: one connected, one weakly connected, one not connected
    drug_strong = Drug(
        id=400, drugbank_id="DB_ABL1", name="StrongDrug",
        status="approved", mechanism_of_action="Targets ABLA"
    )
    drug_weak = Drug(
        id=401, drugbank_id="DB_ABL2", name="WeakDrug",
        status="approved", mechanism_of_action="Targets ABLB"
    )
    drug_none = Drug(
        id=402, drugbank_id="DB_ABL3", name="UnrelatedDrug",
        status="investigational", mechanism_of_action="Irrelevant"
    )
    db.add_all([drug_strong, drug_weak, drug_none])

    # Drug-target links
    db.add(DrugTarget(drug_id=400, target_id=400, action_type="inhibitor", source="test"))
    db.add(DrugTarget(drug_id=401, target_id=401, action_type="agonist", source="test"))

    # Mutations: ABLA is mutated in this cancer
    db.add(Mutation(
        cancer_type_id=400, gene_symbol="ABLA",
        mutation_type="missense", frequency_percent=30.0,
        source="test",
    ))

    # Expression: ABLA overexpressed, ABLC underexpressed
    db.add(CancerMolecularProfile(
        cancer_type_id=400, gene_symbol="ABLA",
        alteration_type="overexpression", expression_zscore=3.0,
        source="test",
    ))
    db.add(CancerMolecularProfile(
        cancer_type_id=400, gene_symbol="ABLC",
        alteration_type="underexpression", expression_zscore=-2.5,
        source="test",
    ))

    # Literature: StrongDrug mentioned with cancer, WeakDrug not
    from datetime import date

    paper1 = Literature(
        id=400, pmid="ABL001",
        title="StrongDrug in ablation test cancer",
        abstract="StrongDrug inhibits ABLA in the ablation test cancer context.",
        pub_date=date(2022, 1, 1),
    )
    paper2 = Literature(
        id=401, pmid="ABL002",
        title="More on ablation test cancer",
        abstract="Genomic analysis of ablation test cancer.",
        pub_date=date(2022, 6, 1),
    )
    db.add_all([paper1, paper2])

    db.add(LiteratureDrug(literature_id=400, drug_id=400))
    db.add(LiteratureCancer(literature_id=400, cancer_type_id=400))
    db.add(LiteratureCancer(literature_id=401, cancer_type_id=400))

    await db.flush()

    return {
        "cancer": cancer,
        "drugs": [drug_strong, drug_weak, drug_none],
    }


# =====================================================================
# Keyword Co-occurrence Baseline
# =====================================================================


class TestKeywordBaseline:
    @pytest.mark.asyncio
    async def test_finds_cooccurring_drugs(self, db, ablation_data):
        study = AblationStudy()
        results = await study.keyword_cooccurrence_baseline(400, db)

        assert len(results) >= 1
        drug_names = [r["drug_name"] for r in results]
        assert "StrongDrug" in drug_names

    @pytest.mark.asyncio
    async def test_returns_correct_structure(self, db, ablation_data):
        study = AblationStudy()
        results = await study.keyword_cooccurrence_baseline(400, db)

        for r in results:
            assert "drug_id" in r
            assert "drug_name" in r
            assert "score" in r
            assert r["method"] == "keyword_cooccurrence"

    @pytest.mark.asyncio
    async def test_nonexistent_cancer(self, db, ablation_data):
        study = AblationStudy()
        results = await study.keyword_cooccurrence_baseline(99999, db)
        assert results == []


# =====================================================================
# Structured-Only Baseline
# =====================================================================


class TestStructuredBaseline:
    @pytest.mark.asyncio
    async def test_finds_direct_target_overlap(self, db, ablation_data):
        study = AblationStudy()
        results = await study.structured_only_baseline(400, db)

        # StrongDrug targets ABLA which is mutated in cancer
        assert len(results) >= 1
        drug_names = [r["drug_name"] for r in results]
        assert "StrongDrug" in drug_names

    @pytest.mark.asyncio
    async def test_strong_scores_higher_than_weak(self, db, ablation_data):
        study = AblationStudy()
        results = await study.structured_only_baseline(400, db)

        scores = {r["drug_name"]: r["score"] for r in results}
        if "StrongDrug" in scores and "WeakDrug" in scores:
            assert scores["StrongDrug"] > scores["WeakDrug"]

    @pytest.mark.asyncio
    async def test_returns_correct_structure(self, db, ablation_data):
        study = AblationStudy()
        results = await study.structured_only_baseline(400, db)

        for r in results:
            assert "drug_id" in r
            assert "drug_name" in r
            assert "score" in r
            assert "direct_targets" in r
            assert "shared_pathways" in r
            assert r["method"] == "structured_only"


# =====================================================================
# Random Baseline
# =====================================================================


class TestRandomBaseline:
    @pytest.mark.asyncio
    async def test_returns_drugs(self, db, ablation_data):
        study = AblationStudy()
        results = await study.random_baseline(400, db, top_k=5)

        assert len(results) <= 5
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_all_scores_zero(self, db, ablation_data):
        study = AblationStudy()
        results = await study.random_baseline(400, db)

        for r in results:
            assert r["score"] == 0
            assert r["method"] == "random"


# =====================================================================
# Ablation Comparison
# =====================================================================


class TestAblationComparison:
    @pytest.mark.asyncio
    async def test_run_ablation_structure(self, db, ablation_data):
        study = AblationStudy()
        result = await study.run_ablation(400, db)

        assert "cancer_type" in result
        assert "comparison" in result
        assert "keyword_cooccurrence" in result["comparison"]
        assert "structured_only" in result["comparison"]
        assert "random" in result["comparison"]
        assert "llm_synthesis" in result["comparison"]

    @pytest.mark.asyncio
    async def test_comparison_has_metrics(self, db, ablation_data):
        study = AblationStudy()
        result = await study.run_ablation(400, db)

        for method_name, method_data in result["comparison"].items():
            assert "proposals" in method_data
            assert "ground_truth_overlap" in method_data
            assert "unique_proposals" in method_data

    @pytest.mark.asyncio
    async def test_nonexistent_cancer(self, db, ablation_data):
        study = AblationStudy()
        result = await study.run_ablation(99999, db)
        assert "error" in result


# =====================================================================
# Aggregation
# =====================================================================


class TestAggregation:
    def test_aggregate_results(self):
        study = AblationStudy()

        results = [
            {
                "ground_truth_successes": 3,
                "comparison": {
                    "keyword_cooccurrence": {
                        "proposals": 10, "ground_truth_overlap": 2,
                        "successes_found": 2, "unique_proposals": 1,
                    },
                    "structured_only": {
                        "proposals": 8, "ground_truth_overlap": 1,
                        "successes_found": 1, "unique_proposals": 0,
                    },
                    "random": {
                        "proposals": 10, "ground_truth_overlap": 0,
                        "successes_found": 0, "unique_proposals": 0,
                    },
                    "llm_synthesis": {
                        "proposals": 5, "ground_truth_overlap": 3,
                        "successes_found": 3, "unique_proposals": 2,
                    },
                },
            },
        ]

        aggregate = study._aggregate_results(results)

        assert aggregate["llm_synthesis"]["total_successes_found"] == 3
        assert aggregate["llm_synthesis"]["total_unique_proposals"] == 2
        assert aggregate["random"]["total_successes_found"] == 0
        assert aggregate["llm_synthesis"]["aggregate_recall"] == 1.0
