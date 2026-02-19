"""Drug Combination Synergy Prediction Engine.

Predicts which pairs of drugs may work synergistically against a cancer type
by analyzing pathway complementarity, target non-overlap, synthetic lethality
patterns, safety compatibility, and clinical precedent.

The core insight: cancer cells survive by maintaining multiple signaling
pathways. A single drug blocks one pathway, but the cancer can escape via
backup routes. A synergistic combination blocks complementary pathways,
cutting off the escape routes.

5 Synergy Scoring Dimensions (each 0-100):
  1. Pathway Complementarity  — Do the drugs cover different but connected pathways?
  2. Target Non-Overlap       — How distinct are their molecular targets?
  3. Synthetic Lethality      — Do the target genes form SL pairs (both needed for survival)?
  4. Safety Compatibility     — Non-overlapping toxicity profiles (different organ targets)
  5. Clinical Precedent       — Existing combination trial evidence

Synergy Classification:
  - Synergistic (score >= 60): Predicted to work better together than alone
  - Additive (40-59): Expected to have combined effect without synergy
  - Uncertain (20-39): Insufficient evidence to predict interaction
  - Antagonistic (<20): Risk of negative interaction
"""

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerMolecularProfile, CancerType
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import Drug, DrugTarget, TrialDrug
from app.models.gene_dependency import CombinationHypothesis, GeneDependency
from app.models.hypothesis import Hypothesis
from app.models.pathway import Pathway, PathwayTarget
from app.models.target import ProteinInteraction, Target

logger = logging.getLogger(__name__)


