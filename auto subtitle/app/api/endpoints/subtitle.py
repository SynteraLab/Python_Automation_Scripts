"""Subtitle generation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_job_service
from app.models.schemas import (
    BatchJobResponse,
    BatchRequest,
    JobResponse,
    JobStatus,
    SubtitleRequest,
)
from app.services.job_service import JobService
from app.workers.pipeline import create_batch_job, create_subtitle_job

router = APIRouter(tags=["Subtitles"])


@router.post("/generate-subtitle", response_model=JobResponse)
async def generate_subtitle(
    req: SubtitleRequest,
    js: JobService = Depends(get_job_service),
) -> JobResponse:
    """Start asynchronous subtitle generation."""
    job_id = await js.create_job(
        metadata={"file_id": req.file_id, "format": req.output_format.value}
    )

    create_subtitle_job(
        job_id=job_id,
        file_id=req.file_id,
        language=req.language,
        output_format=req.output_format.value,
        style_preset=req.style_preset.value,
        sync_correction=req.sync_correction,
        model_size=req.model_size,
    )

    return JobResponse(
        job_id=job_id,
        status=JobStatus.PENDING,
        message="Subtitle generation job queued",
    )


@router.post("/generate-batch", response_model=BatchJobResponse)
async def generate_batch(
    req: BatchRequest,
    js: JobService = Depends(get_job_service),
) -> BatchJobResponse:
    """Start a batch subtitle generation job."""
    batch_id = await js.create_job(metadata={"type": "batch"})

    job_specs = []
    jobs_response = []
    for fid in req.file_ids:
        jid = await js.create_job(metadata={"file_id": fid, "batch": batch_id})
        job_specs.append(
            {
                "job_id": jid,
                "file_id": fid,
                "language": req.language,
                "output_format": req.output_format.value,
                "style_preset": req.style_preset.value,
                "sync_correction": req.sync_correction,
            }
        )
        jobs_response.append(
            JobResponse(job_id=jid, status=JobStatus.PENDING)
        )

    create_batch_job(batch_id, job_specs)

    return BatchJobResponse(
        batch_id=batch_id,
        jobs=jobs_response,
        message=f"Batch of {len(req.file_ids)} files queued",
    )