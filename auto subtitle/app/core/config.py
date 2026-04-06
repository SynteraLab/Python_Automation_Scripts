"""Centralised settings loaded from environment / .env file."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings


_ROOT = Path(__file__).resolve().parents[2]  # project root


class Settings(BaseSettings):
    # ── Application ──────────────────────────────────────────────
    app_name: str = "Auto Subtitle AI"
    app_version: str = "1.0.0"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"

    # ── Redis ────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Celery ───────────────────────────────────────────────────
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── Whisper / faster-whisper ─────────────────────────────────
    whisper_model_size: str = "base"  # tiny | base | small | medium | large-v3
    whisper_device: str = "auto"      # auto | cpu | cuda
    whisper_compute_type: str = "auto"  # auto | int8 | float16 | float32
    whisper_language: Optional[str] = None  # None → auto-detect
    whisper_beam_size: int = 5
    whisper_word_timestamps: bool = True

    # ── Storage ──────────────────────────────────────────────────
    upload_dir: Path = _ROOT / "storage" / "uploads"
    output_dir: Path = _ROOT / "storage" / "outputs"
    temp_dir: Path = _ROOT / "storage" / "temp"
    max_upload_size_mb: int = 500

    # ── Workers ──────────────────────────────────────────────────
    worker_concurrency: int = 2
    max_retries: int = 3
    task_soft_timeout: int = 1800
    task_hard_timeout: int = 3600

    # ── Real-time ────────────────────────────────────────────────
    rt_chunk_seconds: float = 3.0
    rt_sample_rate: int = 16000
    rt_channels: int = 1

    # ── Subtitle defaults ────────────────────────────────────────
    default_format: str = "srt"          # srt | ass
    default_style_preset: str = "netflix"  # netflix | minimal | custom
    max_chars_per_line: int = 42
    max_lines: int = 2

    # ── FFmpeg ───────────────────────────────────────────────────
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    hard_subtitle_encoder: str = "auto"  # auto | libx264 | videotoolbox
    hard_subtitle_crf: int = 18
    hard_subtitle_preset: str = "medium"
    hard_subtitle_hw_bitrate: str = "6M"
    hard_subtitle_hw_maxrate: str = "10M"
    hard_subtitle_hw_bufsize: str = "20M"
    hard_subtitle_validate_output: bool = True

    model_config = {
        "env_file": ".env",
        "env_prefix": "SUBTITLE_",
        "extra": "ignore",
    }

    def ensure_dirs(self) -> None:
        for d in (self.upload_dir, self.output_dir, self.temp_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
