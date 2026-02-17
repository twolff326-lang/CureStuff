"""Celery tasks for report generation and data export.

Tasks:
  - generate_hypothesis_report_task: PDF report for a single hypothesis
  - generate_cancer_summary_task: Cancer type summary PDF
  - generate_novel_discoveries_task: Novel discoveries report PDF
  - generate_executive_summary_task: Executive summary PDF
  - generate_all_reports: Generate all report types (batch)
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
    time_limit=600,
    name="app.tasks.reports.generate_hypothesis_report_task",
)
def generate_hypothesis_report_task(self, hypothesis_id: int):
    """Generate PDF report for a single hypothesis."""
    logger.info("Generating hypothesis report for id=%d", hypothesis_id)
    try:

        async def _generate():
            from app.database import async_session_factory
            from app.services.report_generator import ReportGenerator

            generator = ReportGenerator()
            async with async_session_factory() as session:
                filepath = await generator.generate_hypothesis_report(
                    hypothesis_id, session
                )
                await session.commit()
            return filepath

        filepath = _run_async(_generate())
        logger.info(
            "Hypothesis report generated: %s (id=%d)", filepath, hypothesis_id
        )
        return {"hypothesis_id": hypothesis_id, "file_path": filepath}

    except Exception as exc:
        logger.error(
            "Hypothesis report generation failed for id=%d: %s",
            hypothesis_id,
            exc,
        )
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    max_retries=2,
    time_limit=1200,
    name="app.tasks.reports.generate_cancer_summary_task",
)
def generate_cancer_summary_task(self, cancer_type_id: int):
    """Generate cancer type summary PDF."""
    logger.info("Generating cancer summary report for id=%d", cancer_type_id)
    try:

        async def _generate():
            from app.database import async_session_factory
            from app.services.report_generator import ReportGenerator

            generator = ReportGenerator()
            async with async_session_factory() as session:
                filepath = await generator.generate_cancer_summary_report(
                    cancer_type_id, session
                )
                await session.commit()
            return filepath

        filepath = _run_async(_generate())
        logger.info(
            "Cancer summary report generated: %s (id=%d)", filepath, cancer_type_id
        )
        return {"cancer_type_id": cancer_type_id, "file_path": filepath}

    except Exception as exc:
        logger.error(
            "Cancer summary report failed for id=%d: %s", cancer_type_id, exc
        )
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    max_retries=2,
    time_limit=1800,
    name="app.tasks.reports.generate_novel_discoveries_task",
)
def generate_novel_discoveries_task(
    self, min_score: int = 45, min_novelty: int = 60
):
    """Generate novel discoveries report PDF."""
    logger.info(
        "Generating novel discoveries report (min_score=%d, min_novelty=%d)",
        min_score,
        min_novelty,
    )
    try:

        async def _generate():
            from app.database import async_session_factory
            from app.services.report_generator import ReportGenerator

            generator = ReportGenerator()
            async with async_session_factory() as session:
                filepath = await generator.generate_novel_discoveries_report(
                    session, min_score=min_score, min_novelty=min_novelty
                )
                await session.commit()
            return filepath

        filepath = _run_async(_generate())
        logger.info("Novel discoveries report generated: %s", filepath)
        return {"file_path": filepath}

    except Exception as exc:
        logger.error("Novel discoveries report failed: %s", exc)
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    max_retries=2,
    time_limit=600,
    name="app.tasks.reports.generate_executive_summary_task",
)
def generate_executive_summary_task(self):
    """Generate executive summary PDF."""
    logger.info("Generating executive summary report")
    try:

        async def _generate():
            from app.database import async_session_factory
            from app.services.report_generator import ReportGenerator

            generator = ReportGenerator()
            async with async_session_factory() as session:
                filepath = await generator.generate_executive_summary(session)
                await session.commit()
            return filepath

        filepath = _run_async(_generate())
        logger.info("Executive summary report generated: %s", filepath)
        return {"file_path": filepath}

    except Exception as exc:
        logger.error("Executive summary report failed: %s", exc)
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))


@celery_app.task(
    bind=True,
    max_retries=1,
    time_limit=3600,
    name="app.tasks.reports.generate_all_reports",
)
def generate_all_reports(self):
    """Generate all report types.

    1. Executive summary
    2. Novel discoveries report
    3. Cancer summary for each cancer type
    4. Individual reports for top 50 hypotheses
    """
    logger.info("Starting batch report generation")
    try:

        async def _generate_all():
            from sqlalchemy import select

            from app.database import async_session_factory
            from app.models.cancer_type import CancerType
            from app.models.hypothesis import Hypothesis
            from app.services.report_generator import ReportGenerator

            generator = ReportGenerator()
            results = {
                "executive_summary": None,
                "novel_discoveries": None,
                "cancer_summaries": {},
                "hypothesis_reports": {},
                "errors": [],
            }

            async with async_session_factory() as session:
                # 1. Executive summary
                try:
                    filepath = await generator.generate_executive_summary(session)
                    await session.commit()
                    results["executive_summary"] = filepath
                    logger.info("Executive summary generated: %s", filepath)
                except Exception as e:
                    logger.error("Executive summary failed: %s", e)
                    results["errors"].append(f"executive_summary: {e}")

                # 2. Novel discoveries
                try:
                    filepath = await generator.generate_novel_discoveries_report(
                        session
                    )
                    await session.commit()
                    results["novel_discoveries"] = filepath
                    logger.info("Novel discoveries report generated: %s", filepath)
                except Exception as e:
                    logger.error("Novel discoveries failed: %s", e)
                    results["errors"].append(f"novel_discoveries: {e}")

                # 3. Cancer summaries
                cancer_result = await session.execute(
                    select(CancerType.id, CancerType.tcga_code)
                )
                cancer_types = cancer_result.all()
                for cid, code in cancer_types:
                    try:
                        filepath = await generator.generate_cancer_summary_report(
                            cid, session
                        )
                        await session.commit()
                        results["cancer_summaries"][code] = filepath
                        logger.info(
                            "Cancer summary generated for %s: %s", code, filepath
                        )
                    except Exception as e:
                        logger.error("Cancer summary failed for %s: %s", code, e)
                        results["errors"].append(f"cancer_summary_{code}: {e}")

                # 4. Top 50 hypothesis reports
                top_hypotheses = (
                    await session.execute(
                        select(Hypothesis.id)
                        .order_by(Hypothesis.composite_score.desc())
                        .limit(50)
                    )
                ).scalars().all()

                for hid in top_hypotheses:
                    try:
                        filepath = await generator.generate_hypothesis_report(
                            hid, session
                        )
                        await session.commit()
                        results["hypothesis_reports"][hid] = filepath
                        logger.info(
                            "Hypothesis report generated for id=%d: %s",
                            hid,
                            filepath,
                        )
                    except Exception as e:
                        logger.error(
                            "Hypothesis report failed for id=%d: %s", hid, e
                        )
                        results["errors"].append(f"hypothesis_{hid}: {e}")

            return {
                "executive_summary": results["executive_summary"],
                "novel_discoveries": results["novel_discoveries"],
                "cancer_summaries_count": len(results["cancer_summaries"]),
                "hypothesis_reports_count": len(results["hypothesis_reports"]),
                "errors_count": len(results["errors"]),
                "errors": [str(e)[:200] for e in results["errors"][:20]],
            }

        result = _run_async(_generate_all())
        logger.info(
            "Batch report generation complete: %d cancer summaries, "
            "%d hypothesis reports, %d errors",
            result["cancer_summaries_count"],
            result["hypothesis_reports_count"],
            result["errors_count"],
        )
        return result

    except Exception as exc:
        logger.error("Batch report generation failed: %s", exc)
        raise self.retry(exc=exc, countdown=120 * (2 ** self.request.retries))
