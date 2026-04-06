"""Utilities: probing, thumbnails, progress, history, compression, bandwidth, file discovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("tg_uploader")

LOG_FORMAT = "%(asctime)s │ %(levelname)-7s │ %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"
PROGRESS_REFRESH_SECONDS = 0.15

VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".ts", ".m4v",
})
IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif",
})


# ═══════════════════════════════════════════════════════════════════════
# Logging
# ═══════════════════════════════════════════════════════════════════════


class _ConsoleProgressRenderer:
    def __init__(self, stream) -> None:
        term = os.getenv("TERM", "")
        self._stream = stream
        self._enabled = bool(getattr(stream, "isatty", lambda: False)()) and term.lower() != "dumb"
        self._entries: OrderedDict[int, str] = OrderedDict()
        self._rendered_lines = 0
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def write_log(self, message: str) -> None:
        with self._lock:
            if self._enabled:
                self._clear_progress_block()
            self._write_message(message)
            if self._enabled:
                self._render_progress_block()

    def update(self, key: int, text: str) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._clear_progress_block()
            self._entries[key] = text
            self._render_progress_block()

    def remove(self, key: int) -> None:
        if not self._enabled:
            return
        with self._lock:
            if key not in self._entries:
                return
            self._clear_progress_block()
            self._entries.pop(key, None)
            self._render_progress_block()

    def _write_message(self, message: str) -> None:
        text = message.rstrip("\n")
        self._stream.write(f"{text}\n" if text else "\n")
        self._stream.flush()

    def _clear_progress_block(self) -> None:
        if self._rendered_lines == 0:
            return
        self._stream.write(f"\x1b[{self._rendered_lines}F")
        for idx in range(self._rendered_lines):
            self._stream.write("\x1b[2K")
            if idx < self._rendered_lines - 1:
                self._stream.write("\x1b[1E")
        if self._rendered_lines > 1:
            self._stream.write(f"\x1b[{self._rendered_lines - 1}F")
        self._rendered_lines = 0

    def _render_progress_block(self) -> None:
        items = list(self._entries.values())
        self._rendered_lines = len(items)
        for text in items:
            self._stream.write("\x1b[2K")
            self._stream.write(f"{text}\n")
        self._stream.flush()


class _ProgressLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _PROGRESS_RENDERER.write_log(self.format(record))
        except Exception:
            self.handleError(record)


_PROGRESS_RENDERER = _ConsoleProgressRenderer(sys.stdout)

def setup_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)

    handler = _ProgressLogHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))
    root.addHandler(handler)

    logging.getLogger("telethon").setLevel(logging.WARNING)


# ═══════════════════════════════════════════════════════════════════════
# FFmpeg
# ═══════════════════════════════════════════════════════════════════════

def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


def has_ffprobe() -> bool:
    return shutil.which("ffprobe") is not None


# ═══════════════════════════════════════════════════════════════════════
# Video metadata
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class VideoMeta:
    duration: float = 0.0
    bitrate: int = 0
    width: int = 0
    height: int = 0
    codec: str = ""
    has_audio: bool = False

    @property
    def duration_int(self) -> int:
        return int(self.duration)

    def is_valid(self) -> bool:
        return self.duration > 0 and self.width > 0 and self.height > 0

    @property
    def resolution_str(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return "?"


def probe_video(filepath: Path) -> VideoMeta:
    meta = VideoMeta()
    if not has_ffprobe():
        return meta
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", str(filepath),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            return meta
        data = json.loads(r.stdout)
        fmt = data.get("format", {})
        meta.duration = float(fmt.get("duration", 0))
        meta.bitrate = int(fmt.get("bit_rate", 0))
        for stream in data.get("streams", []):
            ct = stream.get("codec_type", "")
            if ct == "video" and meta.width == 0:
                meta.width = int(stream.get("width", 0))
                meta.height = int(stream.get("height", 0))
                meta.codec = stream.get("codec_name", "")
                if meta.duration <= 0:
                    meta.duration = float(stream.get("duration", 0))
            elif ct == "audio":
                meta.has_audio = True
    except Exception:
        pass
    return meta


# ═══════════════════════════════════════════════════════════════════════
# Thumbnail
# ═══════════════════════════════════════════════════════════════════════

def find_manual_thumbnail(video_path: Path) -> Path | None:
    stem = video_path.stem
    folder = video_path.parent
    for ext in [".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff", ".tif"]:
        candidate = folder / f"{stem}{ext}"
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def generate_thumbnail(filepath: Path, meta: VideoMeta, thumb_width: int = 720) -> Path | None:
    if not has_ffmpeg():
        return None
    thumb_path = filepath.parent / f".thumb_{filepath.stem}.jpg"
    scale = f"scale={thumb_width}:-2"
    if meta.duration > 0:
        seeks = [meta.duration * 0.1, meta.duration * 0.25, meta.duration * 0.5, 1.0]
    else:
        seeks = [1.0, 5.0, 0.0]
    for s in seeks:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", str(max(s, 0)), "-i", str(filepath),
            "-vframes", "1", "-vf", scale, "-q:v", "4", str(thumb_path),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=15)
            if r.returncode == 0 and thumb_path.exists() and thumb_path.stat().st_size > 100:
                return thumb_path
        except Exception:
            continue
    cleanup_thumbnail(thumb_path)
    return None


def resolve_thumbnail(
    video_path: Path, meta: VideoMeta, thumb_size: int = 720,
) -> tuple[Path | None, bool]:
    """Returns (path, is_manual). is_manual=True → don't delete after upload."""
    manual = find_manual_thumbnail(video_path)
    if manual:
        return manual, True
    auto = generate_thumbnail(video_path, meta, thumb_size)
    if auto:
        return auto, False
    return None, False


