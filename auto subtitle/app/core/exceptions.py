"""Project-wide custom exceptions."""

from __future__ import annotations


class SubtitleError(Exception):
    """Base exception for subtitle system."""

    def __init__(self, message: str = "Subtitle processing error", code: int = 500):
        self.message = message
        self.code = code
        super().__init__(self.message)


class TranscriptionError(SubtitleError):
    """Raised when the transcription engine fails."""

    def __init__(self, message: str = "Transcription failed"):
        super().__init__(message, code=500)


class FileProcessingError(SubtitleError):
    """Raised on file I/O or conversion errors."""

    def __init__(self, message: str = "File processing error"):
        super().__init__(message, code=422)


class JobNotFoundError(SubtitleError):
    """Raised when a job ID cannot be found in the store."""

    def __init__(self, job_id: str):
        super().__init__(f"Job {job_id} not found", code=404)


class UnsupportedFormatError(SubtitleError):
    """Raised for unsupported media / subtitle formats."""

    def __init__(self, fmt: str):
        super().__init__(f"Unsupported format: {fmt}", code=422)


class AudioExtractionError(SubtitleError):
    """Raised when FFmpeg audio extraction fails."""

    def __init__(self, message: str = "Audio extraction failed"):
        super().__init__(message, code=500)