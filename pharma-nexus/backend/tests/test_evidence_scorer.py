"""Tests for EvidenceScorer — all 6 scoring dimensions.

Each test uses a MockSession with queued results to simulate database queries.
Tests cover zero-data, partial-data, and full-data scenarios for each dimension.
"""

import pytest

from app.services.evidence_scorer import (
    EvidenceScorer,
    _phase_to_confidence,
    _phase_to_strength,
    _score_to_strength,
)
from tests.conftest import (
    MockResult,
    MockSession,
    make_clinical_trial,
    make_drug,
    make_expression_cache,
    make_literature,
)

scorer = EvidenceScorer()


# ===================================================================
# Helper functions
# ===================================================================


class TestScoreToStrength:
    def test_strong(self):
        assert _score_to_strength(70) == "strong"
        assert _score_to_strength(100) == "strong"

    def test_moderate(self):
        assert _score_to_strength(40) == "moderate"
        assert _score_to_strength(69) == "moderate"

    def test_weak(self):
        assert _score_to_strength(0) == "weak"
        assert _score_to_strength(39) == "weak"


class TestPhaseToStrength:
    def test_phase_3(self):
        assert _phase_to_strength("Phase 3") == "strong"

    def test_phase_4(self):
        assert _phase_to_strength("Phase 4") == "strong"

    def test_phase_2(self):
        assert _phase_to_strength("Phase 2") == "moderate"

    def test_phase_1(self):
        assert _phase_to_strength("Phase 1") == "weak"

    def test_none(self):
        assert _phase_to_strength(None) == "weak"

    def test_empty(self):
        assert _phase_to_strength("") == "weak"


class TestPhaseToConfidence:
    def test_phase_3(self):
        assert _phase_to_confidence("Phase 3") == 0.9

    def test_phase_4(self):
        assert _phase_to_confidence("Phase 4") == 0.9

    def test_phase_2(self):
        assert _phase_to_confidence("Phase 2") == 0.7

    def test_phase_1(self):
        assert _phase_to_confidence("Phase 1") == 0.5

    def test_none(self):
        assert _phase_to_confidence(None) == 0.3


# ===================================================================
# 1. Pathway Overlap Score
# ===================================================================


class TestScorePathwayOverlap:
    @pytest.mark.asyncio
    async def test_with_shared_pathways(self, pathway_data_shared):
        """2 shared pathways, 1 with high significance and direct overlap."""
        db = MockSession()
        result = await scorer.score_pathway_overlap(1, 1, db, pathway_data=pathway_data_shared)

        assert result["score"] > 0
        assert "details" in result
        assert "evidence" in result
        assert result["details"]["shared_pathway_count"] == 2
        assert result["details"]["direct_overlap_bonus"] == 15  # PIK3CA in both

    @pytest.mark.asyncio
    async def test_no_overlap(self, pathway_data_empty):
        """No shared pathways = score 0."""
        db = MockSession()
        result = await scorer.score_pathway_overlap(1, 1, db, pathway_data=pathway_data_empty)

        assert result["score"] == 0
        assert result["details"]["shared_pathway_count"] == 0
        assert result["evidence"] == []

    @pytest.mark.asyncio
    async def test_high_overlap(self, pathway_data_high_overlap):
        """High overlap with multiple significant pathways and direct matches."""
        db = MockSession()
        result = await scorer.score_pathway_overlap(1, 1, db, pathway_data=pathway_data_high_overlap)

        # overlap_score=60 from fixture + direct_overlap=15 = 75
        assert result["score"] > 50
        assert result["details"]["direct_overlap_bonus"] == 15  # CDK4 in both

    @pytest.mark.asyncio
    async def test_score_capped_at_100(self):
        """Score caps at 100 even with extreme overlap data."""
        pathway_data = {
            "shared_pathways": [
                {
                    "pathway_id": i,
                    "pathway_name": f"Pathway {i}",
                    "overlap_significance": 0.95,
                    "drug_targets_in_pathway": ["GENE_A"],
                    "cancer_altered_genes_in_pathway": ["GENE_A"],
                }
                for i in range(20)
            ],
            "shared_count": 20,
            "total_drug_target_pathways": 20,
            "total_cancer_altered_pathways": 20,
        }
        db = MockSession()
        result = await scorer.score_pathway_overlap(1, 1, db, pathway_data=pathway_data)
        assert result["score"] <= 100

    @pytest.mark.asyncio
    async def test_evidence_limited_to_five(self, pathway_data_high_overlap):
        """Evidence list is capped at 5 entries."""
        db = MockSession()
        result = await scorer.score_pathway_overlap(1, 1, db, pathway_data=pathway_data_high_overlap)
        assert len(result["evidence"]) <= 5

    @pytest.mark.asyncio
    async def test_no_direct_overlap_no_bonus(self):
        """No direct gene overlap = 0 direct overlap bonus."""
        data = {
            "shared_pathways": [
                {
                    "pathway_id": 1,
                    "pathway_name": "Test",
                    "overlap_significance": 0.5,
                    "drug_targets_in_pathway": ["GENE_A"],
                    "cancer_altered_genes_in_pathway": ["GENE_B"],
                }
            ],
            "shared_count": 1,
            "total_drug_target_pathways": 3,
            "total_cancer_altered_pathways": 5,
        }
        db = MockSession()
        result = await scorer.score_pathway_overlap(1, 1, db, pathway_data=data)
        assert result["details"]["direct_overlap_bonus"] == 0


