"""Gene expression analysis engine for drug-cancer repurposing hypotheses.

Provides quantitative scoring of drug-cancer pairs based on expression data:
  - Differential expression (tumor vs normal)
  - Drug target expression scoring (action-expression compatibility)
  - Pathway activity scoring (simplified GSEA)
  - Co-expression analysis (Pearson correlation networks)
  - Synthetic lethality potential assessment

Each analysis produces 0-100 scores that feed into the hypothesis engine's
composite scoring (Prompt 8).
"""

import logging
from datetime import datetime, timezone
from typing import Any

import numpy as np
from scipy import stats
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from statsmodels.stats.multitest import multipletests

from app.models.cancer_type import CancerMolecularProfile
from app.models.drug import Drug, DrugTarget
from app.models.evidence import GeneExpression
from app.models.expression_cache import ExpressionScoreCache
from app.models.pathway import Pathway, PathwayTarget
from app.models.target import ProteinInteraction, Target
from app.services.expression_preprocessor import ExpressionPreprocessor

logger = logging.getLogger(__name__)


class ExpressionAnalyzer:
    """Analyzes gene expression data to score drug-cancer repurposing hypotheses.

    Works with data from cBioPortal/TCGA stored in:
    - cancer_molecular_profiles (summary: median expression, z-scores, frequencies)
    - gene_expression (sample-level values for top genes)
    """

    def __init__(self) -> None:
        self.preprocessor = ExpressionPreprocessor()

    # ==================================================================
    # Differential Expression Analysis
    # ==================================================================

    async def compute_differential_expression(
        self, cancer_type_id: int, db_session: AsyncSession
    ) -> list[dict[str, Any]]:
        """Compute differential expression for all genes in a cancer type.

        For each gene in the gene_expression table for this cancer type:
        1. Separate tumor samples (is_tumor=True) from normal (is_tumor=False)
        2. Compute mean/median expression, log2 fold change, Mann-Whitney U test
        3. Apply Benjamini-Hochberg FDR correction across all genes
        4. Store results in cancer_molecular_profiles
        """
        # 1. Batch-fetch grouped expression data
        gene_data = await self.preprocessor.get_grouped_expression(
            cancer_type_id, db_session
        )

        if not gene_data:
            logger.warning(
                "No expression data for cancer_type_id=%d", cancer_type_id
            )
            return []

        # 2. Compute statistics for each gene
        de_results: list[dict[str, Any]] = []
        p_values: list[float] = []
        genes_with_pval: list[str] = []

        for gene, data in gene_data.items():
            tumor_vals = np.array(data["tumor"], dtype=np.float64)

            if len(tumor_vals) < 3:
                continue

            normal_vals = np.array(data["normal"], dtype=np.float64)
            tumor_median = float(np.median(tumor_vals))
            tumor_mean = float(np.mean(tumor_vals))
            tumor_std = float(np.std(tumor_vals))

            result: dict[str, Any] = {
                "gene_symbol": gene,
                "tumor_median": round(tumor_median, 4),
                "tumor_mean": round(tumor_mean, 4),
                "tumor_std": round(tumor_std, 4),
                "n_tumor": len(tumor_vals),
            }

            if len(normal_vals) >= 3:
                normal_median = float(np.median(normal_vals))
                result["normal_median"] = round(normal_median, 4)
                result["n_normal"] = len(normal_vals)

                # Log2 fold change (add pseudocount to avoid log(0))
                pseudo = 0.01
                result["log2_fold_change"] = round(
                    float(np.log2((tumor_median + pseudo) / (normal_median + pseudo))),
                    4,
                )

                # Mann-Whitney U test (non-parametric)
                try:
                    _, p_val = stats.mannwhitneyu(
                        tumor_vals, normal_vals, alternative="two-sided"
                    )
                    result["p_value"] = float(p_val)
                    p_values.append(p_val)
                    genes_with_pval.append(gene)
                except ValueError:
                    result["p_value"] = 1.0
                    p_values.append(1.0)
                    genes_with_pval.append(gene)

                result["direction"] = (
                    "up" if result["log2_fold_change"] > 0 else "down"
                )
            else:
                # No normal samples — use z-score from cBioPortal data
                result["normal_median"] = None
                result["log2_fold_change"] = None
                result["p_value"] = None
                result["direction"] = "unknown"
                result["n_normal"] = len(normal_vals)

            de_results.append(result)

        # 3. FDR correction on all p-values
        if p_values:
            reject, fdr_pvals, _, _ = multipletests(
                p_values, method="fdr_bh", alpha=0.05
            )
            pval_map = {
                gene: (float(fdr_pvals[i]), bool(reject[i]))
                for i, gene in enumerate(genes_with_pval)
            }
            for r in de_results:
                if r["gene_symbol"] in pval_map:
                    r["fdr_adjusted_p"] = pval_map[r["gene_symbol"]][0]
                    r["significant"] = pval_map[r["gene_symbol"]][1]

        # 4. Store results in cancer_molecular_profiles
        await self._store_de_results(cancer_type_id, de_results, db_session)

        logger.info(
            "Computed DE for cancer_type_id=%d: %d genes, %d significant",
            cancer_type_id,
            len(de_results),
            sum(1 for r in de_results if r.get("significant", False)),
        )
        return de_results

    async def _store_de_results(
        self,
        cancer_type_id: int,
        de_results: list[dict],
        db_session: AsyncSession,
    ) -> None:
        """Store differential expression results in cancer_molecular_profiles."""
        for r in de_results:
            if r.get("direction") == "unknown":
                continue

            alteration_type = (
                "overexpression" if r["direction"] == "up" else "underexpression"
            )

            # Check for existing record
            existing = await db_session.execute(
                select(CancerMolecularProfile).where(
                    CancerMolecularProfile.cancer_type_id == cancer_type_id,
                    CancerMolecularProfile.gene_symbol == r["gene_symbol"],
                    CancerMolecularProfile.alteration_type == alteration_type,
                )
            )
            profile = existing.scalar_one_or_none()

            if profile:
                profile.median_expression = r.get("tumor_median")
                profile.expression_zscore = r.get("log2_fold_change")
                if r.get("fdr_adjusted_p") is not None and r["fdr_adjusted_p"] < 0.05:
                    freq = r.get("n_tumor", 0) / max(
                        r.get("n_tumor", 0) + r.get("n_normal", 0), 1
                    ) * 100
                    profile.frequency_percent = round(freq, 1)
            else:
                db_session.add(
                    CancerMolecularProfile(
                        cancer_type_id=cancer_type_id,
                        gene_symbol=r["gene_symbol"],
                        alteration_type=alteration_type,
                        frequency_percent=None,
                        median_expression=r.get("tumor_median"),
                        expression_zscore=r.get("log2_fold_change"),
                        source="differential_expression",
                    )
                )

        await db_session.flush()

    # ==================================================================
    # Drug Target Expression Scoring
    # ==================================================================

    async def score_target_expression(
        self, drug_id: int, cancer_type_id: int, db_session: AsyncSession
    ) -> dict[str, Any]:
        """Score how well a drug's mechanism matches expression in a cancer.

        This is one of the 6 sub-scores in the hypothesis composite score.
        """
        # Check cache first
        cached = await self._get_cached_score(
            "drug_expression", drug_id, cancer_type_id, db_session
        )
        if cached:
            return cached

        # 1. Get all targets for this drug with action types and affinities
        drug_targets = await self._get_drug_targets(drug_id, db_session)

        if not drug_targets:
            return {
                "score": 0,
                "target_scores": [],
                "weighted_score": 0,
                "explanation": "No known drug targets found",
            }

        # 2. For each target, get expression profile in this cancer
        target_scores: list[dict[str, Any]] = []
        for dt in drug_targets:
            expr = await self._get_target_expression(
                dt["gene_symbol"], cancer_type_id, db_session
            )

            # 3. Compute compatibility score
            compatibility = self._compute_action_expression_compatibility(
                action_type=dt["action_type"],
                expression_zscore=expr.get("expression_zscore", 0),
                frequency_overexpressed=expr.get("overexpression_frequency", 0),
                frequency_underexpressed=expr.get("underexpression_frequency", 0),
            )

            # 4. Weight by binding affinity
            affinity_weight = self._affinity_to_weight(dt["binding_affinity_nm"])

            target_scores.append(
                {
                    "gene_symbol": dt["gene_symbol"],
                    "uniprot_id": dt["uniprot_id"],
                    "action_type": dt["action_type"],
                    "binding_affinity_nm": dt["binding_affinity_nm"],
                    "expression_zscore": expr.get("expression_zscore", 0),
                    "expression_direction": expr.get("direction", "unknown"),
                    "frequency_overexpressed": expr.get(
                        "overexpression_frequency", 0
                    ),
                    "compatibility": compatibility["label"],
                    "target_score": compatibility["score"],
                    "weight": affinity_weight,
                    "explanation": compatibility["explanation"],
                }
            )

        # 5. Compute weighted composite
        total_weight = sum(t["weight"] for t in target_scores)
        if total_weight > 0:
            weighted_score = (
                sum(t["target_score"] * t["weight"] for t in target_scores)
                / total_weight
            )
        else:
            weighted_score = 0

        result = {
            "score": min(100, round(weighted_score)),
            "target_scores": target_scores,
            "weighted_score": round(weighted_score, 1),
            "explanation": self._generate_expression_explanation(target_scores),
        }

        # Cache the result
        await self._set_cached_score(
            "drug_expression", drug_id, cancer_type_id, result, db_session
        )
        return result

    async def _get_drug_targets(
        self, drug_id: int, db_session: AsyncSession
    ) -> list[dict[str, Any]]:
        """Get all targets for a drug with action types and affinities."""
        result = await db_session.execute(
            select(
                DrugTarget.action_type,
                DrugTarget.binding_affinity_nm,
                Target.gene_symbol,
                Target.uniprot_id,
            )
            .join(Target, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug_id)
        )
        return [
            {
                "action_type": row.action_type,
                "binding_affinity_nm": row.binding_affinity_nm,
                "gene_symbol": row.gene_symbol,
                "uniprot_id": row.uniprot_id,
            }
            for row in result
        ]

    async def _get_target_expression(
        self, gene_symbol: str, cancer_type_id: int, db_session: AsyncSession
    ) -> dict[str, Any]:
        """Get expression profile for a gene in a cancer type."""
        profiles = await db_session.execute(
            select(CancerMolecularProfile).where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
                CancerMolecularProfile.gene_symbol == gene_symbol,
                CancerMolecularProfile.alteration_type.in_(
                    ["overexpression", "underexpression"]
                ),
            )
        )
        rows = profiles.scalars().all()

        expr: dict[str, Any] = {
            "expression_zscore": 0,
            "direction": "unknown",
            "overexpression_frequency": 0,
            "underexpression_frequency": 0,
        }

        for row in rows:
            if row.alteration_type == "overexpression":
                expr["overexpression_frequency"] = row.frequency_percent or 0
                if (row.expression_zscore or 0) > 0:
                    expr["expression_zscore"] = row.expression_zscore or 0
                    expr["direction"] = "overexpressed"
            elif row.alteration_type == "underexpression":
                expr["underexpression_frequency"] = row.frequency_percent or 0
                if (row.expression_zscore or 0) < 0:
                    expr["expression_zscore"] = row.expression_zscore or 0
                    expr["direction"] = "underexpressed"

        # If no overexpression/underexpression records, check for z-score
        if expr["direction"] == "unknown" and rows:
            best = max(rows, key=lambda r: abs(r.expression_zscore or 0))
            if best.expression_zscore:
                expr["expression_zscore"] = best.expression_zscore
                expr["direction"] = (
                    "overexpressed"
                    if best.expression_zscore > 0
                    else "underexpressed"
                )

        return expr

    def _compute_action_expression_compatibility(
        self,
        action_type: str,
        expression_zscore: float,
        frequency_overexpressed: float,
        frequency_underexpressed: float,
    ) -> dict[str, Any]:
        """Compute how compatible a drug's action is with target expression.

        An inhibitor is most useful when its target is overexpressed.
        An agonist is most useful when its target is underexpressed.
        """
        z = expression_zscore or 0
        freq_over = frequency_overexpressed or 0
        freq_under = frequency_underexpressed or 0

        inhibitory_actions = {
            "inhibitor",
            "antagonist",
            "blocker",
            "negative modulator",
            "suppressor",
        }
        activating_actions = {
            "agonist",
            "activator",
            "positive modulator",
            "inducer",
            "stimulator",
        }

        action_lower = (action_type or "").lower()
        is_inhibitory = any(a in action_lower for a in inhibitory_actions)
        is_activating = any(a in action_lower for a in activating_actions)

        if is_inhibitory:
            if z >= 2.0 and freq_over >= 20:
                score = min(100, 60 + z * 5 + freq_over * 0.5)
                label = "high"
                explanation = (
                    f"Target is overexpressed (z={z:.1f}) in {freq_over:.0f}% "
                    f"of tumors — ideal for inhibition"
                )
            elif z >= 1.0 or freq_over >= 10:
                score = min(70, 30 + z * 10 + freq_over * 0.8)
                label = "moderate"
                explanation = (
                    f"Target is mildly overexpressed (z={z:.1f}) — "
                    f"inhibition may have moderate effect"
                )
            elif z <= -1.0 or freq_under >= 20:
                score = max(0, 10 - abs(z) * 3)
                label = "poor"
                explanation = (
                    f"Target is underexpressed (z={z:.1f}) — "
                    f"little target for the inhibitor to hit"
                )
            else:
                score = 25
                label = "low"
                explanation = (
                    f"Target expression is near normal (z={z:.1f}) — "
                    f"inhibition impact uncertain"
                )
        elif is_activating:
            if z <= -2.0 and freq_under >= 20:
                score = min(100, 60 + abs(z) * 5 + freq_under * 0.5)
                label = "high"
                explanation = (
                    f"Target is underexpressed (z={z:.1f}) in {freq_under:.0f}% "
                    f"of tumors — activation could restore function"
                )
            elif z <= -1.0 or freq_under >= 10:
                score = min(70, 30 + abs(z) * 10 + freq_under * 0.8)
                label = "moderate"
                explanation = (
                    f"Target is mildly underexpressed — activation may help"
                )
            elif z >= 2.0:
                score = max(0, 10 - z * 3)
                label = "poor"
                explanation = (
                    f"Target is already overexpressed (z={z:.1f}) — "
                    f"further activation may be harmful"
                )
            else:
                score = 25
                label = "low"
                explanation = (
                    f"Target expression is near normal — "
                    f"activation impact uncertain"
                )
        else:
            # Unknown action type — use absolute expression deviation as proxy
            deviation = abs(z)
            score = min(50, deviation * 15) if deviation > 1.0 else 15
            label = "unknown"
            explanation = (
                f"Drug action type '{action_type}' is ambiguous — "
                f"scoring on expression deviation"
            )

        return {
            "score": min(100, max(0, round(score))),
            "label": label,
            "explanation": explanation,
        }

    def _affinity_to_weight(self, affinity_nm: float | None) -> float:
        """Convert binding affinity (nM) to a 0-1 weight.

        Lower affinity (in nM) = stronger binding = higher weight.
        """
        if affinity_nm is None:
            return 0.2
        if affinity_nm < 1:
            return 1.0
        elif affinity_nm < 10:
            return 0.9
        elif affinity_nm < 100:
            return 0.7
        elif affinity_nm < 1000:
            return 0.5
        elif affinity_nm < 10000:
            return 0.3
        else:
            return 0.2

    def _generate_expression_explanation(
        self, target_scores: list[dict]
    ) -> str:
        """Generate a human-readable explanation of expression scoring."""
        if not target_scores:
            return "No drug targets with expression data"

        favorable = sum(
            1
            for t in target_scores
            if t["compatibility"] in ("high", "moderate")
        )
        total = len(target_scores)

        if favorable == 0:
            return f"None of the {total} drug targets are favorably expressed in this cancer type"
        elif favorable == total:
            return f"All {total} drug targets are favorably expressed in this cancer type"
        else:
            return (
                f"{favorable} of {total} drug targets are favorably expressed "
                f"in this cancer type"
            )

    # ==================================================================
    # Pathway Activity Scoring
    # ==================================================================

    async def compute_pathway_activity(
        self, cancer_type_id: int, pathway_id: int, db_session: AsyncSession
    ) -> dict[str, Any]:
        """Compute how ACTIVE a pathway is in a specific cancer type.

        A simplified Gene Set Enrichment Analysis (GSEA):
        1. Get all genes in the pathway
        2. For each gene, get its expression z-score in this cancer
        3. Compute pathway-level statistics
        """
        # Check cache
        cached = await self._get_cached_score(
            "pathway_activity", pathway_id, cancer_type_id, db_session
        )
        if cached:
            return cached

        # 1. Get pathway genes
        pathway = await db_session.get(Pathway, pathway_id)
        if not pathway:
            return {
                "pathway_id": pathway_id,
                "activity_score": 0,
                "direction": "not_found",
            }

        pathway_genes = pathway.genes or []
        if not pathway_genes:
            return {
                "pathway_id": pathway_id,
                "pathway_name": pathway.name,
                "activity_score": 0,
                "direction": "no_genes",
                "n_genes_in_pathway": 0,
                "n_genes_with_data": 0,
            }

        # 2. Batch query cancer_molecular_profiles for these genes
        profiles_result = await db_session.execute(
            select(CancerMolecularProfile).where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
                CancerMolecularProfile.gene_symbol.in_(pathway_genes),
                CancerMolecularProfile.alteration_type.in_(
                    ["overexpression", "underexpression"]
                ),
            )
        )
        profiles = profiles_result.scalars().all()

        # 3. Compute statistics
        gene_zscores: dict[str, float] = {}
        for p in profiles:
            z = p.expression_zscore or 0
            gene = p.gene_symbol
            # Keep the record with larger absolute z-score for each gene
            if gene not in gene_zscores or abs(z) > abs(gene_zscores[gene]):
                gene_zscores[gene] = z

        zscores = list(gene_zscores.values())
        n_with_data = len(zscores)

        if n_with_data == 0:
            result = {
                "pathway_id": pathway_id,
                "pathway_name": pathway.name,
                "cancer_type_id": cancer_type_id,
                "activity_score": 0,
                "direction": "no_data",
                "n_genes_in_pathway": len(pathway_genes),
                "n_genes_with_data": 0,
            }
            return result

        zscores_arr = np.array(zscores)
        mean_z = float(np.mean(zscores_arr))
        overexpressed = [g for g, z in gene_zscores.items() if z > 1.5]
        underexpressed = [g for g, z in gene_zscores.items() if z < -1.5]
        pct_over = len(overexpressed) / n_with_data * 100
        pct_under = len(underexpressed) / n_with_data * 100

        # 4. Compute activity score (0-100)
        if mean_z > 0:
            activity_score = min(100, 50 + mean_z * 10 + pct_over * 0.5)
            direction = "activated"
        elif mean_z < -0.5:
            activity_score = min(100, 50 + abs(mean_z) * 10 + pct_under * 0.5)
            direction = "suppressed"
        else:
            activity_score = 30
            direction = "neutral"

        # 5. Find druggable overexpressed genes
        druggable = await self._find_druggable_genes(overexpressed, db_session)

        # Build top gene lists with z-scores
        top_over = sorted(
            [
                {"gene": g, "zscore": round(gene_zscores[g], 2)}
                for g in overexpressed
            ],
            key=lambda x: x["zscore"],
            reverse=True,
        )[:10]
        top_under = sorted(
            [
                {"gene": g, "zscore": round(gene_zscores[g], 2)}
                for g in underexpressed
            ],
            key=lambda x: x["zscore"],
        )[:10]

        result = {
            "pathway_id": pathway_id,
            "pathway_name": pathway.name,
            "cancer_type_id": cancer_type_id,
            "activity_score": round(activity_score),
            "mean_zscore": round(mean_z, 2),
            "pct_overexpressed": round(pct_over, 1),
            "pct_underexpressed": round(pct_under, 1),
            "direction": direction,
            "n_genes_in_pathway": len(pathway_genes),
            "n_genes_with_data": n_with_data,
            "top_overexpressed": top_over,
            "top_underexpressed": top_under,
            "druggable_overexpressed": druggable,
        }

        # Cache the result
        await self._set_cached_score(
            "pathway_activity", pathway_id, cancer_type_id, result, db_session
        )
        return result

    async def score_drug_pathway_activity(
        self, drug_id: int, cancer_type_id: int, db_session: AsyncSession
    ) -> dict[str, Any]:
        """Score how active the drug's target pathways are in this cancer.

        An inhibitor of an INACTIVE pathway is useless.
        An inhibitor of a HYPERACTIVE pathway could be therapeutic.
        """
        # 1. Get all pathways the drug's targets participate in
        drug_targets = await self._get_drug_targets(drug_id, db_session)
        if not drug_targets:
            return {
                "score": 0,
                "pathway_activities": [],
                "explanation": "No known drug targets",
            }

        target_genes = {dt["gene_symbol"] for dt in drug_targets}

        # Get target IDs for pathway lookup
        target_ids_result = await db_session.execute(
            select(Target.id, Target.gene_symbol).where(
                Target.gene_symbol.in_(target_genes)
            )
        )
        target_id_map = {row.id: row.gene_symbol for row in target_ids_result}

        # Get pathways that contain these targets
        pt_result = await db_session.execute(
            select(PathwayTarget.pathway_id, PathwayTarget.target_id).where(
                PathwayTarget.target_id.in_(list(target_id_map.keys()))
            )
        )
        pathway_targets_map: dict[int, list[str]] = {}
        for row in pt_result:
            pid = row.pathway_id
            gene = target_id_map.get(row.target_id, "")
            if pid not in pathway_targets_map:
                pathway_targets_map[pid] = []
            if gene:
                pathway_targets_map[pid].append(gene)

        if not pathway_targets_map:
            return {
                "score": 0,
                "pathway_activities": [],
                "explanation": "Drug targets not found in any pathways",
            }

        # 2. Compute activity for each relevant pathway
        pathway_activities: list[dict[str, Any]] = []
        for pathway_id, targets_in_pathway in pathway_targets_map.items():
            activity = await self.compute_pathway_activity(
                cancer_type_id, pathway_id, db_session
            )

            if activity.get("direction") == "not_found":
                continue

            relevance = self._pathway_drug_relevance(
                activity, targets_in_pathway
            )
            pathway_activities.append(
                {
                    "pathway_name": activity.get("pathway_name", ""),
                    "pathway_id": pathway_id,
                    "activity_score": activity.get("activity_score", 0),
                    "direction": activity.get("direction", "unknown"),
                    "drug_targets_in_pathway": targets_in_pathway,
                    "relevance": relevance,
                }
            )

        # 3. Composite score: weighted average of pathway activities
        if pathway_activities:
            total_weight = sum(
                len(pa["drug_targets_in_pathway"]) for pa in pathway_activities
            )
            weighted_score = (
                sum(
                    pa["activity_score"] * len(pa["drug_targets_in_pathway"])
                    for pa in pathway_activities
                )
                / total_weight
                if total_weight > 0
                else 0
            )
        else:
            weighted_score = 0

        # Sort by activity score
        pathway_activities.sort(
            key=lambda x: x["activity_score"], reverse=True
        )

        activated = sum(
            1 for pa in pathway_activities if pa["direction"] == "activated"
        )
        total = len(pathway_activities)
        if activated > total / 2:
            explanation = (
                f"Drug targets pathways that are predominantly activated "
                f"in this cancer ({activated}/{total} activated)"
            )
        elif activated == 0 and total > 0:
            explanation = "Drug targets pathways that are not activated in this cancer"
        else:
            explanation = (
                f"Mixed pathway activity: {activated}/{total} pathways activated"
            )

        return {
            "score": min(100, round(weighted_score)),
            "pathway_activities": pathway_activities[:20],
            "explanation": explanation,
        }

    def _pathway_drug_relevance(
        self, activity: dict, targets_in_pathway: list[str]
    ) -> str:
        """Generate relevance description for a pathway-drug pair."""
        direction = activity.get("direction", "unknown")
        n_targets = len(targets_in_pathway)
        target_str = ", ".join(targets_in_pathway[:3])
        if n_targets > 3:
            target_str += f" +{n_targets - 3} more"

        if direction == "activated":
            return (
                f"Drug inhibits {n_targets} component(s) ({target_str}) "
                f"of a hyperactivated pathway"
            )
        elif direction == "suppressed":
            return (
                f"Drug targets {n_targets} component(s) ({target_str}) "
                f"in a suppressed pathway"
            )
        else:
            return (
                f"Drug targets {n_targets} component(s) ({target_str}) "
                f"in this pathway"
            )

    async def _find_druggable_genes(
        self, gene_symbols: list[str], db_session: AsyncSession
    ) -> list[str]:
        """Find which genes in a list are also drug targets."""
        if not gene_symbols:
            return []

        result = await db_session.execute(
            select(func.distinct(Target.gene_symbol))
            .join(DrugTarget, DrugTarget.target_id == Target.id)
            .where(Target.gene_symbol.in_(gene_symbols))
        )
        return [row[0] for row in result]

    # ==================================================================
    # Co-Expression Analysis
    # ==================================================================

    async def find_coexpressed_genes(
        self,
        gene_symbol: str,
        cancer_type_id: int,
        db_session: AsyncSession,
        threshold: float = 0.7,
        top_n: int = 50,
    ) -> list[dict[str, Any]]:
        """Find genes whose expression strongly correlates with a given gene.

        Uses Pearson correlation across tumor samples.
        """
        # 1. Get expression vector for the query gene
        query_result = await db_session.execute(
            select(
                GeneExpression.sample_id, GeneExpression.expression_log2
            )
            .where(
                GeneExpression.cancer_type_id == cancer_type_id,
                GeneExpression.gene_symbol == gene_symbol,
                GeneExpression.is_tumor.is_(True),
                GeneExpression.expression_log2.isnot(None),
            )
            .order_by(GeneExpression.sample_id)
        )
        query_samples = {r.sample_id: r.expression_log2 for r in query_result}

        if len(query_samples) < 20:
            return []

        # 2. Get top variable genes to correlate against
        top_genes = await self.preprocessor.get_top_variable_genes(
            cancer_type_id, db_session, top_n=2000
        )
        top_genes = [g for g in top_genes if g != gene_symbol]

        if not top_genes:
            return []

        # 3. Build expression vectors for candidate genes
        sorted_samples = sorted(query_samples.keys())
        query_vector = np.array(
            [query_samples[s] for s in sorted_samples], dtype=np.float64
        )

        # Fetch candidate gene data in chunks
        CHUNK = 500
        correlations: list[dict[str, Any]] = []

        for i in range(0, len(top_genes), CHUNK):
            chunk_genes = top_genes[i : i + CHUNK]

            chunk_result = await db_session.execute(
                select(
                    GeneExpression.gene_symbol,
                    GeneExpression.sample_id,
                    GeneExpression.expression_log2,
                )
                .where(
                    GeneExpression.cancer_type_id == cancer_type_id,
                    GeneExpression.gene_symbol.in_(chunk_genes),
                    GeneExpression.is_tumor.is_(True),
                    GeneExpression.expression_log2.isnot(None),
                )
            )

            # Organize by gene
            gene_vectors: dict[str, dict[str, float]] = {}
            for row in chunk_result:
                g = row.gene_symbol
                if g not in gene_vectors:
                    gene_vectors[g] = {}
                gene_vectors[g][row.sample_id] = row.expression_log2

            # 4. Compute Pearson correlation for each candidate
            for g, sample_vals in gene_vectors.items():
                common_samples = [
                    s for s in sorted_samples if s in sample_vals
                ]
                if len(common_samples) < 20:
                    continue

                q_vals = np.array(
                    [query_samples[s] for s in common_samples],
                    dtype=np.float64,
                )
                g_vals = np.array(
                    [sample_vals[s] for s in common_samples],
                    dtype=np.float64,
                )

                r_val, p_val = stats.pearsonr(q_vals, g_vals)

                if abs(r_val) >= threshold:
                    correlations.append(
                        {
                            "partner_gene": g,
                            "correlation": round(float(r_val), 4),
                            "p_value": float(p_val),
                            "direction": (
                                "positive" if r_val > 0 else "negative"
                            ),
                            "n_samples": len(common_samples),
                        }
                    )

        # 5. Sort by absolute correlation
        correlations.sort(key=lambda x: abs(x["correlation"]), reverse=True)
        correlations = correlations[:top_n]

        # 6. Annotate: is each partner a drug target? A cancer gene?
        if correlations:
            partner_genes = [c["partner_gene"] for c in correlations]

            druggable = set(
                await self._find_druggable_genes(partner_genes, db_session)
            )

            cancer_genes_result = await db_session.execute(
                select(
                    func.distinct(CancerMolecularProfile.gene_symbol)
                ).where(
                    CancerMolecularProfile.cancer_type_id == cancer_type_id,
                    CancerMolecularProfile.gene_symbol.in_(partner_genes),
                )
            )
            cancer_genes = {row[0] for row in cancer_genes_result}

            for c in correlations:
                gene = c["partner_gene"]
                c["is_drug_target"] = gene in druggable
                c["is_cancer_gene"] = gene in cancer_genes
                parts = []
                if c["is_drug_target"]:
                    parts.append("known drug target")
                if c["is_cancer_gene"]:
                    parts.append("known cancer gene")
                if parts:
                    c["interpretation"] = (
                        f"Co-expressed with {' and '.join(parts)} — "
                        f"drug effect may extend to this gene's function"
                    )
                else:
                    c["interpretation"] = "Co-expressed gene"

        return correlations

    # ==================================================================
    # Synthetic Lethality Potential
    # ==================================================================

    async def check_synthetic_lethality_potential(
        self, drug_id: int, cancer_type_id: int, db_session: AsyncSession
    ) -> dict[str, Any]:
        """Check if a drug might exploit synthetic lethality in this cancer.

        Synthetic lethality: if cancer has lost Gene A, and Drug X inhibits
        Gene B (the synthetic lethal partner), Drug X should selectively
        kill cancer cells while sparing normal cells.
        """
        # 1. Get drug targets
        drug_targets = await self._get_drug_targets(drug_id, db_session)
        if not drug_targets:
            return {
                "score": 0,
                "potential_pairs": [],
                "explanation": "No drug targets found",
            }

        # 2. Get genes with loss-of-function in this cancer
        lost_genes_result = await db_session.execute(
            select(CancerMolecularProfile).where(
                CancerMolecularProfile.cancer_type_id == cancer_type_id,
                CancerMolecularProfile.alteration_type.in_(
                    ["deletion", "mutation"]
                ),
                CancerMolecularProfile.frequency_percent >= 5,
            )
        )
        lost_genes = lost_genes_result.scalars().all()

        if not lost_genes:
            return {
                "score": 0,
                "potential_pairs": [],
                "explanation": "No significant loss-of-function genes found in this cancer",
            }

        # 3. Check for synthetic lethal potential
        potential_pairs: list[dict[str, Any]] = []

        for target in drug_targets:
            for lost in lost_genes:
                if target["gene_symbol"] == lost.gene_symbol:
                    continue

                # Check STRING interaction
                interaction_score = await self._get_interaction_score(
                    target["uniprot_id"], lost.gene_symbol, db_session
                )

                # Check shared pathways
                shared_pathways = await self._get_shared_pathways(
                    target["gene_symbol"], lost.gene_symbol, db_session
                )

                # Score this pair
                sl_score = self._score_sl_potential(
                    interaction_score, shared_pathways
                )

                if sl_score > 30:
                    potential_pairs.append(
                        {
                            "drug_target": target["gene_symbol"],
                            "cancer_lost_gene": lost.gene_symbol,
                            "cancer_loss_type": lost.alteration_type,
                            "cancer_loss_frequency": (
                                lost.frequency_percent or 0
                            ),
                            "interaction_score": interaction_score,
                            "shared_pathways": [
                                p["name"] for p in shared_pathways
                            ],
                            "sl_score": sl_score,
                            "confidence": (
                                "high"
                                if sl_score > 70
                                else "moderate"
                                if sl_score > 50
                                else "low"
                            ),
                        }
                    )

        # Sort by score
        potential_pairs.sort(key=lambda x: x["sl_score"], reverse=True)
        potential_pairs = potential_pairs[:10]

        overall_score = (
            max((p["sl_score"] for p in potential_pairs), default=0)
        )

        return {
            "score": round(overall_score),
            "potential_pairs": potential_pairs,
            "explanation": self._generate_sl_explanation(potential_pairs),
        }

    async def _get_interaction_score(
        self,
        uniprot_id: str,
        gene_symbol: str,
        db_session: AsyncSession,
    ) -> int:
        """Get STRING interaction score between a protein and a gene."""
        if not uniprot_id:
            return 0

        # Get the uniprot_id for the partner gene
        target_result = await db_session.execute(
            select(Target.uniprot_id).where(
                Target.gene_symbol == gene_symbol
            )
        )
        partner_uniprot = target_result.scalar_one_or_none()
        if not partner_uniprot:
            return 0

        # Check both directions
        result = await db_session.execute(
            select(ProteinInteraction.interaction_score).where(
                (
                    (ProteinInteraction.protein_a_uniprot == uniprot_id)
                    & (ProteinInteraction.protein_b_uniprot == partner_uniprot)
                )
                | (
                    (ProteinInteraction.protein_a_uniprot == partner_uniprot)
                    & (ProteinInteraction.protein_b_uniprot == uniprot_id)
                )
            )
        )
        score = result.scalar_one_or_none()
        # STRING scores are 0-1000; if stored as 0-1 normalize
        return int(score * 1000) if score and score < 1 else int(score or 0)

    async def _get_shared_pathways(
        self,
        gene_a: str,
        gene_b: str,
        db_session: AsyncSession,
    ) -> list[dict[str, Any]]:
        """Find pathways shared between two genes."""
        result = await db_session.execute(
            text("""
                SELECT p.id, p.name, p.source
                FROM pathways p
                WHERE p.genes @> jsonb_build_array(:gene_a)
                  AND p.genes @> jsonb_build_array(:gene_b)
                LIMIT 10
            """),
            {"gene_a": gene_a, "gene_b": gene_b},
        )
        return [
            {"id": row[0], "name": row[1], "source": row[2]}
            for row in result.fetchall()
        ]

    def _score_sl_potential(
        self,
        interaction_score: int,
        shared_pathways: list[dict],
    ) -> int:
        """Score synthetic lethality potential for a gene pair."""
        score = 0

        # Strong physical interaction suggests functional dependency
        if interaction_score >= 900:
            score += 40
        elif interaction_score >= 700:
            score += 25
        elif interaction_score >= 400:
            score += 10

        # Shared pathways indicate functional relationship
        n_shared = len(shared_pathways)
        if n_shared >= 3:
            score += 35
        elif n_shared >= 1:
            score += 20

        # DNA repair pathway bonus (known SL-rich area)
        dna_repair_keywords = {
            "dna repair",
            "homologous recombination",
            "mismatch repair",
        }
        for p in shared_pathways:
            if any(
                kw in (p.get("name", "")).lower()
                for kw in dna_repair_keywords
            ):
                score += 15
                break

        return min(100, score)

    def _generate_sl_explanation(
        self, potential_pairs: list[dict]
    ) -> str:
        """Generate explanation for synthetic lethality results."""
        if not potential_pairs:
            return "No synthetic lethality potential detected"

        high = [p for p in potential_pairs if p["confidence"] == "high"]
        if high:
            pair = high[0]
            return (
                f"Drug target {pair['drug_target']} has potential synthetic "
                f"lethal interaction with {pair['cancer_lost_gene']}, which is "
                f"lost in {pair['cancer_loss_frequency']:.0f}% of cases"
            )

        pair = potential_pairs[0]
        return (
            f"Potential synthetic lethality between {pair['drug_target']} and "
            f"{pair['cancer_lost_gene']} ({pair['confidence']} confidence)"
        )

    # ==================================================================
    # Cache Management
    # ==================================================================

    async def _get_cached_score(
        self,
        cache_type: str,
        entity_id_1: int,
        entity_id_2: int,
        db_session: AsyncSession,
        max_age_days: int = 7,
    ) -> dict | None:
        """Get a cached score if it exists and is fresh."""
        result = await db_session.execute(
            select(ExpressionScoreCache).where(
                ExpressionScoreCache.cache_type == cache_type,
                ExpressionScoreCache.entity_id_1 == entity_id_1,
                ExpressionScoreCache.entity_id_2 == entity_id_2,
            )
        )
        cache_entry = result.scalar_one_or_none()

        if cache_entry and cache_entry.computed_at:
            age = datetime.now(timezone.utc) - cache_entry.computed_at.replace(
                tzinfo=timezone.utc
            )
            if age.days <= max_age_days:
                return cache_entry.details

        return None

    async def _set_cached_score(
        self,
        cache_type: str,
        entity_id_1: int,
        entity_id_2: int,
        result: dict,
        db_session: AsyncSession,
    ) -> None:
        """Store a score in the cache (upsert)."""
        existing = await db_session.execute(
            select(ExpressionScoreCache).where(
                ExpressionScoreCache.cache_type == cache_type,
                ExpressionScoreCache.entity_id_1 == entity_id_1,
                ExpressionScoreCache.entity_id_2 == entity_id_2,
            )
        )
        cache_entry = existing.scalar_one_or_none()

        if cache_entry:
            cache_entry.score = result.get("score") or result.get(
                "activity_score", 0
            )
            cache_entry.details = result
            cache_entry.computed_at = func.now()
        else:
            db_session.add(
                ExpressionScoreCache(
                    cache_type=cache_type,
                    entity_id_1=entity_id_1,
                    entity_id_2=entity_id_2,
                    score=result.get("score") or result.get(
                        "activity_score", 0
                    ),
                    details=result,
                )
            )

        await db_session.flush()
