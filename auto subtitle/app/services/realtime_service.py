"""Real-time (streaming) subtitle service for WebSocket & CLI."""

from __future__ import annotations

import asyncio
import io
import struct
import threading
from collections import deque
from typing import AsyncIterator, Callable, List, Optional

import numpy as np

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.engines.transcription import TranscriptionEngine, get_transcription_engine
from app.models.schemas import RealtimeSubtitleMessage, TranscriptionSegment

logger = get_logger(__name__)


class RealtimeSession:
    """Manages a single real-time transcription session."""

    def __init__(
        self,
        engine: TranscriptionEngine,
        sample_rate: int = 16000,
        chunk_seconds: float = 3.0,
        language: Optional[str] = None,
    ):
        self._engine = engine
        self._sr = sample_rate
        self._chunk_seconds = chunk_seconds
        self._language = language
        self._buffer = io.BytesIO()
        self._time_offset: float = 0.0
        self._lock = threading.Lock()

    def feed_audio(self, pcm_bytes: bytes) -> List[RealtimeSubtitleMessage]:
        """Feed raw PCM int16 audio bytes, return any completed subtitles."""
        with self._lock:
            self._buffer.write(pcm_bytes)
            buffered_samples = self._buffer.tell() // 2  # 16-bit = 2 bytes
            min_samples = int(self._sr * self._chunk_seconds)

            if buffered_samples < min_samples:
                return []

            # Process buffer
            raw = self._buffer.getvalue()
            self._buffer = io.BytesIO()

        # Decode int16 → float32
        n_samples = len(raw) // 2
        samples = struct.unpack(f"<{n_samples}h", raw)
        audio = np.array(samples, dtype=np.float32) / 32768.0

        # Transcribe
        result = self._engine.transcribe_array(
            audio, self._sr, self._language
        )

        messages: List[RealtimeSubtitleMessage] = []
        for seg in result.segments:
            msg = RealtimeSubtitleMessage(
                text=seg.text,
                start=round(self._time_offset + seg.start, 3),
                end=round(self._time_offset + seg.end, 3),
                is_partial=False,
                language=result.language,
            )
            messages.append(msg)

        self._time_offset += n_samples / self._sr
        return messages

    def flush(self) -> List[RealtimeSubtitleMessage]:
        """Process any remaining audio in the buffer."""
        with self._lock:
            raw = self._buffer.getvalue()
            self._buffer = io.BytesIO()

        if len(raw) < 2 * self._sr * 0.5:  # skip if < 0.5s
            return []

        n_samples = len(raw) // 2
        samples = struct.unpack(f"<{n_samples}h", raw)
        audio = np.array(samples, dtype=np.float32) / 32768.0

        result = self._engine.transcribe_array(
            audio, self._sr, self._language
        )

        messages = [
            RealtimeSubtitleMessage(
                text=seg.text,
                start=round(self._time_offset + seg.start, 3),
                end=round(self._time_offset + seg.end, 3),
                is_partial=False,
                language=result.language,
            )
            for seg in result.segments
        ]
        self._time_offset += n_samples / self._sr
        return messages

    def reset(self) -> None:
        with self._lock:
            self._buffer = io.BytesIO()
        self._time_offset = 0.0


class RealtimeServiceFactory:
    """Create RealtimeSession objects with proper engine."""

    @staticmethod
    def create_session(
        model_size: Optional[str] = None,
        language: Optional[str] = None,
        sample_rate: Optional[int] = None,
    ) -> RealtimeSession:
        settings = get_settings()
        engine = get_transcription_engine(model_size)
        return RealtimeSession(
            engine=engine,
            sample_rate=sample_rate or settings.rt_sample_rate,
            chunk_seconds=settings.rt_chunk_seconds,
            language=language,
        )