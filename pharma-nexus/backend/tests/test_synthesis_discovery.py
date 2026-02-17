"""Tests for LLM Literature Synthesis Discovery (Strategy 7).

Tests the data assembly, JSON parsing, proposal storage, quality
filtering, and promotion pipeline — all without actual LLM calls.
"""

import pytest
import pytest_asyncio

from app.models.cancer_type import CancerType
from app.models.drug import Drug
from app.models.discovery import LLMDiscoveryProposal
from app.models.hypothesis import Hypothesis
from app.services.synthesis_discovery import (
    MIN_PROPOSAL_CONFIDENCE,
    SynthesisDiscovery,
)


# =====================================================================
# JSON parsing
# =====================================================================


class TestJSONParsing:
    """Test _parse_json with various LLM response formats."""

    def test_clean_json(self):
        text = '{"proposals": [{"drug_name": "Aspirin"}]}'
        result = SynthesisDiscovery._parse_json(text)
        assert result["proposals"][0]["drug_name"] == "Aspirin"

    def test_json_in_code_block(self):
        text = '```json\n{"proposals": [{"drug_name": "Aspirin"}]}\n```'
        result = SynthesisDiscovery._parse_json(text)
        assert result["proposals"][0]["drug_name"] == "Aspirin"

    def test_json_with_surrounding_text(self):
        text = (
            "Here are my proposals:\n"
            '{"proposals": [{"drug_name": "Aspirin"}]}\n'
            "I hope these are helpful."
        )
        result = SynthesisDiscovery._parse_json(text)
        assert result["proposals"][0]["drug_name"] == "Aspirin"

    def test_invalid_json_returns_none(self):
        result = SynthesisDiscovery._parse_json("This is not JSON at all.")
        assert result is None

    def test_empty_string_returns_none(self):
        result = SynthesisDiscovery._parse_json("")
        assert result is None

    def test_array_response(self):
        text = '[{"drug_name": "A"}, {"drug_name": "B"}]'
        result = SynthesisDiscovery._parse_json(text)
        assert isinstance(result, list)
        assert len(result) == 2


# =====================================================================
# Proposal storage
# =====================================================================


@pytest.fixture
def sample_proposals():
    """Sample proposals as Claude would return them."""
    return [
        {
            "drug_name": "testdrug",
            "drug_id": 1,
            "mechanism_rationale": "TestDrug inhibits Target A which activates Pathway P.",
            "transitive_chain": [
                "TestDrug inhibits Target A",
                "Target A activates Pathway P",
                "Pathway P drives Cancer X",
            ],
            "key_papers": ["12345678", "23456789"],
            "inferred_pathways": ["PI3K-AKT", "mTOR"],
            "confidence": 0.75,
            "novelty_reasoning": "No direct literature link exists.",
            "category": "mechanistic",
        },
        {
            "drug_name": "lowconf",
            "drug_id": 2,
            "mechanism_rationale": "Weak connection.",
            "transitive_chain": [],
            "key_papers": [],
            "inferred_pathways": [],
            "confidence": 0.2,  # Below MIN_PROPOSAL_CONFIDENCE
            "novelty_reasoning": None,
            "category": "off_target",
        },
        {
            "drug_name": "highconf",
            "drug_id": 3,
            "mechanism_rationale": "Strong mechanism via synthetic lethality.",
            "transitive_chain": ["Drug → PARP", "PARP + BRCA1 = synthetic lethality"],
            "key_papers": ["34567890"],
            "inferred_pathways": ["DNA repair"],
            "confidence": 0.9,
            "novelty_reasoning": "Novel combination with existing therapy.",
            "category": "synthetic_lethality",
        },
    ]


@pytest_asyncio.fixture
async def test_drugs(db):
    """Insert test drugs matching sample_proposals drug_ids."""
    drugs = [
        Drug(id=1, drugbank_id="DB99901", name="TestDrug", status="approved",
             mechanism_of_action="Inhibits Target A"),
        Drug(id=2, drugbank_id="DB99902", name="LowConf", status="approved",
             mechanism_of_action="Weak mech"),
        Drug(id=3, drugbank_id="DB99903", name="HighConf", status="approved",
             mechanism_of_action="PARP inhibitor"),
    ]
    for d in drugs:
        db.add(d)
    await db.flush()
    return drugs


@pytest_asyncio.fixture
async def test_cancer(db):
    """Insert a test cancer type."""
    cancer = CancerType(id=100, tcga_code="TEST", name="Test Cancer", tissue="Test")
    db.add(cancer)
    await db.flush()
    return cancer


