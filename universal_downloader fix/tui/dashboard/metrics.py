"""
System metrics collector.
Tracks download stats, active tasks, performance counters.
"""

import time
import threading
from typing import Any, Dict

from ..engine.events import event_bus


class MetricsCollector:
    """Collects and aggregates runtime metrics."""

    def __init__(self):
        self._lock = threading.RLock()
        self._counters: Dict[str, int] = {
            "downloads_started": 0,
            "downloads_completed": 0,
            "downloads_failed": 0,
            "workflows_executed": 0,
            "commands_executed": 0,
        }
        self._gauges: Dict[str, float] = {
            "active_downloads": 0,
            "uptime": 0,
        }
        self._start_time = time.time()

        # Auto-subscribe to events
        event_bus.on("download.started", self._on_download_started)
        event_bus.on("download.completed", self._on_download_completed)
        event_bus.on("download.failed", self._on_download_failed)
        event_bus.on("workflow.completed", self._on_workflow_completed)

    def _on_download_started(self, event) -> None:
        with self._lock:
            self._counters["downloads_started"] += 1
            self._gauges["active_downloads"] += 1

    def _on_download_completed(self, event) -> None:
        with self._lock:
            self._counters["downloads_completed"] += 1
            self._gauges["active_downloads"] = max(0, self._gauges["active_downloads"] - 1)

    def _on_download_failed(self, event) -> None:
        with self._lock:
            self._counters["downloads_failed"] += 1
            self._gauges["active_downloads"] = max(0, self._gauges["active_downloads"] - 1)

    def _on_workflow_completed(self, event) -> None:
        with self._lock:
            self._counters["workflows_executed"] += 1

    def increment(self, counter: str, value: int = 1) -> None:
        with self._lock:
            self._counters[counter] = self._counters.get(counter, 0) + value

    def set_gauge(self, gauge: str, value: float) -> None:
        with self._lock:
            self._gauges[gauge] = value

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            self._gauges["uptime"] = time.time() - self._start_time
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
            }

    def get_counter(self, name: str) -> int:
        with self._lock:
            return self._counters.get(name, 0)

    def get_gauge(self, name: str) -> float:
        with self._lock:
            if name == "uptime":
                return time.time() - self._start_time
            return self._gauges.get(name, 0)


# ── Singleton ──────────────────────────────────────────────
metrics = MetricsCollector()