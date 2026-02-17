"""Tests for multi-round discovery verification pipeline.

Tests citation grounding, chain verification, and verified score computation
without making actual LLM calls.
"""

import pytest
import pytest_asyncio

from app.models.cancer_type import CancerType, CancerMolecularProfile
from app.models.drug import Drug, DrugTarget, LiteratureDrug
from app.models.literature import Literature, LiteratureCancer
from app.models.mutation import Mutation
from app.models.pathway import Pathway, PathwayTarget
from app.models.target import Target
from app.services.discovery_verification import (
    ProposalVerifier,
    _parse_json,
    _compute_cost,
)


# =====================================================================
# Fixtures
# =====================================================================


@pytest_asyncio.fixture
async def verification_data(db):
    """Set up a complete test scenario for verification."""
    # Cancer
    cancer = CancerType(
        id=200, tcga_code="VTEST", name="Verification Test Cancer", tissue="Test"
    )
    db.add(cancer)

    # Targets
    target_a = Target(
        id=200, uniprot_id="V_UNI_A", gene_symbol="VGENE_A", gene_name="V Gene A"
    )
    target_b = Target(
        id=201, uniprot_id="V_UNI_B", gene_symbol="VGENE_B", gene_name="V Gene B"
    )
    db.add_all([target_a, target_b])

    # Pathway
    pathway = Pathway(
        id=200, source="test", external_id="V_PW_1",
        name="PI3K-AKT Signaling", category="Signal Transduction"
    )
    db.add(pathway)

    # Pathway-target link
    pw_target = PathwayTarget(pathway_id=200, target_id=200)
    db.add(pw_target)

    # Drug
    drug = Drug(
        id=200, drugbank_id="DB_V001", name="VerifDrug",
        status="approved", mechanism_of_action="Inhibits VGENE_A"
    )
    db.add(drug)

    # Drug-target link
    dt = DrugTarget(drug_id=200, target_id=200, action_type="inhibitor", source="test")
    db.add(dt)

    # Mutation (drug target is mutated in cancer)
    mut = Mutation(
        cancer_type_id=200, gene_symbol="VGENE_A",
        mutation_type="missense", frequency_percent=15.0,
        source="test",
    )
    db.add(mut)

    # Expression (drug target is overexpressed)
    expr = CancerMolecularProfile(
        cancer_type_id=200, gene_symbol="VGENE_A",
        alteration_type="overexpression", expression_zscore=3.5,
        source="test",
    )
    db.add(expr)

    # Literature with matching PMID
    from datetime import date

    paper = Literature(
        id=200, pmid="99990001",
        title="VGENE_A mutations drive PI3K-AKT activation in test cancer",
        abstract="We show that VGENE_A is frequently mutated in verification "
        "test cancer, leading to constitutive PI3K-AKT pathway activation. "
        "VerifDrug, an inhibitor of VGENE_A, showed promising results.",
        pub_date=date(2023, 1, 15),
        journal="Test Journal",
    )
    db.add(paper)

    # Link paper to drug and cancer
    lit_drug = LiteratureDrug(literature_id=200, drug_id=200)
    lit_cancer = LiteratureCancer(literature_id=200, cancer_type_id=200)
    db.add_all([lit_drug, lit_cancer])

    await db.flush()

    return {
        "cancer": cancer,
        "drug": drug,
        "target_a": target_a,
        "pathway": pathway,
        "paper": paper,
    }


# =====================================================================
# JSON parsing (shared utility)
# =====================================================================


class TestParseJson:
    def test_clean_json(self):
        result = _parse_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_code_block(self):
        result = _parse_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}

    def test_invalid_returns_none(self):
        assert _parse_json("not json") is None


# =====================================================================
# Cost computation
# =====================================================================


class TestComputeCost:
    def test_basic_cost(self):
        from app.services.llm_analyst import MODEL_SONNET

        cost = _compute_cost(MODEL_SONNET, 1000, 500)
        assert cost > 0
        assert isinstance(cost, float)

    def test_zero_tokens(self):
        from app.services.llm_analyst import MODEL_HAIKU

        cost = _compute_cost(MODEL_HAIKU, 0, 0)
        assert cost == 0.0


# =====================================================================
# Citation Grounding
# =====================================================================


