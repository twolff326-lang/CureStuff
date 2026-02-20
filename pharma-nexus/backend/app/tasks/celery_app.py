from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "pharma_nexus",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.tasks.ingest.*": {"queue": "ingestion"},
        "app.tasks.analyze.*": {"queue": "analysis"},
        "app.tasks.generate.*": {"queue": "generation"},
        "app.tasks.gnn.*": {"queue": "analysis"},
        "app.tasks.monitor.*": {"queue": "ingestion"},
        "app.tasks.pipeline.*": {"queue": "analysis"},
    },
    # Celery Beat schedule for automated tasks
    beat_schedule={
        "literature-monitor-weekly": {
            "task": "app.tasks.monitor.check_literature",
            "schedule": crontab(hour=3, minute=0, day_of_week="monday"),
            "kwargs": {"days_back": 7, "max_hypotheses": 200},
        },
        "auto-monitor-top-hypotheses-daily": {
            "task": "app.tasks.monitor.auto_monitor_top",
            "schedule": crontab(hour=4, minute=0),
            "kwargs": {"min_score": 30.0, "limit": 200},
        },
    },
)

# Auto-discover task modules
celery_app.autodiscover_tasks(["app.tasks"])