# ===================================================================
# 2. Expression Correlation Score
# ===================================================================


class TestScoreExpressionCorrelation:
    @pytest.mark.asyncio
    async def test_cached_score(self):
        """Uses cached expression score when available."""
        cache = make_expression_cache(score=72.0)
        db = MockSession()
        db.queue_result(MockResult(scalar_value=cache))

        result = await scorer.score_expression_correlation(1, 1, db)
        assert result["score"] == 72
        assert len(result["evidence"]) > 0

    @pytest.mark.asyncio
    async def test_no_cache_no_targets(self):
        """No cache + no drug targets = score 0."""
        db = MockSession()
        db.queue_result(MockResult(scalar_value=None))  # cache miss
        db.queue_result(MockResult(rows=[]))  # no targets

        result = await scorer.score_expression_correlation(1, 1, db)
        assert result["score"] == 0
        assert result["details"]["reason"] == "no_targets"

    @pytest.mark.asyncio
    async def test_no_cache_no_expression_data(self):
        """Targets exist but no expression profiles -> score 0."""
        # Mock a drug-target pair
        dt = type("DT", (), {"action_type": "inhibitor"})()
        target = type("T", (), {"id": 1, "gene_symbol": "EGFR"})()

        db = MockSession()
        db.queue_result(MockResult(scalar_value=None))  # cache miss
        db.queue_result(MockResult(rows=[(dt, target)]))  # targets
        db.queue_result(MockResult(scalar_value=None))  # no profile

        result = await scorer.score_expression_correlation(1, 1, db)
        assert result["score"] == 0
        assert result["details"]["reason"] == "no_expression_data"

    @pytest.mark.asyncio
    async def test_inhibitor_overexpression_high_compatibility(self):
        """Inhibitor + high overexpression z-score + potent binding = high compatibility."""
        dt = type("DT", (), {"action_type": "inhibitor", "binding_affinity_nm": 1.0})()
        target = type("T", (), {"id": 1, "gene_symbol": "EGFR"})()
        profile = type("CMP", (), {
            "expression_zscore": 3.0,
            "alteration_type": "overexpression",
        })()

        db = MockSession()
        db.queue_result(MockResult(scalar_value=None))  # cache miss
        db.queue_result(MockResult(rows=[(dt, target)]))  # targets
        db.queue_result(MockResult(scalar_value=profile))  # profile

        result = await scorer.score_expression_correlation(1, 1, db)
        # action_match=1.0, expr_mag=min(3/4,1)=0.75, binding=1.0(1nM)
        # compat = 1.0 * 0.75 * 1.0 = 0.75 -> score = 75
        assert result["score"] == 75

    @pytest.mark.asyncio
    async def test_agonist_underexpression_compatibility(self):
        """Agonist + underexpression + potent binding = good compatibility."""
        dt = type("DT", (), {"action_type": "agonist", "binding_affinity_nm": 1.0})()
        target = type("T", (), {"id": 1, "gene_symbol": "TP53"})()
        profile = type("CMP", (), {
            "expression_zscore": -2.5,
            "alteration_type": "underexpression",
        })()

        db = MockSession()
        db.queue_result(MockResult(scalar_value=None))  # cache miss
        db.queue_result(MockResult(rows=[(dt, target)]))  # targets
        db.queue_result(MockResult(scalar_value=profile))  # profile

        result = await scorer.score_expression_correlation(1, 1, db)
        # action_match=1.0, expr_mag=min(2.5/4,1)=0.625, binding=1.0(1nM)
        # compat = 1.0 * 0.625 * 1.0 = 0.625 -> score = round(62.5) = 62
        assert result["score"] == 62

    @pytest.mark.asyncio
    async def test_mismatched_action_lower_score(self):
        """Non-specific action type gets lower compatibility."""
        dt = type("DT", (), {"action_type": "modulator", "binding_affinity_nm": 100.0})()
        target = type("T", (), {"id": 1, "gene_symbol": "BRAF"})()
        profile = type("CMP", (), {
            "expression_zscore": 2.0,
            "alteration_type": "overexpression",
        })()

        db = MockSession()
        db.queue_result(MockResult(scalar_value=None))
        db.queue_result(MockResult(rows=[(dt, target)]))
        db.queue_result(MockResult(scalar_value=profile))

        result = await scorer.score_expression_correlation(1, 1, db)
        # "modulator" -> unknown -> action_match=0.3
        # expr_mag=min(2/4,1)=0.5, pKi=9-log10(100)=7, binding=(7-5)/4=0.5
        # compat = 0.3 * 0.5 * 0.5 = 0.075 -> score = 8
        assert result["score"] == 8

    @pytest.mark.asyncio
    async def test_zero_zscore(self):
        """Zero z-score gives zero compatibility (expr_magnitude=0)."""
        dt = type("DT", (), {"action_type": "inhibitor", "binding_affinity_nm": 1.0})()
        target = type("T", (), {"id": 1, "gene_symbol": "ALK"})()
        profile = type("CMP", (), {
            "expression_zscore": 0,
            "alteration_type": "overexpression",
        })()

        db = MockSession()
        db.queue_result(MockResult(scalar_value=None))
        db.queue_result(MockResult(rows=[(dt, target)]))
        db.queue_result(MockResult(scalar_value=profile))

        result = await scorer.score_expression_correlation(1, 1, db)
        # zscore=0 -> action_match=0.2, expr_mag=0, binding=1.0
        # compat = 0.2 * 0 * 1.0 = 0 -> score = 0
        assert result["score"] == 0

    @pytest.mark.asyncio
    async def test_cached_score_zero(self):
        """Cached score of 0 returns 0."""
        cache = make_expression_cache(score=0)
        db = MockSession()
        db.queue_result(MockResult(scalar_value=cache))
        result = await scorer.score_expression_correlation(1, 1, db)
        assert result["score"] == 0


