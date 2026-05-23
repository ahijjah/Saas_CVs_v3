from celery import Celery
from celery.schedules import crontab

from config import get_settings

settings = get_settings()

celery_app = Celery(
    "cv_analyzer",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "workers.cv_score",
        "workers.cv_intake",
        "workers.criteria_worker",
        "workers.notification_worker",
        "workers.watchdog",
        "workers.backfill_canonical_fp",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)

celery_app.conf.beat_schedule = {
    "poll-imap-inbox": {
        "task": "workers.cv_intake.poll_imap_inbox",
        "schedule": settings.imap_poll_interval,  # seconds
    },
    "reset-stuck-scoring-tasks": {
        "task": "workers.watchdog.reset_stuck_scoring_tasks",
        "schedule": 300,  # every 5 minutes
    },
}