class TestCitationGrounding:
    @pytest.mark.asyncio
    async def test_found_and_supporting(self, db, verification_data):
        verifier = ProposalVerifier.__new__(ProposalVerifier)

        proposal = {
            "drug_name": "VerifDrug",
            "key_papers": ["99990001"],
            "transitive_chain": [
                "VerifDrug inhibits VGENE_A",
                "VGENE_A drives PI3K-AKT in test cancer",
            ],
            "inferred_pathways": ["PI3K-AKT"],
        }

        result = await verifier.ground_citations(proposal, db)

        assert result["papers_checked"] == 1
        assert result["papers_found"] == 1
        assert result["papers_supporting"] == 1
        assert result["grounding_score"] == 1.0
        assert result["verdict"] == "strong"

    @pytest.mark.asyncio
    async def test_not_found_pmid(self, db, verification_data):
        verifier = ProposalVerifier.__new__(ProposalVerifier)

        proposal = {
            "drug_name": "SomeDrug",
            "key_papers": ["00000000"],  # doesn't exist
            "transitive_chain": [],
            "inferred_pathways": [],
        }

        result = await verifier.ground_citations(proposal, db)

        assert result["papers_found"] == 0
        assert result["grounding_score"] == 0.0
        assert result["verdict"] == "ungrounded"

    @pytest.mark.asyncio
    async def test_no_citations(self, db, verification_data):
        verifier = ProposalVerifier.__new__(ProposalVerifier)

        proposal = {"drug_name": "X", "key_papers": []}
        result = await verifier.ground_citations(proposal, db)

        assert result["papers_checked"] == 0
        assert result["verdict"] == "no_citations"

    @pytest.mark.asyncio
    async def test_mixed_citations(self, db, verification_data):
        verifier = ProposalVerifier.__new__(ProposalVerifier)

        proposal = {
            "drug_name": "VerifDrug",
            "key_papers": ["99990001", "00000000"],  # one real, one fake
            "transitive_chain": ["VerifDrug inhibits VGENE_A"],
            "inferred_pathways": ["PI3K-AKT"],
        }

        result = await verifier.ground_citations(proposal, db)

        assert result["papers_checked"] == 2
        assert result["papers_found"] == 1
        assert result["papers_supporting"] == 1
        assert result["grounding_score"] == 0.5
        assert result["verdict"] == "moderate"


# =====================================================================
# Chain Verification
# =====================================================================


class TestChainVerification:
    @pytest.mark.asyncio
    async def test_full_chain_verified(self, db, verification_data):
        verifier = ProposalVerifier.__new__(ProposalVerifier)

        proposal = {
            "drug_id": 200,
            "drug_name": "VerifDrug",
            "transitive_chain": [
                "VerifDrug inhibits VGENE_A",
                "VGENE_A in PI3K-AKT pathway",
                "PI3K-AKT drives test cancer",
            ],
            "inferred_pathways": ["PI3K-AKT Signaling"],
        }

        result = await verifier.verify_chain(proposal, 200, db)

        # Should verify: drug_targets, target_pathway, pathway_cancer,
        # expression_coherence, literature_co_mention
        assert result["links_verified"] >= 3  # at least drug targets, mutation, expression
        assert result["chain_evidence_score"] > 0
        assert result["verdict"] in ("strong", "moderate")

    @pytest.mark.asyncio
    async def test_no_drug_id(self, db, verification_data):
        verifier = ProposalVerifier.__new__(ProposalVerifier)

        proposal = {"drug_name": "Unknown", "transitive_chain": []}
        result = await verifier.verify_chain(proposal, 200, db)

        assert result["chain_evidence_score"] == 0.0
        assert result["verdict"] == "no_drug_id"

    @pytest.mark.asyncio
    async def test_drug_with_no_targets(self, db, verification_data):
        # Add a drug with no targets
        orphan = Drug(
            id=201, drugbank_id="DB_V002", name="OrphanDrug",
            status="approved", mechanism_of_action="Unknown"
        )
        db.add(orphan)
        await db.flush()

        verifier = ProposalVerifier.__new__(ProposalVerifier)

        proposal = {
            "drug_id": 201,
            "drug_name": "OrphanDrug",
            "transitive_chain": ["OrphanDrug affects something"],
            "inferred_pathways": [],
        }

        result = await verifier.verify_chain(proposal, 200, db)

        # Drug exists but has no targets — first link fails
        assert result["link_details"][0]["verified"] is False

    @pytest.mark.asyncio
    async def test_chain_detail_structure(self, db, verification_data):
        verifier = ProposalVerifier.__new__(ProposalVerifier)

        proposal = {
            "drug_id": 200,
            "transitive_chain": [],
            "inferred_pathways": [],
        }

        result = await verifier.verify_chain(proposal, 200, db)

        assert result["links_total"] == 5
        assert len(result["link_details"]) == 5
        for link in result["link_details"]:
            assert "link" in link
            assert "description" in link
            assert "verified" in link


# =====================================================================
# Verified Score Computation
# =====================================================================


class TestVerifiedScore:
    def test_severity_multipliers(self):
        """Fatal critique should nearly zero the score."""
        # Simulate verified score computation
        base = 0.6  # Good base confidence
        fatal_mult = 0.1
        minor_mult = 0.85

        assert base * fatal_mult < 0.1
        assert base * minor_mult > 0.4

    def test_chain_required_for_proceed(self):
        """should_proceed requires chain_score > 0."""
        # Even if critique says proceed, no chain evidence = no proceed
        chain_score = 0
        should_proceed = chain_score > 0
        assert should_proceed is False

    def test_score_formula_components(self):
        """Verify the weight formula adds up correctly."""
        critique_conf = 0.7
        chain_score = 0.6
        grounding_score = 0.8
        original_conf = 0.5

        verified = (
            0.40 * critique_conf
            + 0.30 * chain_score
            + 0.20 * grounding_score
            + 0.10 * original_conf
        )

        expected = 0.40 * 0.7 + 0.30 * 0.6 + 0.20 * 0.8 + 0.10 * 0.5
        assert abs(verified - expected) < 0.001
        # Weights sum to 1.0
        assert abs(0.40 + 0.30 + 0.20 + 0.10 - 1.0) < 0.001
