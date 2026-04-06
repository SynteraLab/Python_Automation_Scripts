"""
Shared utilities: FFmpeg detection, logging bootstrap, filesystem helpers.
"""

import logging
import os
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

# ── Supported video extensions ────────────────────────────────────────────────
VIDEO_EXTENSIONS = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".3gp", ".ts", ".mts", ".m2ts",
    ".vob", ".ogv", ".rm", ".rmvb", ".asf", ".divx", ".f4v",
}


# ── FFmpeg / FFprobe discovery ────────────────────────────────────────────────

def _probe_binary(name: str) -> Tuple[bool, str]:
    """Return *(found, version_or_error)* for a CLI binary."""
    try:
        r = subprocess.run(
            [name, "-version"], capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            return True, r.stdout.split("\n")[0]
        return False, f"{name} exited with code {r.returncode}"
    except FileNotFoundError:
        return False, f"{name} not found in PATH"
    except subprocess.TimeoutExpired:
        return False, f"{name} version check timed out"
    except Exception as exc:
        return False, str(exc)


def check_ffmpeg(path: str = "ffmpeg") -> Tuple[bool, str]:
    return _probe_binary(path)


def check_ffprobe(path: str = "ffprobe") -> Tuple[bool, str]:
    return _probe_binary(path)


def find_ffmpeg() -> Tuple[str, str]:
    """
    Locate *ffmpeg* and *ffprobe* binaries.

    Checks ``$PATH`` first, then common Homebrew locations on macOS.
    Returns the resolved paths (or bare names as a last resort).
    """
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe

    if platform.system() == "Darwin":
        for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
            ff = os.path.join(prefix, "ffmpeg")
            fp = os.path.join(prefix, "ffprobe")
            if os.path.isfile(ff) and os.path.isfile(fp):
                return ff, fp

    return ffmpeg or "ffmpeg", ffprobe or "ffprobe"


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(log_dir: Path, level: int = logging.INFO) -> Path:
    """
    Configure the ``video2frames`` logger with file + console handlers.

    Returns the path to the log file created for this session.
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"v2f_{ts}.log"

    logger = logging.getLogger("video2frames")
    logger.setLevel(level)
    # avoid duplicate handlers on repeated calls
    if not logger.handlers:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(name)s | %(levelname)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger.addHandler(ch)

    return log_file


# ── Filesystem helpers ────────────────────────────────────────────────────────

def format_size(n: int) -> str:
    """Human-readable byte size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} PB"


def format_duration(seconds: float) -> str:
    """Human-readable duration."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m {s:.0f}s"
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}h {int(m)}m {s:.0f}s"


def find_video_files(directory: Path) -> List[Path]:
    """Return sorted list of video files directly inside *directory*."""
    d = Path(directory)
    if not d.is_dir():
        return []
    return sorted(
        f for f in d.iterdir()
        if f.is_file() and f.suffix.lower() in VIDEO_EXTENSIONS
    )


def estimate_png_size(width: int, height: int) -> int:
    """Conservative estimate of a single PNG frame size (bytes)."""
    return int(width * height * 3 * 0.40)


def get_disk_free_space(path: Path) -> int:
    """Free bytes on the filesystem containing *path*."""
    p = Path(path)
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(str(p)).free


def get_ffmpeg_install_instructions() -> str:
    """Platform-aware install hint for FFmpeg."""
    system = platform.system()
    if system == "Darwin":
        return (
            "Install FFmpeg on macOS:\n"
            "  brew install ffmpeg\n"
            "\n"
            "If Homebrew is not installed:\n"
            "  /bin/bash -c \"$(curl -fsSL "
            "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        )
    if system == "Linux":
        return (
            "Install FFmpeg on Linux:\n"
            "  Ubuntu/Debian:  sudo apt install ffmpeg\n"
            "  Fedora:         sudo dnf install ffmpeg\n"
            "  Arch:           sudo pacman -S ffmpeg"
        )
    return (
        "Install FFmpeg:\n"
        "  Download from https://ffmpeg.org/download.html\n"
        "  and add to your system PATH."
    )