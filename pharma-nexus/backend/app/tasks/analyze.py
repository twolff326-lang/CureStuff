"""Celery tasks for running gene expression analyses and LLM-powered analyses.

Tasks (expression):
  - compute_all_differential_expression: DE analysis for all 33 cancer types
  - compute_drug_expression_scores: Score drug-cancer pairs on expression compatibility
  - compute_pathway_activities: Pathway activity scores for all pathway-cancer combos
  - run_full_expression_analysis: Complete pipeline combining all analyses

Tasks (LLM analysis):
  - generate_narratives_batch: Mechanistic narratives for top hypotheses
  - generate_full_analysis_batch: All 6 analysis types for top hypotheses
  - generate_comparative_analyses: Comparative analyses grouped by cancer type
  - generate_single_analysis: One specific analysis type for one hypothesis
"""

import logging

from app.tasks.celery_app import celery_app
from app.tasks.utils import run_async, task_session

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True, max_retries=2, name="app.tasks.analyze.compute_all_differential_expression"
)
def compute_all_differential_expression(self, cancer_type_id=None):
    """Run differential expression analysis for all (or one) cancer types.

    Processes one cancer type at a time. Checkpoints after each.
    Runtime: ~30-60 minutes for all 33 cancer types.
    """
    logger.info(
        "Starting differential expression computation (cancer_type_id=%s)",
        cancer_type_id,
    )
    try:

        async def _compute():
            from sqlalchemy import select

            from app.models.cancer_type import CancerType
            from app.services.expression_analyzer import ExpressionAnalyzer

            analyzer = ExpressionAnalyzer()
            results = {}

            async with task_session() as session:
                if cancer_type_id:
                    cancer_ids = [cancer_type_id]
                else:
                    result = await session.execute(
                        select(CancerType.id, CancerType.tcga_code)
                    )
                    cancer_ids = [(row.id, row.tcga_code) for row in result]

                total = len(cancer_ids)
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "current": 0, "total": total,
                        "step": "Differential Expression",
                        "detail": f"Starting DE analysis for {total} cancer types",
                        "percent": 0,
                    },
                )
                for i, item in enumerate(cancer_ids):
                    cid = item if isinstance(item, int) else item[0]
                    code = item if isinstance(item, int) else item[1]
                    self.update_state(
                        state="PROGRESS",
                        meta={
                            "current": i, "total": total,
                            "step": "Differential Expression",
                            "detail": f"Processing {code} ({i + 1}/{total})",
                            "percent": round((i / total) * 100),
                        },
                    )
                    logger.info(
                        "Processing cancer type %s (%d/%d)",
                        code, i + 1, total,
                    )

                    try:
                        de_results = await analyzer.compute_differential_expression(
                            cid, session
                        )
                        await session.commit()
                        session.expire_all()  # Release expression data from identity map
                        results[str(code)] = {
                            "genes_analyzed": len(de_results),
                            "significant": sum(
                                1
                                for r in de_results
                                if r.get("significant", False)
                            ),
                        }
                    except Exception as e:
                        logger.error(
                            "DE computation failed for %s: %s", code, e
                        )
                        await session.rollback()
                        results[str(code)] = {"error": str(e)}

            return results

        results = run_async(_compute())
        total_genes = sum(
            r.get("genes_analyzed", 0) for r in results.values()
        )
        total_sig = sum(
            r.get("significant", 0) for r in results.values()
        )
        logger.info(
            "DE computation complete: %d cancer types, %d genes, %d significant",
            len(results),
            total_genes,
            total_sig,
        )
        return {
            "records_processed": total_genes,
            "errors_count": sum(
                1 for r in results.values() if "error" in r
            ),
            "cancer_type_results": results,
        }

    except Exception as exc:
        logger.error("Differential expression computation failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(
    bind=True, max_retries=2, name="app.tasks.analyze.compute_drug_expression_scores"
)
def compute_drug_expression_scores(self, cancer_type_id=None):
    """Score all drug-cancer pairs on expression compatibility.

    If cancer_type_id provided, score all drugs for that cancer.
    Otherwise, score all drug-cancer pairs with existing hypotheses.

    Stores pre-computed scores in expression_score_cache table.
    """
    logger.info(
        "Starting drug expression scoring (cancer_type_id=%s)", cancer_type_id
    )
    try:

        async def _compute():
            from sqlalchemy import select

            from app.models.cancer_type import CancerType
            from app.models.drug import Drug
            from app.services.expression_analyzer import ExpressionAnalyzer

            analyzer = ExpressionAnalyzer()
            scored = 0
            errors = 0

            async with task_session() as session:
                # Get cancer types to process
                if cancer_type_id:
                    cancer_ids = [cancer_type_id]
                else:
                    result = await session.execute(select(CancerType.id))
                    cancer_ids = [row[0] for row in result]

                # Get all drugs
                drug_result = await session.execute(select(Drug.id))
                drug_ids = [row[0] for row in drug_result]

                total_pairs = len(cancer_ids) * len(drug_ids)
                logger.info(
                    "Scoring %d drug-cancer pairs (%d drugs x %d cancers)",
                    total_pairs,
                    len(drug_ids),
                    len(cancer_ids),
                )
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "current": 0, "total": total_pairs,
                        "step": "Drug Expression Scoring",
                        "detail": f"Starting scoring for {total_pairs} drug-cancer pairs",
                        "percent": 0,
                    },
                )

                for cid in cancer_ids:
                    for did in drug_ids:
                        try:
                            await analyzer.score_target_expression(
                                did, cid, session
                            )
                            scored += 1
                            if scored % 500 == 0:
                                await session.commit()
                                session.expire_all()  # Release cached objects
                                pct = round((scored / total_pairs) * 100)
                                self.update_state(
                                    state="PROGRESS",
                                    meta={
                                        "current": scored, "total": total_pairs,
                                        "step": "Drug Expression Scoring",
                                        "detail": f"Scored {scored}/{total_pairs} pairs",
                                        "percent": pct,
                                    },
                                )
                                logger.info(
                                    "Scored %d/%d pairs", scored, total_pairs
                                )
                        except Exception as e:
                            errors += 1
                            logger.debug(
                                "Failed scoring drug=%d cancer=%d: %s",
                                did, cid, e,
                            )

                await session.commit()

            return {"scored": scored, "errors": errors}

        result = run_async(_compute())
        logger.info(
            "Drug expression scoring complete: %d scored, %d errors",
            result["scored"],
            result["errors"],
        )
        return {
            "records_processed": result["scored"],
            "errors_count": result["errors"],
        }

    except Exception as exc:
        logger.error("Drug expression scoring failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(
    bind=True, max_retries=2, name="app.tasks.analyze.compute_pathway_activities"
)
def compute_pathway_activities(self, cancer_type_id=None):
    """Compute pathway activity scores for all pathway-cancer combinations.

    ~2,340 pathways x 33 cancer types = ~77,000 computations.
    Results cached in expression_score_cache with 7-day TTL.
    """
    logger.info(
        "Starting pathway activity computation (cancer_type_id=%s)",
        cancer_type_id,
    )
    try:

        async def _compute():
            from sqlalchemy import select

            from app.models.cancer_type import CancerType
            from app.models.pathway import Pathway
            from app.services.expression_analyzer import ExpressionAnalyzer

            analyzer = ExpressionAnalyzer()
            computed = 0
            errors = 0

            async with task_session() as session:
                # Get cancer types
                if cancer_type_id:
                    cancer_ids = [cancer_type_id]
                else:
                    result = await session.execute(select(CancerType.id))
                    cancer_ids = [row[0] for row in result]

                # Get all pathways
                pathway_result = await session.execute(select(Pathway.id))
                pathway_ids = [row[0] for row in pathway_result]

                total = len(cancer_ids) * len(pathway_ids)
                logger.info(
                    "Computing %d pathway-cancer activities (%d pathways x %d cancers)",
                    total,
                    len(pathway_ids),
                    len(cancer_ids),
                )
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "current": 0, "total": total,
                        "step": "Pathway Activities",
                        "detail": f"Starting computation for {total} pathway-cancer pairs",
                        "percent": 0,
                    },
                )

                for cid in cancer_ids:
                    for pid in pathway_ids:
                        try:
                            await analyzer.compute_pathway_activity(
                                cid, pid, session
                            )
                            computed += 1
                            if computed % 1000 == 0:
                                await session.commit()
                                session.expire_all()  # Release cached objects
                                pct = round((computed / total) * 100)
                                self.update_state(
                                    state="PROGRESS",
                                    meta={
                                        "current": computed, "total": total,
                                        "step": "Pathway Activities",
                                        "detail": f"Computed {computed}/{total} activities",
                                        "percent": pct,
                                    },
                                )
                                logger.info(
                                    "Computed %d/%d activities",
                                    computed, total,
                                )
                        except Exception as e:
                            errors += 1
                            logger.debug(
                                "Failed pathway=%d cancer=%d: %s",
                                pid, cid, e,
                            )

                await session.commit()

            return {"computed": computed, "errors": errors}

        result = run_async(_compute())
        logger.info(
            "Pathway activity computation complete: %d computed, %d errors",
            result["computed"],
            result["errors"],
        )
        return {
            "records_processed": result["computed"],
            "errors_count": result["errors"],
        }

    except Exception as exc:
        logger.error("Pathway activity computation failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(bind=True, name="app.tasks.analyze.run_full_expression_analysis")
def run_full_expression_analysis(self, cancer_type_id=None):
    """Run the complete expression analysis pipeline:

    1. Differential expression for all cancer types
    2. Drug expression scores for all drug-cancer pairs
    3. Pathway activity scores for all pathway-cancer combinations

    This is the main "analysis" trigger that prepares all expression
    data needed by the hypothesis engine.
    """
    logger.info("Starting full expression analysis pipeline")

    # Step 1: Differential expression
    self.update_state(
        state="PROGRESS",
        meta={
            "current": 1, "total": 3,
            "step": "Differential Expression",
            "detail": "Step 1/3: Running differential expression analysis",
            "percent": 0,
        },
    )
    de_result = compute_all_differential_expression.apply(
        kwargs={"cancer_type_id": cancer_type_id}
    )
    de_result.get(timeout=7200)  # 2h timeout

    # Step 2: Drug expression scores
    self.update_state(
        state="PROGRESS",
        meta={
            "current": 2, "total": 3,
            "step": "Drug Expression Scoring",
            "detail": "Step 2/3: Scoring drug-cancer expression pairs",
            "percent": 33,
        },
    )
    drug_result = compute_drug_expression_scores.apply(
        kwargs={"cancer_type_id": cancer_type_id}
    )
    drug_result.get(timeout=14400)  # 4h timeout

    # Step 3: Pathway activities
    self.update_state(
        state="PROGRESS",
        meta={
            "current": 3, "total": 3,
            "step": "Pathway Activities",
            "detail": "Step 3/3: Computing pathway activity scores",
            "percent": 66,
        },
    )
    pathway_result = compute_pathway_activities.apply(
        kwargs={"cancer_type_id": cancer_type_id}
    )
    pathway_result.get(timeout=14400)  # 4h timeout

    return {
        "status": "completed",
        "de_task_id": de_result.id,
        "drug_scoring_task_id": drug_result.id,
        "pathway_activity_task_id": pathway_result.id,
    }


# ------------------------------------------------------------------
# LLM-powered analysis tasks
# ------------------------------------------------------------------


@celery_app.task(
    bind=True, max_retries=1, name="app.tasks.analyze.generate_narratives_batch"
)
def generate_narratives_batch(self, min_score=0.0, limit=100):
    """Generate mechanistic narratives for top hypotheses without one.

    Uses Claude Sonnet for bulk, Claude Opus for top hypotheses (score >= 70).
    """
    logger.info(
        "Starting narrative batch generation (min_score=%.1f, limit=%d)",
        min_score,
        limit,
    )
    self.update_state(
        state="PROGRESS",
        meta={
            "current": 0, "total": limit,
            "step": "Generating Narratives",
            "detail": f"Generating narratives for up to {limit} hypotheses",
            "percent": 0,
        },
    )
    try:

        async def _generate():
            from app.services.llm_analyst import LLMAnalyst

            analyst = LLMAnalyst()
            async with task_session() as session:
                results = await analyst.generate_narratives_batch(
                    session, min_score=min_score, limit=limit
                )
                await session.commit()
            return results

        results = run_async(_generate())
        logger.info(
            "Narrative batch complete: %d/%d generated, %d errors",
            results["completed"],
            results["total"],
            len(results["errors"]),
        )
        return results

    except Exception as exc:
        logger.error("Narrative batch generation failed: %s", exc)
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))


