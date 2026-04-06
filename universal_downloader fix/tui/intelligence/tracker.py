"""
Usage tracking — records user behavior for adaptive UI.
"""

import json
import os
import time
import threading
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..engine.events import event_bus


class UsageTracker:
    """
    Tracks command usage, navigation patterns, and workflow frequency.
    Persists to disk for cross-session learning.
    """

    def __init__(self, data_path: Optional[str] = None):
        self._lock = threading.RLock()
        self._data_path = Path(
            data_path or os.path.expanduser("~/.universal_downloader/usage.json")
        )
        self._command_counts: Counter = Counter()
        self._nav_counts: Counter = Counter()
        self._download_extractors: Counter = Counter()
        self._session_start = time.time()
        self._total_sessions = 0

        self._load()
        self._subscribe_events()

    def _subscribe_events(self) -> None:
        event_bus.on("download.completed", self._on_download)
        event_bus.on("download.failed", self._on_download)
        event_bus.on("mode.changed", self._on_mode_change)

    def _on_download(self, event) -> None:
        ext = event.get("extractor", "unknown")
        with self._lock:
            self._download_extractors[ext] += 1

    def _on_mode_change(self, event) -> None:
        mode = event.get("mode", "")
        with self._lock:
            self._nav_counts[f"mode:{mode}"] += 1

    def track_command(self, command: str) -> None:
        with self._lock:
            self._command_counts[command] += 1

    def track_navigation(self, item_id: str) -> None:
        with self._lock:
            self._nav_counts[item_id] += 1

    def get_top_commands(self, limit: int = 10) -> List[tuple]:
        with self._lock:
            return self._command_counts.most_common(limit)

    def get_top_navigation(self, limit: int = 10) -> List[tuple]:
        with self._lock:
            return self._nav_counts.most_common(limit)

    def get_top_extractors(self, limit: int = 5) -> List[tuple]:
        with self._lock:
            return self._download_extractors.most_common(limit)

    def get_suggested_order(self) -> List[str]:
        """Return navigation items sorted by usage frequency."""
        with self._lock:
            return [item for item, _ in self._nav_counts.most_common()]

    # ── Persistence ────────────────────────────────────────

    def save(self) -> None:
        try:
            self._data_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock:
                data = {
                    "commands": dict(self._command_counts),
                    "navigation": dict(self._nav_counts),
                    "extractors": dict(self._download_extractors),
                    "total_sessions": self._total_sessions + 1,
                }
            with open(self._data_path, "w") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            with open(self._data_path) as f:
                data = json.load(f)
            with self._lock:
                self._command_counts = Counter(data.get("commands", {}))
                self._nav_counts = Counter(data.get("navigation", {}))
                self._download_extractors = Counter(data.get("extractors", {}))
                self._total_sessions = data.get("total_sessions", 0)
        except Exception:
            pass


# ── Singleton ──────────────────────────────────────────────
usage_tracker = UsageTracker()