# ===================================================================
# 3. Literature Support Score
# ===================================================================


class TestScoreLiteratureSupport:
    @pytest.mark.asyncio
    async def test_no_papers(self):
        """No co-mention papers = score 0."""
        db = MockSession()
        db.queue_result(MockResult(rows=[]))  # co-mention papers
        db.queue_result(MockResult(scalar_value=0))  # analyzed count

        result = await scorer.score_literature_support(1, 1, db)
        assert result["score"] == 0

    @pytest.mark.asyncio
    async def test_one_co_mention(self):
        """One co-mention paper produces quality-weighted score."""
        paper = make_literature()
        db = MockSession()
        db.queue_result(MockResult(rows=[paper]))  # 1 co-mention
        db.queue_result(MockResult(scalar_value=0))  # 0 analyzed

        result = await scorer.score_literature_support(1, 1, db)
        # Default mock: study_weight=3.0 (default), findings=1.0, recency=0.6 (no date), specificity=1.0
        # quality = 3.0 * 1.0 * 0.6 * 1.0 = 1.8 -> round = 2
        assert result["score"] == 2
        assert result["details"]["co_mention_papers"] == 1

    @pytest.mark.asyncio
    async def test_multiple_papers_with_analyzed(self):
        """3 co-mentions + 2 analyzed with quality-weighted scoring."""
        papers = [make_literature(id=i, pmid=f"PMD{i}") for i in range(3)]
        db = MockSession()
        db.queue_result(MockResult(rows=papers))
        db.queue_result(MockResult(scalar_value=2))

        result = await scorer.score_literature_support(1, 1, db)
        # 3 papers * 1.8 quality each = 5.4 -> round = 5
        # analyzed_bonus = min(2*2, 10) = 4
        # score = 5 + 4 = 9
        assert result["score"] == 9

    @pytest.mark.asyncio
    async def test_repurposing_bonus(self):
        """Papers with 'repurposing' tag get 2x specificity multiplier."""
        paper = make_literature(relevance_tags=["drug repurposing", "breast cancer"])
        db = MockSession()
        db.queue_result(MockResult(rows=[paper]))
        db.queue_result(MockResult(scalar_value=0))

        result = await scorer.score_literature_support(1, 1, db)
        # quality = 3.0 * 1.0 * 0.6 * 2.0 (repurposing) = 3.6 -> round = 4
        assert result["score"] == 4
        # Verify repurposing was detected in paper breakdown
        breakdown = result["details"]["paper_quality_breakdown"]
        assert len(breakdown) == 1
        assert breakdown[0]["is_repurposing"] is True

    @pytest.mark.asyncio
    async def test_repurposing_multiplier_boosts_quality(self):
        """Repurposing papers get 2x specificity multiplier on quality weight."""
        papers = [
            make_literature(id=i, pmid=f"PMD{i}", relevance_tags=["repurposing candidate"])
            for i in range(10)
        ]
        db = MockSession()
        db.queue_result(MockResult(rows=papers))
        db.queue_result(MockResult(scalar_value=0))

        result = await scorer.score_literature_support(1, 1, db)
        # 10 papers * 3.6 quality each (2x specificity) = 36
        assert result["score"] == 36
        # All papers should be flagged as repurposing
        for detail in result["details"]["paper_quality_breakdown"]:
            assert detail["is_repurposing"] is True

    @pytest.mark.asyncio
    async def test_score_capped_at_100(self):
        """Score caps at 100 even with many papers."""
        papers = [make_literature(id=i, pmid=f"PMD{i}") for i in range(60)]
        db = MockSession()
        db.queue_result(MockResult(rows=papers))
        db.queue_result(MockResult(scalar_value=10))

        result = await scorer.score_literature_support(1, 1, db)
        # 60 * 1.8 quality = 108 -> capped at 100
        # analyzed_bonus = min(10*2, 10) = 10, but already capped
        assert result["score"] == 100

    @pytest.mark.asyncio
    async def test_evidence_limited_to_five(self):
        """Evidence list capped at 5."""
        papers = [make_literature(id=i, pmid=f"PMD{i}") for i in range(10)]
        db = MockSession()
        db.queue_result(MockResult(rows=papers))
        db.queue_result(MockResult(scalar_value=0))

        result = await scorer.score_literature_support(1, 1, db)
        assert len(result["evidence"]) <= 5


