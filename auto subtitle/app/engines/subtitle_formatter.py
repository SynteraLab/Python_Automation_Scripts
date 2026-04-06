"""Generate SRT and styled ASS subtitle files from transcription output."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import List, Optional, cast

import pysubs2

from app.core.config import get_settings
from app.core.logging_config import get_logger
from app.engines.style_presets import STYLE_PRESETS, AssStyle
from app.models.schemas import (
    StylePreset,
    SubtitleFormat,
    TranscriptionResult,
    TranscriptionSegment,
)

logger = get_logger(__name__)


class SubtitleFormatter:
    """Converts transcription segments → subtitle files."""

    def __init__(
        self,
        max_chars: Optional[int] = None,
        max_lines: Optional[int] = None,
    ):
        settings = get_settings()
        self.max_chars = max_chars or settings.max_chars_per_line
        self.max_lines = max_lines or settings.max_lines

    # ── Public API ───────────────────────────────────────────────

    def generate(
        self,
        result: TranscriptionResult,
        output_path: Path,
        fmt: SubtitleFormat = SubtitleFormat.SRT,
        style_preset: StylePreset = StylePreset.NETFLIX,
        play_res: Optional[tuple[int, int]] = None,
    ) -> Path:
        """Generate a subtitle file and return its path."""
        segments = self._wrap_segments(result.segments)

        if fmt == SubtitleFormat.ASS:
            return self._write_ass(segments, output_path, style_preset, play_res)
        return self._write_srt(segments, output_path)

    # ── SRT generation ───────────────────────────────────────────

    def _write_srt(
        self, segments: List[TranscriptionSegment], output: Path
    ) -> Path:
        subs = pysubs2.SSAFile()
        for seg in segments:
            event = pysubs2.SSAEvent(
                start=pysubs2.make_time(s=seg.start),
                end=pysubs2.make_time(s=seg.end),
                text=seg.text,
            )
            subs.events.append(event)

        out = output.with_suffix(".srt")
        subs.save(str(out), format_="srt")
        logger.info("SRT saved → %s (%d events)", out.name, len(subs.events))
        return out

    # ── ASS generation ───────────────────────────────────────────

    def _write_ass(
        self,
        segments: List[TranscriptionSegment],
        output: Path,
        preset: StylePreset,
        play_res: Optional[tuple[int, int]] = None,
    ) -> Path:
        style_def = STYLE_PRESETS.get(preset.value, STYLE_PRESETS["netflix"])

        subs = pysubs2.SSAFile()
        width, height = play_res or (1920, 1080)
        subs.info["PlayResX"] = str(width)
        subs.info["PlayResY"] = str(height)

        pysubs2_style = self._make_pysubs2_style(style_def)
        subs.styles[style_def.name] = pysubs2_style

        for seg in segments:
            text = seg.text.replace("\n", "\\N")  # ASS newline
            event = pysubs2.SSAEvent(
                start=pysubs2.make_time(s=seg.start),
                end=pysubs2.make_time(s=seg.end),
                text=text,
                style=style_def.name,
            )
            subs.events.append(event)

        out = output.with_suffix(".ass")
        subs.save(str(out), format_="ass")
        logger.info(
            "ASS saved → %s (%d events, style=%s, play_res=%sx%s)",
            out.name,
            len(subs.events),
            style_def.name,
            width,
            height,
        )
        return out

    # ── Text wrapping ────────────────────────────────────────────

    def _wrap_segments(
        self, segments: List[TranscriptionSegment]
    ) -> List[TranscriptionSegment]:
        """Wrap long lines to stay within max_chars / max_lines."""
        wrapped: List[TranscriptionSegment] = []
        for seg in segments:
            lines = textwrap.wrap(seg.text, width=self.max_chars)
            if len(lines) > self.max_lines:
                # Split into multiple subtitle events
                chunk_size = self.max_lines
                duration = seg.end - seg.start
                words_total = len(seg.text.split())
                cursor = seg.start

                for i in range(0, len(lines), chunk_size):
                    chunk_lines = lines[i : i + chunk_size]
                    chunk_text = "\n".join(chunk_lines)
                    chunk_words = len(chunk_text.split())
                    chunk_dur = (
                        (chunk_words / max(words_total, 1)) * duration
                    )
                    wrapped.append(
                        TranscriptionSegment(
                            id=len(wrapped),
                            start=round(cursor, 3),
                            end=round(cursor + chunk_dur, 3),
                            text=chunk_text,
                            confidence=seg.confidence,
                        )
                    )
                    cursor += chunk_dur
            else:
                text = "\n".join(lines) if lines else seg.text
                wrapped.append(
                    seg.model_copy(update={"text": text, "id": len(wrapped)})
                )
        return wrapped

    # ── ASS style conversion ─────────────────────────────────────

    @staticmethod
    def _make_pysubs2_style(s: AssStyle) -> pysubs2.SSAStyle:
        return pysubs2.SSAStyle(
            fontname=s.fontname,
            fontsize=s.fontsize,
            primarycolor=_parse_color(s.primary_color),
            secondarycolor=_parse_color(s.secondary_color),
            outlinecolor=_parse_color(s.outline_color),
            backcolor=_parse_color(s.back_color),
            bold=s.bold,
            italic=s.italic,
            borderstyle=s.border_style,
            outline=s.outline,
            shadow=s.shadow,
            alignment=cast("object", s.alignment),
            marginl=s.margin_l,
            marginr=s.margin_r,
            marginv=s.margin_v,
            scalex=s.scale_x,
            scaley=s.scale_y,
            spacing=s.spacing,
            encoding=s.encoding,
        )


def _parse_color(ass_color: str) -> pysubs2.Color:
    """Parse &HAABBGGRR or &HBBGGRR into pysubs2.Color."""
    h = ass_color.replace("&H", "").replace("&h", "")
    h = h.zfill(8)
    a = int(h[0:2], 16)
    b = int(h[2:4], 16)
    g = int(h[4:6], 16)
    r = int(h[6:8], 16)
    return pysubs2.Color(r=r, g=g, b=b, a=a)
