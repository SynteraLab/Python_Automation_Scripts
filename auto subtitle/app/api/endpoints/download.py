"""File download endpoint."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import get_settings

router = APIRouter(tags=["Download"])


@router.get("/download/{filename}")
async def download_file(filename: str) -> FileResponse:
    """Download a generated subtitle file."""
    settings = get_settings()
    path = settings.output_dir / filename

    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    media_map = {
        ".srt": "application/x-subrip",
        ".ass": "text/x-ssa",
        ".vtt": "text/vtt",
    }
    media_type = media_map.get(path.suffix, "application/octet-stream")

    return FileResponse(
        path=str(path),
        filename=filename,
        media_type=media_type,
    )