def cleanup_thumbnail(thumb_path: Path | None) -> None:
    if thumb_path and thumb_path.exists():
        try:
            thumb_path.unlink()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════
# Compression
# ═══════════════════════════════════════════════════════════════════════

async def compress_video(filepath: Path) -> Path | None:
    if not has_ffmpeg():
        return None
    out = filepath.parent / f".compressed_{filepath.stem}.mp4"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "warning",
        "-i", str(filepath), "-c:v", "libx264", "-crf", "23", "-preset", "fast",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out),
    ]
    logger.info(f"  Mengkompresi {filepath.name}...")
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()
    if proc.returncode != 0:
        if out.exists():
            out.unlink()
        return None
    original = filepath.stat().st_size
    compressed = out.stat().st_size
    if compressed >= original * 0.95:
        out.unlink()
        return None
    ratio = (1 - compressed / original) * 100
    logger.info(f"  Terkompresi: {human_size(original)} → {human_size(compressed)} ({ratio:.0f}%)")
    return out


# ═══════════════════════════════════════════════════════════════════════
# Formatting
# ═══════════════════════════════════════════════════════════════════════

def human_size(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024.0:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024.0
    return f"{nbytes:.1f} PB"


def format_duration(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


# ═══════════════════════════════════════════════════════════════════════
# Progress
# ═══════════════════════════════════════════════════════════════════════

class UploadProgress:
    __slots__ = ("_id", "_name", "_start", "_last_print")

    def __init__(self, name: str, prefix: str = "") -> None:
        self._id = id(self)
        self._name = f"{prefix} · {name}" if prefix else name
        self._start = time.monotonic()
        self._last_print = 0.0

    def close(self) -> None:
        _PROGRESS_RENDERER.remove(self._id)

    def _live_text(self, current: int, total: int, speed: float, eta_s: float) -> str:
        pct = (current / total * 100) if total else 0
        return (
            f"  ↑ {self._name}: {pct:5.1f}%  │  {speed / 1048576:.1f} MB/s  │  "
            f"{human_size(current)}/{human_size(total)}  │  ETA {format_duration(eta_s)}"
        )

    def __call__(self, current: int, total: int) -> None:
        now = time.monotonic()
        if now - self._last_print < PROGRESS_REFRESH_SECONDS and current < total:
            return
        self._last_print = now
        elapsed = max(now - self._start, 0.001)
        speed = current / elapsed
        eta_s = ((total - current) / speed) if speed > 0 else 0

        if _PROGRESS_RENDERER.enabled:
            if current >= total:
                self.close()
            else:
                _PROGRESS_RENDERER.update(self._id, self._live_text(current, total, speed, eta_s))
            return

        print(
            f"\r{self._live_text(current, total, speed, eta_s)}   ",
            end="", flush=True,
        )
        if current >= total:
            avg = (total / elapsed) / 1048576
            print(
                f"\r  ✓ {self._name}: done  │  avg {avg:.1f} MB/s  │  "
                f"{human_size(total)}  │  {format_duration(elapsed)}          "
            )


class AlbumProgress:
    __slots__ = ("_id", "_name", "_start", "_last_print")

    def __init__(self, name: str, prefix: str = "") -> None:
        self._id = id(self)
        self._name = f"{prefix} · {name}" if prefix else name
        self._start = time.monotonic()
        self._last_print = 0.0

    def close(self) -> None:
        _PROGRESS_RENDERER.remove(self._id)

    def _live_text(self, current: float, total: int, eta_s: float) -> str:
        pct = (current / total * 100) if total else 0
        return (
            f"  ↑ {self._name}: {pct:5.1f}%  │  "
            f"{current:.1f}/{total} file  │  ETA {format_duration(eta_s)}"
        )

    def __call__(self, current: float, total: int) -> None:
        now = time.monotonic()
        if now - self._last_print < PROGRESS_REFRESH_SECONDS and current < total:
            return
        self._last_print = now
        elapsed = max(now - self._start, 0.001)
        speed = current / elapsed
        eta_s = ((total - current) / speed) if speed > 0 else 0

        if _PROGRESS_RENDERER.enabled:
            if current >= total:
                self.close()
            else:
                _PROGRESS_RENDERER.update(self._id, self._live_text(current, total, eta_s))
            return

        print(
            f"\r{self._live_text(current, total, eta_s)}   ",
            end="", flush=True,
        )
        if current >= total:
            print(
                f"\r  ✓ {self._name}: done  │  {total} file  │  {format_duration(elapsed)}          "
            )


class GlobalProgress:
    def __init__(self, total_files: int, total_bytes: int) -> None:
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.done_files = 0
        self.done_bytes = 0
        self.failed_files = 0
        self._start = time.monotonic()
        self._lock = asyncio.Lock()

    async def file_done(self, size: int, success: bool) -> None:
        async with self._lock:
            if success:
                self.done_files += 1
                self.done_bytes += size
            else:
                self.failed_files += 1
            self._print()

    async def batch_done(self, size: int, count: int, success: bool) -> None:
        async with self._lock:
            if success:
                self.done_files += count
                self.done_bytes += size
            else:
                self.failed_files += count
            self._print()

    def _print(self) -> None:
        elapsed = time.monotonic() - self._start
        total = self.done_files + self.failed_files
        pct = (total / self.total_files * 100) if self.total_files else 0
        speed = self.done_bytes / elapsed if elapsed > 0 else 0
        if speed > 0 and self.done_bytes < self.total_bytes:
            eta = format_duration((self.total_bytes - self.done_bytes) / speed)
        else:
            eta = "-"
        logger.info(
            f"📊 {total}/{self.total_files} ({pct:.0f}%)  │  "
            f"✓{self.done_files} ✗{self.failed_files}  │  "
            f"{human_size(self.done_bytes)}  │  {speed / 1048576:.1f} MB/s  │  ETA {eta}"
        )


# ═══════════════════════════════════════════════════════════════════════
# Bandwidth limiter
# ═══════════════════════════════════════════════════════════════════════

class BandwidthLimiter:
    def __init__(self, bytes_per_sec: int) -> None:
        self.rate = bytes_per_sec
        self._tokens = float(bytes_per_sec) if bytes_per_sec > 0 else 0
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, nbytes: int) -> None:
        if self.rate <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            self._tokens += (now - self._last) * self.rate
            self._last = now
            if self._tokens > self.rate * 2:
                self._tokens = self.rate * 2
            self._tokens -= nbytes
            if self._tokens < 0:
                await asyncio.sleep(-self._tokens / self.rate)
                self._tokens = 0


# ═══════════════════════════════════════════════════════════════════════
# File discovery
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FileEntry:
    path: Path
    is_video: bool
    is_image: bool
    thumbnail: Path | None = None
    thumb_is_manual: bool = False


def collect_files(
    folder: Path, recursive: bool = False, sort: str = "name",
) -> list[FileEntry]:
    """Collect videos and images. Same-stem images become thumbnails."""
    if folder.is_file():
        ext = folder.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            m = find_manual_thumbnail(folder)
            return [FileEntry(
                path=folder, is_video=True, is_image=False,
                thumbnail=m, thumb_is_manual=m is not None,
            )]
        elif ext in IMAGE_EXTENSIONS:
            return [FileEntry(path=folder, is_video=False, is_image=True)]
        return []

    if recursive:
        all_files = [f for f in folder.rglob("*") if f.is_file() and not f.name.startswith(".")]
    else:
        all_files = [f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")]

    videos: list[Path] = []
    images: dict[str, Path] = {}
    for f in all_files:
        ext = f.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            videos.append(f)
        elif ext in IMAGE_EXTENSIONS:
            key = f"{f.parent}|||{f.stem}"
            if key not in images:
                images[key] = f

    video_keys = {f"{v.parent}|||{v.stem}" for v in videos}
    entries: list[FileEntry] = []

    for v in videos:
        key = f"{v.parent}|||{v.stem}"
        thumb = images.get(key)
        entries.append(FileEntry(
            path=v, is_video=True, is_image=False,
            thumbnail=thumb, thumb_is_manual=thumb is not None,
        ))

    for key, img in images.items():
        if key not in video_keys:
            entries.append(FileEntry(path=img, is_video=False, is_image=True))

    sort_keys = {
        "smallest": lambda e: e.path.stat().st_size,
        "largest": lambda e: -e.path.stat().st_size,
        "newest": lambda e: -e.path.stat().st_mtime,
        "oldest": lambda e: e.path.stat().st_mtime,
    }
    entries.sort(key=sort_keys.get(sort, lambda e: e.path.name.lower()))
    return entries


def collect_files_small_mode(
    folder: Path, recursive: bool = False,
) -> tuple[list[Path], list[Path]]:
    """
    Collect files for small mode.
    Returns (videos_sorted_largest_first, photos).
    Same-stem matching NOT applied (all files uploaded).
    """
    if recursive:
        all_files = [f for f in folder.rglob("*") if f.is_file() and not f.name.startswith(".")]
    else:
        all_files = [f for f in folder.iterdir() if f.is_file() and not f.name.startswith(".")]

    videos: list[Path] = []
    photos: list[Path] = []

    for f in all_files:
        ext = f.suffix.lower()
        if ext in VIDEO_EXTENSIONS:
            videos.append(f)
        elif ext in IMAGE_EXTENSIONS:
            photos.append(f)

    # Sort videos largest first
    videos.sort(key=lambda f: -f.stat().st_size)
    # Sort photos by name
    photos.sort(key=lambda f: f.name.lower())

    return videos, photos


# ═══════════════════════════════════════════════════════════════════════
# Upload history
# ═══════════════════════════════════════════════════════════════════════

def _file_fingerprint(filepath: Path) -> str:
    stat = filepath.stat()
    return hashlib.md5(f"{filepath.name}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()


class UploadHistory:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._data: dict[str, Any] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass

    def is_uploaded(self, filepath: Path, target: str) -> bool:
        fp = _file_fingerprint(filepath)
        return f"{fp}:{target}" in self._data

    def mark_uploaded(self, filepath: Path, target: str) -> None:
        fp = _file_fingerprint(filepath)
        self._data[f"{fp}:{target}"] = {
            "file": filepath.name,
            "size": filepath.stat().st_size,
            "time": time.time(),
        }
        try:
            self._path.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════
# Failed tracker
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class FailedFile:
    filename: str
    reason: str


class FailedTracker:
    def __init__(self) -> None:
        self.failures: list[FailedFile] = []
        self._lock = asyncio.Lock()

    async def add(self, filename: str, reason: str) -> None:
        async with self._lock:
            self.failures.append(FailedFile(filename, reason))

    def print_report(self) -> None:
        if not self.failures:
            return
        print()
        logger.error(f"  File gagal ({len(self.failures)}):")
        for i, f in enumerate(self.failures, 1):
            logger.error(f"   {i}. {f.filename}")
            logger.error(f"      Alasan: {f.reason}")
