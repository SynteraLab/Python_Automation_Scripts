"""Subtitle service — sekarang mendukung terjemahan."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.engines.subtitle_formatter import SubtitleFormatter
from app.engines.sync_correction import SyncCorrector
from app.engines.transcription import TranscriptionEngine, get_transcription_engine
from app.models.schemas import (
    SyncMode,
    StylePreset,
    SubtitleFormat,
    TranscriptionResult,
)
from app.utils.ffmpeg_wrapper import FFmpegWrapper
from app.utils.file_manager import FileManager

logger = get_logger(__name__)


class SubtitleService:
    """High-level subtitle generation pipeline (synchronous)."""

    def __init__(
        self,
        model_size: Optional[str] = None,
    ):
        self._settings = get_settings()
        self._fm = FileManager()
        self._ff = FFmpegWrapper()
        self._formatter = SubtitleFormatter()
        self._sync = SyncCorrector()
        self._engine: Optional[TranscriptionEngine] = None
        self._model_size = model_size

    def describe_hard_subtitle_strategy(
        self,
        encoder: Optional[str] = None,
        crf: Optional[int] = None,
        preset: Optional[str] = None,
        target_size: Optional[tuple[int, int]] = None,
    ) -> str:
        """Return a short description of the active hard subtitle encode strategy."""
        return self._ff.describe_hardsub_strategy(
            encoder=encoder,
            crf=crf,
            preset=preset,
            target_size=target_size,
        )

    @property
    def engine(self) -> TranscriptionEngine:
        if self._engine is None:
            self._engine = get_transcription_engine(self._model_size)
        return self._engine

    @staticmethod
    def _count_changed_segments(before, after) -> int:
        total = min(len(before), len(after))
        changed = 0
        for i in range(total):
            src = " ".join((before[i].text or "").split())
            dst = " ".join((after[i].text or "").split())
            if src != dst:
                changed += 1
        return changed

    @staticmethod
    def _normalize_sync_mode(
        sync_mode: Optional[SyncMode | str],
        apply_sync: bool,
    ) -> SyncMode:
        if sync_mode is None:
            return SyncMode.FULL if apply_sync else SyncMode.OFF
        if isinstance(sync_mode, SyncMode):
            return sync_mode

        normalized = str(sync_mode).strip().lower()
        try:
            return SyncMode(normalized)
        except ValueError as exc:
            raise ValueError(
                f"Unsupported sync mode '{sync_mode}'. Use off, light, or full."
            ) from exc

    @staticmethod
    def _normalize_render_size(
        render_width: Optional[int],
        render_height: Optional[int],
    ) -> Optional[tuple[int, int]]:
        if render_width is None and render_height is None:
            return None
        if render_width is None or render_height is None:
            raise ValueError("Both render width and render height must be provided together.")
        if render_width <= 0 or render_height <= 0:
            raise ValueError("Render width and height must be greater than zero.")
        if render_width % 2 != 0 or render_height % 2 != 0:
            raise ValueError("Render width and height must be even numbers.")
        return render_width, render_height

    def _resolve_ass_play_resolution(
        self,
        input_path: Path,
        fmt: SubtitleFormat,
        hard_subtitle: bool,
        render_size: Optional[tuple[int, int]],
    ) -> Optional[tuple[int, int]]:
        if fmt != SubtitleFormat.ASS or not self._ff.is_video_file(input_path):
            return None
        if hard_subtitle and render_size is not None:
            return render_size

        try:
            return self._ff.get_video_dimensions(input_path)
        except Exception as exc:
            logger.warning("Could not detect source resolution for ASS PlayRes: %s", exc)
            return render_size

    def generate_from_file(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        language: Optional[str] = None,
        fmt: SubtitleFormat = SubtitleFormat.SRT,
        style_preset: StylePreset = StylePreset.NETFLIX,
        apply_sync: bool = True,
        sync_mode: Optional[SyncMode | str] = None,
        beam_size: Optional[int] = None,
        initial_prompt: Optional[str] = None,
        translate_to: Optional[str] = None,
        embed_subtitle: bool = False,
        hard_subtitle: bool = False,
        overwrite_video: bool = False,
        keep_subtitle_file: bool = True,
        hard_subtitle_encoder: Optional[str] = None,
        hard_subtitle_crf: Optional[int] = None,
        hard_subtitle_preset: Optional[str] = None,
        render_width: Optional[int] = None,
        render_height: Optional[int] = None,
        progress_callback=None,
    ) -> Path:
        """
        Full pipeline: extract → transcribe → translate → correct → format.

        Args:
            input_path: Path ke file video/audio
            output_path: Path output (None = otomatis)
            language: Bahasa sumber (None = auto-detect)
            fmt: Format output (srt/ass)
            style_preset: Preset styling ASS
            apply_sync: Koreksi timing otomatis
            sync_mode: off | light | full
            beam_size: Beam size transkripsi (lebih kecil = lebih cepat)
            initial_prompt: Context hint untuk menjaga istilah/nama tetap konsisten
            translate_to: Kode bahasa tujuan (misal: "id" untuk Indonesia)
            embed_subtitle: Sisipkan soft subtitle ke video tanpa re-encode
            hard_subtitle: Burn subtitle permanen ke video (re-encode)
            overwrite_video: Timpa file video asli (pakai file sementara lalu replace)
            keep_subtitle_file: Simpan file .srt/.ass terpisah
            hard_subtitle_encoder: Override encoder hard subtitle
            hard_subtitle_crf: Override CRF hard subtitle
            hard_subtitle_preset: Override preset hard subtitle
            render_width/render_height: Target resolusi render hard subtitle
            progress_callback: Fungsi callback(pct, message)
        """
        sync_mode_value = self._normalize_sync_mode(sync_mode, apply_sync)
        render_size = (
            self._normalize_render_size(render_width, render_height)
            if hard_subtitle
            else None
        )
        effective_hardsub_crf = (
            hard_subtitle_crf
            if hard_subtitle_crf is not None
            else self._settings.hard_subtitle_crf
        )
        effective_hardsub_preset = (
            hard_subtitle_preset or self._settings.hard_subtitle_preset
        )

        audio_path: Optional[Path] = None
        try:
            # 1 — Ekstrak audio
            if progress_callback:
                progress_callback(5, "Extracting audio…")
            audio_path = self._ff.extract_audio(input_path)

            # 2 — Load transcription model first so TUI does not look frozen
            if progress_callback:
                progress_callback(10, "Loading transcription model…")
            engine = self.engine
            if progress_callback:
                progress_callback(14, f"Transcription model ready — {engine.describe_backend()}")

            # 3 — Transkrip (speech-to-text)
            if progress_callback:
                progress_callback(15, f"Transcribing audio using {engine.describe_backend()}…")

            def _transcribe_progress(pct: float, msg: str) -> None:
                if progress_callback is None:
                    return
                mapped = 15 + (max(0.0, min(100.0, pct)) / 100.0) * 30.0
                progress_callback(mapped, msg)

            result = engine.transcribe(
                audio_path,
                language=language,
                beam_size=beam_size,
                word_timestamps=(sync_mode_value != SyncMode.OFF),
                initial_prompt=initial_prompt,
                progress_callback=_transcribe_progress if progress_callback else None,
            )

            detected_lang = result.language
            if progress_callback:
                progress_callback(
                    45,
                    f"Transcription done — detected language: {detected_lang}",
                )

            # 4 — Terjemahan
            if translate_to and translate_to != detected_lang:
                if progress_callback:
                    progress_callback(
                        50,
                        f"Translating: {detected_lang} → {translate_to}…",
                    )

                try:
                    from app.engines.translator import TranslationEngine

                    translator = TranslationEngine(
                        source_lang=detected_lang,
                        target_lang=translate_to,
                    )
                    source_segments = list(result.segments)

                    def _translate_progress(pct: float, msg: str) -> None:
                        if progress_callback:
                            mapped = 50 + (pct / 100.0) * 20
                            progress_callback(mapped, msg)

                    result = translator.translate_result(
                        result,
                        progress_callback=_translate_progress,
                    )

                    changed_count = self._count_changed_segments(source_segments, result.segments)
                    if changed_count == 0 and len(source_segments) > 0:
                        raise RuntimeError(
                            "Translation produced no changed lines. "
                            "Check internet connection / Google Translate access and retry."
                        )

                    if progress_callback:
                        progress_callback(70, f"Translation complete ✓ ({changed_count} lines changed)")

                except ImportError:
                    msg = "Translation dependency missing: install `deep-translator`"
                    logger.error(msg)
                    if progress_callback:
                        progress_callback(70, f"✗ {msg}")
                    raise RuntimeError(msg)
                except Exception as exc:
                    logger.error("Translation failed: %s", exc)
                    if progress_callback:
                        progress_callback(70, f"✗ Translation error: {exc}")
                    raise
            else:
                if progress_callback:
                    progress_callback(70, "No translation needed")

            # 5 — Koreksi sinkronisasi
            if sync_mode_value == SyncMode.FULL:
                if progress_callback:
                    progress_callback(75, "Applying full sync correction…")
                result = self._sync.correct(result, audio_path)
            elif sync_mode_value == SyncMode.LIGHT:
                if progress_callback:
                    progress_callback(75, "Applying fast sync correction…")
                result = self._sync.correct_fast(result)
            elif progress_callback:
                progress_callback(75, "Sync correction skipped")

            # 6 — Format subtitle
            if progress_callback:
                progress_callback(85, "Generating subtitle file…")

            if output_path is None:
                suffix = f"_{translate_to}" if translate_to else ""
                ext = "ass" if fmt == SubtitleFormat.ASS else "srt"
                output_path = self._fm.get_output_path(
                    f"{input_path.stem}{suffix}", ext
                )

            subtitle_path = self._formatter.generate(
                result,
                output_path,
                fmt=fmt,
                style_preset=style_preset,
                play_res=self._resolve_ass_play_resolution(
                    input_path=input_path,
                    fmt=fmt,
                    hard_subtitle=hard_subtitle,
                    render_size=render_size,
                ),
            )

            final_output_path = subtitle_path
            video_mode = "hard" if hard_subtitle else "soft" if embed_subtitle else "none"

            if video_mode != "none":
                if not self._ff.is_video_file(input_path):
                    raise ValueError(
                        "Embed subtitle hanya didukung untuk file video (bukan audio)."
                    )

                language_tag = translate_to or detected_lang or language
                title_tag = f"Auto Subtitle ({language_tag or 'und'})"

                if video_mode == "hard":
                    strategy = self.describe_hard_subtitle_strategy(
                        encoder=hard_subtitle_encoder,
                        crf=effective_hardsub_crf,
                        preset=effective_hardsub_preset,
                        target_size=render_size,
                    )
                    if progress_callback:
                        progress_callback(92, f"Burning hard subtitle into video ({strategy})…")
                    final_output_path = self._ff.burn_subtitle_into_video(
                        input_video=input_path,
                        subtitle_path=subtitle_path,
                        overwrite_input=overwrite_video,
                        encoder=hard_subtitle_encoder,
                        crf=effective_hardsub_crf,
                        preset=effective_hardsub_preset,
                        target_width=render_size[0] if render_size else None,
                        target_height=render_size[1] if render_size else None,
                    )
                else:
                    if progress_callback:
                        progress_callback(92, "Embedding soft subtitle into video (no re-encode)…")
                    final_output_path = self._ff.embed_subtitle_track(
                        input_video=input_path,
                        subtitle_path=subtitle_path,
                        overwrite_input=overwrite_video,
                        language=language_tag,
                        title=title_tag,
                    )

                if not keep_subtitle_file:
                    subtitle_path.unlink(missing_ok=True)

                if progress_callback:
                    progress_callback(98, f"Video updated → {final_output_path.name}")

            if progress_callback:
                progress_callback(100, "Complete ✓")

            return final_output_path
        finally:
            if audio_path is not None:
                audio_path.unlink(missing_ok=True)

    def generate_from_file_id(
        self,
        file_id: str,
        language: Optional[str] = None,
        fmt: SubtitleFormat = SubtitleFormat.SRT,
        style_preset: StylePreset = StylePreset.NETFLIX,
        apply_sync: bool = True,
        sync_mode: Optional[SyncMode | str] = None,
        beam_size: Optional[int] = None,
        initial_prompt: Optional[str] = None,
        translate_to: Optional[str] = None,
        embed_subtitle: bool = False,
        hard_subtitle: bool = False,
        overwrite_video: bool = False,
        keep_subtitle_file: bool = True,
        hard_subtitle_encoder: Optional[str] = None,
        hard_subtitle_crf: Optional[int] = None,
        hard_subtitle_preset: Optional[str] = None,
        render_width: Optional[int] = None,
        render_height: Optional[int] = None,
        progress_callback=None,
    ) -> Path:
        """Generate subtitles for an uploaded file by its ID."""
        input_path = self._fm.get_upload_path(file_id)

        suffix = f"_{translate_to}" if translate_to else ""
        output_path = self._fm.get_output_path(
            f"{file_id}{suffix}", fmt.value
        )

        return self.generate_from_file(
            input_path=input_path,
            output_path=output_path,
            language=language,
            fmt=fmt,
            style_preset=style_preset,
            apply_sync=apply_sync,
            sync_mode=sync_mode,
            beam_size=beam_size,
            initial_prompt=initial_prompt,
            translate_to=translate_to,
            embed_subtitle=embed_subtitle,
            hard_subtitle=hard_subtitle,
            overwrite_video=overwrite_video,
            keep_subtitle_file=keep_subtitle_file,
            hard_subtitle_encoder=hard_subtitle_encoder,
            hard_subtitle_crf=hard_subtitle_crf,
            hard_subtitle_preset=hard_subtitle_preset,
            render_width=render_width,
            render_height=render_height,
            progress_callback=progress_callback,
        )