@celery_app.task(
    bind=True, max_retries=1, name="app.tasks.analyze.generate_full_analysis_batch"
)
def generate_full_analysis_batch(self, min_score=50.0, limit=20):
    """Generate all 6 analysis types for top hypotheses.

    Each hypothesis gets: narrative, critique, comparative, literature_synthesis,
    experiment_design, and confidence assessment.
    """
    logger.info(
        "Starting full analysis batch (min_score=%.1f, limit=%d)",
        min_score,
        limit,
    )
    self.update_state(
        state="PROGRESS",
        meta={
            "current": 0, "total": limit,
            "step": "Full Analysis",
            "detail": f"Running 6 analysis types for up to {limit} hypotheses",
            "percent": 0,
        },
    )
    try:

        async def _generate():
            from app.services.llm_analyst import LLMAnalyst

            analyst = LLMAnalyst()
            async with task_session() as session:
                results = await analyst.generate_full_analysis_batch(
                    session, min_score=min_score, limit=limit
                )
                await session.commit()
            return results

        results = run_async(_generate())
        logger.info(
            "Full analysis batch complete: %d/%d generated, %d errors",
            results["completed"],
            results["total"],
            len(results["errors"]),
        )
        return results

    except Exception as exc:
        logger.error("Full analysis batch failed: %s", exc)
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))


