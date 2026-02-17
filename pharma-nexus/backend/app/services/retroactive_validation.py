"""Retroactive validation: the "time machine" test for drug repurposing.

The gold standard for evaluating a computational drug repurposing method:
take a known successful repurposing, hide all evidence published AFTER
the discovery, and ask: would the system have predicted it?

Example: Imatinib was repurposed for GIST in 2002.
  1. Filter literature to papers published before 2002
  2. Run Strategy 7 on pre-2002 literature about GI cancers
  3. Check if Imatinib appears in the proposals
  4. If yes: the method has genuine predictive power
  5. If no: the method is only retrospectively explaining known facts

This module runs this test across all 23 ground truth cases from
validation.py, producing precision@k, recall, and discovery-date
stratified metrics.
"""

import logging
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.cancer_type import CancerType, CancerMolecularProfile
from app.models.drug import Drug, DrugTarget, LiteratureDrug
from app.models.literature import Literature, LiteratureCancer
from app.models.mutation import Mutation
from app.models.target import Target
from app.services.validation import GROUND_TRUTH_CASES

logger = logging.getLogger(__name__)


class RetroactiveValidator:
    """Tests whether the system could have predicted known repurposings
    using only evidence available before each discovery.
    """

    def __init__(self) -> None:
        self._results: list[dict[str, Any]] = []

    async def evaluate_case(
        self,
        case: dict[str, Any],
        session: AsyncSession,
    ) -> dict[str, Any]:
        """Evaluate a single ground truth case retroactively.

        For successes with known approval years, restricts literature
        to pre-discovery papers and checks whether structured evidence
        would have connected the drug to the cancer.
        """
        drug_name = case["drug_name"]
        cancer_name = case["cancer_name"]
        outcome = case["outcome"]
        approval_year = case.get("approval_year")
        tcga_code = case.get("cancer_tcga_code")
        drugbank_id = case.get("drug_drugbank_id")

        # Resolve drug and cancer in database
        drug_result = await session.execute(
            select(Drug).where(Drug.drugbank_id == drugbank_id)
        )
        drug = drug_result.scalar_one_or_none()

        cancer = None
        if tcga_code:
            cancer_result = await session.execute(
                select(CancerType).where(CancerType.tcga_code == tcga_code)
            )
            cancer = cancer_result.scalar_one_or_none()

        if not drug or not cancer:
            # Try name-based matching
            if not drug:
                drug_result = await session.execute(
                    select(Drug).where(func.lower(Drug.name) == drug_name.lower())
                )
                drug = drug_result.scalar_one_or_none()
            if not cancer:
                cancer_result = await session.execute(
                    select(CancerType).where(
                        func.lower(CancerType.name).contains(cancer_name.lower())
                    )
                )
                cancer = cancer_result.scalar_one_or_none()

        if not drug or not cancer:
            return {
                "drug_name": drug_name,
                "cancer_name": cancer_name,
                "outcome": outcome,
                "status": "not_in_database",
                "drug_found": drug is not None,
                "cancer_found": cancer is not None,
            }

        # For the time-machine test, we define a cutoff date.
        # For successes: 2 years before FDA approval (early investigation phase)
        # For ongoing: use current date (these are still being investigated)
        # For failures: use current date (all evidence is relevant)
        if outcome == "success" and approval_year:
            cutoff_year = approval_year - 2
            cutoff_date = date(cutoff_year, 1, 1)
        else:
            cutoff_date = None  # No restriction

        # Gather evidence components with optional time filtering
        evidence = await self._gather_evidence(
            drug.id, cancer.id, cutoff_date, session
        )

        # Compute a "retroactive predictability" score
        predictability = self._score_predictability(evidence)

        return {
            "drug_name": drug_name,
            "cancer_name": cancer_name,
            "outcome": outcome,
            "approval_year": approval_year,
            "cutoff_date": str(cutoff_date) if cutoff_date else None,
            "status": "evaluated",
            "drug_id": drug.id,
            "cancer_type_id": cancer.id,
            "evidence": evidence,
            "predictability_score": predictability["score"],
            "predictability_details": predictability["details"],
            "would_have_predicted": predictability["score"] >= 0.3,
        }

    async def _gather_evidence(
        self,
        drug_id: int,
        cancer_type_id: int,
        cutoff_date: date | None,
        session: AsyncSession,
    ) -> dict[str, Any]:
        """Gather all evidence links between drug and cancer, optionally
        filtered to before the cutoff date.
        """
        evidence: dict[str, Any] = {}

        # 1. Drug targets
        target_result = await session.execute(
            select(Target.gene_symbol, DrugTarget.action_type)
            .join(DrugTarget, DrugTarget.target_id == Target.id)
            .where(DrugTarget.drug_id == drug_id)
        )
        drug_targets = target_result.all()
        drug_target_genes = {r[0] for r in drug_targets}
        evidence["drug_targets"] = {
            "count": len(drug_targets),
            "genes": list(drug_target_genes)[:20],
        }

        # 2. Cancer mutations overlapping with drug targets
        if drug_target_genes:
            mut_result = await session.execute(
                select(Mutation.gene_symbol, Mutation.frequency_percent)
                .where(
                    Mutation.cancer_type_id == cancer_type_id,
                    Mutation.gene_symbol.in_(drug_target_genes),
                )
            )
            mutations = mut_result.all()
            evidence["target_mutations"] = {
                "count": len(mutations),
                "genes": [{"gene": r[0], "freq": r[1]} for r in mutations],
            }
        else:
            evidence["target_mutations"] = {"count": 0, "genes": []}

        # 3. Expression correlation
        if drug_target_genes:
            expr_result = await session.execute(
                select(
                    CancerMolecularProfile.gene_symbol,
                    CancerMolecularProfile.expression_zscore,
                )
                .where(
                    CancerMolecularProfile.cancer_type_id == cancer_type_id,
                    CancerMolecularProfile.gene_symbol.in_(drug_target_genes),
                )
            )
            expressions = expr_result.all()
            dysregulated = [
                {"gene": r[0], "zscore": float(r[1])}
                for r in expressions
                if r[1] and abs(r[1]) > 1.5
            ]
            evidence["expression_changes"] = {
                "total": len(expressions),
                "dysregulated": dysregulated,
            }
        else:
            evidence["expression_changes"] = {"total": 0, "dysregulated": []}

        # 4. Literature co-mentions (with optional time filter)
        lit_query = (
            select(func.count())
            .select_from(LiteratureDrug)
            .join(
                LiteratureCancer,
                LiteratureCancer.literature_id == LiteratureDrug.literature_id,
            )
            .join(
                Literature,
                Literature.id == LiteratureDrug.literature_id,
            )
            .where(
                LiteratureDrug.drug_id == drug_id,
                LiteratureCancer.cancer_type_id == cancer_type_id,
            )
        )
        if cutoff_date:
            lit_query = lit_query.where(Literature.pub_date < cutoff_date)

        co_mention_result = await session.execute(lit_query)
        co_mention_count = co_mention_result.scalar() or 0
        evidence["literature_co_mentions"] = {
            "count": co_mention_count,
            "time_filtered": cutoff_date is not None,
        }

        # 5. Total papers about this cancer (for context)
        cancer_lit_query = (
            select(func.count())
            .select_from(LiteratureCancer)
            .join(Literature, Literature.id == LiteratureCancer.literature_id)
            .where(LiteratureCancer.cancer_type_id == cancer_type_id)
        )
        if cutoff_date:
            cancer_lit_query = cancer_lit_query.where(
                Literature.pub_date < cutoff_date
            )
        cancer_paper_result = await session.execute(cancer_lit_query)
        evidence["cancer_papers_available"] = cancer_paper_result.scalar() or 0

        # 6. Papers about this drug
        drug_lit_query = (
            select(func.count())
            .select_from(LiteratureDrug)
            .join(Literature, Literature.id == LiteratureDrug.literature_id)
            .where(LiteratureDrug.drug_id == drug_id)
        )
        if cutoff_date:
            drug_lit_query = drug_lit_query.where(
                Literature.pub_date < cutoff_date
            )
        drug_paper_result = await session.execute(drug_lit_query)
        evidence["drug_papers_available"] = drug_paper_result.scalar() or 0

        return evidence

    def _score_predictability(
        self, evidence: dict[str, Any]
    ) -> dict[str, Any]:
        """Score how predictable a repurposing was from the evidence.

        Components:
          - target_overlap (0-0.3): Drug targets overlap with cancer mutations
          - expression_signal (0-0.2): Drug targets are dysregulated
          - literature_signal (0-0.3): Pre-discovery literature co-mentions
          - data_availability (0-0.2): Enough papers to reason from
        """
        details: dict[str, float] = {}

        # Target overlap: do drug targets hit cancer-mutated genes?
        target_muts = evidence.get("target_mutations", {}).get("count", 0)
        details["target_overlap"] = min(target_muts * 0.1, 0.3)

        # Expression signal: are drug targets dysregulated in this cancer?
        dysregulated = len(
            evidence.get("expression_changes", {}).get("dysregulated", [])
        )
        details["expression_signal"] = min(dysregulated * 0.05, 0.2)

        # Literature signal: do papers co-mention drug and cancer?
        co_mentions = evidence.get("literature_co_mentions", {}).get("count", 0)
        details["literature_signal"] = min(co_mentions * 0.03, 0.3)

        # Data availability: are there enough papers to reason from?
        cancer_papers = evidence.get("cancer_papers_available", 0)
        drug_papers = evidence.get("drug_papers_available", 0)
        min_papers = min(cancer_papers, drug_papers)
        details["data_availability"] = min(min_papers * 0.01, 0.2)

        total = sum(details.values())

        return {
            "score": round(total, 3),
            "details": {k: round(v, 3) for k, v in details.items()},
        }

    async def run_full_evaluation(
        self,
        session: AsyncSession,
        cases: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Run retroactive evaluation across all ground truth cases.

        Returns per-case results plus aggregate metrics.
        """
        if cases is None:
            cases = GROUND_TRUTH_CASES

        results = []
        for case in cases:
            try:
                result = await self.evaluate_case(case, session)
                results.append(result)
            except Exception as e:
                logger.error(
                    "Retroactive eval failed for %s/%s: %s",
                    case["drug_name"], case["cancer_name"], e,
                )
                results.append({
                    "drug_name": case["drug_name"],
                    "cancer_name": case["cancer_name"],
                    "outcome": case["outcome"],
                    "status": "error",
                    "error": str(e),
                })

        # Compute aggregate metrics
        metrics = self._compute_metrics(results)

        return {
            "cases_evaluated": len(results),
            "metrics": metrics,
            "cases": results,
        }

    def _compute_metrics(
        self, results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Compute aggregate evaluation metrics."""
        evaluated = [r for r in results if r.get("status") == "evaluated"]
        if not evaluated:
            return {"error": "No cases could be evaluated"}

        successes = [r for r in evaluated if r["outcome"] == "success"]
        failures = [r for r in evaluated if r["outcome"] == "failure"]
        ongoing = [r for r in evaluated if r["outcome"] in ("ongoing", "partial")]

        # Would-have-predicted rate by outcome
        success_predicted = sum(
            1 for r in successes if r.get("would_have_predicted")
        )
        failure_predicted = sum(
            1 for r in failures if r.get("would_have_predicted")
        )

        # Score distributions
        success_scores = [r["predictability_score"] for r in successes]
        failure_scores = [r["predictability_score"] for r in failures]

        # Separation: can we distinguish successes from failures?
        separation = None
        if success_scores and failure_scores:
            s_mean = sum(success_scores) / len(success_scores)
            f_mean = sum(failure_scores) / len(failure_scores)

            # AUC proxy: fraction of (success, failure) pairs correctly ordered
            correct_pairs = sum(
                1 for s in success_scores for f in failure_scores if s > f
            )
            total_pairs = len(success_scores) * len(failure_scores)
            auc_proxy = correct_pairs / total_pairs if total_pairs > 0 else 0.5

            separation = {
                "success_mean_score": round(s_mean, 3),
                "failure_mean_score": round(f_mean, 3),
                "score_gap": round(s_mean - f_mean, 3),
                "auc_proxy": round(auc_proxy, 3),
            }

        # Precision at various thresholds
        all_scored = sorted(
            evaluated,
            key=lambda x: x.get("predictability_score", 0),
            reverse=True,
        )
        precision_at_k: dict[str, float] = {}
        for k in [5, 10, 15]:
            if k > len(all_scored):
                continue
            top_k = all_scored[:k]
            true_positives = sum(
                1 for r in top_k
                if r["outcome"] in ("success", "ongoing", "partial")
            )
            precision_at_k[f"precision@{k}"] = round(true_positives / k, 3)

        return {
            "total_evaluated": len(evaluated),
            "successes": {
                "count": len(successes),
                "would_have_predicted": success_predicted,
                "recall": (
                    round(success_predicted / len(successes), 3)
                    if successes else 0
                ),
            },
            "failures": {
                "count": len(failures),
                "falsely_predicted": failure_predicted,
                "false_positive_rate": (
                    round(failure_predicted / len(failures), 3)
                    if failures else 0
                ),
            },
            "ongoing": {"count": len(ongoing)},
            "separation": separation,
            **precision_at_k,
        }
