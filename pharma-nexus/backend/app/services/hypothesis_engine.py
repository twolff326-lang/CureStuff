"""Core drug repurposing hypothesis discovery engine.

The HEART of Pharma Nexus. Cross-references drug targets, cancer molecular
profiles, pathways, literature, and expression data to generate scored
repurposing hypotheses.

6 Discovery Strategies:
  1. direct_target      — Drug targets a gene mutated/altered in the cancer
  2. pathway_mediated   — Drug targets a gene in a pathway dysregulated in the cancer
  3. interaction_network — Drug target interacts with cancer-altered proteins (PPI)
  4. expression_driven  — Drug action matches expression changes in the cancer
  5. literature_seeded  — Literature mentions drug + cancer in repurposing context
  6. analog_discovery   — Similar drugs (by mechanism embedding) target related cancers

Pipeline:
  1. Identify candidates (drug-cancer pairs) via strategies
  2. Score all 6 evidence dimensions via EvidenceScorer
  3. Compute weighted composite score via ScoringConfig
  4. Assemble evidence records
  5. Store hypotheses in database (upsert on drug_id + cancer_type_id)
"""

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerMolecularProfile, CancerType
from app.models.clinical_trial import ClinicalTrial
from app.models.drug import Drug, DrugTarget, LiteratureDrug, TrialDrug
from app.models.hypothesis import Hypothesis, HypothesisEvidence
from app.models.literature import Literature, LiteratureCancer
from app.models.mutation import Mutation
from app.models.pathway import PathwayTarget
from app.models.target import Target
from app.services.evidence_scorer import EvidenceScorer
from app.services.pathway_analyzer import PathwayAnalyzer
from app.services.scoring_config import ScoringConfig

logger = logging.getLogger(__name__)