# ===================================================================
# 4. Clinical Evidence Score
# ===================================================================


class TestScoreClinicalEvidence:
    @pytest.mark.asyncio
    async def test_cancer_not_found(self):
        """Unknown cancer type returns score 0."""
        db = MockSession()
        db.queue_result(MockResult(rows=[]))  # cancer not found

        result = await scorer.score_clinical_evidence(1, 999, db)
        assert result["score"] == 0
        assert result["details"]["reason"] == "cancer_not_found"

    @pytest.mark.asyncio
    async def test_no_trials(self):
        """No trials = score 0 (plus any OT bonus)."""
        cancer = type("Row", (), {"name": "Breast Cancer", "tcga_code": "BRCA"})()
        db = MockSession()
        db.queue_result(MockResult(rows=[cancer]))  # cancer found
        db.queue_result(MockResult(rows=[]))  # no trials
        db.queue_result(MockResult(rows=[]))  # no target_ids
        # ot_bonus query won't happen because target_ids is empty

        result = await scorer.score_clinical_evidence(1, 1, db)
        assert result["details"]["relevant_trials"] == 0
        assert result["details"]["trial_phase_score"] == 0

    @pytest.mark.asyncio
    async def test_phase2_completed_trial(self):
        """Phase 2 completed trial scores 15 * 1.2 = 18."""
        cancer = type("Row", (), {"name": "Breast Cancer", "tcga_code": "BRCA"})()
        trial = make_clinical_trial(
            phase="Phase 2", status="completed", conditions=["Breast Cancer"]
        )

        db = MockSession()
        db.queue_result(MockResult(rows=[cancer]))  # cancer name
        db.queue_result(MockResult(rows=[trial]))  # trials
        db.queue_result(MockResult(rows=[]))  # no target_ids

        result = await scorer.score_clinical_evidence(1, 1, db)
        assert result["details"]["relevant_trials"] == 1
        # Phase 2 = 15, completed = *1.2 = 18
        assert result["details"]["trial_phase_score"] == 18

    @pytest.mark.asyncio
    async def test_phase3_terminated_trial(self):
        """Phase 3 terminated trial: 25 * 0.5 = 12."""
        cancer = type("Row", (), {"name": "Lung Cancer", "tcga_code": "LUAD"})()
        trial = make_clinical_trial(
            phase="Phase 3", status="terminated",
            title="Phase 3 trial in Lung Cancer",
            conditions=["Lung Cancer"],
        )

        db = MockSession()
        db.queue_result(MockResult(rows=[cancer]))
        db.queue_result(MockResult(rows=[trial]))
        db.queue_result(MockResult(rows=[]))

        result = await scorer.score_clinical_evidence(1, 1, db)
        assert result["details"]["trial_phase_score"] == 12  # 25 * 0.5

    @pytest.mark.asyncio
    async def test_irrelevant_trial_not_counted(self):
        """Trial for different cancer not counted."""
        cancer = type("Row", (), {"name": "Breast Cancer", "tcga_code": "BRCA"})()
        trial = make_clinical_trial(
            title="Phase 3 trial in Lung Cancer",
            conditions=["Lung Cancer"],
        )

        db = MockSession()
        db.queue_result(MockResult(rows=[cancer]))
        db.queue_result(MockResult(rows=[trial]))
        db.queue_result(MockResult(rows=[]))

        result = await scorer.score_clinical_evidence(1, 1, db)
        assert result["details"]["relevant_trials"] == 0
        assert result["details"]["trial_phase_score"] == 0

    @pytest.mark.asyncio
    async def test_opentargets_bonus(self):
        """OpenTargets target-disease association adds bonus."""
        cancer = type("Row", (), {"name": "Breast Cancer", "tcga_code": "BRCA"})()
        assoc = type("TDA", (), {
            "disease_name": "Breast Cancer",
            "overall_score": 0.8,
        })()

        db = MockSession()
        db.queue_result(MockResult(rows=[cancer]))  # cancer name
        db.queue_result(MockResult(rows=[]))  # no trials
        db.queue_result(MockResult(rows=[(1,)]))  # target_ids
        db.queue_result(MockResult(rows=[assoc]))  # OT associations

        result = await scorer.score_clinical_evidence(1, 1, db)
        assert result["details"]["opentargets_bonus"] == 12  # 0.8 * 15 = 12

    @pytest.mark.asyncio
    async def test_opentargets_bonus_capped(self):
        """OT bonus caps at 25."""
        cancer = type("Row", (), {"name": "Breast Cancer", "tcga_code": "BRCA"})()
        assocs = [
            type("TDA", (), {"disease_name": "Breast Cancer", "overall_score": 0.9})()
            for _ in range(5)
        ]

        db = MockSession()
        db.queue_result(MockResult(rows=[cancer]))
        db.queue_result(MockResult(rows=[]))
        db.queue_result(MockResult(rows=[(1,)]))
        db.queue_result(MockResult(rows=assocs))

        result = await scorer.score_clinical_evidence(1, 1, db)
        assert result["details"]["opentargets_bonus"] <= 25

    @pytest.mark.asyncio
    async def test_evidence_limited_to_five(self):
        """Evidence list capped at 5 trials."""
        cancer = type("Row", (), {"name": "Breast Cancer", "tcga_code": "BRCA"})()
        trials = [
            make_clinical_trial(
                id=i, nct_id=f"NCT{i:08d}",
                conditions=["Breast Cancer"],
            )
            for i in range(10)
        ]

        db = MockSession()
        db.queue_result(MockResult(rows=[cancer]))
        db.queue_result(MockResult(rows=trials))
        db.queue_result(MockResult(rows=[]))

        result = await scorer.score_clinical_evidence(1, 1, db)
        assert len(result["evidence"]) <= 5


