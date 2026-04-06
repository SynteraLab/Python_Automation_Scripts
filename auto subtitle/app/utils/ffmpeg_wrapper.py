"""FFmpeg wrapper for audio extraction and media probing."""

from __future__ import annotations

import json
import platform
import subprocess
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.exceptions import AudioExtractionError
from app.core.logging_config import get_logger

logger = get_logger(__name__)


class FFmpegWrapper:
    """Thin wrapper around FFmpeg / FFprobe CLIs."""

    _VIDEO_EXTENSIONS = {
        ".mp4", ".mkv", ".mov", ".m4v", ".avi", ".webm", ".flv", ".wmv", ".ts",
    }

    def __init__(self) -> None:
        self._settings = get_settings()
        self._ffmpeg = self._settings.ffmpeg_path
        self._ffprobe = self._settings.ffprobe_path

    def describe_hardsub_strategy(
        self,
        encoder: Optional[str] = None,
        crf: Optional[int] = None,
        preset: Optional[str] = None,
        target_size: Optional[tuple[int, int]] = None,
    ) -> str:
        """Return a short human-readable hard subtitle strategy summary."""
        ffmpeg_bin = self._find_ffmpeg_for_hardsub()
        if ffmpeg_bin is None:
            return "FFmpeg with libass support required"

        effective_crf = crf if crf is not None else self._settings.hard_subtitle_crf
        effective_preset = preset or self._settings.hard_subtitle_preset
        attempts = self._hardsub_attempts(ffmpeg_bin, requested_encoder=encoder)
        primary = attempts[0]
        size_note = ""
        if target_size is not None:
            size_note = f" @ {target_size[0]}x{target_size[1]}"

        if primary == "h264_videotoolbox":
            return f"hardware h264_videotoolbox{size_note} + validated temp output"

        return (
            "validated temp output, libx264 "
            f"preset={effective_preset} "
            f"CRF {effective_crf}{size_note}"
        )

    # ── Public API ───────────────────────────────────────────────

    def extract_audio(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        sample_rate: int = 16000,
        mono: bool = True,
    ) -> Path:
        """Extract audio as 16-bit PCM WAV (Whisper-ready)."""
        if output_path is None:
            output_path = (
                self._settings.temp_dir / f"{input_path.stem}_audio.wav"
            )

        channels = "1" if mono else "2"

        cmd = [
            self._ffmpeg,
            "-y",
            "-i", str(input_path),
            "-vn",                         # drop video
            "-acodec", "pcm_s16le",        # 16-bit PCM
            "-ar", str(sample_rate),       # sample rate
            "-ac", channels,               # channels
            str(output_path),
        ]

        logger.info("Extracting audio → %s", output_path.name)
        self._run(cmd)
        return output_path

    def get_duration(self, path: Path) -> float:
        """Return media duration in seconds."""
        cmd = [
            self._ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "json",
            str(path),
        ]
        result = self._run(cmd, capture=True)
        data = json.loads(result.stdout)
        return float(data["format"]["duration"])

    def get_media_info(self, path: Path) -> dict:
        """Return full media info as dict."""
        cmd = [
            self._ffprobe,
            "-v", "error",
            "-show_format",
            "-show_streams",
            "-of", "json",
            str(path),
        ]
        result = self._run(cmd, capture=True)
        return json.loads(result.stdout)

    def get_video_dimensions(self, path: Path) -> tuple[int, int]:
        """Return the first video stream resolution."""
        info = self.get_media_info(path)
        for stream in info.get("streams", []):
            if stream.get("codec_type") != "video":
                continue
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            if width > 0 and height > 0:
                return width, height
        raise AudioExtractionError(f"Could not detect video dimensions: {path}")

    def is_video_file(self, path: Path) -> bool:
        return path.suffix.lower() in self._VIDEO_EXTENSIONS

    def embed_subtitle_track(
        self,
        input_video: Path,
        subtitle_path: Path,
        output_path: Optional[Path] = None,
        overwrite_input: bool = False,
        language: Optional[str] = None,
        title: Optional[str] = None,
    ) -> Path:
        """
        Embed subtitle as soft-subtitle without re-encoding video/audio.

        Notes:
        - Resolution/quality stays identical (`-c:v copy -c:a copy`).
        - In-place overwrite is done safely via temporary file then replace.
        """
        if not input_video.exists():
            raise AudioExtractionError(f"Input video not found: {input_video}")
        if not subtitle_path.exists():
            raise AudioExtractionError(f"Subtitle file not found: {subtitle_path}")
        if not self.is_video_file(input_video):
            raise AudioExtractionError(f"Input is not a supported video file: {input_video}")

        if overwrite_input:
            target_path = input_video
        else:
            target_path = output_path or input_video.with_name(
                f"{input_video.stem}_subbed{input_video.suffix}"
            )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output = self._temporary_output_path(target_path, "subtmp")
        temp_output.unlink(missing_ok=True)

        subtitle_codec = self._subtitle_codec_for_container(target_path, subtitle_path)
        language_tag = (language or "und").replace("_", "-")

        cmd = [
            self._ffmpeg,
            "-y",
            "-i", str(input_video),
            "-i", str(subtitle_path),
            "-map", "0:v?",
            "-map", "0:a?",
            "-map", "1:0",
            "-c:v", "copy",
            "-c:a", "copy",
            "-c:s", subtitle_codec,
            "-metadata:s:s:0", f"language={language_tag}",
            "-disposition:s:0", "default",
        ]

        if title:
            cmd.extend(["-metadata:s:s:0", f"title={title}"])

        cmd.append(str(temp_output))

        logger.info(
            "Embedding subtitle track (%s) → %s",
            subtitle_codec,
            target_path.name,
        )
        try:
            self._run(cmd)
            return self._finalize_output(temp_output, target_path, input_video)
        except Exception:
            temp_output.unlink(missing_ok=True)
            raise

    def burn_subtitle_into_video(
        self,
        input_video: Path,
        subtitle_path: Path,
        output_path: Optional[Path] = None,
        overwrite_input: bool = False,
        encoder: Optional[str] = None,
        crf: int = 18,
        preset: str = "medium",
        target_width: Optional[int] = None,
        target_height: Optional[int] = None,
    ) -> Path:
        """
        Burn subtitle permanently into video frames (hard subtitle).

        Notes:
        - Hard subtitle requires video re-encode.
        - Resolution stays the same unless scale filter is added elsewhere.
        """
        if not input_video.exists():
            raise AudioExtractionError(f"Input video not found: {input_video}")
        if not subtitle_path.exists():
            raise AudioExtractionError(f"Subtitle file not found: {subtitle_path}")
        if not self.is_video_file(input_video):
            raise AudioExtractionError(f"Input is not a supported video file: {input_video}")
        if (target_width is None) != (target_height is None):
            raise AudioExtractionError("Both target_width and target_height are required together")
        if target_width is not None and target_width <= 0:
            raise AudioExtractionError("target_width must be greater than zero")
        if target_height is not None and target_height <= 0:
            raise AudioExtractionError("target_height must be greater than zero")

        ffmpeg_bin = self._find_ffmpeg_for_hardsub()
        if ffmpeg_bin is None:
            raise AudioExtractionError(
                "FFmpeg build does not support hard subtitles (`subtitles` filter missing). "
                "Install FFmpeg with libass support (e.g. `brew install ffmpeg-full`) and retry."
            )

        if overwrite_input:
            target_path = input_video
        else:
            target_path = output_path or input_video.with_name(
                f"{input_video.stem}_hardsub{input_video.suffix}"
            )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temp_output = self._temporary_output_path(target_path, "hardsubtmp")

        video_filter = self._build_hardsub_filter_chain(
            subtitle_path=subtitle_path,
            target_width=target_width,
            target_height=target_height,
        )
        attempts = self._hardsub_attempts(ffmpeg_bin, requested_encoder=encoder)
        errors: list[str] = []

        for encoder in attempts:
            temp_output.unlink(missing_ok=True)
            cmd = self._build_hardsub_command(
                ffmpeg_bin=ffmpeg_bin,
                input_video=input_video,
                video_filter=video_filter,
                output_path=temp_output,
                encoder=encoder,
                crf=crf,
                preset=preset,
            )

            logger.info(
                "Burning hard subtitle (%s) → %s",
                self._encoder_label(encoder, crf=crf, preset=preset),
                target_path.name,
            )

            try:
                self._run(cmd)
                return self._finalize_output(temp_output, target_path, input_video)
            except Exception as exc:
                temp_output.unlink(missing_ok=True)
                errors.append(f"{encoder}: {exc}")
                logger.warning("Hard subtitle attempt failed (%s): %s", encoder, exc)

        raise AudioExtractionError(
            "Hard subtitle encoding failed after all attempts: "
            + " | ".join(errors[:3])
        )

    @staticmethod
    def _subtitle_codec_for_container(video_path: Path, subtitle_path: Path) -> str:
        container = video_path.suffix.lower()
        subtitle_ext = subtitle_path.suffix.lower()

        if container in {".mp4", ".m4v", ".mov"}:
            return "mov_text"
        if subtitle_ext == ".ass":
            return "ass"
        return "srt"

    @staticmethod
    def _subtitle_filter_arg(subtitle_path: Path) -> str:
        raw = str(subtitle_path)
        escaped = (
            raw
            .replace("\\", "\\\\")
            .replace(":", "\\:")
            .replace("'", "\\'")
            .replace(",", "\\,")
        )
        return f"subtitles=filename='{escaped}'"

    def _build_hardsub_filter_chain(
        self,
        subtitle_path: Path,
        target_width: Optional[int] = None,
        target_height: Optional[int] = None,
    ) -> str:
        filters: list[str] = []
        if target_width is not None and target_height is not None:
            filters.extend(self._scale_filters(target_width, target_height))
        filters.append(self._subtitle_filter_arg(subtitle_path))
        return ",".join(filters)

    @staticmethod
    def _scale_filters(target_width: int, target_height: int) -> list[str]:
        return [
            (
                "scale="
                f"w={target_width}:h={target_height}:"
                "force_original_aspect_ratio=decrease"
            ),
            f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2:color=black",
        ]

    def _supports_subtitles_filter(self, ffmpeg_bin: Optional[str] = None) -> bool:
        cmd = [ffmpeg_bin or self._ffmpeg, "-hide_banner", "-filters"]
        try:
            result = self._run(cmd, capture=True)
        except AudioExtractionError:
            return False

        text = f"{result.stdout}\n{result.stderr}".lower()
        return " subtitles " in text or "\n.. subtitles" in text

    def _find_ffmpeg_for_hardsub(self) -> Optional[str]:
        if self._supports_subtitles_filter(self._ffmpeg):
            return self._ffmpeg

        candidates = [
            "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
            "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
        ]
        for candidate in candidates:
            if Path(candidate).exists() and self._supports_subtitles_filter(candidate):
                return candidate

        return None

    # ── Internals ────────────────────────────────────────────────

    @staticmethod
    def _temporary_output_path(target_path: Path, tag: str) -> Path:
        return target_path.with_name(f".{target_path.stem}.{tag}{target_path.suffix}")

    def _finalize_output(
        self,
        temp_output: Path,
        target_path: Path,
        input_video: Path,
    ) -> Path:
        if self._settings.hard_subtitle_validate_output:
            self._validate_output_media(input_video, temp_output)
        temp_output.replace(target_path)
        return target_path

    def _validate_output_media(self, input_path: Path, output_path: Path) -> None:
        if not output_path.exists():
            raise AudioExtractionError(f"FFmpeg output missing: {output_path}")
        if output_path.stat().st_size <= 0:
            raise AudioExtractionError(f"FFmpeg output is empty: {output_path}")

        info = self.get_media_info(output_path)
        streams = info.get("streams", [])
        if not any(stream.get("codec_type") == "video" for stream in streams):
            raise AudioExtractionError("Validated output has no video stream")

        format_info = info.get("format", {})
        try:
            output_duration = float(format_info.get("duration") or 0.0)
        except (TypeError, ValueError):
            output_duration = 0.0
        if output_duration <= 0.0:
            raise AudioExtractionError("Validated output has invalid duration")

        try:
            input_duration = self.get_duration(input_path)
        except Exception:
            return

        tolerance = max(1.0, min(10.0, input_duration * 0.05))
        if abs(output_duration - input_duration) > tolerance:
            raise AudioExtractionError(
                "Validated output duration mismatch: "
                f"input={input_duration:.2f}s output={output_duration:.2f}s"
            )

    def _hardsub_attempts(
        self,
        ffmpeg_bin: str,
        requested_encoder: Optional[str] = None,
    ) -> list[str]:
        requested = str(
            requested_encoder or self._settings.hard_subtitle_encoder or "auto"
        ).strip().lower()
        attempts: list[str] = []

        def add(encoder: str) -> None:
            if encoder in attempts:
                return
            if encoder == "h264_videotoolbox" and not self._encoder_available(ffmpeg_bin, encoder):
                return
            attempts.append(encoder)

        hardware_requested = requested in {"auto", "videotoolbox", "h264_videotoolbox"}
        if hardware_requested and platform.system() == "Darwin":
            add("h264_videotoolbox")

        if requested in {"auto", "libx264", "x264"} or not attempts:
            add("libx264")

        return attempts or ["libx264"]

    def _encoder_available(self, ffmpeg_bin: str, encoder_name: str) -> bool:
        cmd = [ffmpeg_bin, "-hide_banner", "-encoders"]
        try:
            result = self._run(cmd, capture=True)
        except AudioExtractionError:
            return False

        text = f"{result.stdout}\n{result.stderr}"
        return encoder_name in text

    def _build_hardsub_command(
        self,
        ffmpeg_bin: str,
        input_video: Path,
        video_filter: str,
        output_path: Path,
        encoder: str,
        crf: int,
        preset: str,
    ) -> list[str]:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-nostats",
            "-i", str(input_video),
            "-vf", video_filter,
            "-map", "0:v:0",
            "-map", "0:a?",
            "-sn",
        ]

        if encoder == "h264_videotoolbox":
            cmd.extend([
                "-c:v", "h264_videotoolbox",
                "-allow_sw", "1",
                "-b:v", self._settings.hard_subtitle_hw_bitrate,
                "-maxrate", self._settings.hard_subtitle_hw_maxrate,
                "-bufsize", self._settings.hard_subtitle_hw_bufsize,
            ])
        else:
            cmd.extend([
                "-c:v", "libx264",
                "-preset", preset,
                "-crf", str(crf),
            ])

        cmd.extend([
            "-pix_fmt", "yuv420p",
            "-c:a", "copy",
        ])

        if output_path.suffix.lower() in {".mp4", ".m4v", ".mov"}:
            cmd.extend(["-movflags", "+faststart"])

        cmd.append(str(output_path))
        return cmd

    def _encoder_label(self, encoder: str, crf: int, preset: str) -> str:
        if encoder == "h264_videotoolbox":
            return "h264_videotoolbox hardware"
        return f"libx264 crf={crf} preset={preset}"

    @staticmethod
    def _run(
        cmd: list[str],
        capture: bool = False,
    ) -> subprocess.CompletedProcess:
        try:
            if capture:
                return subprocess.run(
                    cmd,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            return subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            raise AudioExtractionError(
                "FFmpeg/FFprobe not found. Install via: brew install ffmpeg"
            )
        except subprocess.CalledProcessError as exc:
            logger.error("FFmpeg error: %s", exc.stderr)
            raise AudioExtractionError(
                f"FFmpeg failed: {exc.stderr[:300]}"
            ) from exc
