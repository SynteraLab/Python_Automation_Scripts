"""Handles file uploads with validation."""

from __future__ import annotations

from pathlib import Path

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import FileProcessingError
from app.core.logging_config import get_logger
from app.models.schemas import UploadResponse
from app.utils.ffmpeg_wrapper import FFmpegWrapper
from app.utils.file_manager import FileManager

logger = get_logger(__name__)


class UploadService:
    def __init__(self):
        self._fm = FileManager()
        self._ff = FFmpegWrapper()

    async def handle_upload(self, file: UploadFile) -> UploadResponse:
        settings = get_settings()

        if not file.filename:
            raise FileProcessingError("No filename provided")

        content = await file.read()
        size = len(content)

        if size > settings.max_upload_size_mb * 1024 * 1024:
            raise FileProcessingError(
                f"File too large ({size // (1024*1024)}MB). "
                f"Max: {settings.max_upload_size_mb}MB"
            )

        file_id, path = self._fm.save_upload(content, file.filename)

        # Probe duration (best-effort)
        duration = None
        try:
            duration = self._ff.get_duration(path)
        except Exception:
            logger.warning("Could not probe duration for %s", path.name)

        return UploadResponse(
            file_id=file_id,
            filename=file.filename,
            size_bytes=size,
            duration_seconds=duration,
        )