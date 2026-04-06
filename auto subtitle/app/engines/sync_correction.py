"""AI-based subtitle synchronisation correction.

Two strategies:
  1. Silence-based  — detect silent regions in audio, snap subtitle boundaries
  2. Word-alignment — use word-level timestamps from Whisper for precise sync
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from app.core.logging_config import get_logger
from app.models.schemas import TranscriptionResult, TranscriptionSegment

logger = get_logger(__name__)


class SyncCorrector:
    """Correct subtitle timing by analysing the source audio."""

    def __init__(
        self,
        silence_threshold_db: float = -35.0,
        min_silence_ms: int = 300,
        padding_ms: int = 80,
        min_gap_ms: int = 80,
    ):
        self.silence_threshold = 10 ** (silence_threshold_db / 20.0)
        self.min_silence_samples = 0  # set when sample_rate is known
        self.min_silence_ms = min_silence_ms
        self.padding_ms = padding_ms
        self.min_gap_ms = min_gap_ms

    # ── Public API ───────────────────────────────────────────────

    def correct(
        self,
        result: TranscriptionResult,
        audio_path: Path,
    ) -> TranscriptionResult:
        """Apply full sync correction pipeline."""
        audio, sr = self._load_wav(audio_path)
        self.min_silence_samples = int(sr * self.min_silence_ms / 1000)
        padding = self.padding_ms / 1000.0
        min_gap = self.min_gap_ms / 1000.0

        # 1 — Detect speech / silence boundaries
        speech_regions = self._detect_speech_regions(audio, sr)
        logger.info("Detected %d speech regions", len(speech_regions))

        # 2 — Snap subtitle boundaries to speech
        corrected_segments = self._snap_to_speech(
            result.segments, speech_regions, padding
        )

        # 3 — Use word-level timestamps for fine-tuning
        corrected_segments = self._word_level_refine(corrected_segments)

        # 4 — Enforce minimum gaps, no overlaps
        corrected_segments = self._enforce_gaps(corrected_segments, min_gap)

        # 5 — Clamp to audio duration
        duration = len(audio) / sr
        corrected_segments = self._clamp_times(corrected_segments, duration)

        logger.info(
            "Sync correction applied to %d segments", len(corrected_segments)
        )
        return result.model_copy(update={"segments": corrected_segments})

    def correct_fast(self, result: TranscriptionResult) -> TranscriptionResult:
        """Apply a lightweight sync pass using word timestamps only."""
        min_gap = self.min_gap_ms / 1000.0
        corrected_segments = self._word_level_refine(result.segments)
        corrected_segments = self._enforce_gaps(corrected_segments, min_gap)

        duration = result.duration
        if duration <= 0.0 and corrected_segments:
            duration = max(seg.end for seg in corrected_segments)

        corrected_segments = self._clamp_times(corrected_segments, duration)
        logger.info(
            "Fast sync correction applied to %d segments", len(corrected_segments)
        )
        return result.model_copy(update={"segments": corrected_segments})

    # ── Speech detection ─────────────────────────────────────────

    def _detect_speech_regions(
        self, audio: np.ndarray, sr: int
    ) -> List[Tuple[float, float]]:
        """Return list of (start, end) in seconds for speech regions."""
        frame_len = int(sr * 0.03)  # 30 ms frames
        hop = frame_len

        energy = []
        for i in range(0, len(audio) - frame_len, hop):
            frame = audio[i : i + frame_len]
            rms = np.sqrt(np.mean(frame ** 2))
            energy.append(rms)

        is_speech = [e > self.silence_threshold for e in energy]

        # Merge consecutive speech frames
        regions: List[Tuple[float, float]] = []
        start_idx: Optional[int] = None

        for i, s in enumerate(is_speech):
            if s and start_idx is None:
                start_idx = i
            elif not s and start_idx is not None:
                start_t = start_idx * hop / sr
                end_t = i * hop / sr
                if (end_t - start_t) > 0.05:  # min 50ms
                    regions.append((start_t, end_t))
                start_idx = None

        # Close last region
        if start_idx is not None:
            regions.append((start_idx * hop / sr, len(audio) / sr))

        return self._merge_close_regions(regions, gap=0.3)

    @staticmethod
    def _merge_close_regions(
        regions: List[Tuple[float, float]], gap: float
    ) -> List[Tuple[float, float]]:
        if not regions:
            return regions
        merged = [regions[0]]
        for start, end in regions[1:]:
            if start - merged[-1][1] < gap:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        return merged

    # ── Snap subtitle edges to speech ────────────────────────────

    @staticmethod
    def _snap_to_speech(
        segments: List[TranscriptionSegment],
        speech_regions: List[Tuple[float, float]],
        padding: float,
    ) -> List[TranscriptionSegment]:
        if not speech_regions:
            return segments

        corrected = []
        for seg in segments:
            # Find the speech region that best overlaps this segment
            best_region = None
            best_overlap = 0.0
            for rs, re in speech_regions:
                overlap_start = max(seg.start, rs)
                overlap_end = min(seg.end, re)
                overlap = max(0.0, overlap_end - overlap_start)
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_region = (rs, re)

            if best_region:
                new_start = max(seg.start, best_region[0] - padding)
                new_end = min(seg.end, best_region[1] + padding)
                # Ensure minimum duration
                if new_end - new_start < 0.3:
                    new_end = new_start + max(0.3, seg.end - seg.start)
                corrected.append(
                    seg.model_copy(
                        update={
                            "start": round(new_start, 3),
                            "end": round(new_end, 3),
                        }
                    )
                )
            else:
                corrected.append(seg)

        return corrected

    # ── Word-level refinement ────────────────────────────────────

    @staticmethod
    def _word_level_refine(
        segments: List[TranscriptionSegment],
    ) -> List[TranscriptionSegment]:
        """If word timestamps are available, tighten segment boundaries."""
        refined = []
        for seg in segments:
            if seg.words and len(seg.words) > 0:
                first_word = seg.words[0]
                last_word = seg.words[-1]
                new_start = max(0.0, first_word.start - 0.05)
                new_end = last_word.end + 0.05
                refined.append(
                    seg.model_copy(
                        update={
                            "start": round(new_start, 3),
                            "end": round(new_end, 3),
                        }
                    )
                )
            else:
                refined.append(seg)
        return refined

    # ── Enforce gaps / remove overlaps ───────────────────────────

    @staticmethod
    def _enforce_gaps(
        segments: List[TranscriptionSegment], min_gap: float
    ) -> List[TranscriptionSegment]:
        if not segments:
            return segments
        fixed = [segments[0]]
        for seg in segments[1:]:
            prev = fixed[-1]
            if seg.start < prev.end + min_gap:
                mid = (prev.end + seg.start) / 2.0
                fixed[-1] = prev.model_copy(
                    update={"end": round(mid, 3)}
                )
                seg = seg.model_copy(
                    update={"start": round(mid + min_gap, 3)}
                )
            fixed.append(seg)
        return fixed

    @staticmethod
    def _clamp_times(
        segments: List[TranscriptionSegment], duration: float
    ) -> List[TranscriptionSegment]:
        return [
            seg.model_copy(
                update={
                    "start": max(0.0, seg.start),
                    "end": min(duration, seg.end),
                }
            )
            for seg in segments
        ]

    # ── WAV loading ──────────────────────────────────────────────

    @staticmethod
    def _load_wav(path: Path) -> Tuple[np.ndarray, int]:
        """Load a WAV file as float32 numpy array."""
        with wave.open(str(path), "rb") as wf:
            sr = wf.getframerate()
            n_frames = wf.getnframes()
            n_channels = wf.getnchannels()
            raw = wf.readframes(n_frames)

        samples = struct.unpack(f"<{n_frames * n_channels}h", raw)
        audio = np.array(samples, dtype=np.float32) / 32768.0

        if n_channels > 1:
            audio = audio.reshape(-1, n_channels).mean(axis=1)

        return audio, sr
