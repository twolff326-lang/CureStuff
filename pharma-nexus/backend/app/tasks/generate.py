"""Celery tasks for hypothesis generation, rescoring, and combination prediction.

Tasks:
  - generate_hypotheses_for_cancer: Generate hypotheses for one cancer type
  - generate_hypotheses_for_drug: Generate hypotheses for one drug
  - generate_all_hypotheses: Generate hypotheses for all cancer types
  - rescore_hypotheses: Rescore all hypotheses with current/new weights
  - generate_combinations_for_cancer: Generate combination hypotheses for one cancer type
  - generate_all_combinations: Generate combination hypotheses for all cancer types
"""

import asyncio
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine from a sync Celery task."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@celery_app.task(
    bind=True,
    max_retries=2,
    name="app.tasks.generate.generate_hypotheses_for_cancer",
)
def generate_hypotheses_for_cancer(self, cancer_type_id, min_score=15.0):
    """Generate hypotheses for all drugs against a specific cancer type.

    Identifies candidates via 6 strategies, scores across 6 dimensions,
    and stores hypotheses above the minimum score threshold.
    """
    logger.info(
        "Starting hypothesis generation for cancer_type_id=%d (min_score=%.1f)",
        cancer_type_id,
        min_score,
    )
    try:

        async def _generate():
            from app.database import async_session_factory
            from app.services.hypothesis_engine import HypothesisEngine

            engine = HypothesisEngine()
            async with async_session_factory() as session:
                results = await engine.generate_for_cancer(
                    cancer_type_id, session, min_score=min_score
                )
                return {
                    "cancer_type_id": cancer_type_id,
                    "hypotheses_generated": len(results),
                    "top_scores": [
                        {
                            "drug_id": r["drug_id"],
                            "title": r["title"],
                            "composite_score": r["composite_score"],
                        }
                        for r in sorted(
                            results,
                            key=lambda x: x["composite_score"],
                            reverse=True,
                        )[:10]
                    ],
                }

        result = _run_async(_generate())
        logger.info(
            "Hypothesis generation complete for cancer_type_id=%d: %d hypotheses",
            cancer_type_id,
            result["hypotheses_generated"],
        )
        return result

    except Exception as exc:
        logger.error(
            "Hypothesis generation failed for cancer_type_id=%d: %s",
            cancer_type_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    max_retries=2,
    name="app.tasks.generate.generate_hypotheses_for_drug",
)
def generate_hypotheses_for_drug(self, drug_id, min_score=15.0):
    """Generate hypotheses for a specific drug against all cancer types."""
    logger.info(
        "Starting hypothesis generation for drug_id=%d (min_score=%.1f)",
        drug_id,
        min_score,
    )
    try:

        async def _generate():
            from app.database import async_session_factory
            from app.services.hypothesis_engine import HypothesisEngine

            engine = HypothesisEngine()
            async with async_session_factory() as session:
                results = await engine.generate_for_drug(
                    drug_id, session, min_score=min_score
                )
                return {
                    "drug_id": drug_id,
                    "hypotheses_generated": len(results),
                    "top_scores": [
                        {
                            "cancer_type_id": r["cancer_type_id"],
                            "title": r["title"],
                            "composite_score": r["composite_score"],
                        }
                        for r in sorted(
                            results,
                            key=lambda x: x["composite_score"],
                            reverse=True,
                        )[:10]
                    ],
                }

        result = _run_async(_generate())
        logger.info(
            "Hypothesis generation complete for drug_id=%d: %d hypotheses",
            drug_id,
            result["hypotheses_generated"],
        )
        return result

    except Exception as exc:
        logger.error(
            "Hypothesis generation failed for drug_id=%d: %s", drug_id, exc
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    max_retries=1,
    name="app.tasks.generate.generate_all_hypotheses",
)
def generate_all_hypotheses(self, min_score=15.0):
    """Generate hypotheses for all cancer types.

    Iterates over all cancer types and generates hypotheses for each.
    Runtime: 1-4 hours depending on data volume.
    """
    logger.info("Starting full hypothesis generation (min_score=%.1f)", min_score)
    try:

        async def _generate():
            from sqlalchemy import select

            from app.database import async_session_factory
            from app.models.cancer_type import CancerType
            from app.services.hypothesis_engine import HypothesisEngine

            engine = HypothesisEngine()
            total_generated = 0
            errors = 0
            results_by_cancer = {}

            async with async_session_factory() as session:
                cancer_result = await session.execute(
                    select(CancerType.id, CancerType.tcga_code)
                )
                cancer_types = cancer_result.all()
                total = len(cancer_types)

                for i, (cid, code) in enumerate(cancer_types):
                    logger.info(
                        "Generating hypotheses for %s (%d/%d)",
                        code, i + 1, total,
                    )
                    try:
                        results = await engine.generate_for_cancer(
                            cid, session, min_score=min_score
                        )
                        count = len(results)
                        total_generated += count
                        results_by_cancer[code] = count
                        logger.info(
                            "Generated %d hypotheses for %s", count, code
                        )
                    except Exception as e:
                        logger.error(
                            "Failed generating for %s: %s", code, e
                        )
                        errors += 1
                        results_by_cancer[code] = f"error: {str(e)[:100]}"

            return {
                "total_generated": total_generated,
                "cancer_types_processed": total,
                "errors": errors,
                "results_by_cancer": results_by_cancer,
            }

        result = _run_async(_generate())
        logger.info(
            "Full hypothesis generation complete: %d total hypotheses, %d errors",
            result["total_generated"],
            result["errors"],
        )
        return result

    except Exception as exc:
        logger.error("Full hypothesis generation failed: %s", exc)
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    max_retries=2,
    name="app.tasks.generate.rescore_hypotheses",
)
def rescore_hypotheses(self, preset_name=None):
    """Rescore all existing hypotheses with current or specified weights.

    Args:
        preset_name: Optional name of scoring preset to use.
                     If None, uses the active default preset.
    """
    logger.info(
        "Starting hypothesis rescoring (preset=%s)",
        preset_name or "active_default",
    )
    try:

        async def _rescore():
            from app.database import async_session_factory
            from app.services.hypothesis_engine import HypothesisEngine
            from app.services.scoring_config import ScoringConfig

            engine = HypothesisEngine()
            config = ScoringConfig()

            async with async_session_factory() as session:
                weights = None
                if preset_name:
                    preset = await config.get_preset(preset_name, session)
                    if preset:
                        weights = preset["weights"]
                    else:
                        logger.warning(
                            "Preset '%s' not found, using active default",
                            preset_name,
                        )

                return await engine.rescore_all(session, weights=weights)

        result = _run_async(_rescore())
        logger.info(
            "Hypothesis rescoring complete: %d/%d rescored",
            result["rescored"],
            result["total"],
        )
        return result

    except Exception as exc:
        logger.error("Hypothesis rescoring failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    max_retries=2,
    name="app.tasks.generate.generate_combinations_for_cancer",
)
def generate_combinations_for_cancer(
    self, cancer_type_id, min_single_score=30.0, max_pairs=200
):
    """Generate drug combination hypotheses for a cancer type.

    Pairs top single-drug hypotheses and scores each pair for synergy
    based on pathway complementarity, target non-overlap, synthetic
    lethality, safety compatibility, and clinical precedent.
    """
    logger.info(
        "Starting combination generation for cancer_type_id=%d "
        "(min_single_score=%.1f, max_pairs=%d)",
        cancer_type_id, min_single_score, max_pairs,
    )
    try:

        async def _generate():
            from app.database import async_session_factory
            from app.services.combination_engine import CombinationEngine

            engine = CombinationEngine()
            async with async_session_factory() as session:
                results = await engine.generate_combinations(
                    cancer_type_id, session,
                    min_single_score=min_single_score,
                    max_pairs=max_pairs,
                )
                return {
                    "cancer_type_id": cancer_type_id,
                    "combinations_generated": len(results),
                    "top_synergies": [
                        {
                            "drug_a": r["drug_a"]["name"],
                            "drug_b": r["drug_b"]["name"],
                            "synergy_score": r["synergy_score"],
                            "classification": r["synergy_classification"],
                        }
                        for r in results[:10]
                    ],
                }

        result = _run_async(_generate())
        logger.info(
            "Combination generation complete for cancer_type_id=%d: %d combinations",
            cancer_type_id, result["combinations_generated"],
        )
        return result

    except Exception as exc:
        logger.error(
            "Combination generation failed for cancer_type_id=%d: %s",
            cancer_type_id, exc,
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    max_retries=1,
    name="app.tasks.generate.generate_all_combinations",
)
def generate_all_combinations(self, min_single_score=30.0, max_pairs=200):
    """Generate drug combination hypotheses for all cancer types."""
    logger.info(
        "Starting full combination generation "
        "(min_single_score=%.1f, max_pairs=%d)",
        min_single_score, max_pairs,
    )
    try:

        async def _generate():
            from sqlalchemy import select

            from app.database import async_session_factory
            from app.models.cancer_type import CancerType
            from app.services.combination_engine import CombinationEngine

            engine = CombinationEngine()
            total_generated = 0
            errors = 0

            async with async_session_factory() as session:
                cancer_result = await session.execute(
                    select(CancerType.id, CancerType.tcga_code)
                )
                cancer_types = cancer_result.all()
                total = len(cancer_types)

                for i, (cid, code) in enumerate(cancer_types):
                    logger.info(
                        "Generating combinations for %s (%d/%d)",
                        code, i + 1, total,
                    )
                    try:
                        results = await engine.generate_combinations(
                            cid, session,
                            min_single_score=min_single_score,
                            max_pairs=max_pairs,
                        )
                        total_generated += len(results)
                    except Exception as e:
                        logger.error(
                            "Failed generating combinations for %s: %s", code, e
                        )
                        errors += 1

            return {
                "total_generated": total_generated,
                "cancer_types_processed": total,
                "errors": errors,
            }

        result = _run_async(_generate())
        logger.info(
            "Full combination generation complete: %d total, %d errors",
            result["total_generated"], result["errors"],
        )
        return result

    except Exception as exc:
        logger.error("Full combination generation failed: %s", exc)
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))