class CombinationEngine:
    """Predicts synergistic drug combinations for cancer types.

    Works by taking pairs of drugs that each individually have repurposing
    evidence (from the single-drug HypothesisEngine) and scoring whether
    combining them would be synergistic.
    """

    async def generate_combinations(
        self,
        cancer_type_id: int,
        db: AsyncSession,
        min_single_score: float = 30.0,
        max_pairs: int = 200,
    ) -> list[dict[str, Any]]:
        """Generate combination hypotheses for a cancer type.

        Steps:
          1. Get top single-drug hypotheses for this cancer
          2. Generate candidate pairs (avoiding redundancy)
          3. Score each pair across 5 synergy dimensions
          4. Store results and return top combinations
        """
        logger.info(
            "Generating combination hypotheses for cancer_type_id=%d",
            cancer_type_id,
        )

        # Step 1: Get top single-drug hypotheses
        hyp_result = await db.execute(
            select(Hypothesis)
            .where(
                Hypothesis.cancer_type_id == cancer_type_id,
                Hypothesis.composite_score >= min_single_score,
            )
            .order_by(Hypothesis.composite_score.desc())
            .limit(30)  # Top 30 drugs → up to 435 pairs
        )
        hypotheses = hyp_result.scalars().all()

        if len(hypotheses) < 2:
            logger.info("Need at least 2 hypotheses to generate combinations")
            return []

        # Step 2: Generate pairs (N choose 2)
        pairs = []
        for i, h_a in enumerate(hypotheses):
            for h_b in hypotheses[i + 1:]:
                pairs.append((h_a, h_b))
                if len(pairs) >= max_pairs:
                    break
            if len(pairs) >= max_pairs:
                break

        logger.info(
            "Scoring %d drug pairs for cancer_type_id=%d",
            len(pairs), cancer_type_id,
        )

        # Step 3: Score each pair
        results = []
        for idx, (h_a, h_b) in enumerate(pairs):
            try:
                combo = await self._score_combination(
                    h_a, h_b, cancer_type_id, db
                )
                if combo and combo.get("synergy_score", 0) > 0:
                    results.append(combo)

                if (idx + 1) % 50 == 0:
                    await db.commit()
                    logger.info(
                        "Scored %d/%d pairs for cancer_type_id=%d",
                        idx + 1, len(pairs), cancer_type_id,
                    )

            except Exception as e:
                logger.debug(
                    "Error scoring combination drug_a=%d drug_b=%d: %s",
                    h_a.drug_id, h_b.drug_id, e,
                )

        await db.commit()

        # Sort by synergy score
        results.sort(key=lambda x: x["synergy_score"], reverse=True)

        logger.info(
            "Generated %d combination hypotheses for cancer_type_id=%d",
            len(results), cancer_type_id,
        )
        return results

    # ==================================================================
    # Per-pair scoring
    # ==================================================================

    async def _score_combination(
        self,
        hyp_a: Hypothesis,
        hyp_b: Hypothesis,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any] | None:
        """Score a single drug pair for synergy potential."""
        drug_a_id = hyp_a.drug_id
        drug_b_id = hyp_b.drug_id

        # Get targets for both drugs
        targets_a = await self._get_drug_targets(drug_a_id, db)
        targets_b = await self._get_drug_targets(drug_b_id, db)

        if not targets_a or not targets_b:
            return None

        genes_a = {t["gene_symbol"] for t in targets_a}
        genes_b = {t["gene_symbol"] for t in targets_b}

        # Score 5 dimensions
        pathway_score = await self._score_pathway_complementarity(
            drug_a_id, drug_b_id, genes_a, genes_b, cancer_type_id, db
        )
        target_score = self._score_target_non_overlap(
            targets_a, targets_b, genes_a, genes_b
        )
        sl_score = await self._score_synthetic_lethality(
            genes_a, genes_b, cancer_type_id, db
        )
        safety_score = await self._score_safety_compatibility(
            drug_a_id, drug_b_id, db
        )
        clinical_score = await self._score_clinical_precedent(
            drug_a_id, drug_b_id, cancer_type_id, db
        )

        # Weighted composite
        weights = {
            "pathway_complementarity": 0.30,
            "target_non_overlap": 0.20,
            "synthetic_lethality": 0.25,
            "safety_compatibility": 0.10,
            "clinical_precedent": 0.15,
        }

        synergy = (
            weights["pathway_complementarity"] * pathway_score["score"]
            + weights["target_non_overlap"] * target_score["score"]
            + weights["synthetic_lethality"] * sl_score["score"]
            + weights["safety_compatibility"] * safety_score["score"]
            + weights["clinical_precedent"] * clinical_score["score"]
        )
        synergy = round(min(max(synergy, 0), 100), 1)

        # Classify
        if synergy >= 60:
            classification = "synergistic"
        elif synergy >= 40:
            classification = "additive"
        elif synergy >= 20:
            classification = "uncertain"
        else:
            classification = "antagonistic"

        # Get drug names
        drug_a_name = await self._get_drug_name(drug_a_id, db)
        drug_b_name = await self._get_drug_name(drug_b_id, db)
        cancer_name = await self._get_cancer_name(cancer_type_id, db)

        # Build rationale
        rationale = self._generate_rationale(
            drug_a_name, drug_b_name, cancer_name,
            pathway_score, target_score, sl_score,
            safety_score, clinical_score, synergy, classification,
        )

        # Store in database
        existing_result = await db.execute(
            select(CombinationHypothesis).where(
                CombinationHypothesis.drug_a_id == drug_a_id,
                CombinationHypothesis.drug_b_id == drug_b_id,
                CombinationHypothesis.cancer_type_id == cancer_type_id,
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            existing.pathway_complementarity_score = pathway_score["score"]
            existing.target_non_overlap_score = target_score["score"]
            existing.synthetic_lethality_score = sl_score["score"]
            existing.safety_compatibility_score = safety_score["score"]
            existing.clinical_precedent_score = clinical_score["score"]
            existing.synergy_score = synergy
            existing.synergy_classification = classification
            existing.rationale = rationale
            existing.shared_pathways = pathway_score.get("shared_pathways", [])
            existing.complementary_pathways = pathway_score.get("complementary_pathways", [])
            existing.details = {
                "pathway": pathway_score["details"],
                "target": target_score["details"],
                "synthetic_lethality": sl_score["details"],
                "safety": safety_score["details"],
                "clinical": clinical_score["details"],
            }
            combo_id = existing.id
        else:
            combo = CombinationHypothesis(
                drug_a_id=drug_a_id,
                drug_b_id=drug_b_id,
                cancer_type_id=cancer_type_id,
                hypothesis_a_id=hyp_a.id,
                hypothesis_b_id=hyp_b.id,
                pathway_complementarity_score=pathway_score["score"],
                target_non_overlap_score=target_score["score"],
                synthetic_lethality_score=sl_score["score"],
                safety_compatibility_score=safety_score["score"],
                clinical_precedent_score=clinical_score["score"],
                synergy_score=synergy,
                synergy_classification=classification,
                rationale=rationale,
                shared_pathways=pathway_score.get("shared_pathways", []),
                complementary_pathways=pathway_score.get("complementary_pathways", []),
                details={
                    "pathway": pathway_score["details"],
                    "target": target_score["details"],
                    "synthetic_lethality": sl_score["details"],
                    "safety": safety_score["details"],
                    "clinical": clinical_score["details"],
                },
            )
            db.add(combo)
            await db.flush()
            combo_id = combo.id

        return {
            "id": combo_id,
            "drug_a": {"id": drug_a_id, "name": drug_a_name},
            "drug_b": {"id": drug_b_id, "name": drug_b_name},
            "cancer_type_id": cancer_type_id,
            "synergy_score": synergy,
            "synergy_classification": classification,
            "dimension_scores": {
                "pathway_complementarity": pathway_score["score"],
                "target_non_overlap": target_score["score"],
                "synthetic_lethality": sl_score["score"],
                "safety_compatibility": safety_score["score"],
                "clinical_precedent": clinical_score["score"],
            },
            "rationale": rationale,
            "individual_scores": {
                "drug_a_composite": hyp_a.composite_score,
                "drug_b_composite": hyp_b.composite_score,
            },
        }

    # ==================================================================
    # Synergy Dimension Scorers
    # ==================================================================

    async def _score_pathway_complementarity(
        self,
        drug_a_id: int,
        drug_b_id: int,
        genes_a: set[str],
        genes_b: set[str],
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Score pathway complementarity between two drugs.

        Best case: drugs hit different arms of the same cancer-relevant pathway
        network, providing coverage that one drug alone can't achieve.

        Scoring:
          - Drug A pathways vs Drug B pathways overlap < 30%: complementary (good)
          - Both drugs' pathways overlap with cancer-altered pathways: relevant
          - Score = complementarity * cancer_relevance
        """
        # Get pathway IDs for each drug
        pw_a = await self._get_drug_pathway_ids(drug_a_id, db)
        pw_b = await self._get_drug_pathway_ids(drug_b_id, db)

        # Get cancer-altered pathways
        cancer_genes_result = await db.execute(
            select(CancerMolecularProfile.gene_symbol).where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
            ).distinct().limit(200)
        )
        cancer_genes = {row[0] for row in cancer_genes_result.all() if row[0]}

        cancer_pw_result = await db.execute(
            select(PathwayTarget.pathway_id).where(
                PathwayTarget.target_id.in_(
                    select(Target.id).where(Target.gene_symbol.in_(cancer_genes))
                )
            ).distinct()
        )
        cancer_pws = {row[0] for row in cancer_pw_result.all()}

        set_a = set(pw_a)
        set_b = set(pw_b)

        shared = set_a & set_b
        unique_a = set_a - set_b
        unique_b = set_b - set_a
        total_union = set_a | set_b

        # Complementarity: how much unique coverage does each drug add?
        if not total_union:
            return {"score": 0, "details": {"reason": "no_pathways"}, "shared_pathways": [], "complementary_pathways": []}

        overlap_ratio = len(shared) / len(total_union) if total_union else 0
        # Low overlap = good complementarity
        complementarity = 1.0 - overlap_ratio

        # Cancer relevance: how many of the covered pathways are cancer-relevant?
        cancer_relevant = total_union & cancer_pws
        relevance = len(cancer_relevant) / len(total_union) if total_union else 0

        # Bonus: unique pathways from each drug that are cancer-relevant
        unique_a_cancer = unique_a & cancer_pws
        unique_b_cancer = unique_b & cancer_pws
        unique_cancer_coverage = len(unique_a_cancer) + len(unique_b_cancer)
        coverage_bonus = min(unique_cancer_coverage * 5, 25)

        score = min(round(complementarity * relevance * 75 + coverage_bonus), 100)

        return {
            "score": score,
            "details": {
                "drug_a_pathways": len(set_a),
                "drug_b_pathways": len(set_b),
                "shared_pathways": len(shared),
                "unique_a": len(unique_a),
                "unique_b": len(unique_b),
                "cancer_relevant_pathways": len(cancer_relevant),
                "overlap_ratio": round(overlap_ratio, 3),
                "complementarity": round(complementarity, 3),
                "cancer_relevance": round(relevance, 3),
            },
            "shared_pathways": list(shared)[:10],
            "complementary_pathways": list(unique_a | unique_b)[:10],
        }

    def _score_target_non_overlap(
        self,
        targets_a: list[dict],
        targets_b: list[dict],
        genes_a: set[str],
        genes_b: set[str],
    ) -> dict[str, Any]:
        """Score target non-overlap.

        Drugs hitting completely different targets are more likely to be
        synergistic because they attack the cancer from different angles.
        Complete overlap suggests redundancy (additive at best).
        """
        shared_genes = genes_a & genes_b
        all_genes = genes_a | genes_b

        if not all_genes:
            return {"score": 0, "details": {"reason": "no_targets"}}

        overlap_ratio = len(shared_genes) / len(all_genes)
        non_overlap = 1.0 - overlap_ratio

        # Bonus for diverse action types
        actions_a = {t.get("action_type", "unknown") for t in targets_a}
        actions_b = {t.get("action_type", "unknown") for t in targets_b}
        action_diversity = len(actions_a | actions_b) / max(len(actions_a) + len(actions_b), 1)

        score = min(round(non_overlap * 80 + action_diversity * 20), 100)

        return {
            "score": score,
            "details": {
                "targets_a": len(genes_a),
                "targets_b": len(genes_b),
                "shared_targets": len(shared_genes),
                "shared_genes": list(shared_genes)[:10],
                "overlap_ratio": round(overlap_ratio, 3),
                "action_diversity": round(action_diversity, 3),
            },
        }

    async def _score_synthetic_lethality(
        self,
        genes_a: set[str],
        genes_b: set[str],
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Score synthetic lethality potential.

        Synthetic lethality: cell survives loss of gene A alone OR gene B alone,
        but dies when BOTH are lost. If Drug A inhibits gene A and Drug B inhibits
        gene B, the combination may be synthetically lethal.

        We approximate this using:
          1. DepMap co-dependency: both genes essential in the same lineage
          2. PPI proximity: targets interact in the same protein complex
          3. Pathway co-membership: targets in the same pathway but different arms
        """
        # Get cancer lineage for DepMap lookup
        cancer_result = await db.execute(
            select(CancerType.name, CancerType.tissue, CancerType.organ).where(
                CancerType.id == cancer_type_id
            )
        )
        cancer_row = cancer_result.first()
        if not cancer_row:
            return {"score": 0, "details": {"reason": "cancer_not_found"}}

        from app.services.evidence_scorer import _cancer_to_depmap_lineage
        lineage = _cancer_to_depmap_lineage(cancer_row.name, cancer_row.tissue, cancer_row.organ)

        # 1. Co-dependency: are both drug target sets essential?
        dep_result = await db.execute(
            select(GeneDependency).where(
                GeneDependency.gene_symbol.in_(list(genes_a | genes_b)),
                GeneDependency.lineage == lineage,
                GeneDependency.gene_effect < -0.5,  # Essential genes only
            )
        )
        essential_deps = {d.gene_symbol for d in dep_result.scalars().all()}

        essential_a = genes_a & essential_deps
        essential_b = genes_b & essential_deps

        co_dep_score = 0
        if essential_a and essential_b:
            # Both drugs target essential genes — strong SL signal
            co_dep_score = min(len(essential_a) + len(essential_b), 6) * 12
        elif essential_a or essential_b:
            co_dep_score = 15  # One drug targets essential genes

        # 2. PPI proximity between target sets
        ppi_score = 0
        if genes_a and genes_b:
            # Check if any targets from drug A interact with targets from drug B
            uniprots_a_result = await db.execute(
                select(Target.uniprot_id).where(Target.gene_symbol.in_(genes_a))
            )
            uniprots_a = {row[0] for row in uniprots_a_result.all() if row[0]}

            uniprots_b_result = await db.execute(
                select(Target.uniprot_id).where(Target.gene_symbol.in_(genes_b))
            )
            uniprots_b = {row[0] for row in uniprots_b_result.all() if row[0]}

            if uniprots_a and uniprots_b:
                interaction_count = 0
                for up_a in list(uniprots_a)[:10]:
                    int_result = await db.execute(
                        select(func.count(ProteinInteraction.id)).where(
                            (
                                (ProteinInteraction.protein_a_uniprot == up_a)
                                & (ProteinInteraction.protein_b_uniprot.in_(uniprots_b))
                            ) | (
                                (ProteinInteraction.protein_b_uniprot == up_a)
                                & (ProteinInteraction.protein_a_uniprot.in_(uniprots_b))
                            ),
                            ProteinInteraction.interaction_score >= 700,
                        )
                    )
                    interaction_count += int_result.scalar() or 0

                ppi_score = min(interaction_count * 10, 30)

        score = min(co_dep_score + ppi_score, 100)

        return {
            "score": score,
            "details": {
                "lineage": lineage,
                "essential_targets_a": list(essential_a),
                "essential_targets_b": list(essential_b),
                "co_dependency_score": co_dep_score,
                "ppi_proximity_score": ppi_score,
            },
        }

    async def _score_safety_compatibility(
        self,
        drug_a_id: int,
        drug_b_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Score safety compatibility of a drug pair.

        Both drugs should be individually safe (approved or investigational),
        and ideally target different organ systems to minimize overlapping
        toxicity.
        """
        drug_a_result = await db.execute(
            select(Drug.status, Drug.indication, Drug.pharmacodynamics).where(
                Drug.id == drug_a_id
            )
        )
        drug_a = drug_a_result.first()

        drug_b_result = await db.execute(
            select(Drug.status, Drug.indication, Drug.pharmacodynamics).where(
                Drug.id == drug_b_id
            )
        )
        drug_b = drug_b_result.first()

        if not drug_a or not drug_b:
            return {"score": 0, "details": {"reason": "drug_not_found"}}

        # Approval status compatibility
        status_map = {"approved": 40, "investigational": 25, "experimental": 10, "withdrawn": 0}
        status_a = status_map.get((drug_a.status or "").lower(), 15)
        status_b = status_map.get((drug_b.status or "").lower(), 15)
        safety_base = min((status_a + status_b) / 2, 50)

        # Indication diversity (different primary indications = lower overlap toxicity risk)
        ind_a = (drug_a.indication or "").lower()
        ind_b = (drug_b.indication or "").lower()
        if ind_a and ind_b and ind_a != ind_b:
            diversity_bonus = 25
        else:
            diversity_bonus = 10

        # Both approved = high confidence in safety profiles
        both_approved_bonus = 25 if (
            (drug_a.status or "").lower() == "approved"
            and (drug_b.status or "").lower() == "approved"
        ) else 0

        score = min(round(safety_base + diversity_bonus + both_approved_bonus), 100)

        return {
            "score": score,
            "details": {
                "drug_a_status": drug_a.status,
                "drug_b_status": drug_b.status,
                "safety_base": safety_base,
                "indication_diversity_bonus": diversity_bonus,
                "both_approved_bonus": both_approved_bonus,
            },
        }

    async def _score_clinical_precedent(
        self,
        drug_a_id: int,
        drug_b_id: int,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Score based on existing combination trial evidence.

        Check if these two drugs have been tested together in any clinical trial,
        especially for this cancer type.
        """
        # Get trials for each drug
        trials_a_result = await db.execute(
            select(ClinicalTrial.id, ClinicalTrial.title, ClinicalTrial.phase, ClinicalTrial.status)
            .join(TrialDrug, TrialDrug.trial_id == ClinicalTrial.id)
            .where(TrialDrug.drug_id == drug_a_id)
        )
        trials_a = {row.id: row for row in trials_a_result.all()}

        trials_b_result = await db.execute(
            select(TrialDrug.trial_id)
            .where(TrialDrug.drug_id == drug_b_id)
        )
        trial_b_ids = {row[0] for row in trials_b_result.all()}

        # Combination trials: trials that include BOTH drugs
        combo_trials = [
            trials_a[tid] for tid in (set(trials_a.keys()) & trial_b_ids)
        ]

        if not combo_trials:
            # No combination trials found — modest score for novelty
            return {
                "score": 20,
                "details": {
                    "combination_trials": 0,
                    "note": "No existing combination trials found — novel combination",
                },
            }

        # Score based on phase of combination trials
        phase_scores = {"Phase 4": 30, "Phase 3": 25, "Phase 2": 18, "Phase 1": 10}
        total = 0
        for trial in combo_trials:
            phase = trial.phase or ""
            base = 5
            for pk, pv in phase_scores.items():
                if pk.lower() in phase.lower():
                    base = pv
                    break
            status = (trial.status or "").lower()
            if status in ("completed", "active, not recruiting"):
                base = int(base * 1.2)
            total += base

        score = min(total + 20, 100)  # 20 base for having any combo trials

        return {
            "score": score,
            "details": {
                "combination_trials": len(combo_trials),
                "trial_phases": [t.phase for t in combo_trials],
            },
        }

    # ==================================================================
    # Helpers
    # ==================================================================

    async def _get_drug_targets(
        self, drug_id: int, db: AsyncSession
    ) -> list[dict]:
        result = await db.execute(
            select(Target.gene_symbol, Target.uniprot_id, DrugTarget.action_type)
            .join(DrugTarget, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug_id)
        )
        return [
            {"gene_symbol": row[0], "uniprot_id": row[1], "action_type": row[2]}
            for row in result.all()
        ]

    async def _get_drug_pathway_ids(
        self, drug_id: int, db: AsyncSession
    ) -> list[int]:
        result = await db.execute(
            select(PathwayTarget.pathway_id)
            .join(Target, PathwayTarget.target_id == Target.id)
            .join(DrugTarget, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug_id)
            .distinct()
        )
        return [row[0] for row in result.all()]

    async def _get_drug_name(self, drug_id: int, db: AsyncSession) -> str:
        result = await db.execute(
            select(Drug.name).where(Drug.id == drug_id)
        )
        return result.scalar_one_or_none() or f"Drug#{drug_id}"

    async def _get_cancer_name(self, cancer_type_id: int, db: AsyncSession) -> str:
        result = await db.execute(
            select(CancerType.name).where(CancerType.id == cancer_type_id)
        )
        return result.scalar_one_or_none() or f"Cancer#{cancer_type_id}"

    def _generate_rationale(
        self,
        drug_a_name: str,
        drug_b_name: str,
        cancer_name: str,
        pathway_score: dict,
        target_score: dict,
        sl_score: dict,
        safety_score: dict,
        clinical_score: dict,
        synergy: float,
        classification: str,
    ) -> str:
        """Generate human-readable combination rationale."""
        parts = [
            f"Combination of {drug_a_name} + {drug_b_name} for {cancer_name} "
            f"is predicted to be {classification} (synergy score: {synergy}/100)."
        ]

        pd = pathway_score["details"]
        if pd.get("complementarity", 0) > 0.5:
            parts.append(
                f"Strong pathway complementarity: drugs cover {pd.get('drug_a_pathways', 0)} "
                f"and {pd.get('drug_b_pathways', 0)} pathways respectively with only "
                f"{pd.get('overlap_ratio', 0):.0%} overlap."
            )

        td = target_score["details"]
        shared = td.get("shared_targets", 0)
        if shared == 0:
            parts.append("Drugs target completely non-overlapping molecular targets.")
        elif shared <= 2:
            parts.append(f"Minimal target overlap ({shared} shared targets).")

        sld = sl_score["details"]
        if sld.get("essential_targets_a") and sld.get("essential_targets_b"):
            parts.append(
                f"Synthetic lethality signal: both drugs target essential genes "
                f"({', '.join(sld['essential_targets_a'][:3])} and "
                f"{', '.join(sld['essential_targets_b'][:3])}) per DepMap CRISPR data."
            )

        cd = clinical_score["details"]
        if cd.get("combination_trials", 0) > 0:
            parts.append(
                f"Clinical precedent: {cd['combination_trials']} existing combination "
                f"trial(s) found."
            )

        return " ".join(parts)