# ===================================================================
# 5. Safety Score
# ===================================================================


class TestScoreSafety:
    @pytest.mark.asyncio
    async def test_drug_not_found(self):
        """Unknown drug returns score 0."""
        db = MockSession()
        db.queue_result(MockResult(scalar_value=None))  # drug not found

        result = await scorer.score_safety(999, 1, db)
        assert result["score"] == 0

    @pytest.mark.asyncio
    async def test_approved_drug_with_moa_and_cancer_indication(self):
        """Approved + MoA + cancer indication = 60 + 15 + 10 = 85 (+ bioassays)."""
        drug = make_drug(status="approved", indication="Treatment of various cancer types")
        db = MockSession()
        db.queue_result(MockResult(scalar_value=drug))  # drug
        db.queue_result(MockResult(scalar_value="Breast Cancer"))  # cancer name
        db.queue_result(MockResult(scalar_value=0))  # 0 bioassays

        result = await scorer.score_safety(1, 1, db)
        # approved=60, moa=15, cancer in indication=10
        assert result["score"] == 85

    @pytest.mark.asyncio
    async def test_approved_drug_exact_cancer_match(self):
        """Exact cancer name match in indication = +20."""
        drug = make_drug(status="approved", indication="Treatment of breast cancer")
        db = MockSession()
        db.queue_result(MockResult(scalar_value=drug))
        db.queue_result(MockResult(scalar_value="Breast Cancer"))
        db.queue_result(MockResult(scalar_value=0))

        result = await scorer.score_safety(1, 1, db)
        # approved=60, moa=15, exact match=20
        assert result["score"] == 95

    @pytest.mark.asyncio
    async def test_investigational_no_moa(self):
        """Investigational drug without MoA."""
        drug = make_drug(status="investigational", mechanism_of_action=None, indication=None)
        db = MockSession()
        db.queue_result(MockResult(scalar_value=drug))
        db.queue_result(MockResult(scalar_value="Lung Cancer"))
        db.queue_result(MockResult(scalar_value=0))

        result = await scorer.score_safety(1, 1, db)
        assert result["score"] == 40  # base only

    @pytest.mark.asyncio
    async def test_experimental_drug(self):
        """Experimental drug base score = 20."""
        drug = make_drug(status="experimental", mechanism_of_action=None, indication=None)
        db = MockSession()
        db.queue_result(MockResult(scalar_value=drug))
        db.queue_result(MockResult(scalar_value="Colon Cancer"))
        db.queue_result(MockResult(scalar_value=0))

        result = await scorer.score_safety(1, 1, db)
        assert result["score"] == 20

    @pytest.mark.asyncio
    async def test_withdrawn_drug(self):
        """Withdrawn drug base score = 5."""
        drug = make_drug(status="withdrawn", mechanism_of_action=None, indication=None)
        db = MockSession()
        db.queue_result(MockResult(scalar_value=drug))
        db.queue_result(MockResult(scalar_value="Brain Cancer"))
        db.queue_result(MockResult(scalar_value=0))

        result = await scorer.score_safety(1, 1, db)
        assert result["score"] == 5

    @pytest.mark.asyncio
    async def test_unknown_status_defaults_to_30(self):
        """Unknown drug status defaults to base 30."""
        drug = make_drug(status="nutraceutical", mechanism_of_action=None, indication=None)
        db = MockSession()
        db.queue_result(MockResult(scalar_value=drug))
        db.queue_result(MockResult(scalar_value="Liver Cancer"))
        db.queue_result(MockResult(scalar_value=0))

        result = await scorer.score_safety(1, 1, db)
        assert result["score"] == 30

    @pytest.mark.asyncio
    async def test_bioassay_bonus(self):
        """Active bioassays add +2 each, max 10."""
        drug = make_drug(status="approved", indication=None)
        db = MockSession()
        db.queue_result(MockResult(scalar_value=drug))
        db.queue_result(MockResult(scalar_value="Pancreatic Cancer"))
        db.queue_result(MockResult(scalar_value=8))  # 8 active bioassays

        result = await scorer.score_safety(1, 1, db)
        # approved=60, moa=15, cancer_bonus=0, bioassay=min(8*2, 10)=10
        assert result["details"]["bioassay_bonus"] == 10

    @pytest.mark.asyncio
    async def test_score_capped_at_100(self):
        """Safety score caps at 100."""
        drug = make_drug(
            status="approved",
            indication="Treatment of breast cancer and other neoplasms",
        )
        db = MockSession()
        db.queue_result(MockResult(scalar_value=drug))
        db.queue_result(MockResult(scalar_value="Breast Cancer"))
        db.queue_result(MockResult(scalar_value=10))  # many bioassays

        result = await scorer.score_safety(1, 1, db)
        assert result["score"] <= 100


