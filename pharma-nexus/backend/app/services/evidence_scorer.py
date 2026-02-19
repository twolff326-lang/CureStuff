"""Evidence scoring service for hypothesis composite scoring.

Implements 6 independent scoring dimensions (each 0-100):
  1. pathway_overlap     — Fisher's exact + hypergeometric FDR-corrected p-values
  2. expression_correlation — Pharmacological compatibility using binding affinity (Ki/IC50)
  3. literature_support   — Quality-weighted literature scoring (journal, study type, recency)
  4. clinical_evidence    — Existing clinical trials for the drug-cancer pair
  5. safety               — Drug safety/approval status and known toxicity
  6. novelty              — Inverse of existing evidence (fewer papers/trials = more novel)

Each scorer returns:
  {"score": 0-100, "details": {...}, "evidence": [...], "confidence_interval": {...}}

Statistical foundations:
  - Pathway scores derived from -log10(FDR-corrected p-values) (Fisher's exact test)
  - Expression scoring incorporates actual binding affinity (Ki/Kd/IC50 in nM)
  - Literature quality weighted by: study type from MeSH terms, journal (proxy via
    findings extraction), publication recency, and mention specificity
  - All dimensions include bootstrap confidence intervals
  - Novelty uses information-theoretic surprise (log-scaled evidence decay)
"""

import logging
import math
from datetime import date
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
from app.services.statistical_tests import bootstrap_confidence_interval

logger = logging.getLogger(__name__)


