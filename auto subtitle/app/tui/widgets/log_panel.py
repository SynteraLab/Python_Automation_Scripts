"""Scrolling log panel — fixed version."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static


LogLevel = Literal["info", "success", "warning", "error", "debug"]

_STYLES = {
    "info":    ("ℹ ", ""),
    "success": ("✅", "[green]"),
    "warning": ("⚠ ", "[yellow]"),
    "error":   ("❌", "[red]"),
    "debug":   ("🔍", "[dim]"),
}


class LogPanel(VerticalScroll):
    """Scrollable log that accumulates messages."""

    DEFAULT_CSS = """
    LogPanel {
        height: 12;
        border: round $primary-background-lighten-2;
        background: $surface-darken-1;
        margin: 1 0;
        padding: 0 1;
    }
    """

    def __init__(self, max_lines: int = 200, **kwargs):
        super().__init__(**kwargs)
        self._max_lines = max_lines
        self._count = 0

    def write(self, message: str, level: LogLevel = "info") -> None:
        """Add a log line."""
        icon, style = _STYLES.get(level, _STYLES["info"])
        ts = datetime.now().strftime("%H:%M:%S")
        close = "[/]" if style else ""
        text = f"[dim]{ts}[/]  {icon} {style}{message}{close}"

        label = Static(text)
        self.mount(label)
        self._count += 1

        # Remove old lines if too many
        if self._count > self._max_lines:
            children = list(self.children)
            for child in children[: self._count - self._max_lines]:
                child.remove()
            self._count = self._max_lines

        self.scroll_end(animate=False)

    def clear_log(self) -> None:
        """Remove all log lines."""
        self.remove_children()
        self._count = 0