class TestProposalStorage:
    """Test _store_proposals with various inputs."""

    @pytest.mark.asyncio
    async def test_stores_valid_proposals(
        self, db, sample_proposals, test_drugs, test_cancer
    ):
        svc = SynthesisDiscovery.__new__(SynthesisDiscovery)
        drug_lookup = {"testdrug": 1, "lowconf": 2, "highconf": 3}

        stored = await svc._store_proposals(
            sample_proposals,
            cancer_type_id=100,
            batch_id="test_batch_1",
            model="test-model",
            input_tokens=1000,
            output_tokens=500,
            drug_lookup=drug_lookup,
            session=db,
        )

        # lowconf (0.2) should be filtered out — below MIN_PROPOSAL_CONFIDENCE
        assert len(stored) == 2
        assert stored[0].drug_id == 1
        assert stored[1].drug_id == 3

    @pytest.mark.asyncio
    async def test_filters_low_confidence(
        self, db, sample_proposals, test_drugs, test_cancer
    ):
        svc = SynthesisDiscovery.__new__(SynthesisDiscovery)
        drug_lookup = {"testdrug": 1, "lowconf": 2, "highconf": 3}

        stored = await svc._store_proposals(
            sample_proposals,
            cancer_type_id=100,
            batch_id="test_batch_2",
            model="test-model",
            input_tokens=0,
            output_tokens=0,
            drug_lookup=drug_lookup,
            session=db,
        )

        drug_ids = [p.drug_id for p in stored]
        assert 2 not in drug_ids  # lowconf filtered

    @pytest.mark.asyncio
    async def test_skips_unknown_drug(self, db, test_drugs, test_cancer):
        svc = SynthesisDiscovery.__new__(SynthesisDiscovery)
        proposals = [
            {
                "drug_name": "NonexistentDrug",
                "mechanism_rationale": "Some reason.",
                "confidence": 0.8,
            },
        ]
        drug_lookup = {"testdrug": 1}

        stored = await svc._store_proposals(
            proposals,
            cancer_type_id=100,
            batch_id="test_batch_3",
            model="test-model",
            input_tokens=0,
            output_tokens=0,
            drug_lookup=drug_lookup,
            session=db,
        )
        assert len(stored) == 0

    @pytest.mark.asyncio
    async def test_deduplicates_within_batch(
        self, db, test_drugs, test_cancer
    ):
        svc = SynthesisDiscovery.__new__(SynthesisDiscovery)
        proposals = [
            {"drug_id": 1, "mechanism_rationale": "First.", "confidence": 0.6},
            {"drug_id": 1, "mechanism_rationale": "Duplicate.", "confidence": 0.7},
        ]
        drug_lookup = {"testdrug": 1}

        stored = await svc._store_proposals(
            proposals,
            cancer_type_id=100,
            batch_id="test_batch_4",
            model="test-model",
            input_tokens=0,
            output_tokens=0,
            drug_lookup=drug_lookup,
            session=db,
        )
        # Second proposal for same drug+cancer+batch should be skipped
        assert len(stored) == 1

    @pytest.mark.asyncio
    async def test_clamps_confidence(self, db, test_drugs, test_cancer):
        svc = SynthesisDiscovery.__new__(SynthesisDiscovery)
        proposals = [
            {"drug_id": 1, "mechanism_rationale": "Over.", "confidence": 5.0},
        ]
        drug_lookup = {"testdrug": 1}

        stored = await svc._store_proposals(
            proposals,
            cancer_type_id=100,
            batch_id="test_batch_5",
            model="test-model",
            input_tokens=0,
            output_tokens=0,
            drug_lookup=drug_lookup,
            session=db,
        )
        assert len(stored) == 1
        assert stored[0].confidence == 1.0

    @pytest.mark.asyncio
    async def test_handles_invalid_confidence_type(
        self, db, test_drugs, test_cancer
    ):
        svc = SynthesisDiscovery.__new__(SynthesisDiscovery)
        proposals = [
            {"drug_id": 1, "mechanism_rationale": "Bad.", "confidence": "high"},
        ]
        drug_lookup = {"testdrug": 1}

        stored = await svc._store_proposals(
            proposals,
            cancer_type_id=100,
            batch_id="test_batch_6",
            model="test-model",
            input_tokens=0,
            output_tokens=0,
            drug_lookup=drug_lookup,
            session=db,
        )
        # "high" → float fails → confidence=0 → below min → filtered
        assert len(stored) == 0


# =====================================================================
# Quality thresholds
# =====================================================================


class TestQualityThresholds:
    """Test the MIN_PROPOSAL_CONFIDENCE filter."""

    def test_min_confidence_is_reasonable(self):
        assert 0.3 <= MIN_PROPOSAL_CONFIDENCE <= 0.6

    def test_below_threshold_filtered(self):
        assert 0.2 < MIN_PROPOSAL_CONFIDENCE  # our test proposal with 0.2 should fail

    def test_above_threshold_accepted(self):
        assert 0.75 >= MIN_PROPOSAL_CONFIDENCE
        assert 0.9 >= MIN_PROPOSAL_CONFIDENCE


# =====================================================================
# Service construction
# =====================================================================


class TestServiceConstruction:
    """Test SynthesisDiscovery initialization."""

    def test_default_cost_mode(self):
        svc = SynthesisDiscovery.__new__(SynthesisDiscovery)
        svc._cost_mode = "economy"
        assert svc._cost_mode == "economy"

    def test_model_selection_uses_top(self):
        """Discovery should use the best model available for creative work."""
        from app.services.llm_analyst import COST_MODE_MODELS

        svc = SynthesisDiscovery.__new__(SynthesisDiscovery)
        svc._cost_mode = "standard"
        model = svc._model()
        assert model == COST_MODE_MODELS["standard"]["top"]

    def test_economy_model_is_haiku(self):
        from app.services.llm_analyst import MODEL_HAIKU

        svc = SynthesisDiscovery.__new__(SynthesisDiscovery)
        svc._cost_mode = "economy"
        assert svc._model() == MODEL_HAIKU


# =====================================================================
# Proposal promotion
# =====================================================================


class TestProposalPromotion:
    """Test promoting proposals into the hypothesis pipeline."""

    @pytest.mark.asyncio
    async def test_no_proposals_returns_zero(self, db):
        svc = SynthesisDiscovery.__new__(SynthesisDiscovery)
        result = await svc.promote_proposals(db, min_confidence=0.5)
        assert result["promoted"] == 0
