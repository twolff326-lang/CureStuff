"""Celery tasks for real-time literature monitoring.

Tasks:
  - check_literature: Poll PubMed for new papers affecting tracked hypotheses
  - auto_monitor_top: Auto-enable monitoring for top-scoring hypotheses

Scheduled via Celery Beat (configured in celery_app.py).
"""

import logging

from app.tasks.celery_app import celery_app
from app.tasks.utils import run_async, task_session

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, name="app.tasks.monitor.check_literature")
def check_literature(self, days_back=7, max_hypotheses=100):
    """Poll PubMed for new papers and rescore affected hypotheses.

    This is the main monitoring task, intended to run on a schedule
    (e.g., weekly via Celery Beat).
    """
    logger.info("Starting literature monitoring check (days_back=%d)", days_back)
    try:
        async def _check():
            from app.services.literature_monitor import LiteratureMonitor

            async with task_session() as session:
                monitor = LiteratureMonitor(session)
                result = await monitor.check_for_new_literature(
                    days_back=days_back,
                    max_hypotheses=max_hypotheses,
                )
                await session.commit()
                return result

        result = run_async(_check())
        logger.info("Literature monitoring complete: %s", result)
        return result

    except Exception as exc:
        logger.error("Literature monitoring failed: %s", exc)
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(bind=True, name="app.tasks.monitor.auto_monitor_top")
def auto_monitor_top(self, min_score=30.0, limit=200):
    """Auto-enable monitoring for all hypotheses above a score threshold.

    Creates MonitoringConfig entries for top hypotheses that aren't
    already being monitored. Intended to run once after hypothesis
    generation, or periodically to pick up new high-scoring pairs.
    """
    logger.info("Auto-enabling monitoring for hypotheses with score >= %.1f", min_score)
    try:
        async def _auto_monitor():
            from sqlalchemy import select

            from app.models.hypothesis import Hypothesis
            from app.models.literature_alert import MonitoringConfig
            from app.services.literature_monitor import LiteratureMonitor

            async with task_session() as session:
                # Get top hypotheses
                result = await session.execute(
                    select(Hypothesis.id).where(
                        Hypothesis.composite_score >= min_score
                    ).order_by(Hypothesis.composite_score.desc()).limit(limit)
                )
                hyp_ids = [r[0] for r in result.all()]

                # Get already-monitored IDs
                existing = await session.execute(
                    select(MonitoringConfig.hypothesis_id)
                )
                already_monitored = set(r[0] for r in existing.all())

                # Enable monitoring for new ones
                monitor = LiteratureMonitor(session)
                enabled = 0
                for hid in hyp_ids:
                    if hid not in already_monitored:
                        await monitor.enable_monitoring(hid)
                        enabled += 1

                await session.commit()
                return {"enabled": enabled, "already_monitored": len(already_monitored)}

        result = run_async(_auto_monitor())
        logger.info("Auto-monitoring setup complete: %s", result)
        return result

    except Exception as exc:
        logger.error("Auto-monitoring setup failed: %s", exc)
        return {"status": "error", "error": str(exc)}
