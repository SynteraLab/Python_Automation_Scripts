"""
Bottom status bar with live information.
Shows mode, theme, active downloads, time.
"""

import time
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.timer import Timer
from typing import Optional


class StatusItem(Static):
    """Single status bar item."""

    DEFAULT_CSS = """
    StatusItem {
        margin: 0 1;
        height: 1;
    }
    """


class StatusBar(Widget):
    """
    Bottom status bar showing live system info.

    Updates every second via timer.
    """

    DEFAULT_CSS = """
    StatusBar {
        dock: bottom;
        height: 1;
        background: $surface;
        color: $text-primary;
        layout: horizontal;
        padding: 0 1;
    }
    StatusBar StatusItem {
        margin: 0 1;
    }
    """

    mode_name: reactive[str] = reactive("Build")
    theme_name: reactive[str] = reactive("Dark")
    active_downloads: reactive[int] = reactive(0)
    status_text: reactive[str] = reactive("Ready")
    workflow_status: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield StatusItem(id="sb-mode")
            yield StatusItem(id="sb-status")
            yield StatusItem(id="sb-downloads")
            yield StatusItem(id="sb-workflow")
            yield StatusItem(id="sb-theme")
            yield StatusItem(id="sb-time")
            yield StatusItem(id="sb-help")

    def on_mount(self) -> None:
        self._update_all()
        self.set_interval(1.0, self._update_time)

    def _update_all(self) -> None:
        self._update_mode()
        self._update_status()
        self._update_downloads()
        self._update_theme()
        self._update_time()
        self._update_workflow()
        self._update_help()

    def watch_mode_name(self, value: str) -> None:
        self._update_mode()

    def watch_theme_name(self, value: str) -> None:
        self._update_theme()

    def watch_active_downloads(self, value: int) -> None:
        self._update_downloads()

    def watch_status_text(self, value: str) -> None:
        self._update_status()

    def watch_workflow_status(self, value: str) -> None:
        self._update_workflow()

    def _update_mode(self) -> None:
        mode_icons = {
            "Build": "🏗",
            "Run": "▶️",
            "Monitor": "📊",
            "Focus": "🎯",
        }
        icon = mode_icons.get(self.mode_name, "")
        try:
            self.query_one("#sb-mode", StatusItem).update(f" {icon} {self.mode_name} ")
        except Exception:
            pass

    def _update_status(self) -> None:
        try:
            self.query_one("#sb-status", StatusItem).update(f" {self.status_text} ")
        except Exception:
            pass

    def _update_downloads(self) -> None:
        try:
            if self.active_downloads > 0:
                text = f" ⬇ {self.active_downloads} active "
            else:
                text = " ⬇ idle "
            self.query_one("#sb-downloads", StatusItem).update(text)
        except Exception:
            pass

    def _update_workflow(self) -> None:
        try:
            text = f" 🔄 {self.workflow_status} " if self.workflow_status else ""
            self.query_one("#sb-workflow", StatusItem).update(text)
        except Exception:
            pass

    def _update_theme(self) -> None:
        try:
            self.query_one("#sb-theme", StatusItem).update(f" 🎨 {self.theme_name} ")
        except Exception:
            pass

    def _update_time(self) -> None:
        try:
            now = time.strftime("%H:%M:%S")
            self.query_one("#sb-time", StatusItem).update(f" {now} ")
        except Exception:
            pass

    def _update_help(self) -> None:
        try:
            self.query_one("#sb-help", StatusItem).update(" Ctrl+P: Commands | ?: Help ")
        except Exception:
            pass
