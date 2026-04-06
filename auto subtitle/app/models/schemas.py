"""Pydantic schemas used across the API, services, and workers."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enumerations ─────────────────────────────────────────────────

class JobStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class SubtitleFormat(str, enum.Enum):
    SRT = "srt"
    ASS = "ass"


class StylePreset(str, enum.Enum):
    NETFLIX = "netflix"
    MINIMAL = "minimal"
    CUSTOM = "custom"


class SyncMode(str, enum.Enum):
    OFF = "off"
    LIGHT = "light"
    FULL = "full"


# ── Request schemas ──────────────────────────────────────────────

class SubtitleRequest(BaseModel):
    file_id: str = Field(..., description="Uploaded file identifier")
    language: Optional[str] = Field(None, description="ISO 639-1 code, None=auto")
    output_format: SubtitleFormat = SubtitleFormat.SRT
    style_preset: StylePreset = StylePreset.NETFLIX
    model_size: Optional[str] = Field(None, description="Whisper model override")
    sync_correction: bool = Field(True, description="Apply AI sync correction")
    word_timestamps: bool = Field(True)
    max_chars_per_line: Optional[int] = None
    max_lines: Optional[int] = None


class BatchRequest(BaseModel):
    file_ids: List[str]
    language: Optional[str] = None
    output_format: SubtitleFormat = SubtitleFormat.SRT
    style_preset: StylePreset = StylePreset.NETFLIX
    sync_correction: bool = True


# ── Response schemas ─────────────────────────────────────────────

class UploadResponse(BaseModel):
    file_id: str
    filename: str
    size_bytes: int
    duration_seconds: Optional[float] = None
    message: str = "File uploaded successfully"


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    message: str = ""


class JobStatusResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: float = Field(0.0, ge=0.0, le=100.0)
    message: str = ""
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class BatchJobResponse(BaseModel):
    batch_id: str
    jobs: List[JobResponse]
    message: str = "Batch processing started"


class DownloadInfo(BaseModel):
    file_id: str
    filename: str
    download_url: str


# ── Internal data models ─────────────────────────────────────────

class TranscriptionSegment(BaseModel):
    """A single segment returned by the transcription engine."""
    id: int = 0
    start: float
    end: float
    text: str
    words: Optional[List["WordTimestamp"]] = None
    confidence: float = 0.0


class WordTimestamp(BaseModel):
    word: str
    start: float
    end: float
    probability: float = 0.0


class TranscriptionResult(BaseModel):
    language: str = "en"
    language_probability: float = 0.0
    duration: float = 0.0
    segments: List[TranscriptionSegment] = []


class RealtimeSubtitleMessage(BaseModel):
    """WebSocket outgoing message."""
    text: str
    start: float
    end: float
    is_partial: bool = False
    language: Optional[str] = None


class RealtimeConfig(BaseModel):
    """WebSocket incoming configuration."""
    language: Optional[str] = None
    model_size: str = "base"
    sample_rate: int = 16000
