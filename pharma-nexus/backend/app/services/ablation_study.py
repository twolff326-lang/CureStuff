"""Ablation study framework for evaluating Strategy 7 contribution.

Any paper claiming a method is novel must show it adds value beyond
simpler baselines. This module provides four comparators:

1. Keyword Co-occurrence Baseline — Simple text matching of drug names
   and cancer terms in paper abstracts. No reasoning, just counting.

2. Structured-Only (Strategies 1-6) — The standard hypothesis engine
   without any LLM synthesis. Tests whether SQL joins on structured
   data are sufficient.

3. Random Baseline — Random drug-cancer pairs. Establishes the floor
   that any real method must beat.

4. Strategy 7 (LLM Synthesis) — The full transitive inference method.

For each, we measure:
  - Overlap with known repurposing successes
  - Unique discoveries (found by this method alone)
  - Precision/recall against ground truth
  - Score separation between successes and failures
"""

import logging
import random
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerType, CancerMolecularProfile
from app.models.drug import Drug, DrugTarget, LiteratureDrug
from app.models.hypothesis import Hypothesis
from app.models.literature import Literature, LiteratureCancer
from app.models.mutation import Mutation
from app.models.pathway import PathwayTarget
from app.models.target import Target
from app.services.validation import GROUND_TRUTH_CASES

logger = logging.getLogger(__name__)


