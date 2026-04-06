"""
Progress tracking and terminal display helpers.
"""

import re
import sys
import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple
import shutil


class Colors:
    """ANSI color codes for terminal output."""

    RESET = '\033[0m'
    BOLD = '\033[1m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    DIM = '\033[2m'
    BROWN = '\033[38;2;91;58;34m'
    TAN = '\033[38;2;166;132;96m'


@dataclass
class DownloadProgress:
    """Tracks download progress."""

    total_bytes: int
    downloaded_bytes: int = 0
    start_time: float = 0.0
    current_speed: float = 0.0

    @property
    def progress(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return (self.downloaded_bytes / self.total_bytes) * 100

    @property
    def eta(self) -> Optional[int]:
        if self.current_speed <= 0:
            return None
        remaining = self.total_bytes - self.downloaded_bytes
        return int(remaining / self.current_speed)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time


class ProgressBar:
    """Flexible terminal progress bar for download workflows."""

    SPINNER = ["|", "/", "-", "\\"]
    FILLED_BAR = "▰"
    EMPTY_BAR = "▱"
    ANSI_PATTERN = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
    SIZE_PATTERN = re.compile(r"(?i)(\d+(?:\.\d+)?)\s*([kmgtpe]?i?b)")

    def __init__(
        self,
        total: Optional[float],
        description: str = "",
        unit: str = "B",
        bar_length: int = 24,
        show_speed: bool = True,
        show_eta: bool = True,
        stage: str = "download",
    ):
        self.total = float(total) if total and total > 0 else None
        self.description = description or "download"
        self.unit = unit
        self.bar_length = bar_length
        self.show_speed = show_speed
        self.show_eta = show_eta
        self.stage = stage

        self.current = 0.0
        self.transferred_bytes = 0.0
        self.start_time = time.time()
        self.last_render = 0.0
        self.spinner_idx = 0

        self._detail = ""
        self._external_speed: Optional[float] = None
        self._external_eta: Optional[float] = None
        self._finished = False
        self._interactive = sys.stdout.isatty()
        self._last_visible_length = 0
        self._samples: Deque[Tuple[float, float, float]] = deque(maxlen=60)
        self._lock = threading.Lock()
        self._record_sample()

    @property
    def elapsed(self) -> float:
        return max(0.0, time.time() - self.start_time)

    @property
    def progress(self) -> float:
        if not self.total or self.total <= 0:
            return 0.0
        return max(0.0, min(100.0, (self.current / self.total) * 100.0))

    @property
    def speed(self) -> float:
        if self._external_speed and self._external_speed > 0:
            return self._external_speed
        byte_speed = self._byte_speed()
        if byte_speed > 0:
            return byte_speed
        return self._unit_speed()

    @property
    def eta(self) -> Optional[int]:
        if self._external_eta is not None and self._external_eta >= 0:
            return int(self._external_eta)
        if not self.total or self.total <= 0:
            return None
        if self.current >= self.total:
            return 0

        rate = 0.0
        if self.unit == "B" and self._external_speed and self._external_speed > 0:
            rate = self._external_speed
        elif self.unit == "B":
            rate = self._byte_speed()
        else:
            rate = self._unit_speed()

        if rate <= 0:
            return None

        remaining = self.total - self.current
        return int(max(0.0, remaining / rate))

    def update(
        self,
        amount: float,
        byte_amount: Optional[float] = None,
        total: Optional[float] = None,
        stage: Optional[str] = None,
        detail: Optional[str] = None,
        speed: Optional[float] = None,
        eta: Optional[float] = None,
    ) -> None:
        with self._lock:
            self.current += amount
            if self.unit == "B" and byte_amount is None:
                byte_amount = amount
            if byte_amount is not None:
                self.transferred_bytes += byte_amount
            if total and total > 0:
                self.total = float(total)
            if stage:
                self.stage = stage
            if detail is not None:
                self._detail = detail
            self._external_speed = speed
            self._external_eta = eta
            self._record_sample()
            self._render_if_needed(force=self._should_force_render())

    def set(
        self,
        value: Optional[float] = None,
        total: Optional[float] = None,
        transferred_bytes: Optional[float] = None,
        stage: Optional[str] = None,
        detail: Optional[str] = None,
        speed: Optional[float] = None,
        eta: Optional[float] = None,
    ) -> None:
        with self._lock:
            if value is not None:
                self.current = float(value)
                if self.unit == "B" and transferred_bytes is None:
                    transferred_bytes = value
            if total and total > 0:
                self.total = float(total)
            if transferred_bytes is not None:
                self.transferred_bytes = float(transferred_bytes)
            if stage:
                self.stage = stage
            if detail is not None:
                self._detail = detail
            self._external_speed = speed
            self._external_eta = eta
            self._record_sample()
            self._render_if_needed(force=self._should_force_render())

    def set_stage(self, stage: str, detail: Optional[str] = None) -> None:
        with self._lock:
            self.stage = stage
            if detail is not None:
                self._detail = detail
            self._render_if_needed(force=True)

    def finish(self, detail: Optional[str] = None) -> None:
        with self._lock:
            if self._finished:
                return
            if detail is not None:
                self._detail = detail
            if self.total is not None and self.current < self.total:
                self.current = self.total
            if self.unit == "B" and self.transferred_bytes < self.current:
                self.transferred_bytes = self.current
            self._record_sample()
            self._finished = True
            self._render(final=True, status_symbol="OK", status_color=Colors.GREEN)

    def interrupt(self, message: str = "") -> None:
        with self._lock:
            if self._finished:
                return
            if message:
                self._detail = message
            self._finished = True
            self._render(final=True, status_symbol="!", status_color=Colors.YELLOW)

    def error(self, message: str = "") -> None:
        with self._lock:
            if self._finished:
                return
            if message:
                self._detail = message
            self._finished = True
            self._render(final=True, status_symbol="X", status_color=Colors.RED)

    def _record_sample(self) -> None:
        self._samples.append((time.time(), self.current, self.transferred_bytes))

    def _should_force_render(self) -> bool:
        if self.total is not None and self.current >= self.total:
            return True
        return False

    def _render_if_needed(self, force: bool = False) -> None:
        if self._finished:
            return
        now = time.time()
        interval = 0.10 if self._interactive else 0.90
        if force or (now - self.last_render) >= interval:
            self._render()

    def _unit_speed(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        start = self._samples[0]
        end = self._samples[-1]
        delta_t = end[0] - start[0]
        if delta_t <= 0:
            return 0.0
        return max(0.0, (end[1] - start[1]) / delta_t)

    def _byte_speed(self) -> float:
        if len(self._samples) < 2:
            return 0.0
        start = self._samples[0]
        end = self._samples[-1]
        delta_t = end[0] - start[0]
        if delta_t <= 0:
            return 0.0
        return max(0.0, (end[2] - start[2]) / delta_t)

    def _render(
        self,
        final: bool = False,
        status_symbol: Optional[str] = None,
        status_color: Optional[str] = None,
    ) -> None:
        if not final and self._finished:
            return

        self.last_render = time.time()
        try:
            term_width = shutil.get_terminal_size().columns
        except Exception:
            term_width = 100

        parts = []
        label = self.description[:22]
        stage = (self.stage or "download").upper()[:10]

        if final:
            indicator = status_symbol or "OK"
            color = status_color or Colors.GREEN
        else:
            indicator = self.SPINNER[self.spinner_idx % len(self.SPINNER)]
            color = Colors.CYAN
            self.spinner_idx += 1

        prefix = f"{color}{indicator}{Colors.RESET} [{stage}] {label:<22}"
        parts.append(prefix)

        if self.total and self.total > 0:
            filled = int(self.bar_length * min(self.current / self.total, 1.0))
            filled = max(0, min(self.bar_length, filled))
            filled_bar = self.FILLED_BAR * filled
            empty_bar = self.EMPTY_BAR * (self.bar_length - filled)
            bar = f"{Colors.BROWN}{filled_bar}{Colors.TAN}{empty_bar}{Colors.RESET}"
            parts.append(f"[{bar}]")
            parts.append(f"{self.progress:5.1f}%")

        primary = self._primary_progress_text()
        if primary:
            parts.append(primary)

        secondary = self._secondary_progress_text(final=final)
        if secondary:
            parts.extend(secondary)

        if self._detail:
            parts.append(self._detail[:40])

        line = " | ".join(parts)
        visible_line = self.ANSI_PATTERN.sub("", line)
        if len(visible_line) > term_width:
            visible_line = visible_line[: max(0, term_width - 1)]
            line = visible_line

        padding = " " * max(0, self._last_visible_length - len(visible_line))
        self._last_visible_length = len(visible_line)

        if self._interactive:
            sys.stdout.write(f"\r{line}{padding}")
            if final:
                sys.stdout.write("\n")
        else:
            sys.stdout.write(f"{line}{padding}\n")
        sys.stdout.flush()

    def _primary_progress_text(self) -> str:
        if self.unit == "B":
            current = self._format_size(self.current)
            if self.total and self.total > 0:
                return f"{current}/{self._format_size(self.total)}"
            return current

        if self.unit == "s":
            current = self._format_time(self.current)
            if self.total and self.total > 0:
                return f"{current}/{self._format_time(self.total)}"
            return f"time {current}"

        if self.unit == "%":
            if self.total and self.total > 0:
                return f"{self.current:5.1f}%"
            return f"{self.current:5.1f}%"

        current_text = self._format_unit_value(self.current)
        if self.total and self.total > 0:
            total_text = self._format_unit_value(self.total)
            return f"{current_text}/{total_text} {self.unit}"
        return f"{current_text} {self.unit}"

    def _secondary_progress_text(self, final: bool = False) -> list[str]:
        parts: list[str] = []

        if self.unit != "B" and self.transferred_bytes > 0:
            parts.append(f"data {self._format_size(self.transferred_bytes)}")

        if self.show_speed:
            speed_text = self._speed_text(final=final)
            if speed_text:
                parts.append(speed_text)

        if self.show_eta and not final:
            eta = self.eta
            if eta is not None:
                parts.append(f"ETA {self._format_time(eta)}")

        if final:
            parts.append(f"elapsed {self._format_time(self.elapsed)}")

        return parts

    def _speed_text(self, final: bool = False) -> str:
        if final and self.transferred_bytes > 0 and self.elapsed > 0:
            return f"avg {self._format_size(self.transferred_bytes / self.elapsed)}/s"

        if self._external_speed and self._external_speed > 0:
            return f"{self._format_size(self._external_speed)}/s"

        byte_speed = self._byte_speed()
        if byte_speed > 0:
            return f"{self._format_size(byte_speed)}/s"

        unit_speed = self._unit_speed()
        if self.unit != "B" and unit_speed > 0:
            return f"{unit_speed:.1f}{self.unit}/s"

        return ""

    @staticmethod
    def _format_unit_value(value: float) -> str:
        if abs(value - round(value)) < 0.05:
            return str(int(round(value)))
        return f"{value:.1f}"

    @classmethod
    def parse_size_text(cls, text: Optional[str]) -> Optional[float]:
        if not text:
            return None
        cleaned = text.strip().replace("/s", "")
        if cleaned.lower() in {"n/a", "na", "?", "--"}:
            return None
        match = cls.SIZE_PATTERN.search(cleaned)
        if not match:
            return None

        value = float(match.group(1))
        unit = match.group(2).lower()
        factors = {
            "b": 1,
            "kb": 1000,
            "mb": 1000 ** 2,
            "gb": 1000 ** 3,
            "tb": 1000 ** 4,
            "pb": 1000 ** 5,
            "kib": 1024,
            "mib": 1024 ** 2,
            "gib": 1024 ** 3,
            "tib": 1024 ** 4,
            "pib": 1024 ** 5,
        }
        factor = factors.get(unit)
        if factor is None:
            return None
        return value * factor

    @classmethod
    def parse_duration_text(cls, text: Optional[str]) -> Optional[float]:
        if not text:
            return None
        cleaned = text.strip().lower()
        if cleaned in {"n/a", "na", "?", "--", "inf"}:
            return None

        if ":" in cleaned:
            parts = cleaned.split(":")
            try:
                if len(parts) == 3:
                    hours = int(parts[0])
                    minutes = int(parts[1])
                    seconds = float(parts[2])
                    return (hours * 3600) + (minutes * 60) + seconds
                if len(parts) == 2:
                    minutes = int(parts[0])
                    seconds = float(parts[1])
                    return (minutes * 60) + seconds
            except ValueError:
                return None

        matches = re.findall(r"(\d+(?:\.\d+)?)([hms])", cleaned)
        if not matches:
            return None

        total_seconds = 0.0
        for value_text, unit in matches:
            value = float(value_text)
            if unit == "h":
                total_seconds += value * 3600
            elif unit == "m":
                total_seconds += value * 60
            else:
                total_seconds += value
        return total_seconds

    @staticmethod
    def _format_size(size: float) -> str:
        size = float(max(0.0, size))
        for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
            if size < 1024.0 or unit == "PB":
                if unit == "B":
                    return f"{int(size)}{unit}"
                return f"{size:.1f}{unit}"
            size /= 1024.0
        return f"{size:.1f}PB"

    @staticmethod
    def _format_time(seconds: float) -> str:
        total = int(max(0.0, seconds))
        if total < 60:
            return f"{total}s"
        if total < 3600:
            return f"{total // 60}m{total % 60:02d}s"
        hours = total // 3600
        minutes = (total % 3600) // 60
        secs = total % 60
        return f"{hours}h{minutes:02d}m{secs:02d}s"


class MultiProgressDisplay:
    """Display multiple progress bars for concurrent downloads."""

    def __init__(self, total_tasks: int):
        self.total_tasks = total_tasks
        self.tasks: dict[str, ProgressBar] = {}
        self.completed = 0
        self._lock = threading.Lock()

    def add_task(self, task_id: str, total: int, description: str) -> ProgressBar:
        with self._lock:
            bar = ProgressBar(total, description)
            self.tasks[task_id] = bar
            return bar

    def complete_task(self, task_id: str) -> None:
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id].finish()
                self.completed += 1

    def fail_task(self, task_id: str, error: str = "") -> None:
        with self._lock:
            if task_id in self.tasks:
                self.tasks[task_id].error(error)

    @property
    def overall_progress(self) -> float:
        if self.total_tasks <= 0:
            return 0.0
        return (self.completed / self.total_tasks) * 100