@celery_app.task(
    bind=True, max_retries=1, name="app.tasks.analyze.generate_comparative_analyses"
)
def generate_comparative_analyses(self, cancer_type_id=None, min_score=30.0):
    """Generate comparative analyses for hypotheses grouped by cancer type.

    Compares each hypothesis against others targeting the same cancer,
    identifying unique advantages and synergies.
    """
    logger.info(
        "Starting comparative analysis generation (cancer_type_id=%s, min_score=%.1f)",
        cancer_type_id,
        min_score,
    )
    self.update_state(
        state="PROGRESS",
        meta={
            "current": 0, "total": 0,
            "step": "Comparative Analysis",
            "detail": "Generating comparative analyses by cancer type",
            "percent": 0,
        },
    )
    try:

        async def _generate():
            from app.services.llm_analyst import LLMAnalyst

            analyst = LLMAnalyst()
            async with task_session() as session:
                results = await analyst.generate_comparative_analyses(
                    session,
                    cancer_type_id=cancer_type_id,
                    min_score=min_score,
                )
                await session.commit()
            return results

        results = run_async(_generate())
        logger.info(
            "Comparative analysis complete: %d cancer types, %d analyses, %d errors",
            results["cancer_types_processed"],
            results["total_analyses"],
            len(results["errors"]),
        )
        return results

    except Exception as exc:
        logger.error("Comparative analysis generation failed: %s", exc)
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))


