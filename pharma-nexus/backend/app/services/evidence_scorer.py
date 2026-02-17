"""Evidence scoring service for hypothesis composite scoring.

Implements 6 independent scoring dimensions (each 0-100):
  1. pathway_overlap     — Shared pathways between drug targets and cancer alterations
  2. expression_correlation — Drug target expression compatibility in the cancer type
  3. literature_support   — Supporting publications mentioning drug + cancer
  4. clinical_evidence    — Existing clinical trials for the drug-cancer pair
  5. safety               — Drug safety/approval status and known toxicity
  6. novelty              — Inverse of existing evidence (fewer papers/trials = more novel)

Each scorer returns a dict with {"score": 0-100, "details": {...}, "evidence": [...]}.
"""

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerMolecularProfile, CancerType
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import Drug, DrugTarget, LiteratureDrug, TrialDrug
from app.models.evidence import Bioassay
from app.models.expression_cache import ExpressionScoreCache
from app.models.literature import Literature, LiteratureCancer
from app.models.pathway import Pathway, PathwayTarget
from app.models.target import Target
from app.models.target_disease import TargetDiseaseAssociation

logger = logging.getLogger(__name__)


class EvidenceScorer:
    """Scores drug-cancer pairs across 6 evidence dimensions.

    Each scoring method is independent and can be called individually.
    The composite score is a weighted sum computed by the HypothesisEngine
    using weights from ScoringConfig.
    """

    # ------------------------------------------------------------------
    # 1. Pathway Overlap Score
    # ------------------------------------------------------------------

    async def score_pathway_overlap(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
        pathway_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Score based on shared pathways between drug targets and cancer alterations.

        Uses pre-computed pathway overlap data from PathwayAnalyzer if provided,
        otherwise computes it on the fly.

        Scoring:
          - Base: (shared_pathways / max(drug_pathways, cancer_pathways)) * 50
          - Bonus for each shared pathway with high significance: +10 per pathway (max 30)
          - Bonus for direct target-alteration overlap: +20
          - Cap at 100
        """
        if pathway_data is None:
            from app.services.pathway_analyzer import PathwayAnalyzer

            analyzer = PathwayAnalyzer(db)
            pathway_data = await analyzer.get_pathway_overlap(drug_id, cancer_type_id)

        shared = pathway_data.get("shared_pathways", [])
        total_drug = max(pathway_data.get("total_drug_target_pathways", 1), 1)
        total_cancer = max(pathway_data.get("total_cancer_altered_pathways", 1), 1)
        shared_count = pathway_data.get("shared_count", len(shared))

        # Base score: fraction of shared pathways
        denominator = max(total_drug, total_cancer)
        base_score = (shared_count / denominator) * 50

        # Bonus for highly significant shared pathways
        sig_bonus = 0
        for pw in shared:
            if pw.get("overlap_significance", 0) >= 0.7:
                sig_bonus += 10
        sig_bonus = min(sig_bonus, 30)

        # Bonus for direct target overlap (drug target gene is also cancer-altered)
        direct_overlap = 0
        for pw in shared:
            drug_genes = set(pw.get("drug_targets_in_pathway", []))
            cancer_genes = set(pw.get("cancer_altered_genes_in_pathway", []))
            if drug_genes & cancer_genes:
                direct_overlap = 20
                break

        score = min(round(base_score + sig_bonus + direct_overlap), 100)

        evidence = []
        for pw in shared[:5]:
            evidence.append({
                "evidence_type": "pathway_overlap",
                "source_type": "pathway",
                "source_id": str(pw.get("pathway_id", "")),
                "description": (
                    f"Shared pathway: {pw.get('pathway_name', 'Unknown')} — "
                    f"drug targets: {', '.join(pw.get('drug_targets_in_pathway', [])[:3])}, "
                    f"cancer genes: {', '.join(pw.get('cancer_altered_genes_in_pathway', [])[:3])}"
                ),
                "strength": "strong" if pw.get("overlap_significance", 0) >= 0.7 else "moderate",
                "confidence": pw.get("overlap_significance", 0.5),
                "raw_data": pw,
            })

        return {
            "score": score,
            "details": {
                "shared_pathway_count": shared_count,
                "total_drug_pathways": total_drug,
                "total_cancer_pathways": total_cancer,
                "base_score": round(base_score, 1),
                "significance_bonus": sig_bonus,
                "direct_overlap_bonus": direct_overlap,
            },
            "evidence": evidence,
        }

    # ------------------------------------------------------------------
    # 2. Expression Correlation Score
    # ------------------------------------------------------------------

    async def score_expression_correlation(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Score based on expression compatibility of drug targets in the cancer type.

        Reads from expression_score_cache (precomputed by ExpressionAnalyzer).
        Falls back to on-the-fly computation if not cached.

        Scoring:
          - Directly uses the cached drug_expression score (0-100)
          - If no cached score, compute a lightweight approximation
        """
        # Check cache first
        cache_result = await db.execute(
            select(ExpressionScoreCache).where(
                ExpressionScoreCache.cache_type == "drug_expression",
                ExpressionScoreCache.entity_id_1 == drug_id,
                ExpressionScoreCache.entity_id_2 == cancer_type_id,
            )
        )
        cached = cache_result.scalar_one_or_none()

        if cached:
            details = cached.details or {}
            score = round(cached.score) if cached.score else 0

            evidence = []
            for target_info in details.get("target_scores", [])[:5]:
                evidence.append({
                    "evidence_type": "expression_correlation",
                    "source_type": "expression_analysis",
                    "source_id": f"drug_{drug_id}_cancer_{cancer_type_id}",
                    "description": (
                        f"Target {target_info.get('gene_symbol', '?')}: "
                        f"{target_info.get('action_type', '?')} with "
                        f"z-score={target_info.get('zscore', 0):.2f}, "
                        f"compatibility={target_info.get('compatibility', 0):.2f}"
                    ),
                    "strength": _score_to_strength(target_info.get("compatibility", 0) * 100),
                    "confidence": min(target_info.get("compatibility", 0), 1.0),
                    "raw_data": target_info,
                })

            return {"score": score, "details": details, "evidence": evidence}

        # Fallback: lightweight approximation
        return await self._compute_expression_score_lightweight(
            drug_id, cancer_type_id, db
        )

    async def _compute_expression_score_lightweight(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Lightweight expression scoring when cache miss occurs."""
        # Get drug targets
        result = await db.execute(
            select(DrugTarget, Target)
            .join(Target, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug_id)
        )
        targets = result.all()

        if not targets:
            return {
                "score": 0,
                "details": {"reason": "no_targets"},
                "evidence": [],
            }

        scores = []
        evidence = []
        for dt, target in targets:
            # Get expression profile in this cancer
            profile_result = await db.execute(
                select(CancerMolecularProfile).where(
                    CancerMolecularProfile.cancer_type_id == cancer_type_id,
                    CancerMolecularProfile.gene_symbol == target.gene_symbol,
                    CancerMolecularProfile.alteration_type.in_(
                        ["overexpression", "underexpression"]
                    ),
                )
            )
            profile = profile_result.scalar_one_or_none()

            if profile:
                action = (dt.action_type or "").lower()
                zscore = profile.expression_zscore or 0

                # Action-expression compatibility
                if "inhibit" in action and zscore > 0:
                    compat = min(zscore / 3.0, 1.0)
                elif "agonist" in action and zscore < 0:
                    compat = min(abs(zscore) / 3.0, 1.0)
                elif zscore != 0:
                    compat = min(abs(zscore) / 5.0, 0.5)
                else:
                    compat = 0.1

                scores.append(compat)
                evidence.append({
                    "evidence_type": "expression_correlation",
                    "source_type": "molecular_profile",
                    "source_id": f"target_{target.id}",
                    "description": (
                        f"{target.gene_symbol}: {action or 'unknown action'}, "
                        f"z-score={zscore:.2f}, compatibility={compat:.2f}"
                    ),
                    "strength": _score_to_strength(compat * 100),
                    "confidence": compat,
                    "raw_data": {
                        "gene_symbol": target.gene_symbol,
                        "action_type": action,
                        "zscore": zscore,
                        "compatibility": compat,
                    },
                })

        if not scores:
            return {
                "score": 0,
                "details": {"reason": "no_expression_data"},
                "evidence": [],
            }

        avg_score = sum(scores) / len(scores)
        return {
            "score": min(round(avg_score * 100), 100),
            "details": {
                "targets_scored": len(scores),
                "avg_compatibility": round(avg_score, 3),
            },
            "evidence": evidence[:5],
        }

    # ------------------------------------------------------------------
    # 3. Literature Support Score
    # ------------------------------------------------------------------

    async def score_literature_support(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Score based on literature mentioning both the drug and cancer type.

        Scoring:
          - Count papers mentioning both drug AND cancer: co_mentions
          - Count papers mentioning drug with analyzed findings: analyzed_count
          - Score = min(co_mentions * 8 + analyzed_count * 3, 100)
          - Bonus for papers with "repurposing" in relevance_tags
        """
        # Count co-mention papers (lightweight — no full object load)
        co_count_query = (
            select(func.count(Literature.id))
            .join(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
            .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
            .where(
                LiteratureDrug.drug_id == drug_id,
                LiteratureCancer.cancer_type_id == cancer_type_id,
            )
        )
        co_count_result = await db.execute(co_count_query)
        co_count = co_count_result.scalar() or 0

        # Fetch only top papers for evidence and repurposing check (limit 20)
        co_papers_query = (
            select(Literature)
            .join(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
            .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
            .where(
                LiteratureDrug.drug_id == drug_id,
                LiteratureCancer.cancer_type_id == cancer_type_id,
            )
            .limit(20)
        )
        co_papers_result = await db.execute(co_papers_query)
        co_papers = co_papers_result.scalars().all()

        # Papers mentioning drug with analyzed findings
        analyzed_query = (
            select(func.count(Literature.id))
            .join(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
            .where(
                LiteratureDrug.drug_id == drug_id,
                Literature.analysis_status == "analyzed",
            )
        )
        analyzed_result = await db.execute(analyzed_query)
        analyzed_count = analyzed_result.scalar() or 0

        # Check for repurposing-specific papers (only from fetched subset)
        repurposing_bonus = 0
        for paper in co_papers:
            tags = paper.relevance_tags or []
            if any("repurpos" in str(t).lower() for t in tags):
                repurposing_bonus += 5
        repurposing_bonus = min(repurposing_bonus, 20)

        score = min(co_count * 8 + analyzed_count * 3 + repurposing_bonus, 100)

        evidence = []
        for paper in co_papers[:5]:
            findings = paper.extracted_findings or {}
            evidence.append({
                "evidence_type": "literature_support",
                "source_type": "pubmed",
                "source_id": paper.pmid,
                "description": (
                    f"{paper.title[:120]}... "
                    f"(PMID: {paper.pmid}, {paper.journal or 'Unknown journal'})"
                ),
                "strength": "strong" if findings else "moderate",
                "confidence": 0.8 if findings else 0.5,
                "raw_data": {
                    "pmid": paper.pmid,
                    "title": paper.title,
                    "journal": paper.journal,
                    "pub_date": str(paper.pub_date) if paper.pub_date else None,
                    "has_findings": bool(findings),
                    "relevance_tags": paper.relevance_tags,
                },
            })

        return {
            "score": score,
            "details": {
                "co_mention_papers": co_count,
                "analyzed_papers": analyzed_count,
                "repurposing_bonus": repurposing_bonus,
            },
            "evidence": evidence,
        }

    # ------------------------------------------------------------------
    # 4. Clinical Evidence Score
    # ------------------------------------------------------------------

    async def score_clinical_evidence(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Score based on clinical trial evidence for the drug-cancer pair.

        Scoring:
          - Trials directly testing drug for this cancer type
          - Phase weights: Phase 3/4 = 25, Phase 2 = 15, Phase 1 = 8, Other = 3
          - Active/completed trials score higher than terminated
          - Target-disease associations from OpenTargets add bonus
          - Cap at 100
        """
        # Get cancer type name for condition matching
        cancer_result = await db.execute(
            select(CancerType.name, CancerType.tcga_code).where(
                CancerType.id == cancer_type_id
            )
        )
        cancer_row = cancer_result.first()
        if not cancer_row:
            return {"score": 0, "details": {"reason": "cancer_not_found"}, "evidence": []}
        cancer_name = cancer_row.name.lower()

        # Count total drug trials (lightweight)
        total_trial_count_result = await db.execute(
            select(func.count(ClinicalTrial.id))
            .join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
            .where(TrialDrug.drug_id == drug_id)
        )
        total_drug_trials = total_trial_count_result.scalar() or 0

        # Fetch limited set of trials for this drug (cap at 50 most recent)
        trial_query = (
            select(ClinicalTrial)
            .join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
            .where(TrialDrug.drug_id == drug_id)
            .order_by(ClinicalTrial.id.desc())
            .limit(50)
        )
        trial_result = await db.execute(trial_query)
        fetched_trials = trial_result.scalars().all()

        # Filter trials relevant to this cancer type
        relevant_trials = []
        for trial in fetched_trials:
            conditions = trial.conditions or []
            title_lower = trial.title.lower() if trial.title else ""
            condition_match = any(
                cancer_name in str(c).lower() for c in conditions
            ) or cancer_name in title_lower
            if condition_match:
                relevant_trials.append(trial)

        # Score based on phase
        phase_scores = {"Phase 4": 25, "Phase 3": 25, "Phase 2": 15, "Phase 1": 8}
        trial_score = 0
        for trial in relevant_trials:
            phase = trial.phase or ""
            base = 3
            for phase_key, phase_val in phase_scores.items():
                if phase_key.lower() in phase.lower():
                    base = phase_val
                    break
            # Active/completed bonus
            status = (trial.status or "").lower()
            if status in ("completed", "active, not recruiting"):
                base = int(base * 1.2)
            elif status in ("terminated", "withdrawn"):
                base = int(base * 0.5)
            trial_score += base

        # OpenTargets target-disease association bonus
        drug_targets_result = await db.execute(
            select(DrugTarget.target_id).where(DrugTarget.drug_id == drug_id)
        )
        target_ids = [r[0] for r in drug_targets_result.all()]

        ot_bonus = 0
        if target_ids:
            ot_result = await db.execute(
                select(TargetDiseaseAssociation).where(
                    TargetDiseaseAssociation.target_id.in_(target_ids),
                )
            )
            associations = ot_result.scalars().all()
            for assoc in associations:
                disease_name = (assoc.disease_name or "").lower()
                if cancer_name in disease_name or disease_name in cancer_name:
                    ot_bonus += round((assoc.overall_score or 0) * 15)
            ot_bonus = min(ot_bonus, 25)

        score = min(trial_score + ot_bonus, 100)

        evidence = []
        for trial in relevant_trials[:5]:
            evidence.append({
                "evidence_type": "clinical_evidence",
                "source_type": "clinical_trial",
                "source_id": trial.nct_id,
                "description": (
                    f"{trial.title[:120]}... "
                    f"({trial.phase or 'Unknown phase'}, {trial.status or 'Unknown status'})"
                ),
                "strength": _phase_to_strength(trial.phase),
                "confidence": _phase_to_confidence(trial.phase),
                "raw_data": {
                    "nct_id": trial.nct_id,
                    "phase": trial.phase,
                    "status": trial.status,
                    "enrollment": trial.enrollment,
                },
            })

        return {
            "score": score,
            "details": {
                "relevant_trials": len(relevant_trials),
                "total_drug_trials": total_drug_trials,
                "trial_phase_score": trial_score,
                "opentargets_bonus": ot_bonus,
            },
            "evidence": evidence,
        }

    # ------------------------------------------------------------------
    # 5. Safety Score
    # ------------------------------------------------------------------

    async def score_safety(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Score based on drug safety profile and approval status.

        Scoring:
          - Approved drug: base 60
          - Investigational: base 40
          - Experimental: base 20
          - Withdrawn: base 5
          - Bonus for known mechanism of action: +15
          - Bonus for existing cancer indication: +20
          - Bonus for active bioassays: +2 each (max 10)
          - Cap at 100
        """
        drug_result = await db.execute(
            select(Drug).where(Drug.id == drug_id)
        )
        drug = drug_result.scalar_one_or_none()
        if not drug:
            return {"score": 0, "details": {"reason": "drug_not_found"}, "evidence": []}

        # Base score from approval status
        status = (drug.status or "").lower()
        status_scores = {
            "approved": 60,
            "investigational": 40,
            "experimental": 20,
            "withdrawn": 5,
        }
        base_score = status_scores.get(status, 30)

        # Mechanism of action bonus
        moa_bonus = 15 if drug.mechanism_of_action else 0

        # Cancer indication bonus
        cancer_bonus = 0
        cancer_result = await db.execute(
            select(CancerType.name).where(CancerType.id == cancer_type_id)
        )
        cancer_name = cancer_result.scalar_one_or_none()
        if cancer_name and drug.indication:
            indication_lower = drug.indication.lower()
            if "cancer" in indication_lower or "tumor" in indication_lower or "neoplasm" in indication_lower:
                cancer_bonus = 10
            if cancer_name.lower() in indication_lower:
                cancer_bonus = 20

        # Bioassay safety signals
        bioassay_result = await db.execute(
            select(func.count(Bioassay.id)).where(
                Bioassay.drug_id == drug_id,
                Bioassay.activity_outcome == "active",
            )
        )
        active_assays = bioassay_result.scalar() or 0
        assay_bonus = min(active_assays * 2, 10)

        score = min(base_score + moa_bonus + cancer_bonus + assay_bonus, 100)

        evidence = [{
            "evidence_type": "safety",
            "source_type": "drug_profile",
            "source_id": drug.drugbank_id,
            "description": (
                f"{drug.name}: {drug.status} drug"
                f"{', known MoA' if drug.mechanism_of_action else ''}"
                f"{', cancer indication' if cancer_bonus > 0 else ''}"
            ),
            "strength": _score_to_strength(score),
            "confidence": 0.9 if status == "approved" else 0.6,
            "raw_data": {
                "drug_name": drug.name,
                "drugbank_id": drug.drugbank_id,
                "status": drug.status,
                "has_moa": bool(drug.mechanism_of_action),
                "has_cancer_indication": cancer_bonus > 0,
                "active_bioassays": active_assays,
            },
        }]

        return {
            "score": score,
            "details": {
                "drug_status": drug.status,
                "base_score": base_score,
                "moa_bonus": moa_bonus,
                "cancer_indication_bonus": cancer_bonus,
                "bioassay_bonus": assay_bonus,
            },
            "evidence": evidence,
        }

    # ------------------------------------------------------------------
    # 6. Novelty Score
    # ------------------------------------------------------------------

    async def score_novelty(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Score inversely proportional to existing evidence.

        The FEWER existing publications and trials, the HIGHER the novelty score.
        A completely unexplored drug-cancer pair gets 100.

        Scoring:
          - co_mention_papers: each reduces score by 8 (floor at 0)
          - relevant_trials: each reduces score by 15 (floor at 0)
          - Drug already indicated for this cancer: score = 5
          - Start from 100 and subtract
        """
        # Count co-mention papers
        co_count_result = await db.execute(
            select(func.count(Literature.id))
            .join(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
            .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
            .where(
                LiteratureDrug.drug_id == drug_id,
                LiteratureCancer.cancer_type_id == cancer_type_id,
            )
        )
        co_papers = co_count_result.scalar() or 0

        # Count relevant trials
        cancer_name_result = await db.execute(
            select(CancerType.name).where(CancerType.id == cancer_type_id)
        )
        cancer_name = (cancer_name_result.scalar_one_or_none() or "").lower()

        # Fetch limited trials for relevance check (cap at 50 most recent)
        trial_query = (
            select(ClinicalTrial)
            .join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
            .where(TrialDrug.drug_id == drug_id)
            .order_by(ClinicalTrial.id.desc())
            .limit(50)
        )
        trial_result = await db.execute(trial_query)
        fetched_trials = trial_result.scalars().all()

        relevant_trials = 0
        for trial in fetched_trials:
            conditions = trial.conditions or []
            title_lower = trial.title.lower() if trial.title else ""
            if any(cancer_name in str(c).lower() for c in conditions) or cancer_name in title_lower:
                relevant_trials += 1

        # Check if drug is already indicated for this cancer
        drug_result = await db.execute(
            select(Drug.indication).where(Drug.id == drug_id)
        )
        indication = (drug_result.scalar_one_or_none() or "").lower()
        already_indicated = cancer_name and cancer_name in indication

        if already_indicated:
            score = 5
        else:
            score = max(100 - co_papers * 8 - relevant_trials * 15, 0)

        evidence = [{
            "evidence_type": "novelty",
            "source_type": "novelty_assessment",
            "source_id": f"drug_{drug_id}_cancer_{cancer_type_id}",
            "description": (
                f"Novelty assessment: {co_papers} co-mention papers, "
                f"{relevant_trials} relevant trials"
                f"{', already indicated' if already_indicated else ''}"
            ),
            "strength": _score_to_strength(score),
            "confidence": 0.85,
            "raw_data": {
                "co_mention_papers": co_papers,
                "relevant_trials": relevant_trials,
                "already_indicated": already_indicated,
            },
        }]

        return {
            "score": score,
            "details": {
                "co_mention_papers": co_papers,
                "relevant_trials": relevant_trials,
                "already_indicated": already_indicated,
            },
            "evidence": evidence,
        }

    # ------------------------------------------------------------------
    # Composite scoring
    # ------------------------------------------------------------------

    async def score_all_dimensions(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
        pathway_data: dict[str, Any] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Score all 6 dimensions for a drug-cancer pair.

        Returns a dict keyed by dimension name, each containing:
          {"score": int, "details": dict, "evidence": list}
        """
        results = {}

        results["pathway_overlap"] = await self.score_pathway_overlap(
            drug_id, cancer_type_id, db, pathway_data=pathway_data
        )
        results["expression_correlation"] = await self.score_expression_correlation(
            drug_id, cancer_type_id, db
        )
        results["literature_support"] = await self.score_literature_support(
            drug_id, cancer_type_id, db
        )
        results["clinical_evidence"] = await self.score_clinical_evidence(
            drug_id, cancer_type_id, db
        )
        results["safety"] = await self.score_safety(
            drug_id, cancer_type_id, db
        )
        results["novelty"] = await self.score_novelty(
            drug_id, cancer_type_id, db
        )

        return results


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _score_to_strength(score: float) -> str:
    if score >= 70:
        return "strong"
    if score >= 40:
        return "moderate"
    return "weak"


def _phase_to_strength(phase: str | None) -> str:
    if not phase:
        return "weak"
    phase_lower = phase.lower()
    if "3" in phase_lower or "4" in phase_lower:
        return "strong"
    if "2" in phase_lower:
        return "moderate"
    return "weak"


def _phase_to_confidence(phase: str | None) -> float:
    if not phase:
        return 0.3
    phase_lower = phase.lower()
    if "3" in phase_lower or "4" in phase_lower:
        return 0.9
    if "2" in phase_lower:
        return 0.7
    return 0.5
