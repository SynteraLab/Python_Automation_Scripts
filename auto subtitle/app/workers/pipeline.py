"""Higher-level pipeline helpers for composing Celery chains."""

from __future__ import annotations

from typing import List, Optional

from celery import chain, group

from app.workers.tasks import batch_generate, generate_subtitle


def create_subtitle_job(
    job_id: str,
    file_id: str,
    language: Optional[str] = None,
    output_format: str = "srt",
    style_preset: str = "netflix",
    sync_correction: bool = True,
    model_size: Optional[str] = None,
):
    """Create and dispatch a single subtitle job."""
    return generate_subtitle.delay(
        job_id=job_id,
        file_id=file_id,
        language=language,
        output_format=output_format,
        style_preset=style_preset,
        model_size=model_size,
        sync_correction=sync_correction,
    )


def create_batch_job(batch_id: str, job_specs: List[dict]):
    """Dispatch a batch of subtitle jobs."""
    return batch_generate.delay(batch_id=batch_id, jobs=job_specs)