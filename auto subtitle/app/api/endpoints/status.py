"""Job status endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies import get_job_service
from app.models.schemas import JobStatusResponse
from app.services.job_service import JobService

router = APIRouter(tags=["Status"])


@router.get("/status/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: str,
    js: JobService = Depends(get_job_service),
) -> JobStatusResponse:
    """Query the status and progress of a subtitle generation job."""
    return await js.get(job_id)