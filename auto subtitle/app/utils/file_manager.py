"""File upload, storage, and cleanup utilities."""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import List, Optional

from app.core.config import get_settings
from app.core.exceptions import FileProcessingError
from app.core.logging_config import get_logger

logger = get_logger(__name__)

ALLOWED_MEDIA = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv",  # video
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a",  # audio
}


class FileManager:
    """Manage uploaded and generated files on disk."""

    def __init__(self) -> None:
        self._settings = get_settings()

    def generate_file_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def save_upload(self, content: bytes, original_name: str) -> tuple[str, Path]:
        """Save uploaded bytes, return (file_id, path)."""
        ext = Path(original_name).suffix.lower()
        if ext not in ALLOWED_MEDIA:
            raise FileProcessingError(
                f"Unsupported file type '{ext}'. Allowed: {ALLOWED_MEDIA}"
            )

        file_id = self.generate_file_id()
        dest = self._settings.upload_dir / f"{file_id}{ext}"
        dest.write_bytes(content)
        logger.info("Saved upload %s → %s (%d bytes)", original_name, dest.name, len(content))
        return file_id, dest

    def get_upload_path(self, file_id: str) -> Path:
        """Resolve an uploaded file by its ID (any extension)."""
        for path in self._settings.upload_dir.iterdir():
            if path.stem == file_id:
                return path
        raise FileProcessingError(f"Upload not found: {file_id}")

    def get_output_path(self, file_id: str, ext: str) -> Path:
        if not ext.startswith("."):
            ext = f".{ext}"
        return self._settings.output_dir / f"{file_id}{ext}"

    def get_temp_path(self, name: str) -> Path:
        return self._settings.temp_dir / name

    def list_outputs(self, file_id: str) -> List[Path]:
        return list(self._settings.output_dir.glob(f"{file_id}*"))

    def cleanup_temp(self, file_id: str) -> None:
        for f in self._settings.temp_dir.glob(f"{file_id}*"):
            f.unlink(missing_ok=True)

    def cleanup_all_temp(self) -> None:
        for f in self._settings.temp_dir.iterdir():
            if f.is_file():
                f.unlink(missing_ok=True)

    @staticmethod
    def scan_folder(
        folder: Path, extensions: Optional[set[str]] = None
    ) -> List[Path]:
        """Recursively find media files in a folder."""
        exts = extensions or ALLOWED_MEDIA
        return sorted(
            p for p in folder.rglob("*") if p.suffix.lower() in exts
        )