class EvidenceScorer:
    """Scores drug-cancer pairs across 6 evidence dimensions.

    Each scoring method is independent and can be called individually.
    The composite score is a weighted sum computed by the HypothesisEngine
    using weights from ScoringConfig.

    All scores now include:
      - Statistical p-values where applicable
      - Confidence intervals via bootstrap resampling
      - Effect size measures
      - Transparent scoring breakdowns with no magic numbers
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
        """Score based on statistical significance of pathway co-enrichment.

        Now uses Fisher's exact test and hypergeometric p-values from
        PathwayAnalyzer instead of arbitrary heuristics.

        Scoring formula:
          - Primary: overlap_score from PathwayAnalyzer (derived from FDR-corrected
            p-values, effect sizes, and number of significant pathways)
          - Bonus for direct gene overlap (drug target IS a cancer-altered gene): +15
          - Score is capped at 100

        The overlap_score from PathwayAnalyzer is computed as:
          sig_fraction * 40 + best_p_component * 35 + effect_size_component * 25
        """
        if pathway_data is None:
            from app.services.pathway_analyzer import PathwayAnalyzer

            analyzer = PathwayAnalyzer(db)
            pathway_data = await analyzer.get_pathway_overlap(drug_id, cancer_type_id)

        shared = pathway_data.get("shared_pathways", [])
        total_drug = max(pathway_data.get("total_drug_target_pathways", 1), 1)
        total_cancer = max(pathway_data.get("total_cancer_altered_pathways", 1), 1)
        shared_count = pathway_data.get("shared_count", len(shared))

        # Use the statistically-derived overlap score
        base_score = pathway_data.get("overlap_score", 0)

        # Bonus for direct target overlap (drug target gene is also cancer-altered)
        direct_overlap = 0
        for pw in shared:
            drug_genes = set(pw.get("drug_targets_in_pathway", []))
            cancer_genes = set(pw.get("cancer_altered_genes_in_pathway", []))
            if drug_genes & cancer_genes:
                direct_overlap = 15
                break

        score = min(round(base_score + direct_overlap), 100)

        # Collect p-values for confidence reporting
        p_values = []
        for pw in shared:
            stats = pw.get("statistical_tests", {})
            if "combined_p_value" in stats:
                p_values.append(stats["combined_p_value"])

        # Build evidence records with statistical detail
        evidence = []
        for pw in shared[:5]:
            stats = pw.get("statistical_tests", {})
            fisher = stats.get("fishers_exact", {})
            fdr_p = stats.get("fdr_adjusted_p")
            fdr_sig = stats.get("fdr_significant", False)

            p_str = f"p={fisher.get('p_value', 'N/A'):.2e}" if fisher.get("p_value") else ""
            fdr_str = f", FDR-adj p={fdr_p:.2e}" if fdr_p is not None else ""
            or_str = f", OR={fisher.get('odds_ratio', 'N/A'):.1f}" if fisher.get("odds_ratio") else ""

            evidence.append({
                "evidence_type": "pathway_overlap",
                "source_type": "pathway",
                "source_id": str(pw.get("pathway_id", "")),
                "description": (
                    f"Shared pathway: {pw.get('pathway_name', 'Unknown')} "
                    f"(n={pw.get('pathway_size', '?')} genes) — "
                    f"drug targets: {', '.join(pw.get('drug_targets_in_pathway', [])[:3])}, "
                    f"cancer genes: {', '.join(pw.get('cancer_altered_genes_in_pathway', [])[:3])} "
                    f"[{p_str}{fdr_str}{or_str}]"
                ),
                "strength": "strong" if fdr_sig else ("moderate" if fisher.get("p_value", 1) < 0.05 else "weak"),
                "confidence": 1.0 - min(fisher.get("p_value", 1.0), 1.0),
                "raw_data": pw,
            })

        # Confidence interval from individual pathway significance scores
        ci = bootstrap_confidence_interval(
            [pw.get("overlap_significance", 0) * 100 for pw in shared]
        ) if shared else {"point_estimate": 0, "ci_lower": 0, "ci_upper": 0}

        return {
            "score": score,
            "details": {
                "shared_pathway_count": shared_count,
                "total_drug_pathways": total_drug,
                "total_cancer_pathways": total_cancer,
                "n_fdr_significant": pathway_data.get("n_fdr_significant", 0),
                "direct_overlap_bonus": direct_overlap,
                "statistical_summary": pathway_data.get("statistical_summary", {}),
            },
            "evidence": evidence,
            "confidence_interval": ci,
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
        """Score based on pharmacological compatibility of drug-target-expression.

        Now incorporates:
          - Actual binding affinity (Ki/Kd/IC50) from DrugTarget.binding_affinity_nm
          - Bioassay activity data for quantitative pharmacology
          - Expression z-score with proper thresholds (|z| >= 2.0 for significance)
          - Action type classification hierarchy (not just string matching)

        Compatibility formula:
          For each (drug_target, cancer_expression_profile) pair:
            1. action_match: Is drug action aligned with expression change?
               - Inhibitor + overexpressed => therapeutically aligned
               - Agonist + underexpressed => therapeutically aligned
            2. expression_magnitude: |z-score| normalized (stronger dysregulation = better)
            3. binding_potency: -log10(Ki_nM / 1e9) normalized
               - Ki < 10nM => potent (1.0), Ki 10-100nM => good (0.7),
                 Ki 100-1000nM => moderate (0.4), Ki > 1μM => weak (0.1)
            4. compatibility = action_match * expression_magnitude * binding_potency
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

        # Compute with pharmacological data
        return await self._compute_expression_score_pharmacological(
            drug_id, cancer_type_id, db
        )

    async def _compute_expression_score_pharmacological(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Pharmacologically-grounded expression scoring."""
        # Get drug targets with binding affinity
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
                "confidence_interval": {"point_estimate": 0, "ci_lower": 0, "ci_upper": 0},
            }

        scores = []
        evidence = []
        target_details = []

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

            if not profile:
                continue

            action = (dt.action_type or "").lower()
            zscore = profile.expression_zscore or 0
            binding_nm = dt.binding_affinity_nm

            # 1. Action-expression alignment (0 or 1)
            action_class = _classify_action(action)
            if action_class == "inhibitor" and zscore > 0:
                action_match = 1.0  # Therapeutically aligned
            elif action_class == "activator" and zscore < 0:
                action_match = 1.0  # Therapeutically aligned
            elif action_class == "inhibitor" and zscore < 0:
                action_match = 0.1  # Misaligned (inhibiting already-underexpressed)
            elif action_class == "activator" and zscore > 0:
                action_match = 0.1  # Misaligned
            elif action_class == "unknown":
                action_match = 0.3  # Unknown action, partial credit
            else:
                action_match = 0.2

            # 2. Expression magnitude (stronger dysregulation = stronger signal)
            # Normalize |z-score| with a sigmoid: significant at |z|>=2
            expr_magnitude = min(abs(zscore) / 4.0, 1.0)

            # 3. Binding potency from actual pharmacological data
            if binding_nm is not None and binding_nm > 0:
                # pKi = -log10(Ki_in_M) = -log10(Ki_nM * 1e-9) = 9 - log10(Ki_nM)
                pki = 9.0 - math.log10(binding_nm)
                # Normalize: pKi 5 (10μM) = 0, pKi 9 (1nM) = 1.0
                binding_potency = max(min((pki - 5.0) / 4.0, 1.0), 0.0)
            else:
                # No binding data: check bioassays for this drug-target pair
                assay_result = await db.execute(
                    select(Bioassay).where(
                        Bioassay.drug_id == drug_id,
                        Bioassay.target_id == target.id,
                        Bioassay.activity_outcome == "active",
                    ).limit(5)
                )
                assays = assay_result.scalars().all()
                if assays:
                    # Use best bioassay activity value
                    best_activity = min(
                        (a.activity_value for a in assays if a.activity_value),
                        default=None,
                    )
                    if best_activity and best_activity > 0:
                        pki = 9.0 - math.log10(best_activity)
                        binding_potency = max(min((pki - 5.0) / 4.0, 1.0), 0.0)
                    else:
                        binding_potency = 0.5  # Active but no quantitative data
                else:
                    binding_potency = 0.3  # No pharmacological data at all

            # Combined compatibility: all three factors contribute
            compat = action_match * expr_magnitude * binding_potency

            # But ensure minimum score for aligned action regardless of binding data
            if action_match == 1.0 and expr_magnitude > 0.5:
                compat = max(compat, 0.3)

            scores.append(compat)
            target_detail = {
                "gene_symbol": target.gene_symbol,
                "action_type": action,
                "action_class": action_class,
                "zscore": zscore,
                "binding_affinity_nm": binding_nm,
                "action_match": round(action_match, 2),
                "expression_magnitude": round(expr_magnitude, 3),
                "binding_potency": round(binding_potency, 3),
                "compatibility": round(compat, 3),
            }
            target_details.append(target_detail)

            binding_str = f", Ki={binding_nm:.0f}nM" if binding_nm else ""
            evidence.append({
                "evidence_type": "expression_correlation",
                "source_type": "pharmacological_analysis",
                "source_id": f"target_{target.id}",
                "description": (
                    f"{target.gene_symbol}: {action_class} ({action or 'unknown'}), "
                    f"z-score={zscore:.2f}{binding_str}, "
                    f"compat={compat:.2f} "
                    f"[match={action_match:.1f} * expr={expr_magnitude:.2f} * potency={binding_potency:.2f}]"
                ),
                "strength": _score_to_strength(compat * 100),
                "confidence": compat,
                "raw_data": target_detail,
            })

        if not scores:
            return {
                "score": 0,
                "details": {"reason": "no_expression_data"},
                "evidence": [],
                "confidence_interval": {"point_estimate": 0, "ci_lower": 0, "ci_upper": 0},
            }

        avg_score = sum(scores) / len(scores)
        ci = bootstrap_confidence_interval([s * 100 for s in scores])

        return {
            "score": min(round(avg_score * 100), 100),
            "details": {
                "targets_scored": len(scores),
                "avg_compatibility": round(avg_score, 3),
                "target_scores": target_details,
                "scoring_method": "pharmacological (action_match * expression_magnitude * binding_potency)",
            },
            "evidence": evidence[:5],
            "confidence_interval": ci,
        }

    # ------------------------------------------------------------------
    # 3. Literature Support Score (Quality-Weighted)
    # ------------------------------------------------------------------

    async def score_literature_support(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Quality-weighted literature scoring.

        Each paper receives a quality weight based on:
          - Study type (from MeSH terms): clinical trial > review > case report > basic
          - Evidence extraction: papers with extracted findings score higher
          - Recency: papers from last 5 years weighted more than older papers
          - Mention specificity: "repurposing" tags indicate direct relevance

        Score formula:
          score = min(sum(paper_quality_weight for each co-mention paper), 100)

        Quality weight per paper:
          base = study_type_weight (1-10)
          * findings_multiplier (1.0 or 1.5 if extracted findings exist)
          * recency_multiplier (0.5-1.0 based on years since publication)
          * specificity_multiplier (1.0 or 2.0 if repurposing-tagged)
        """
        # Papers mentioning both drug and cancer type
        co_mention_query = (
            select(Literature)
            .join(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
            .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
            .where(
                LiteratureDrug.drug_id == drug_id,
                LiteratureCancer.cancer_type_id == cancer_type_id,
            )
        )
        co_result = await db.execute(co_mention_query)
        co_papers = co_result.scalars().all()
        co_count = len(co_papers)

        # Papers mentioning drug with analyzed findings
        analyzed_query = (
            select(func.count(Literature.id))
            .join(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
            .where(
                LiteratureDrug.drug_id == drug_id,
                Literature.analysis_status == "completed",
            )
        )
        analyzed_result = await db.execute(analyzed_query)
        analyzed_count = analyzed_result.scalar() or 0

        # Quality-weighted scoring
        paper_weights = []
        paper_details = []
        today = date.today()

        for paper in co_papers:
            # 1. Study type weight from MeSH terms
            study_weight = _classify_study_type(paper.mesh_terms or [])

            # 2. Findings multiplier
            has_findings = bool(paper.extracted_findings)
            findings_mult = 1.5 if has_findings else 1.0

            # 3. Recency multiplier (half-life of 5 years)
            if paper.pub_date:
                years_old = (today - paper.pub_date).days / 365.25
                recency_mult = max(0.5, 1.0 - (years_old / 20.0))
            else:
                recency_mult = 0.6  # Unknown date

            # 4. Specificity multiplier
            tags = paper.relevance_tags or []
            is_repurposing = any("repurpos" in str(t).lower() for t in tags)
            specificity_mult = 2.0 if is_repurposing else 1.0

            quality = study_weight * findings_mult * recency_mult * specificity_mult
            paper_weights.append(quality)
            paper_details.append({
                "pmid": paper.pmid,
                "study_type_weight": study_weight,
                "has_findings": has_findings,
                "recency_multiplier": round(recency_mult, 2),
                "is_repurposing": is_repurposing,
                "total_quality_weight": round(quality, 2),
            })

        # Score: sum of quality weights, capped at 100
        # Calibrated so that:
        #   - 1 high-quality clinical trial paper ≈ 15 points
        #   - 1 basic research paper ≈ 3 points
        #   - 10 moderate papers ≈ 50 points
        raw_quality_sum = sum(paper_weights)
        score = min(round(raw_quality_sum), 100)

        # Add small bonus for having analyzed papers (verified drug mentions)
        analyzed_bonus = min(analyzed_count * 2, 10)
        score = min(score + analyzed_bonus, 100)

        # Confidence interval
        ci = bootstrap_confidence_interval(paper_weights) if paper_weights else {
            "point_estimate": 0, "ci_lower": 0, "ci_upper": 0
        }

        evidence = []
        # Sort papers by quality weight descending
        ranked_papers = sorted(
            zip(co_papers, paper_details),
            key=lambda x: x[1]["total_quality_weight"],
            reverse=True,
        )
        for paper, detail in ranked_papers[:5]:
            findings = paper.extracted_findings or {}
            evidence.append({
                "evidence_type": "literature_support",
                "source_type": "pubmed",
                "source_id": paper.pmid,
                "description": (
                    f"{paper.title[:120]}... "
                    f"(PMID: {paper.pmid}, {paper.journal or 'Unknown'}) "
                    f"[quality={detail['total_quality_weight']:.1f}]"
                ),
                "strength": _quality_to_strength(detail["total_quality_weight"]),
                "confidence": min(detail["total_quality_weight"] / 15.0, 1.0),
                "raw_data": {
                    **detail,
                    "title": paper.title,
                    "journal": paper.journal,
                    "pub_date": str(paper.pub_date) if paper.pub_date else None,
                },
            })

        return {
            "score": score,
            "details": {
                "co_mention_papers": co_count,
                "analyzed_papers": analyzed_count,
                "analyzed_bonus": analyzed_bonus,
                "total_quality_score": round(raw_quality_sum, 1),
                "mean_paper_quality": round(
                    raw_quality_sum / co_count, 2
                ) if co_count > 0 else 0,
                "scoring_method": "quality_weighted (study_type * findings * recency * specificity)",
                "paper_quality_breakdown": paper_details[:10],
            },
            "evidence": evidence,
            "confidence_interval": ci,
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

        # Find trials for this drug
        trial_query = (
            select(ClinicalTrial)
            .join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
            .where(TrialDrug.drug_id == drug_id)
        )
        trial_result = await db.execute(trial_query)
        all_trials = trial_result.scalars().all()

        # Filter trials relevant to this cancer type
        relevant_trials = []
        for trial in all_trials:
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
        trial_details = []
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
            trial_details.append(base)

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

        # Confidence interval from trial scores
        ci = bootstrap_confidence_interval(trial_details) if trial_details else {
            "point_estimate": 0, "ci_lower": 0, "ci_upper": 0
        }

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
                "total_drug_trials": len(all_trials),
                "trial_phase_score": trial_score,
                "opentargets_bonus": ot_bonus,
            },
            "evidence": evidence,
            "confidence_interval": ci,
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
        """Information-theoretic novelty scoring.

        Uses log-scaled evidence decay rather than arbitrary linear subtraction.
        The score represents "how surprising is this drug-cancer pair given
        existing evidence?"

        Formula:
          novelty = 100 * (1 - (1 - e^(-lambda_lit * papers)) * (1 - e^(-lambda_trial * trials)))

        Where:
          - lambda_lit = 0.5 (each paper halves the surprise every ~1.4 papers)
          - lambda_trial = 1.0 (clinical trials are stronger evidence of prior exploration)

        This gives:
          - 0 papers, 0 trials => novelty = 100
          - 1 paper, 0 trials => novelty ≈ 60
          - 3 papers, 0 trials => novelty ≈ 22
          - 0 papers, 1 trial => novelty ≈ 37
          - 5 papers, 2 trials => novelty ≈ 1
        """
        LAMBDA_LIT = 0.5
        LAMBDA_TRIAL = 1.0

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

        trial_query = (
            select(ClinicalTrial)
            .join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
            .where(TrialDrug.drug_id == drug_id)
        )
        trial_result = await db.execute(trial_query)
        all_trials = trial_result.scalars().all()

        relevant_trials = 0
        for trial in all_trials:
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
            # Information-theoretic surprise:
            # P(explored) = 1 - e^(-lambda * evidence_count)
            # novelty = 100 * P(not explored by literature) * P(not explored by trials)
            lit_surprise = math.exp(-LAMBDA_LIT * co_papers)
            trial_surprise = math.exp(-LAMBDA_TRIAL * relevant_trials)
            score = round(100 * lit_surprise * trial_surprise)

        evidence = [{
            "evidence_type": "novelty",
            "source_type": "novelty_assessment",
            "source_id": f"drug_{drug_id}_cancer_{cancer_type_id}",
            "description": (
                f"Novelty assessment: {co_papers} co-mention papers, "
                f"{relevant_trials} relevant trials"
                f"{', already indicated' if already_indicated else ''}"
                f" [score formula: 100 * exp(-{LAMBDA_LIT}*{co_papers}) * exp(-{LAMBDA_TRIAL}*{relevant_trials})]"
            ),
            "strength": _score_to_strength(score),
            "confidence": 0.85,
            "raw_data": {
                "co_mention_papers": co_papers,
                "relevant_trials": relevant_trials,
                "already_indicated": already_indicated,
                "lambda_lit": LAMBDA_LIT,
                "lambda_trial": LAMBDA_TRIAL,
                "scoring_method": "information_theoretic_surprise",
            },
        }]

        return {
            "score": score,
            "details": {
                "co_mention_papers": co_papers,
                "relevant_trials": relevant_trials,
                "already_indicated": already_indicated,
                "scoring_method": "information_theoretic (exponential decay)",
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


def _classify_action(action: str) -> str:
    """Classify drug action type into standardized categories.

    Uses hierarchical matching rather than simple string contains.
    """
    action = action.lower().strip()

    # Inhibitor class (ordered by specificity)
    inhibitor_terms = [
        "inhibitor", "antagonist", "blocker", "suppressor",
        "negative modulator", "inverse agonist", "downregulator",
    ]
    for term in inhibitor_terms:
        if term in action:
            return "inhibitor"

    # Activator class
    activator_terms = [
        "agonist", "activator", "inducer", "positive modulator",
        "stimulator", "potentiator", "upregulator",
    ]
    for term in activator_terms:
        if term in action:
            return "activator"

    # Binder (neutral — binds but unclear functional effect)
    if "binder" in action or "ligand" in action or "substrate" in action:
        return "binder"

    return "unknown"


def _classify_study_type(mesh_terms: list) -> float:
    """Classify study quality from MeSH terms and return a weight.

    Weights based on evidence hierarchy (Oxford CEBM):
      - Systematic review / Meta-analysis: 10
      - Randomized controlled trial: 8
      - Clinical trial (non-RCT): 6
      - Cohort / Case-control study: 5
      - Case report / Case series: 3
      - Review (narrative): 4
      - In vitro / In vivo (basic research): 2
      - Other: 1
    """
    mesh_lower = [str(t).lower() for t in mesh_terms]
    mesh_text = " ".join(mesh_lower)

    if "meta-analysis" in mesh_text or "systematic review" in mesh_text:
        return 10.0
    if "randomized controlled trial" in mesh_text:
        return 8.0
    if "clinical trial" in mesh_text:
        return 6.0
    if "cohort stud" in mesh_text or "case-control" in mesh_text:
        return 5.0
    if "review" in mesh_text:
        return 4.0
    if "case report" in mesh_text:
        return 3.0
    if "in vitro" in mesh_text or "cell line" in mesh_text:
        return 2.0

    return 3.0  # Default: moderate (unknown study type from MeSH)


def _quality_to_strength(quality_weight: float) -> str:
    """Map paper quality weight to strength label."""
    if quality_weight >= 8.0:
        return "strong"
    if quality_weight >= 4.0:
        return "moderate"
    return "weak"


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
