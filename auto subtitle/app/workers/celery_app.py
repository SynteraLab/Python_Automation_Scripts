"""Celery application configuration."""

from __future__ import annotations

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "subtitle_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_soft_time_limit=settings.task_soft_timeout,
    task_time_limit=settings.task_hard_timeout,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=50,      # restart workers periodically (free GPU mem)
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="subtitles",
    task_routes={
        "app.workers.tasks.*": {"queue": "subtitles"},
    },
)

celery_app.autodiscover_tasks(["app.workers"])