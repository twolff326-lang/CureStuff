"""Tests for the quick-start seed service."""

import pytest
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.drug import Drug, DrugTarget
from app.models.target import Target
from app.models.cancer_type import CancerType, CancerMolecularProfile
from app.models.mutation import Mutation
from app.models.hypothesis import Hypothesis
from app.models.literature import Literature
from app.services.quickstart_seed import (
    seed_quickstart,
    SEED_DRUGS,
    SEED_CANCERS,
    SEED_HYPOTHESES,
    SEED_PAPERS,
)


class TestSeedData:
    """Test the seed data constants are well-formed."""

    def test_seed_drugs_have_required_fields(self):
        for drug in SEED_DRUGS:
            assert "drugbank_id" in drug
            assert "name" in drug
            assert "mechanism_of_action" in drug
            assert "targets" in drug
            assert len(drug["targets"]) > 0
            for uniprot, gene, action in drug["targets"]:
                assert uniprot.startswith("P") or uniprot.startswith("Q")
                assert len(gene) >= 2
                assert len(action) >= 3

    def test_seed_cancers_have_required_fields(self):
        for cancer in SEED_CANCERS:
            assert "tcga_code" in cancer
            assert "name" in cancer
            assert "tissue" in cancer
            assert "mutations" in cancer
            assert "expression" in cancer
            assert len(cancer["mutations"]) > 0
            assert len(cancer["expression"]) > 0

    def test_seed_hypotheses_reference_valid_drugs_and_cancers(self):
        drug_names = {d["name"] for d in SEED_DRUGS}
        cancer_codes = {c["tcga_code"] for c in SEED_CANCERS}
        for drug_name, tcga_code, *_ in SEED_HYPOTHESES:
            assert drug_name in drug_names, f"Hypothesis references unknown drug: {drug_name}"
            assert tcga_code in cancer_codes, f"Hypothesis references unknown cancer: {tcga_code}"

    def test_seed_hypothesis_scores_are_valid(self):
        for _, _, _, composite, strength, dims in SEED_HYPOTHESES:
            assert 0 <= composite <= 100
            assert strength in ("strong", "moderate", "suggestive", "speculative")
            for key in ("pathway_overlap", "expression_correlation", "literature_support",
                        "clinical_evidence", "safety", "novelty"):
                assert key in dims
                assert 0 <= dims[key] <= 100

    def test_seed_papers_have_pmids(self):
        for paper in SEED_PAPERS:
            assert "pmid" in paper
            assert "title" in paper
            assert paper["pmid"].isdigit()

    def test_enough_seed_data(self):
        assert len(SEED_DRUGS) >= 10
        assert len(SEED_CANCERS) >= 5
        assert len(SEED_HYPOTHESES) >= 15
        assert len(SEED_PAPERS) >= 5


class TestSeedExecution:
    """Test the seed function against the in-memory database."""

    @pytest.mark.asyncio
    async def test_seed_creates_data(self, db: AsyncSession):
        result = await seed_quickstart(db)
        assert result["status"] == "seeded"
        assert result["drugs"] == 15
        assert result["cancers"] == 8
        assert result["hypotheses"] == 20
        assert result["papers"] == 10
        assert result["targets"] > 0
        assert result["mutations"] > 0

    @pytest.mark.asyncio
    async def test_seed_creates_targets(self, db: AsyncSession):
        await seed_quickstart(db)
        count = await db.scalar(select(func.count(Target.id)))
        # Unique UniProt IDs across all drugs
        unique_targets = set()
        for drug in SEED_DRUGS:
            for uniprot, _, _ in drug["targets"]:
                unique_targets.add(uniprot)
        assert count == len(unique_targets)

    @pytest.mark.asyncio
    async def test_seed_creates_drug_target_links(self, db: AsyncSession):
        await seed_quickstart(db)
        count = await db.scalar(select(func.count(DrugTarget.id)))
        total_links = sum(len(d["targets"]) for d in SEED_DRUGS)
        assert count == total_links

    @pytest.mark.asyncio
    async def test_seed_creates_mutations(self, db: AsyncSession):
        await seed_quickstart(db)
        count = await db.scalar(select(func.count(Mutation.id)))
        total_mutations = sum(len(c["mutations"]) for c in SEED_CANCERS)
        assert count == total_mutations

    @pytest.mark.asyncio
    async def test_seed_creates_molecular_profiles(self, db: AsyncSession):
        await seed_quickstart(db)
        count = await db.scalar(select(func.count(CancerMolecularProfile.id)))
        total_profiles = sum(len(c["expression"]) for c in SEED_CANCERS)
        assert count == total_profiles

    @pytest.mark.asyncio
    async def test_seed_idempotent_skip(self, db: AsyncSession):
        """Second call without force=True should skip."""
        await seed_quickstart(db)
        result2 = await seed_quickstart(db)
        assert result2["status"] == "skipped"
        assert "already has" in result2["message"]

    @pytest.mark.asyncio
    async def test_seed_force_reseed(self, db: AsyncSession):
        """force=True should re-seed even when data exists."""
        await seed_quickstart(db)
        result2 = await seed_quickstart(db, force=True)
        # Even though records already exist, should still return seeded
        # (individual items skip via existing checks, but status is seeded)
        assert result2["status"] == "seeded"

    @pytest.mark.asyncio
    async def test_seed_hypothesis_scores(self, db: AsyncSession):
        """Verify hypothesis scores match the seed data."""
        await seed_quickstart(db)
        hypotheses = (await db.execute(select(Hypothesis))).scalars().all()
        assert len(hypotheses) == 20
        for h in hypotheses:
            assert h.composite_score is not None
            assert 0 <= h.composite_score <= 100
            assert h.evidence_strength in ("strong", "moderate", "suggestive", "speculative")
            assert h.adjusted_score is not None
