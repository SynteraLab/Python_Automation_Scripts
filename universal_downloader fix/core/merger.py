"""
FFmpeg merger for combining video/audio streams.
"""

import asyncio
import subprocess
import shutil
import re
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional, List, Dict
import logging
import json

from utils.progress import ProgressBar

logger = logging.getLogger(__name__)


def _emit_status(status_callback: Optional[Callable[[str], None]], message: str) -> None:
    if not status_callback:
        return
    try:
        status_callback(message)
    except Exception:
        pass


class FFmpegError(Exception):
    """FFmpeg-related errors."""
    pass


class FFmpegMerger:
    """Handles FFmpeg operations for merging and converting media."""

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        self._available = self._verify_ffmpeg()

    def _verify_ffmpeg(self) -> bool:
        """Verify FFmpeg is available."""
        path = shutil.which(self.ffmpeg_path)
        if not path:
            logger.warning(
                f"FFmpeg not found. Install with: brew install ffmpeg (macOS) "
                f"or sudo apt install ffmpeg (Linux)"
            )
            return False
        logger.debug(f"FFmpeg found at: {path}")
        return True

    @property
    def is_available(self) -> bool:
        return self._available

    async def merge_video_audio(
        self, video_path: str, audio_path: str,
        output_path: str,
        codec: str = "copy",
        progress: Optional[ProgressBar] = None,
        total_duration: Optional[float] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Merge separate video and audio files."""
        cmd = [
            self.ffmpeg_path, '-y',
            '-i', video_path,
            '-i', audio_path,
            '-c', codec,
            '-map', '0:v:0',
            '-map', '1:a:0',
            '-movflags', '+faststart',
            output_path
        ]
        await self._run_ffmpeg(
            cmd,
            progress=progress,
            total_duration=total_duration,
            stage="merge",
            detail="video + audio",
            status_callback=status_callback,
        )

    async def concat_segments(
        self,
        concat_file: str,
        output_path: str,
        codec: str = "copy",
        progress: Optional[ProgressBar] = None,
        total_duration: Optional[float] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Concatenate media segments using concat demuxer."""
        cmd = [
            self.ffmpeg_path, '-y',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file,
            '-c', codec,
            '-movflags', '+faststart',
            output_path
        ]
        await self._run_ffmpeg(
            cmd,
            progress=progress,
            total_duration=total_duration,
            stage="merge",
            detail="concat segments",
            status_callback=status_callback,
        )

    async def download_stream(
        self, url: str, output_path: str,
        headers: Optional[Dict[str, str]] = None,
        progress: Optional[ProgressBar] = None,
        total_duration: Optional[float] = None,
        stage: str = "ffmpeg",
        detail: Optional[str] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Download HLS/DASH stream using FFmpeg."""
        cmd = [self.ffmpeg_path, '-y']
        if headers:
            header_str = '\r\n'.join(f'{k}: {v}' for k, v in headers.items())
            cmd.extend(['-headers', header_str])
        cmd.extend([
            '-i', url,
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            '-movflags', '+faststart',
            output_path
        ])
        await self._run_ffmpeg(
            cmd,
            progress=progress,
            total_duration=total_duration,
            stage=stage,
            detail=detail or "stream copy",
            status_callback=status_callback,
        )

    async def convert(
        self, input_path: str, output_path: str,
        video_codec: str = "libx264", audio_codec: str = "aac", **kwargs
    ) -> None:
        """Convert media file to different format."""
        cmd = [
            self.ffmpeg_path, '-y',
            '-i', input_path,
            '-c:v', video_codec,
            '-c:a', audio_codec,
        ]
        if 'crf' in kwargs:
            cmd.extend(['-crf', str(kwargs['crf'])])
        if 'preset' in kwargs:
            cmd.extend(['-preset', kwargs['preset']])
        if 'audio_bitrate' in kwargs:
            cmd.extend(['-b:a', kwargs['audio_bitrate']])
        cmd.append(output_path)
        await self._run_ffmpeg(cmd)

    async def extract_audio(
        self, input_path: str, output_path: str, codec: str = "copy"
    ) -> None:
        """Extract audio from video file."""
        cmd = [
            self.ffmpeg_path, '-y',
            '-i', input_path,
            '-vn', '-c:a', codec,
            output_path
        ]
        await self._run_ffmpeg(cmd)

    async def get_duration(self, file_path: str) -> Optional[float]:
        """Get duration of media file."""
        cmd = [
            self.ffprobe_path, '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            file_path
        ]
        try:
            result = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await result.communicate()
            return float(stdout.decode().strip())
        except Exception:
            return None

    async def get_info(self, file_path: str) -> Dict:
        """Get detailed media information."""
        cmd = [
            self.ffprobe_path, '-v', 'quiet',
            '-print_format', 'json',
            '-show_format', '-show_streams',
            file_path
        ]
        result = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await result.communicate()
        return json.loads(stdout.decode())

    async def _run_ffmpeg(
        self,
        cmd: List[str],
        progress: Optional[ProgressBar] = None,
        total_duration: Optional[float] = None,
        stage: str = "ffmpeg",
        detail: str = "",
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Run FFmpeg command."""
        logger.debug(f"Running: {' '.join(cmd)}")
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        assert process.stderr is not None
        stderr_pipe = process.stderr
        recent_stderr = deque(maxlen=8)
        status_state = {"last_emit_at": 0.0}

        while True:
            line = await stderr_pipe.readline()
            if not line:
                break

            decoded = line.decode('utf-8', errors='replace').strip()
            if not decoded:
                continue

            recent_stderr.append(decoded)
            if progress is None or 'time=' not in decoded:
                continue

            time_match = re.search(r'time=(\d+:\d+:\d+(?:\.\d+)?)', decoded)
            size_match = re.search(r'size=\s*([^\s]+)', decoded)
            speed_match = re.search(r'speed=\s*([^\s]+)', decoded)
            bitrate_match = re.search(r'bitrate=\s*([^\s]+)', decoded)

            current_time = ProgressBar.parse_duration_text(time_match.group(1)) if time_match else None
            transferred_bytes = ProgressBar.parse_size_text(size_match.group(1)) if size_match else None

            detail_parts = []
            if detail:
                detail_parts.append(detail)
            if speed_match:
                speed_text = speed_match.group(1)
                if speed_text not in {'N/A', '0x'}:
                    detail_parts.append(f"ffmpeg {speed_text}")
            if bitrate_match:
                bitrate_text = bitrate_match.group(1)
                if bitrate_text != 'N/A':
                    detail_parts.append(f"bitrate {bitrate_text}")

            progress.set(
                value=current_time,
                total=total_duration,
                transferred_bytes=transferred_bytes,
                stage=stage,
                detail=' | '.join(detail_parts),
            )

            now = time.monotonic()
            last_emit_at = float(status_state.get("last_emit_at", 0.0))
            if current_time is not None and (not last_emit_at or (now - last_emit_at) >= 1.0):
                status_state["last_emit_at"] = now
                detail_items = []
                if total_duration and total_duration > 0:
                    percent = int(max(0.0, min(100.0, (current_time / total_duration) * 100.0)))
                    detail_items.append(f"{percent}%")
                    detail_items.append(
                        f"{ProgressBar._format_time(current_time)} / {ProgressBar._format_time(total_duration)}"
                    )
                else:
                    detail_items.append(f"{ProgressBar._format_time(current_time)} processed")
                if transferred_bytes is not None:
                    detail_items.append(f"size {ProgressBar._format_size(transferred_bytes)}")
                if speed_match and speed_match.group(1) not in {'N/A', '0x'}:
                    detail_items.append(f"speed {speed_match.group(1)}")
                if bitrate_match and bitrate_match.group(1) != 'N/A':
                    detail_items.append(f"bitrate {bitrate_match.group(1)}")
                _emit_status(status_callback, f"{stage.upper()} progress: {' • '.join(detail_items)}")

        await process.wait()
        if process.returncode != 0:
            error_msg = recent_stderr[-1] if recent_stderr else "Unknown error"
            logger.error(f"FFmpeg failed: {error_msg[:500]}")
            if progress is not None:
                progress.interrupt("ffmpeg stopped")
            raise FFmpegError(f"FFmpeg failed with code {process.returncode}")
        if progress is not None:
            progress.finish(detail=detail or stage)
        logger.debug("FFmpeg completed successfully")