class HypothesisEngine:
    """Core engine for generating drug repurposing hypotheses.

    Orchestrates candidate identification, multi-dimensional scoring,
    evidence assembly, and hypothesis storage.
    """

    def __init__(self):
        self.scorer = EvidenceScorer()
        self.config = ScoringConfig()

    # ==================================================================
    # Main Entry Points
    # ==================================================================

    async def generate_for_cancer(
        self,
        cancer_type_id: int,
        db: AsyncSession,
        min_score: float = 15.0,
    ) -> list[dict[str, Any]]:
        """Generate hypotheses for all drugs against a specific cancer type.

        This is the primary batch generation method. It:
        1. Identifies candidate drugs via all 6 strategies
        2. Scores each candidate
        3. Stores hypotheses above min_score threshold
        """
        logger.info("Generating hypotheses for cancer_type_id=%d", cancer_type_id)

        # Identify candidates from all strategies
        candidates = await self._identify_candidates_for_cancer(
            cancer_type_id, db
        )
        logger.info(
            "Found %d candidate drug-cancer pairs for cancer_type_id=%d",
            len(candidates), cancer_type_id,
        )

        # Get active scoring weights
        weights = await self.config.get_active_weights(db)

        # Score and store each candidate
        results = []
        for i, candidate in enumerate(candidates):
            drug_id = candidate["drug_id"]
            try:
                hypothesis = await self._score_and_store_hypothesis(
                    drug_id=drug_id,
                    cancer_type_id=cancer_type_id,
                    strategies=candidate["strategies"],
                    weights=weights,
                    db=db,
                )
                if hypothesis and hypothesis.get("composite_score", 0) >= min_score:
                    results.append(hypothesis)

                if (i + 1) % 50 == 0:
                    await db.commit()
                    logger.info(
                        "Scored %d/%d candidates for cancer_type_id=%d",
                        i + 1, len(candidates), cancer_type_id,
                    )

            except Exception as e:
                logger.debug(
                    "Failed scoring drug=%d cancer=%d: %s",
                    drug_id, cancer_type_id, e,
                )

        await db.commit()
        logger.info(
            "Generated %d hypotheses for cancer_type_id=%d (threshold=%.1f)",
            len(results), cancer_type_id, min_score,
        )
        return results

    async def generate_for_drug(
        self,
        drug_id: int,
        db: AsyncSession,
        min_score: float = 15.0,
    ) -> list[dict[str, Any]]:
        """Generate hypotheses for a specific drug against all cancer types."""
        logger.info("Generating hypotheses for drug_id=%d", drug_id)

        # Get all cancer types
        cancer_result = await db.execute(select(CancerType.id))
        cancer_ids = [row[0] for row in cancer_result.all()]

        weights = await self.config.get_active_weights(db)
        results = []

        for cid in cancer_ids:
            try:
                # Check if drug has any relevance to this cancer
                strategies = await self._identify_strategies_for_pair(
                    drug_id, cid, db
                )
                if not strategies:
                    continue

                hypothesis = await self._score_and_store_hypothesis(
                    drug_id=drug_id,
                    cancer_type_id=cid,
                    strategies=strategies,
                    weights=weights,
                    db=db,
                )
                if hypothesis and hypothesis.get("composite_score", 0) >= min_score:
                    results.append(hypothesis)

            except Exception as e:
                logger.debug(
                    "Failed scoring drug=%d cancer=%d: %s",
                    drug_id, cid, e,
                )

        await db.commit()
        logger.info(
            "Generated %d hypotheses for drug_id=%d", len(results), drug_id
        )
        return results

    async def rescore_all(
        self,
        db: AsyncSession,
        weights: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Rescore all existing hypotheses with current or provided weights.

        Used when scoring weights change to update all composite scores.
        """
        if weights is None:
            weights = await self.config.get_active_weights(db)

        result = await db.execute(
            select(Hypothesis).order_by(Hypothesis.id)
        )
        all_hypotheses = result.scalars().all()
        total = len(all_hypotheses)

        rescored = 0
        for hyp in all_hypotheses:
            try:
                # Recompute composite from stored dimension scores
                dimension_scores = {
                    "pathway_overlap": {"score": hyp.pathway_overlap_score or 0},
                    "expression_correlation": {"score": hyp.expression_correlation_score or 0},
                    "literature_support": {"score": hyp.literature_support_score or 0},
                    "clinical_evidence": {"score": hyp.clinical_evidence_score or 0},
                    "safety": {"score": hyp.safety_score or 0},
                    "novelty": {"score": hyp.novelty_score or 0},
                    "causal_dependency": {"score": hyp.causal_dependency_score or 0},
                    "gnn_link": {"score": hyp.gnn_link_score or 0},
                }
                new_composite = self.config.compute_composite_score(
                    dimension_scores, weights
                )
                hyp.composite_score = new_composite
                hyp.evidence_strength = self.config.determine_evidence_strength(
                    new_composite
                )
                rescored += 1

                if rescored % 100 == 0:
                    await db.flush()

            except Exception as e:
                logger.debug("Failed rescoring hypothesis %d: %s", hyp.id, e)

        await db.commit()
        logger.info("Rescored %d/%d hypotheses", rescored, total)
        return {"rescored": rescored, "total": total}

    # ==================================================================
    # Candidate Identification
    # ==================================================================

    async def _identify_candidates_for_cancer(
        self,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Identify candidate drugs for a cancer type using all 6 strategies.

        Returns deduplicated list of {drug_id, drug_name, strategies: [...]}.
        """
        candidates: dict[int, dict] = {}  # drug_id -> info

        # Strategy 1: Direct target
        direct = await self._strategy_direct_target(cancer_type_id, db)
        for d in direct:
            did = d["drug_id"]
            if did not in candidates:
                candidates[did] = {"drug_id": did, "drug_name": d["drug_name"], "strategies": []}
            candidates[did]["strategies"].append("direct_target")

        # Strategy 2: Pathway mediated
        pathway = await self._strategy_pathway_mediated(cancer_type_id, db)
        for d in pathway:
            did = d["drug_id"]
            if did not in candidates:
                candidates[did] = {"drug_id": did, "drug_name": d["drug_name"], "strategies": []}
            if "pathway_mediated" not in candidates[did]["strategies"]:
                candidates[did]["strategies"].append("pathway_mediated")

        # Strategy 3: Interaction network
        interaction = await self._strategy_interaction_network(cancer_type_id, db)
        for d in interaction:
            did = d["drug_id"]
            if did not in candidates:
                candidates[did] = {"drug_id": did, "drug_name": d["drug_name"], "strategies": []}
            if "interaction_network" not in candidates[did]["strategies"]:
                candidates[did]["strategies"].append("interaction_network")

        # Strategy 4: Expression driven
        expression = await self._strategy_expression_driven(cancer_type_id, db)
        for d in expression:
            did = d["drug_id"]
            if did not in candidates:
                candidates[did] = {"drug_id": did, "drug_name": d["drug_name"], "strategies": []}
            if "expression_driven" not in candidates[did]["strategies"]:
                candidates[did]["strategies"].append("expression_driven")

        # Strategy 5: Literature seeded
        literature = await self._strategy_literature_seeded(cancer_type_id, db)
        for d in literature:
            did = d["drug_id"]
            if did not in candidates:
                candidates[did] = {"drug_id": did, "drug_name": d["drug_name"], "strategies": []}
            if "literature_seeded" not in candidates[did]["strategies"]:
                candidates[did]["strategies"].append("literature_seeded")

        # Strategy 6: Analog discovery
        analog = await self._strategy_analog_discovery(cancer_type_id, db)
        for d in analog:
            did = d["drug_id"]
            if did not in candidates:
                candidates[did] = {"drug_id": did, "drug_name": d["drug_name"], "strategies": []}
            if "analog_discovery" not in candidates[did]["strategies"]:
                candidates[did]["strategies"].append("analog_discovery")

        return list(candidates.values())

    async def _identify_strategies_for_pair(
        self,
        drug_id: int,
        cancer_type_id: int,
        db: AsyncSession,
    ) -> list[str]:
        """Quick check which strategies apply to a specific drug-cancer pair."""
        strategies = []

        # Direct target check
        target_genes_result = await db.execute(
            select(Target.gene_symbol)
            .join(DrugTarget, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug_id)
        )
        drug_genes = set(row[0] for row in target_genes_result.all() if row[0])

        if drug_genes:
            # Check mutations
            mut_result = await db.execute(
                select(func.count(Mutation.id)).where(
                    Mutation.cancer_type_id == cancer_type_id,
                    Mutation.gene_symbol.in_(drug_genes),
                )
            )
            if (mut_result.scalar() or 0) > 0:
                strategies.append("direct_target")

            # Check expression profiles
            expr_result = await db.execute(
                select(func.count(CancerMolecularProfile.id)).where(
                    CancerMolecularProfile.cancer_type_id == cancer_type_id,
                    CancerMolecularProfile.gene_symbol.in_(drug_genes),
                )
            )
            if (expr_result.scalar() or 0) > 0:
                strategies.append("expression_driven")

        # Pathway check (lightweight)
        analyzer = PathwayAnalyzer(db)
        overlap = await analyzer.get_pathway_overlap(drug_id, cancer_type_id)
        if overlap.get("shared_count", 0) > 0:
            strategies.append("pathway_mediated")

        # Literature check
        lit_result = await db.execute(
            select(func.count(Literature.id))
            .join(LiteratureDrug, LiteratureDrug.literature_id == Literature.id)
            .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
            .where(
                LiteratureDrug.drug_id == drug_id,
                LiteratureCancer.cancer_type_id == cancer_type_id,
            )
        )
        if (lit_result.scalar() or 0) > 0:
            strategies.append("literature_seeded")

        return strategies

    # ------------------------------------------------------------------
    # Individual Strategies
    # ------------------------------------------------------------------

    async def _strategy_direct_target(
        self, cancer_type_id: int, db: AsyncSession
    ) -> list[dict]:
        """Strategy 1: Drug directly targets a gene mutated/altered in the cancer."""
        # Get cancer-altered genes
        cancer_genes: set[str] = set()

        mut_result = await db.execute(
            select(Mutation.gene_symbol).where(
                Mutation.cancer_type_id == cancer_type_id
            ).distinct()
        )
        cancer_genes.update(row[0] for row in mut_result.all() if row[0])

        profile_result = await db.execute(
            select(CancerMolecularProfile.gene_symbol).where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
                CancerMolecularProfile.alteration_type.in_(
                    ["overexpression", "underexpression", "mutation"]
                ),
            ).distinct()
        )
        cancer_genes.update(row[0] for row in profile_result.all() if row[0])

        if not cancer_genes:
            return []

        # Find drugs targeting these genes
        result = await db.execute(
            select(Drug.id, Drug.name)
            .join(DrugTarget, DrugTarget.drug_id == Drug.id)
            .join(Target, DrugTarget.target_id == Target.id)
            .where(Target.gene_symbol.in_(cancer_genes))
            .distinct()
        )

        return [{"drug_id": row[0], "drug_name": row[1]} for row in result.all()]

    async def _strategy_pathway_mediated(
        self, cancer_type_id: int, db: AsyncSession
    ) -> list[dict]:
        """Strategy 2: Drug targets a gene in a pathway dysregulated in the cancer."""
        analyzer = PathwayAnalyzer(db)
        nodes = await analyzer.get_druggable_pathway_nodes(cancer_type_id)

        seen: set[int] = set()
        results = []
        for node in nodes:
            did = node["drug_id"]
            if did not in seen:
                seen.add(did)
                results.append({
                    "drug_id": did,
                    "drug_name": node["drug_name"],
                })
        return results

    async def _strategy_interaction_network(
        self, cancer_type_id: int, db: AsyncSession
    ) -> list[dict]:
        """Strategy 3: Drug target interacts with cancer-altered proteins via PPI.

        Finds drugs whose targets have high-confidence STRING interactions
        with genes mutated/altered in the cancer.
        """
        # Get cancer-altered gene uniprot IDs
        cancer_genes_result = await db.execute(
            select(Mutation.gene_symbol).where(
                Mutation.cancer_type_id == cancer_type_id
            ).distinct().limit(50)
        )
        cancer_genes = [row[0] for row in cancer_genes_result.all() if row[0]]

        if not cancer_genes:
            return []

        # Get uniprot IDs for cancer genes
        from app.models.target import ProteinInteraction

        uniprot_result = await db.execute(
            select(Target.uniprot_id).where(
                Target.gene_symbol.in_(cancer_genes)
            )
        )
        cancer_uniprots = set(row[0] for row in uniprot_result.all() if row[0])

        if not cancer_uniprots:
            return []

        # Find proteins interacting with cancer genes (high confidence)
        interacting_uniprots: set[str] = set()
        for uniprot in list(cancer_uniprots)[:30]:
            int_result = await db.execute(
                select(
                    ProteinInteraction.protein_a_uniprot,
                    ProteinInteraction.protein_b_uniprot,
                ).where(
                    (ProteinInteraction.protein_a_uniprot == uniprot)
                    | (ProteinInteraction.protein_b_uniprot == uniprot),
                    ProteinInteraction.interaction_score >= 700,
                ).limit(20)
            )
            for row in int_result.all():
                neighbor = row[1] if row[0] == uniprot else row[0]
                interacting_uniprots.add(neighbor)

        # Remove cancer genes themselves
        interacting_uniprots -= cancer_uniprots

        if not interacting_uniprots:
            return []

        # Find drugs targeting these interacting proteins
        result = await db.execute(
            select(Drug.id, Drug.name)
            .join(DrugTarget, DrugTarget.drug_id == Drug.id)
            .join(Target, DrugTarget.target_id == Target.id)
            .where(Target.uniprot_id.in_(interacting_uniprots))
            .distinct()
        )

        return [{"drug_id": row[0], "drug_name": row[1]} for row in result.all()]

    async def _strategy_expression_driven(
        self, cancer_type_id: int, db: AsyncSession
    ) -> list[dict]:
        """Strategy 4: Drug action aligns with expression changes.

        Finds drugs where:
        - Inhibitors target overexpressed genes
        - Agonists target underexpressed genes
        """
        # Get overexpressed genes
        over_result = await db.execute(
            select(CancerMolecularProfile.gene_symbol).where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
                CancerMolecularProfile.alteration_type == "overexpression",
                CancerMolecularProfile.expression_zscore >= 2.0,
            ).distinct().limit(100)
        )
        overexpressed = set(row[0] for row in over_result.all() if row[0])

        # Get underexpressed genes
        under_result = await db.execute(
            select(CancerMolecularProfile.gene_symbol).where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
                CancerMolecularProfile.alteration_type == "underexpression",
                CancerMolecularProfile.expression_zscore <= -2.0,
            ).distinct().limit(100)
        )
        underexpressed = set(row[0] for row in under_result.all() if row[0])

        candidates = []

        # Inhibitors for overexpressed genes
        if overexpressed:
            result = await db.execute(
                select(Drug.id, Drug.name)
                .join(DrugTarget, DrugTarget.drug_id == Drug.id)
                .join(Target, DrugTarget.target_id == Target.id)
                .where(
                    Target.gene_symbol.in_(overexpressed),
                    DrugTarget.action_type.ilike("%inhibit%"),
                )
                .distinct()
            )
            candidates.extend(
                {"drug_id": row[0], "drug_name": row[1]}
                for row in result.all()
            )

        # Agonists for underexpressed genes
        if underexpressed:
            result = await db.execute(
                select(Drug.id, Drug.name)
                .join(DrugTarget, DrugTarget.drug_id == Drug.id)
                .join(Target, DrugTarget.target_id == Target.id)
                .where(
                    Target.gene_symbol.in_(underexpressed),
                    DrugTarget.action_type.ilike("%agonist%"),
                )
                .distinct()
            )
            candidates.extend(
                {"drug_id": row[0], "drug_name": row[1]}
                for row in result.all()
            )

        # Deduplicate
        seen: set[int] = set()
        unique = []
        for c in candidates:
            if c["drug_id"] not in seen:
                seen.add(c["drug_id"])
                unique.append(c)
        return unique

    async def _strategy_literature_seeded(
        self, cancer_type_id: int, db: AsyncSession
    ) -> list[dict]:
        """Strategy 5: Literature mentions drug + cancer in repurposing context."""
        result = await db.execute(
            select(Drug.id, Drug.name)
            .join(LiteratureDrug, LiteratureDrug.drug_id == Drug.id)
            .join(Literature, LiteratureDrug.literature_id == Literature.id)
            .join(LiteratureCancer, LiteratureCancer.literature_id == Literature.id)
            .where(LiteratureCancer.cancer_type_id == cancer_type_id)
            .distinct()
        )

        return [{"drug_id": row[0], "drug_name": row[1]} for row in result.all()]

    async def _strategy_analog_discovery(
        self, cancer_type_id: int, db: AsyncSession
    ) -> list[dict]:
        """Strategy 6: Similar drugs (by mechanism embedding) to known cancer drugs.

        Finds drugs not yet associated with this cancer but whose mechanism
        embeddings are close to drugs that ARE associated.
        """
        # Get drugs already associated with this cancer (via hypotheses or trials)
        known_drug_ids: set[int] = set()

        hyp_result = await db.execute(
            select(Hypothesis.drug_id).where(
                Hypothesis.cancer_type_id == cancer_type_id,
                Hypothesis.composite_score >= 50,
            )
        )
        known_drug_ids.update(row[0] for row in hyp_result.all())

        trial_result = await db.execute(
            select(TrialDrug.drug_id)
            .join(ClinicalTrial, TrialDrug.trial_id == ClinicalTrial.id)
        )
        known_drug_ids.update(row[0] for row in trial_result.all())

        if not known_drug_ids:
            return []

        # Get mechanism embeddings of known drugs
        known_drugs_result = await db.execute(
            select(Drug.id, Drug.mechanism_embedding).where(
                Drug.id.in_(list(known_drug_ids)[:20]),
                Drug.mechanism_embedding.isnot(None),
            )
        )
        known_embeddings = known_drugs_result.all()

        if not known_embeddings:
            return []

        # Find similar drugs by embedding distance
        analog_drugs: set[int] = set()
        for known_id, embedding in known_embeddings[:5]:
            if embedding is None:
                continue
            try:
                result = await db.execute(
                    select(Drug.id, Drug.name)
                    .where(
                        Drug.id.notin_(known_drug_ids),
                        Drug.mechanism_embedding.isnot(None),
                    )
                    .order_by(Drug.mechanism_embedding.cosine_distance(embedding))
                    .limit(10)
                )
                for row in result.all():
                    if row[0] not in analog_drugs:
                        analog_drugs.add(row[0])
            except Exception:
                pass

        if not analog_drugs:
            return []

        # Get drug info for analogs
        result = await db.execute(
            select(Drug.id, Drug.name).where(Drug.id.in_(analog_drugs))
        )
        return [{"drug_id": row[0], "drug_name": row[1]} for row in result.all()]

    # ==================================================================
    # Scoring & Storage
    # ==================================================================

    async def _score_and_store_hypothesis(
        self,
        drug_id: int,
        cancer_type_id: int,
        strategies: list[str],
        weights: dict[str, float],
        db: AsyncSession,
    ) -> dict[str, Any] | None:
        """Score a drug-cancer pair and store/update the hypothesis."""
        # Get pathway data (shared across multiple scorers)
        analyzer = PathwayAnalyzer(db)
        pathway_data = await analyzer.get_pathway_overlap(drug_id, cancer_type_id)

        # Score all 7 dimensions
        dimension_scores = await self.scorer.score_all_dimensions(
            drug_id, cancer_type_id, db, pathway_data=pathway_data
        )

        # Compute composite score
        composite = self.config.compute_composite_score(dimension_scores, weights)
        evidence_strength = self.config.determine_evidence_strength(composite)

        # Generate title and summary
        drug_result = await db.execute(
            select(Drug.name).where(Drug.id == drug_id)
        )
        drug_name = drug_result.scalar_one_or_none() or f"Drug#{drug_id}"

        cancer_result = await db.execute(
            select(CancerType.name).where(CancerType.id == cancer_type_id)
        )
        cancer_name = cancer_result.scalar_one_or_none() or f"Cancer#{cancer_type_id}"

        title = f"{drug_name} for {cancer_name}"
        summary = self._generate_summary(
            drug_name, cancer_name, strategies, dimension_scores, composite
        )

        # Upsert hypothesis
        existing_result = await db.execute(
            select(Hypothesis).where(
                Hypothesis.drug_id == drug_id,
                Hypothesis.cancer_type_id == cancer_type_id,
            )
        )
        hypothesis = existing_result.scalar_one_or_none()

        if hypothesis:
            # Update existing
            hypothesis.title = title
            hypothesis.summary = summary
            hypothesis.composite_score = composite
            hypothesis.evidence_strength = evidence_strength
            hypothesis.pathway_overlap_score = dimension_scores["pathway_overlap"]["score"]
            hypothesis.expression_correlation_score = dimension_scores["expression_correlation"]["score"]
            hypothesis.literature_support_score = dimension_scores["literature_support"]["score"]
            hypothesis.clinical_evidence_score = dimension_scores["clinical_evidence"]["score"]
            hypothesis.safety_score = dimension_scores["safety"]["score"]
            hypothesis.novelty_score = dimension_scores["novelty"]["score"]
            hypothesis.causal_dependency_score = dimension_scores["causal_dependency"]["score"]
            hypothesis.gnn_link_score = dimension_scores.get("gnn_link", {}).get("score", 0)
        else:
            # Create new
            hypothesis = Hypothesis(
                drug_id=drug_id,
                cancer_type_id=cancer_type_id,
                title=title,
                summary=summary,
                composite_score=composite,
                evidence_strength=evidence_strength,
                pathway_overlap_score=dimension_scores["pathway_overlap"]["score"],
                expression_correlation_score=dimension_scores["expression_correlation"]["score"],
                literature_support_score=dimension_scores["literature_support"]["score"],
                clinical_evidence_score=dimension_scores["clinical_evidence"]["score"],
                safety_score=dimension_scores["safety"]["score"],
                novelty_score=dimension_scores["novelty"]["score"],
                causal_dependency_score=dimension_scores["causal_dependency"]["score"],
                gnn_link_score=dimension_scores.get("gnn_link", {}).get("score", 0),
                status="generated",
            )
            db.add(hypothesis)
            await db.flush()

        # Store evidence records
        await self._store_evidence(hypothesis.id, dimension_scores, db)

        return {
            "hypothesis_id": hypothesis.id,
            "drug_id": drug_id,
            "cancer_type_id": cancer_type_id,
            "title": title,
            "composite_score": composite,
            "evidence_strength": evidence_strength,
            "strategies": strategies,
            "dimension_scores": {
                k: v["score"] for k, v in dimension_scores.items()
            },
        }

    async def _store_evidence(
        self,
        hypothesis_id: int,
        dimension_scores: dict[str, dict[str, Any]],
        db: AsyncSession,
    ) -> None:
        """Store evidence records for a hypothesis.

        Clears existing evidence and inserts fresh records from scoring.
        """
        # Delete existing evidence for this hypothesis
        existing = await db.execute(
            select(HypothesisEvidence).where(
                HypothesisEvidence.hypothesis_id == hypothesis_id
            )
        )
        for ev in existing.scalars().all():
            await db.delete(ev)
        await db.flush()

        # Insert new evidence from all dimensions
        for dim_name, dim_result in dimension_scores.items():
            for ev_data in dim_result.get("evidence", []):
                evidence = HypothesisEvidence(
                    hypothesis_id=hypothesis_id,
                    evidence_type=ev_data.get("evidence_type", dim_name),
                    source_type=ev_data.get("source_type"),
                    source_id=ev_data.get("source_id"),
                    description=ev_data.get("description"),
                    strength=ev_data.get("strength", "weak"),
                    confidence=ev_data.get("confidence", 0.0),
                    raw_data=ev_data.get("raw_data"),
                )
                db.add(evidence)

    def _generate_summary(
        self,
        drug_name: str,
        cancer_name: str,
        strategies: list[str],
        dimension_scores: dict[str, dict[str, Any]],
        composite: float,
    ) -> str:
        """Generate a human-readable summary of the hypothesis."""
        parts = [f"{drug_name} shows potential for repurposing against {cancer_name}."]

        # Highlight discovery strategies
        strategy_names = {
            "direct_target": "direct target overlap",
            "pathway_mediated": "pathway-mediated connection",
            "interaction_network": "protein interaction network proximity",
            "expression_driven": "expression-action compatibility",
            "literature_seeded": "literature evidence",
            "analog_discovery": "structural analog similarity",
        }
        strategy_text = ", ".join(
            strategy_names.get(s, s) for s in strategies
        )
        parts.append(f"Identified via: {strategy_text}.")

        # Highlight top scoring dimensions
        scored = [
            (name, data["score"])
            for name, data in dimension_scores.items()
            if data["score"] > 0
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        if scored:
            top = scored[0]
            dim_labels = {
                "pathway_overlap": "pathway overlap",
                "expression_correlation": "expression correlation",
                "literature_support": "literature support",
                "clinical_evidence": "clinical evidence",
                "safety": "safety profile",
                "novelty": "novelty",
                "causal_dependency": "causal dependency (DepMap)",
            }
            parts.append(
                f"Strongest signal: {dim_labels.get(top[0], top[0])} "
                f"(score: {top[1]}/100)."
            )

        parts.append(
            f"Composite score: {composite}/100 "
            f"({self.config.determine_evidence_strength(composite)})."
        )

        return " ".join(parts)
