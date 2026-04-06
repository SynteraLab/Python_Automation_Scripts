"""Celery tasks — the actual units of work."""

from __future__ import annotations

from typing import Optional

from celery import Task
from celery.utils.log import get_task_logger

from app.core.config import get_settings
from app.models.schemas import JobStatus, StylePreset, SubtitleFormat
from app.services.job_service import JobServiceSync
from app.workers.celery_app import celery_app

task_logger = get_task_logger(__name__)


def _get_job_service() -> JobServiceSync:
    settings = get_settings()
    return JobServiceSync(settings.redis_url)


class SubtitleTask(Task):
    """Base task with automatic retry and error reporting."""

    autoretry_for = (Exception,)
    max_retries = 3
    retry_backoff = True
    retry_backoff_max = 120


@celery_app.task(
    bind=True,
    base=SubtitleTask,
    name="app.workers.tasks.generate_subtitle",
)
def generate_subtitle(
    self: Task,
    job_id: str,
    file_id: str,
    language: Optional[str] = None,
    output_format: str = "srt",
    style_preset: str = "netflix",
    model_size: Optional[str] = None,
    sync_correction: bool = True,
) -> dict:
    """Full subtitle generation pipeline as a Celery task."""
    js = _get_job_service()

    try:
        js.update(job_id, status=JobStatus.PROCESSING, progress=5, message="Starting…")

        # Import here so the worker process loads models lazily
        from app.services.subtitle_service import SubtitleService

        def _progress(pct: float, msg: str) -> None:
            js.update(job_id, progress=pct, message=msg)

        svc = SubtitleService(model_size=model_size)
        output_path = svc.generate_from_file_id(
            file_id=file_id,
            language=language,
            fmt=SubtitleFormat(output_format),
            style_preset=StylePreset(style_preset),
            apply_sync=sync_correction,
            progress_callback=_progress,
        )

        result = {
            "output_file": output_path.name,
            "output_format": output_format,
        }
        js.update(
            job_id,
            status=JobStatus.COMPLETED,
            progress=100,
            message="Subtitle generation complete",
            result=result,
        )
        task_logger.info("Job %s completed → %s", job_id, output_path.name)
        return result

    except Exception as exc:
        task_logger.exception("Job %s failed", job_id)
        js.update(
            job_id,
            status=JobStatus.FAILED,
            error=str(exc),
            message="Job failed",
        )
        raise


@celery_app.task(
    bind=True,
    base=SubtitleTask,
    name="app.workers.tasks.batch_generate",
)
def batch_generate(
    self: Task,
    batch_id: str,
    jobs: list[dict],
) -> dict:
    """Dispatch individual subtitle jobs and track batch progress."""
    js = _get_job_service()
    js.update(batch_id, status=JobStatus.PROCESSING, message="Dispatching batch jobs…")

    dispatched = []
    for job_spec in jobs:
        task = generate_subtitle.delay(
            job_id=job_spec["job_id"],
            file_id=job_spec["file_id"],
            language=job_spec.get("language"),
            output_format=job_spec.get("output_format", "srt"),
            style_preset=job_spec.get("style_preset", "netflix"),
            sync_correction=job_spec.get("sync_correction", True),
        )
        dispatched.append(task.id)

    result = {"dispatched_tasks": len(dispatched)}
    js.update(
        batch_id,
        status=JobStatus.COMPLETED,
        progress=100,
        message=f"Dispatched {len(dispatched)} jobs",
        result=result,
    )
    return result