@celery_app.task(
    bind=True, max_retries=2, name="app.tasks.analyze.generate_single_analysis"
)
def generate_single_analysis(self, hypothesis_id, analysis_type):
    """Generate a single analysis type for a specific hypothesis.

    analysis_type must be one of: narrative, critique, comparative,
    literature_synthesis, experiment_design, confidence.
    """
    logger.info(
        "Generating %s analysis for hypothesis %d",
        analysis_type,
        hypothesis_id,
    )
    try:

        async def _generate():
            from app.services.llm_analyst import LLMAnalyst

            analyst = LLMAnalyst()
            method_map = {
                "narrative": analyst.generate_narrative,
                "critique": analyst.generate_critique,
                "comparative": analyst.generate_comparative_analysis,
                "literature_synthesis": analyst.synthesize_literature,
                "experiment_design": analyst.design_experiments,
                "confidence": analyst.assess_confidence,
            }

            if analysis_type not in method_map:
                raise ValueError(
                    f"Invalid analysis_type '{analysis_type}'. "
                    f"Must be one of: {', '.join(method_map)}"
                )

            async with task_session() as session:
                result = await method_map[analysis_type](hypothesis_id, session)
                await session.commit()
            return result

        result = run_async(_generate())
        logger.info(
            "%s analysis complete for hypothesis %d (model=%s, tokens=%d+%d)",
            analysis_type,
            hypothesis_id,
            result.get("model", "unknown"),
            result.get("tokens", {}).get("input", 0),
            result.get("tokens", {}).get("output", 0),
        )
        return result

    except Exception as exc:
        logger.error(
            "%s analysis failed for hypothesis %d: %s",
            analysis_type,
            hypothesis_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
