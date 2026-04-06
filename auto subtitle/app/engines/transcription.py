# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false
"""
Transcription engine — fixed for macOS file descriptor issue.

Handles:
  - "bad value(s) in fds_to_keep" error
  - Falls back to openai-whisper if faster-whisper fails
  - Thread-safe model caching
"""

from __future__ import annotations

import os
import platform
import sys
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np

from app.core.config import get_settings
from app.core.exceptions import TranscriptionError
from app.core.logging_config import get_logger
from app.models.schemas import (
    TranscriptionResult,
    TranscriptionSegment,
    WordTimestamp,
)

logger = get_logger(__name__)

_engine_lock = threading.Lock()
_engine_cache: dict[str, "TranscriptionEngine"] = {}


def _fix_macos_fork_safety() -> None:
    """Fix macOS fork safety issue that causes 'bad value(s) in fds_to_keep'."""
    if platform.system() == "Darwin":
        os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"
        os.environ["no_proxy"] = "*"  # prevent proxy-related subprocess issues
        logger.debug("macOS fork safety workaround applied")


def _fix_file_descriptors() -> None:
    """Ensure file descriptors are in a clean state."""
    try:
        import resource
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        if soft < 1024:
            resource.setrlimit(resource.RLIMIT_NOFILE, (min(4096, hard), hard))
            logger.debug("File descriptor limit raised to %d", min(4096, hard))
    except Exception:
        pass


def _patch_tqdm_mp_lock() -> None:
    """Avoid tqdm multiprocessing lock failures on some Python 3.14 TTY contexts."""
    try:
        import tqdm.std as tqdm_std
    except Exception:
        return

    lock_cls = tqdm_std.TqdmDefaultWriteLock
    if getattr(lock_cls, "_auto_subtitle_patched", False):
        return

    @classmethod
    def _safe_create_mp_lock(cls):
        if not hasattr(cls, "mp_lock"):
            try:
                from multiprocessing import RLock

                cls.mp_lock = RLock()
            except Exception:
                cls.mp_lock = None

    lock_cls.create_mp_lock = _safe_create_mp_lock
    lock_cls._auto_subtitle_patched = True


# Apply fixes at import time
_fix_macos_fork_safety()
_fix_file_descriptors()
_patch_tqdm_mp_lock()


def get_transcription_engine(
    model_size: Optional[str] = None,
) -> "TranscriptionEngine":
    """Singleton-per-model factory (thread-safe)."""
    settings = get_settings()
    key = model_size or settings.whisper_model_size
    if key not in _engine_cache:
        with _engine_lock:
            if key not in _engine_cache:
                _engine_cache[key] = TranscriptionEngine(model_size=key)
    return _engine_cache[key]


