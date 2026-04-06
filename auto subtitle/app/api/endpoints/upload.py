"""File upload endpoint."""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from app.models.schemas import UploadResponse
from app.services.upload_service import UploadService

router = APIRouter(tags=["Upload"])


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)) -> UploadResponse:
    """Upload a video or audio file for subtitle generation."""
    svc = UploadService()
    return await svc.handle_upload(file)