# ===================================================================
# 6. Novelty Score
# ===================================================================


class TestScoreNovelty:
    @pytest.mark.asyncio
    async def test_completely_novel(self):
        """Zero papers and zero trials = novelty 100."""
        db = MockSession()
        db.queue_result(MockResult(scalar_value=0))  # co-mention count
        db.queue_result(MockResult(scalar_value="Breast Cancer"))  # cancer name
        db.queue_result(MockResult(rows=[]))  # no trials
        db.queue_result(MockResult(scalar_value=None))  # drug indication

        result = await scorer.score_novelty(1, 1, db)
        assert result["score"] == 100

    @pytest.mark.asyncio
    async def test_few_papers_reduces_novelty(self):
        """2 co-mention papers with exponential decay."""
        db = MockSession()
        db.queue_result(MockResult(scalar_value=2))
        db.queue_result(MockResult(scalar_value="Lung Cancer"))
        db.queue_result(MockResult(rows=[]))
        db.queue_result(MockResult(scalar_value=None))

        result = await scorer.score_novelty(1, 1, db)
        # 100 * exp(-0.5 * 2) * exp(-1.0 * 0) = 100 * 0.3679 = 37
        assert result["score"] == 37

    @pytest.mark.asyncio
    async def test_trials_reduce_novelty(self):
        """1 relevant trial with exponential decay."""
        trial = make_clinical_trial(conditions=["Breast Cancer"])
        db = MockSession()
        db.queue_result(MockResult(scalar_value=0))  # no papers
        db.queue_result(MockResult(scalar_value="Breast Cancer"))
        db.queue_result(MockResult(rows=[trial]))
        db.queue_result(MockResult(scalar_value=None))

        result = await scorer.score_novelty(1, 1, db)
        # 100 * exp(-0.5 * 0) * exp(-1.0 * 1) = 100 * 0.3679 = 37
        assert result["score"] == 37

    @pytest.mark.asyncio
    async def test_already_indicated(self):
        """Drug already indicated for this cancer = score 5."""
        db = MockSession()
        db.queue_result(MockResult(scalar_value=0))
        db.queue_result(MockResult(scalar_value="Breast Cancer"))
        db.queue_result(MockResult(rows=[]))
        db.queue_result(MockResult(scalar_value="Treatment of breast cancer"))

        result = await scorer.score_novelty(1, 1, db)
        assert result["score"] == 5
        assert result["details"]["already_indicated"] is True

    @pytest.mark.asyncio
    async def test_many_papers_and_trials_floor_at_zero(self):
        """Novelty floors at 0, never negative."""
        trials = [
            make_clinical_trial(id=i, nct_id=f"NCT{i}", conditions=["Colon Cancer"])
            for i in range(10)
        ]
        db = MockSession()
        db.queue_result(MockResult(scalar_value=15))  # 15 papers
        db.queue_result(MockResult(scalar_value="Colon Cancer"))
        db.queue_result(MockResult(rows=trials))
        db.queue_result(MockResult(scalar_value=None))

        result = await scorer.score_novelty(1, 1, db)
        assert result["score"] == 0  # 100 - 15*8 - 10*15 = -170 -> capped at 0

    @pytest.mark.asyncio
    async def test_irrelevant_trials_not_counted(self):
        """Trials for different cancer don't reduce novelty."""
        trial = make_clinical_trial(
            title="Trial for Lung Cancer",
            conditions=["Lung Cancer"],
        )
        db = MockSession()
        db.queue_result(MockResult(scalar_value=0))
        db.queue_result(MockResult(scalar_value="Breast Cancer"))
        db.queue_result(MockResult(rows=[trial]))
        db.queue_result(MockResult(scalar_value=None))

        result = await scorer.score_novelty(1, 1, db)
        assert result["score"] == 100  # trial is for different cancer


