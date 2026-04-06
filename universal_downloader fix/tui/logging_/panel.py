"""
Structured logging manager with Rich/Textual log panel support.

Features:
- Captures Python logging output
- Stores log entries with metadata
- Filter by level, source, keyword
- Emits events for live log panel updates
- Thread-safe circular buffer

Usage:
    from tui.logging_ import log_manager, LogLevel

    log_manager.info("Download started", source="downloader", url="...")
    log_manager.error("Connection failed", source="network")

    # Get filtered entries
    errors = log_manager.get_entries(level=LogLevel.ERROR, limit=50)

    # Subscribe to new entries
    event_bus.on("log.entry", update_log_panel)
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Dict, List, Optional

from rich.markup import escape

from ..engine.events import event_bus

logger = logging.getLogger(__name__)


class LogLevel(IntEnum):
    """Log levels matching Python logging."""
    DEBUG = 10
    INFO = 20
    WARNING = 30
    ERROR = 40
    CRITICAL = 50


LEVEL_STYLES = {
    LogLevel.DEBUG: ("DEBUG", "dim"),
    LogLevel.INFO: ("INFO", "cyan"),
    LogLevel.WARNING: ("WARN", "yellow"),
    LogLevel.ERROR: ("ERROR", "red"),
    LogLevel.CRITICAL: ("CRIT", "bold red"),
}


@dataclass
class LogEntry:
    """Single log entry with metadata."""
    timestamp: float = field(default_factory=time.time)
    level: LogLevel = LogLevel.INFO
    message: str = ""
    source: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    @property
    def level_name(self) -> str:
        info = LEVEL_STYLES.get(self.level)
        return info[0] if info else "UNKN"

    @property
    def level_style(self) -> str:
        info = LEVEL_STYLES.get(self.level)
        return info[1] if info else "dim"

    @property
    def time_str(self) -> str:
        """Format timestamp as HH:MM:SS."""
        return time.strftime("%H:%M:%S", time.localtime(self.timestamp))

    def to_rich_markup(self) -> str:
        """Format as Rich markup string."""
        style = self.level_style
        source_part = f" [{style}]{escape(self.source)}[/{style}]" if self.source else ""
        return (
            f"[dim]{self.time_str}[/dim] "
            f"[{style}]{self.level_name:>5}[/{style}]"
            f"{source_part} "
            f"{escape(self.message)}"
        )

    def matches_filter(
        self,
        level: Optional[LogLevel] = None,
        source: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> bool:
        """Check if entry matches filter criteria."""
        if level is not None and self.level < level:
            return False
        if source is not None and source.lower() not in self.source.lower():
            return False
        if keyword is not None and keyword.lower() not in self.message.lower():
            return False
        return True


class LogManager:
    """
    Central log manager with circular buffer and event emission.

    Thread-safe. Integrates with Python logging via LogHandler.
    """

    def __init__(self, max_entries: int = 2000):
        self._entries: List[LogEntry] = []
        self._max_entries = max_entries
        self._lock = threading.RLock()
        self._min_level: LogLevel = LogLevel.DEBUG
        self._handlers: List[Callable[[LogEntry], None]] = []
        self._python_handler: Optional["_TUILogHandler"] = None

    # ── Logging Methods ────────────────────────────────────

    def log(
        self,
        level: LogLevel,
        message: str,
        source: str = "",
        **data: Any,
    ) -> LogEntry:
        """Create and store a log entry."""
        entry = LogEntry(
            level=level,
            message=message,
            source=source,
            data=data,
        )

        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self._max_entries:
                self._entries = self._entries[-self._max_entries:]

        # Notify handlers
        for handler in self._handlers:
            try:
                handler(entry)
            except Exception:
                pass

        # Emit event (non-blocking)
        try:
            event_bus.emit(
                "log.entry",
                source="LogManager",
                entry=entry,
                level=entry.level_name,
                message=message,
                log_source=source,
            )
        except Exception:
            pass

        return entry

    def debug(self, message: str, source: str = "", **data) -> LogEntry:
        return self.log(LogLevel.DEBUG, message, source, **data)

    def info(self, message: str, source: str = "", **data) -> LogEntry:
        return self.log(LogLevel.INFO, message, source, **data)

    def warning(self, message: str, source: str = "", **data) -> LogEntry:
        return self.log(LogLevel.WARNING, message, source, **data)

    def error(self, message: str, source: str = "", **data) -> LogEntry:
        return self.log(LogLevel.ERROR, message, source, **data)

    def critical(self, message: str, source: str = "", **data) -> LogEntry:
        return self.log(LogLevel.CRITICAL, message, source, **data)

    # ── Retrieval ──────────────────────────────────────────

    def get_entries(
        self,
        level: Optional[LogLevel] = None,
        source: Optional[str] = None,
        keyword: Optional[str] = None,
        limit: int = 100,
    ) -> List[LogEntry]:
        """Get filtered log entries (newest first)."""
        with self._lock:
            entries = list(reversed(self._entries))

        filtered = [
            e for e in entries
            if e.matches_filter(level=level, source=source, keyword=keyword)
        ]

        return filtered[:limit]

    def get_recent(self, limit: int = 50) -> List[LogEntry]:
        """Get most recent entries."""
        with self._lock:
            return list(reversed(self._entries[-limit:]))

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def count_by_level(self) -> Dict[str, int]:
        """Count entries grouped by level."""
        counts: Dict[str, int] = {}
        with self._lock:
            for entry in self._entries:
                name = entry.level_name
                counts[name] = counts.get(name, 0) + 1
        return counts

    # ── Handler Registration ───────────────────────────────

    def add_handler(self, handler: Callable[[LogEntry], None]) -> None:
        """Add a callback handler for new log entries."""
        self._handlers.append(handler)

    def remove_handler(self, handler: Callable[[LogEntry], None]) -> None:
        """Remove a callback handler."""
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass

    # ── Python Logging Integration ─────────────────────────

    def install_python_handler(
        self,
        logger_name: Optional[str] = None,
        level: int = logging.DEBUG,
    ) -> None:
        """
        Install a Python logging handler that forwards to this LogManager.
        Captures standard logging output for display in TUI log panel.
        """
        if self._python_handler is not None:
            return  # Already installed

        self._python_handler = _TUILogHandler(self)
        self._python_handler.setLevel(level)

        target_logger = logging.getLogger(logger_name)
        target_logger.addHandler(self._python_handler)

    def uninstall_python_handler(self, logger_name: Optional[str] = None) -> None:
        """Remove the Python logging handler."""
        if self._python_handler is None:
            return

        target_logger = logging.getLogger(logger_name)
        target_logger.removeHandler(self._python_handler)
        self._python_handler = None

    # ── Cleanup ────────────────────────────────────────────

    def clear(self) -> None:
        """Clear all log entries."""
        with self._lock:
            self._entries.clear()


class _TUILogHandler(logging.Handler):
    """
    Python logging.Handler that forwards records to LogManager.
    Bridges standard Python logging → TUI log panel.
    """

    _LEVEL_MAP = {
        logging.DEBUG: LogLevel.DEBUG,
        logging.INFO: LogLevel.INFO,
        logging.WARNING: LogLevel.WARNING,
        logging.ERROR: LogLevel.ERROR,
        logging.CRITICAL: LogLevel.CRITICAL,
    }

    def __init__(self, log_manager: LogManager):
        super().__init__()
        self._log_manager = log_manager

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = self._LEVEL_MAP.get(record.levelno, LogLevel.INFO)
            message = self.format(record) if self.formatter else record.getMessage()
            source = record.name

            # Avoid re-entrancy: don't log about logging
            if "LogManager" in source or "EventBus" in source:
                return

            self._log_manager.log(level, message, source=source)
        except Exception:
            pass  # Never let logging handler crash the app


# ── Singleton ──────────────────────────────────────────────
log_manager = LogManager()
