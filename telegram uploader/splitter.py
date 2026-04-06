"""FFmpeg video splitter with faststart post-processing."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

from utils import has_ffmpeg, probe_video, human_size, VideoMeta

logger = logging.getLogger("tg_uploader")


class VideoSplitter:
    def __init__(self, max_size_bytes: int) -> None:
        self.max_size_bytes = max_size_bytes

    def needs_split(self, filepath: Path) -> bool:
        return filepath.stat().st_size > self.max_size_bytes

    async def split(self, filepath: Path) -> list[Path]:
        if not has_ffmpeg():
            logger.error("FFmpeg tidak ditemukan.")
            return []

        meta = probe_video(filepath)
        seg_dur = self._calc_segment_duration(filepath, meta)

        parts = await self._run_split(filepath, seg_dur)
        if not parts:
            return []

        # Retry shorter if oversized
        oversized = any(p.stat().st_size > self.max_size_bytes * 1.05 for p in parts)
        if oversized and seg_dur > 15:
            logger.info("  Parts terlalu besar, retry...")
            self.cleanup_parts(filepath)
            parts = await self._run_split(filepath, seg_dur * 0.65)
            if not parts:
                return []

        total_size = sum(p.stat().st_size for p in parts)
        logger.info(f"  Split selesai: {len(parts)} parts ({human_size(total_size)})")

        # Post-process: faststart per part (safe per-file, NOT with -f segment)
        parts = await self._faststart_parts(parts)
        return parts

    async def _run_split(self, filepath: Path, seg_dur: float) -> list[Path]:
        work_dir = self._work_dir(filepath)
        work_dir.mkdir(exist_ok=True)
        suffix = filepath.suffix or ".mp4"
        safe_prefix = "part"
        pattern = str(work_dir / f"{safe_prefix}%03d{suffix}")

        # NO -movflags +faststart — conflicts with -f segment, corrupts files
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
            "-i", str(filepath), "-c", "copy", "-map", "0",
            "-f", "segment", "-segment_time", str(int(max(seg_dur, 10))),
            "-reset_timestamps", "1", "-avoid_negative_ts", "make_zero",
            pattern,
        ]

        logger.info(f"  Splitting {filepath.name} (segment ≈{int(seg_dur)}s)")
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr_bytes = await proc.communicate()

        if proc.returncode != 0:
            logger.error(f"  FFmpeg error:\n{stderr_bytes.decode(errors='replace')[-600:]}")
            shutil.rmtree(work_dir, ignore_errors=True)
            return []

        # iterdir (not glob) — safe for special chars like []
        parts = sorted(
            p for p in work_dir.iterdir()
            if p.is_file()
            and p.name.startswith(safe_prefix)
            and p.suffix.lower() == suffix.lower()
        )
        if not parts:
            logger.error(f"  Tidak ada parts untuk {filepath.name}")
            shutil.rmtree(work_dir, ignore_errors=True)
            return []
        return parts

    async def _faststart_parts(self, parts: list[Path]) -> list[Path]:
        """Apply faststart to each part so Telegram can stream them."""
        logger.info(f"  Applying faststart to {len(parts)} parts...")
        fixed = []
        for part in parts:
            out = part.parent / f"_fs_{part.name}"
            cmd = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
                "-i", str(part), "-c", "copy", "-movflags", "+faststart", str(out),
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode == 0 and out.exists() and out.stat().st_size > 0:
                part.unlink()
                out.rename(part)
            else:
                if out.exists():
                    out.unlink()
            fixed.append(part)
        return fixed

    def _calc_segment_duration(self, filepath: Path, meta: VideoMeta) -> float:
        total_size = filepath.stat().st_size
        duration = meta.duration
        if duration <= 0:
            bitrate = meta.bitrate if meta.bitrate > 0 else 8_000_000
            duration = (total_size * 8) / bitrate
        target = self.max_size_bytes * 0.88
        return max(duration * (target / total_size), 10.0)

    @staticmethod
    def _work_dir(filepath: Path) -> Path:
        return filepath.parent / f".split_{filepath.stem}"

    @staticmethod
    def cleanup_parts(filepath: Path) -> None:
        d = filepath.parent / f".split_{filepath.stem}"
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