# ===================================================================
# score_all_dimensions
# ===================================================================


class TestScoreAllDimensions:
    @pytest.mark.asyncio
    async def test_returns_all_ten_dimensions(self):
        """score_all_dimensions returns all 10 dimension keys."""
        # We need mock results for all 10 scorers
        db = MockSession()

        # Pathway (uses pathway_data param, no DB calls)
        # Expression: cache miss, no targets
        db.queue_result(MockResult(scalar_value=None))  # cache miss
        db.queue_result(MockResult(rows=[]))  # no targets

        # Literature: no papers
        db.queue_result(MockResult(rows=[]))  # co-mention
        db.queue_result(MockResult(scalar_value=0))  # analyzed

        # Clinical: cancer not found
        db.queue_result(MockResult(rows=[]))  # cancer row

        # Safety: drug not found
        db.queue_result(MockResult(scalar_value=None))

        # Novelty
        db.queue_result(MockResult(scalar_value=0))  # co-mention count
        db.queue_result(MockResult(scalar_value=None))  # cancer name
        db.queue_result(MockResult(rows=[]))  # trials
        db.queue_result(MockResult(scalar_value=None))  # indication

        # Causal dependency: cancer not found
        db.queue_result(MockResult(rows=[]))  # cancer row

        # GNN link: no training run
        db.queue_result(MockResult(scalar_value=None))  # latest run

        # Mutation context: cancer not found
        db.queue_result(MockResult(rows=[]))  # cancer row

        # Polypharmacology: no official targets
        db.queue_result(MockResult(rows=[]))  # official targets
        db.queue_result(MockResult(rows=[]))  # bioassay hits

        pathway_data = {
            "shared_pathways": [],
            "shared_count": 0,
            "total_drug_target_pathways": 1,
            "total_cancer_altered_pathways": 1,
        }

        result = await scorer.score_all_dimensions(
            1, 1, db, pathway_data=pathway_data
        )

        expected_keys = {
            "pathway_overlap",
            "expression_correlation",
            "literature_support",
            "clinical_evidence",
            "safety",
            "novelty",
            "causal_dependency",
            "gnn_link",
            "mutation_context",
            "polypharmacology",
        }
        assert set(result.keys()) == expected_keys

        # Each dimension should have score, details, evidence
        for dim in expected_keys:
            assert "score" in result[dim]
            assert "details" in result[dim]