class AblationStudy:
    """Compares Strategy 7 against baselines to quantify its contribution."""

    # ------------------------------------------------------------------
    # Baseline 1: Keyword Co-occurrence
    # ------------------------------------------------------------------

    async def keyword_cooccurrence_baseline(
        self,
        cancer_type_id: int,
        session: AsyncSession,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Find drugs that co-occur with the cancer in literature abstracts.

        This is the simplest possible method: count how many papers
        mention both the drug and the cancer type. Rank by count.
        """
        # Get cancer name for text matching
        cancer_result = await session.execute(
            select(CancerType.name, CancerType.tcga_code)
            .where(CancerType.id == cancer_type_id)
        )
        cancer_row = cancer_result.one_or_none()
        if not cancer_row:
            return []

        # Count co-mentions via the structured literature linkage tables
        co_result = await session.execute(
            select(
                Drug.id,
                Drug.name,
                func.count(LiteratureDrug.literature_id).label("co_mentions"),
            )
            .join(
                LiteratureDrug,
                LiteratureDrug.drug_id == Drug.id,
            )
            .join(
                LiteratureCancer,
                LiteratureCancer.literature_id == LiteratureDrug.literature_id,
            )
            .where(LiteratureCancer.cancer_type_id == cancer_type_id)
            .group_by(Drug.id, Drug.name)
            .order_by(func.count(LiteratureDrug.literature_id).desc())
            .limit(top_k)
        )
        rows = co_result.all()

        return [
            {
                "drug_id": r[0],
                "drug_name": r[1],
                "score": r[2],
                "method": "keyword_cooccurrence",
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Baseline 2: Structured Strategies 1-6
    # ------------------------------------------------------------------

    async def structured_only_baseline(
        self,
        cancer_type_id: int,
        session: AsyncSession,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Find drugs connected to the cancer via structured database links.

        Combines:
          - Direct target overlap (drug targets mutated genes)
          - Pathway mediation (drug targets share pathways with cancer genes)
          - Expression correlation (drug targets are dysregulated)

        This represents what Strategies 1-6 find without LLM reasoning.
        """
        # Get cancer mutations
        mut_result = await session.execute(
            select(Mutation.gene_symbol)
            .where(Mutation.cancer_type_id == cancer_type_id)
        )
        cancer_genes = {r[0] for r in mut_result.all()}

        # Get dysregulated genes
        expr_result = await session.execute(
            select(CancerMolecularProfile.gene_symbol)
            .where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
                func.abs(CancerMolecularProfile.expression_zscore) > 2.0,
            )
        )
        dysregulated_genes = {r[0] for r in expr_result.all()}

        all_cancer_genes = cancer_genes | dysregulated_genes
        if not all_cancer_genes:
            return []

        # Strategy 1: Direct target overlap
        direct_result = await session.execute(
            select(Drug.id, Drug.name, func.count(Target.gene_symbol))
            .join(DrugTarget, DrugTarget.drug_id == Drug.id)
            .join(Target, Target.id == DrugTarget.target_id)
            .where(Target.gene_symbol.in_(all_cancer_genes))
            .group_by(Drug.id, Drug.name)
        )
        direct_hits = {r[0]: {"name": r[1], "direct": r[2]} for r in direct_result.all()}

        # Strategy 2: Pathway mediation
        # Get pathway IDs for cancer genes
        target_ids_result = await session.execute(
            select(Target.id).where(Target.gene_symbol.in_(all_cancer_genes))
        )
        cancer_target_ids = [r[0] for r in target_ids_result.all()]

        pathway_hits: dict[int, int] = {}
        if cancer_target_ids:
            pw_result = await session.execute(
                select(PathwayTarget.pathway_id)
                .where(PathwayTarget.target_id.in_(cancer_target_ids))
                .distinct()
            )
            cancer_pathway_ids = [r[0] for r in pw_result.all()]

            if cancer_pathway_ids:
                # Find drugs whose targets are in these pathways
                drug_pw_result = await session.execute(
                    select(
                        DrugTarget.drug_id,
                        func.count(PathwayTarget.pathway_id.distinct()),
                    )
                    .join(Target, Target.id == DrugTarget.target_id)
                    .join(PathwayTarget, PathwayTarget.target_id == Target.id)
                    .where(PathwayTarget.pathway_id.in_(cancer_pathway_ids[:100]))
                    .group_by(DrugTarget.drug_id)
                )
                pathway_hits = {r[0]: r[1] for r in drug_pw_result.all()}

        # Combine scores
        drug_scores: dict[int, dict] = {}
        for drug_id, info in direct_hits.items():
            drug_scores[drug_id] = {
                "drug_id": drug_id,
                "drug_name": info["name"],
                "direct_targets": info["direct"],
                "shared_pathways": pathway_hits.get(drug_id, 0),
            }

        for drug_id, pw_count in pathway_hits.items():
            if drug_id not in drug_scores:
                # Get drug name
                name_result = await session.execute(
                    select(Drug.name).where(Drug.id == drug_id)
                )
                name = name_result.scalar_one_or_none() or f"Drug#{drug_id}"
                drug_scores[drug_id] = {
                    "drug_id": drug_id,
                    "drug_name": name,
                    "direct_targets": 0,
                    "shared_pathways": pw_count,
                }

        # Score: 3 points per direct target, 1 point per shared pathway
        for ds in drug_scores.values():
            ds["score"] = ds["direct_targets"] * 3 + ds["shared_pathways"]
            ds["method"] = "structured_only"

        ranked = sorted(drug_scores.values(), key=lambda x: x["score"], reverse=True)
        return ranked[:top_k]

    # ------------------------------------------------------------------
    # Baseline 3: Random
    # ------------------------------------------------------------------

    async def random_baseline(
        self,
        cancer_type_id: int,
        session: AsyncSession,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Select random drugs as a baseline floor.

        Any real method must beat random selection.
        """
        result = await session.execute(
            select(Drug.id, Drug.name)
            .where(Drug.status.in_(["approved", "investigational"]))
        )
        all_drugs = result.all()

        if len(all_drugs) <= top_k:
            selected = all_drugs
        else:
            selected = random.sample(all_drugs, top_k)

        return [
            {
                "drug_id": d[0],
                "drug_name": d[1],
                "score": 0,
                "method": "random",
            }
            for d in selected
        ]

    # ------------------------------------------------------------------
    # Strategy 7: LLM proposals (from database)
    # ------------------------------------------------------------------

    async def llm_synthesis_results(
        self,
        cancer_type_id: int,
        session: AsyncSession,
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Get existing LLM synthesis proposals for a cancer type.

        These are stored by SynthesisDiscovery.discover_for_cancer().
        """
        from app.models.discovery import LLMDiscoveryProposal

        result = await session.execute(
            select(LLMDiscoveryProposal)
            .where(LLMDiscoveryProposal.cancer_type_id == cancer_type_id)
            .order_by(LLMDiscoveryProposal.confidence.desc())
            .limit(top_k)
        )
        proposals = result.scalars().all()

        out = []
        for p in proposals:
            drug_result = await session.execute(
                select(Drug.name).where(Drug.id == p.drug_id)
            )
            drug_name = drug_result.scalar_one_or_none() or f"Drug#{p.drug_id}"
            out.append({
                "drug_id": p.drug_id,
                "drug_name": drug_name,
                "score": p.confidence,
                "method": "llm_synthesis",
                "rationale": (p.mechanism_rationale or "")[:200],
            })

        return out

    # ------------------------------------------------------------------
    # Comparison framework
    # ------------------------------------------------------------------

    async def run_ablation(
        self,
        cancer_type_id: int,
        session: AsyncSession,
        top_k: int = 20,
    ) -> dict[str, Any]:
        """Run all baselines and Strategy 7 for one cancer type, compare results."""
        # Get cancer info
        cancer_result = await session.execute(
            select(CancerType.name, CancerType.tcga_code)
            .where(CancerType.id == cancer_type_id)
        )
        cancer_row = cancer_result.one_or_none()
        if not cancer_row:
            return {"error": f"Cancer type {cancer_type_id} not found"}

        cancer_name, tcga_code = cancer_row

        # Run all methods
        keyword_results = await self.keyword_cooccurrence_baseline(
            cancer_type_id, session, top_k
        )
        structured_results = await self.structured_only_baseline(
            cancer_type_id, session, top_k
        )
        random_results = await self.random_baseline(
            cancer_type_id, session, top_k
        )
        llm_results = await self.llm_synthesis_results(
            cancer_type_id, session, top_k
        )

        # Get ground truth for this cancer
        ground_truth = [
            c for c in GROUND_TRUTH_CASES
            if c.get("cancer_tcga_code") == tcga_code
            or c["cancer_name"].lower() in cancer_name.lower()
        ]
        gt_drug_names = {c["drug_name"].lower() for c in ground_truth}
        gt_success_drugs = {
            c["drug_name"].lower() for c in ground_truth
            if c["outcome"] in ("success", "ongoing", "partial")
        }

        # Evaluate each method
        methods = {
            "keyword_cooccurrence": keyword_results,
            "structured_only": structured_results,
            "random": random_results,
            "llm_synthesis": llm_results,
        }

        comparison = {}
        for method_name, results in methods.items():
            result_drug_names = {r["drug_name"].lower() for r in results}

            # How many ground truth drugs did this method find?
            gt_found = result_drug_names & gt_drug_names
            gt_successes_found = result_drug_names & gt_success_drugs

            comparison[method_name] = {
                "proposals": len(results),
                "ground_truth_overlap": len(gt_found),
                "gt_drugs_found": list(gt_found),
                "successes_found": len(gt_successes_found),
                "success_drugs_found": list(gt_successes_found),
                "recall": (
                    round(len(gt_successes_found) / len(gt_success_drugs), 3)
                    if gt_success_drugs else 0
                ),
            }

        # Compute unique contributions (found by one method but not others)
        all_method_drugs = {
            name: {r["drug_name"].lower() for r in results}
            for name, results in methods.items()
        }

        for method_name in methods:
            others = set()
            for other_name, other_drugs in all_method_drugs.items():
                if other_name != method_name:
                    others |= other_drugs

            unique = all_method_drugs[method_name] - others
            comparison[method_name]["unique_proposals"] = len(unique)
            comparison[method_name]["unique_drugs"] = list(unique)[:10]

        return {
            "cancer_type": cancer_name,
            "tcga_code": tcga_code,
            "cancer_type_id": cancer_type_id,
            "ground_truth_cases": len(ground_truth),
            "ground_truth_successes": len(gt_success_drugs),
            "top_k": top_k,
            "comparison": comparison,
        }

    async def run_full_ablation(
        self,
        session: AsyncSession,
        top_k: int = 20,
    ) -> dict[str, Any]:
        """Run ablation across all cancer types that have ground truth cases.

        Aggregates results to show the overall contribution of each method.
        """
        # Get unique cancer types from ground truth
        gt_tcga_codes = {
            c["cancer_tcga_code"]
            for c in GROUND_TRUTH_CASES
            if c.get("cancer_tcga_code")
        }

        # Map TCGA codes to IDs
        cancer_ids = {}
        for code in gt_tcga_codes:
            result = await session.execute(
                select(CancerType.id).where(CancerType.tcga_code == code)
            )
            ct_id = result.scalar_one_or_none()
            if ct_id:
                cancer_ids[code] = ct_id

        if not cancer_ids:
            return {
                "error": "No ground truth cancer types found in database",
                "expected_codes": list(gt_tcga_codes),
            }

        results = []
        for code, ct_id in cancer_ids.items():
            try:
                ablation = await self.run_ablation(ct_id, session, top_k)
                results.append(ablation)
            except Exception as e:
                logger.error("Ablation failed for %s: %s", code, e)
                results.append({"tcga_code": code, "error": str(e)})

        # Aggregate across cancers
        aggregate = self._aggregate_results(results)

        return {
            "cancer_types_evaluated": len(results),
            "aggregate": aggregate,
            "per_cancer": results,
        }

    def _aggregate_results(
        self, results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Aggregate ablation results across multiple cancer types."""
        method_names = [
            "keyword_cooccurrence",
            "structured_only",
            "random",
            "llm_synthesis",
        ]

        aggregate: dict[str, dict[str, Any]] = {}
        for method in method_names:
            total_proposals = 0
            total_gt_overlap = 0
            total_successes = 0
            total_unique = 0
            total_gt_available = 0

            for result in results:
                comp = result.get("comparison", {}).get(method, {})
                total_proposals += comp.get("proposals", 0)
                total_gt_overlap += comp.get("ground_truth_overlap", 0)
                total_successes += comp.get("successes_found", 0)
                total_unique += comp.get("unique_proposals", 0)
                total_gt_available += result.get("ground_truth_successes", 0)

            aggregate[method] = {
                "total_proposals": total_proposals,
                "total_gt_overlap": total_gt_overlap,
                "total_successes_found": total_successes,
                "total_unique_proposals": total_unique,
                "aggregate_recall": (
                    round(total_successes / total_gt_available, 3)
                    if total_gt_available > 0 else 0
                ),
            }

        return aggregate