class TranscriptionEngine:
    """High-level transcription — tries faster-whisper, falls back to whisper."""

    def __init__(
        self,
        model_size: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
    ) -> None:
        settings = get_settings()
        self.model_size = model_size or settings.whisper_model_size
        self.device = self._resolve_device(device or settings.whisper_device)
        self.compute_type = self._resolve_compute(
            compute_type or settings.whisper_compute_type, self.device
        )
        self._model = None
        self._backend = None  # "faster-whisper" or "whisper"
        self._load_model()

    def describe_backend(self) -> str:
        """Return a compact description of the loaded transcription backend."""
        if self._backend == "faster-whisper":
            return f"faster-whisper {self.model_size} ({self.device}/{self.compute_type})"
        if self._backend == "whisper":
            return f"openai-whisper {self.model_size} ({self.device})"
        return f"whisper {self.model_size}"

    # ══════════════════════════════════════════════════════
    # MODEL LOADING — with fallback
    # ══════════════════════════════════════════════════════

    def _load_model(self) -> None:
        """Try faster-whisper first, then openai-whisper, then whisper."""
        errors: list[str] = []

        # Attempt 1: faster-whisper with thread fix
        ok, error = self._try_faster_whisper()
        if ok:
            return
        if error:
            errors.append(f"faster-whisper: {error}")

        # Attempt 2: faster-whisper with inter_threads=1
        ok, error = self._try_faster_whisper_safe()
        if ok:
            return
        if error:
            errors.append(f"faster-whisper safe: {error}")

        # Attempt 3: openai-whisper (pip install openai-whisper)
        ok, error = self._try_openai_whisper()
        if ok:
            return
        if error:
            errors.append(f"openai-whisper: {error}")

        detail = "\n".join(f"- {item}" for item in errors[:3])

        raise TranscriptionError(
            "Could not load any whisper model.\n"
            "Try: pip install openai-whisper\n"
            "Or:  pip install --upgrade faster-whisper ctranslate2"
            + (f"\nDetails:\n{detail}" if detail else "")
        )

    def _try_faster_whisper(self) -> tuple[bool, Optional[str]]:
        """Attempt to load faster-whisper normally."""
        try:
            from faster_whisper import WhisperModel

            logger.info(
                "Loading faster-whisper: model=%s device=%s compute=%s",
                self.model_size, self.device, self.compute_type,
            )

            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=1,           # ← KEY FIX: single thread avoids fd issue
                num_workers=1,           # ← KEY FIX: single worker
            )
            self._backend = "faster-whisper"
            logger.info("✅ faster-whisper loaded successfully")
            return True, None

        except Exception as exc:
            logger.warning("faster-whisper normal load failed: %s", exc)
            return False, str(exc)

    def _try_faster_whisper_safe(self) -> tuple[bool, Optional[str]]:
        """Attempt faster-whisper with maximum safety settings."""
        try:
            from faster_whisper import WhisperModel

            logger.info("Trying faster-whisper safe mode...")

            # Force CPU, single thread, int8
            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
                cpu_threads=1,
                num_workers=1,
                download_root=None,
            )
            self.device = "cpu"
            self.compute_type = "int8"
            self._backend = "faster-whisper"
            logger.info("✅ faster-whisper loaded (safe mode: cpu/int8)")
            return True, None

        except Exception as exc:
            logger.warning("faster-whisper safe mode failed: %s", exc)
            return False, str(exc)

    def _try_openai_whisper(self) -> tuple[bool, Optional[str]]:
        """Attempt to load openai-whisper as fallback."""
        try:
            import whisper

            logger.info("Loading openai-whisper: model=%s", self.model_size)

            self._model = whisper.load_model(
                self.model_size,
                device=self.device if self.device != "auto" else "cpu",
            )
            self._backend = "whisper"
            logger.info("✅ openai-whisper loaded successfully")
            return True, None

        except ImportError:
            logger.warning(
                "openai-whisper not installed. "
                "Install with: pip install openai-whisper"
            )
            return False, "package not installed"
        except Exception as exc:
            logger.warning("openai-whisper load failed: %s", exc)
            return False, str(exc)

    # ══════════════════════════════════════════════════════
    # TRANSCRIPTION — routes to correct backend
    # ══════════════════════════════════════════════════════

    def transcribe(
        self,
        audio_path: Path | str,
        language: Optional[str] = None,
        beam_size: int | None = None,
        word_timestamps: bool = True,
        initial_prompt: Optional[str] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> TranscriptionResult:
        """Transcribe an audio file."""
        settings = get_settings()
        audio_path = Path(audio_path)

        if not audio_path.exists():
            raise TranscriptionError(f"Audio file not found: {audio_path}")

        if self._backend == "faster-whisper":
            return self._transcribe_faster(
                audio_path,
                language,
                beam_size,
                word_timestamps,
                initial_prompt,
                progress_callback,
            )
        elif self._backend == "whisper":
            return self._transcribe_openai(
                audio_path,
                language,
                word_timestamps,
                initial_prompt,
                progress_callback,
            )
        else:
            raise TranscriptionError("No transcription backend loaded")

    def transcribe_array(
        self,
        audio: np.ndarray,
        sample_rate: int = 16000,
        language: Optional[str] = None,
    ) -> TranscriptionResult:
        """Transcribe numpy audio array (for real-time)."""
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.max() > 1.0:
            audio = audio / 32768.0

        if self._backend == "faster-whisper":
            return self._transcribe_array_faster(audio, language)
        elif self._backend == "whisper":
            return self._transcribe_array_openai(audio, language)
        else:
            raise TranscriptionError("No transcription backend loaded")

    # ══════════════════════════════════════════════════════
    # FASTER-WHISPER BACKEND
    # ══════════════════════════════════════════════════════

    def _transcribe_faster(
        self,
        audio_path: Path,
        language: Optional[str],
        beam_size: Optional[int],
        word_timestamps: bool,
        initial_prompt: Optional[str],
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> TranscriptionResult:
        """Transcribe using faster-whisper."""
        settings = get_settings()

        try:
            if progress_callback:
                progress_callback(
                    2.0,
                    f"Transcribing audio using {self.describe_backend()}…",
                )

            segments_iter, info = self._model.transcribe(
                str(audio_path),
                language=language or settings.whisper_language,
                beam_size=beam_size or settings.whisper_beam_size,
                word_timestamps=word_timestamps,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                initial_prompt=initial_prompt,
            )

            segments = self._parse_faster_segments(
                segments_iter,
                word_timestamps,
                total_duration=info.duration,
                progress_callback=progress_callback,
            )

            result = TranscriptionResult(
                language=info.language,
                language_probability=info.language_probability,
                duration=info.duration,
                segments=segments,
            )

            logger.info(
                "Transcription complete: lang=%s segments=%d duration=%.1fs",
                result.language, len(result.segments), result.duration,
            )
            if progress_callback:
                progress_callback(
                    100.0,
                    f"Transcription complete — {len(result.segments)} segment(s)",
                )
            return result

        except Exception as exc:
            logger.exception("faster-whisper transcription failed")
            raise TranscriptionError(str(exc)) from exc

    def _transcribe_array_faster(
        self,
        audio: np.ndarray,
        language: Optional[str],
    ) -> TranscriptionResult:
        """Transcribe array using faster-whisper."""
        try:
            segments_iter, info = self._model.transcribe(
                audio,
                language=language,
                beam_size=3,
                word_timestamps=True,
                vad_filter=True,
            )
            segments = self._parse_faster_segments(segments_iter, True)
            return TranscriptionResult(
                language=info.language,
                language_probability=info.language_probability,
                duration=info.duration,
                segments=segments,
            )
        except Exception as exc:
            raise TranscriptionError(str(exc)) from exc

    @staticmethod
    def _parse_faster_segments(
        segments_iter,
        word_timestamps: bool,
        total_duration: Optional[float] = None,
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> List[TranscriptionSegment]:
        """Parse faster-whisper segments into our model."""
        segments: List[TranscriptionSegment] = []
        duration_hint = float(total_duration or 0.0)
        last_progress = 0.0
        last_emit = 0.0
        for idx, seg in enumerate(segments_iter):
            words = None
            if word_timestamps and seg.words:
                words = [
                    WordTimestamp(
                        word=w.word.strip(),
                        start=w.start,
                        end=w.end,
                        probability=w.probability,
                    )
                    for w in seg.words
                ]

            segments.append(
                TranscriptionSegment(
                    id=idx,
                    start=seg.start,
                    end=seg.end,
                    text=seg.text.strip(),
                    words=words,
                    confidence=getattr(seg, "avg_logprob", 0.0),
                )
            )

            if progress_callback:
                now = time.monotonic()
                progress = None
                if duration_hint > 0:
                    progress = max(
                        0.0,
                        min(99.0, (float(seg.end) / duration_hint) * 100.0),
                    )

                should_emit = False
                if progress is not None:
                    if progress >= 99.0 or progress - last_progress >= 2.0:
                        should_emit = True
                    elif now - last_emit >= 1.5:
                        should_emit = True
                elif idx == 0 or (idx + 1) % 5 == 0 or now - last_emit >= 1.5:
                    should_emit = True

                if should_emit:
                    if duration_hint > 0:
                        message = (
                            f"Transcribing audio… {min(seg.end, duration_hint):.1f}/"
                            f"{duration_hint:.1f}s ({idx + 1} seg)"
                        )
                        progress_callback(progress or 0.0, message)
                        last_progress = progress or last_progress
                    else:
                        progress_callback(
                            0.0,
                            f"Transcribing audio… {idx + 1} segment(s)",
                        )
                    last_emit = now
        return segments

    # ══════════════════════════════════════════════════════
    # OPENAI-WHISPER BACKEND (FALLBACK)
    # ══════════════════════════════════════════════════════

    def _transcribe_openai(
        self,
        audio_path: Path,
        language: Optional[str],
        word_timestamps: bool,
        initial_prompt: Optional[str],
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> TranscriptionResult:
        """Transcribe using openai-whisper."""
        try:
            if progress_callback:
                progress_callback(
                    5.0,
                    f"Transcribing audio using {self.describe_backend()}…",
                )

            options = {
                "fp16": False,
                "verbose": False,
            }
            if language:
                options["language"] = language
            if initial_prompt:
                options["initial_prompt"] = initial_prompt
            if word_timestamps:
                options["word_timestamps"] = True

            result = self._model.transcribe(str(audio_path), **options)

            if progress_callback:
                progress_callback(90.0, "Finalizing transcription segments…")

            segments = self._parse_openai_segments(
                result.get("segments", []),
                word_timestamps,
            )

            detected_lang = result.get("language", "en")
            duration = 0.0
            if segments:
                duration = segments[-1].end

            transcription = TranscriptionResult(
                language=detected_lang,
                language_probability=0.0,
                duration=duration,
                segments=segments,
            )

            logger.info(
                "Transcription complete (openai-whisper): lang=%s segments=%d",
                detected_lang, len(segments),
            )
            if progress_callback:
                progress_callback(
                    100.0,
                    f"Transcription complete — {len(segments)} segment(s)",
                )
            return transcription

        except Exception as exc:
            logger.exception("openai-whisper transcription failed")
            raise TranscriptionError(str(exc)) from exc

    def _transcribe_array_openai(
        self,
        audio: np.ndarray,
        language: Optional[str],
    ) -> TranscriptionResult:
        """Transcribe array using openai-whisper."""
        try:
            import whisper

            # openai-whisper expects float32 numpy array
            # Pad/trim to 30 seconds as whisper expects
            audio_padded = whisper.pad_or_trim(audio)
            mel = whisper.log_mel_spectrogram(audio_padded).to(self._model.device)

            options = whisper.DecodingOptions(
                language=language,
                fp16=False,
            )
            result = whisper.decode(self._model, mel, options)

            segments = [
                TranscriptionSegment(
                    id=0,
                    start=0.0,
                    end=len(audio) / 16000.0,
                    text=result.text.strip(),
                )
            ]

            return TranscriptionResult(
                language=result.language or "en",
                language_probability=0.0,
                duration=len(audio) / 16000.0,
                segments=segments,
            )

        except Exception as exc:
            raise TranscriptionError(str(exc)) from exc

    @staticmethod
    def _parse_openai_segments(
        raw_segments: list,
        word_timestamps: bool,
    ) -> List[TranscriptionSegment]:
        """Parse openai-whisper segments."""
        segments: List[TranscriptionSegment] = []
        for idx, seg in enumerate(raw_segments):
            words = None
            if word_timestamps and "words" in seg:
                words = [
                    WordTimestamp(
                        word=w.get("word", "").strip(),
                        start=w.get("start", 0.0),
                        end=w.get("end", 0.0),
                        probability=w.get("probability", 0.0),
                    )
                    for w in seg["words"]
                ]

            segments.append(
                TranscriptionSegment(
                    id=idx,
                    start=seg.get("start", 0.0),
                    end=seg.get("end", 0.0),
                    text=seg.get("text", "").strip(),
                    words=words,
                )
            )
        return segments

    # ══════════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════════

    @staticmethod
    def _resolve_device(device: str) -> str:
        if device != "auto":
            return device
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "cpu"  # MPS not supported by ctranslate2
        except ImportError:
            pass
        return "cpu"

    @staticmethod
    def _resolve_compute(compute_type: str, device: str) -> str:
        if compute_type != "auto":
            return compute_type
        if device == "cuda":
            return "float16"
        if platform.machine() == "arm64":
            return "int8"
        return "int8"
