"""
Main workspace screen — the primary screen after app launch.
Contains sidebar, workspace area, status bar, and optional log panel.
"""

import re

from rich.markup import escape
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, RichLog
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual import events
from typing import Optional

from ..widgets.sidebar import Sidebar
from ..widgets.status_bar import StatusBar
from ..engine.events import event_bus
from ..logging_ import log_manager


class LogPanel(Static):
    """Collapsible log panel at the bottom."""

    _PROGRESS_KIND = "progress"
    _FINAL_KIND = "final"
    _PROGRESS_RE = re.compile(r"^(?:Progress:|HLS progress:|yt-dlp progress:|[A-Z-]+ progress:)")

    DEFAULT_CSS = """
    LogPanel {
        height: 12;
        background: $surface;
        border-top: solid $border;
    }
    LogPanel.hidden {
        display: none;
    }
    LogPanel #log-title {
        height: 1;
        padding: 0 1;
        text-style: bold;
        color: $primary;
        background: $surface;
    }
    LogPanel #log-live {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $panel;
    }
    LogPanel RichLog {
        height: 1fr;
        padding: 0 1;
        background: $surface;
    }
    """

    visible: reactive[bool] = reactive(True)

    def compose(self) -> ComposeResult:
        yield Static("📜 Logs", id="log-title")
        yield Static("[dim]Live progress: idle[/dim]", id="log-live")
        yield RichLog(id="log-output", highlight=True, markup=True, wrap=True)

    def on_mount(self) -> None:
        # Subscribe to log events
        log_manager.add_handler(self._on_log_entry)
        # Show recent entries
        for entry in reversed(log_manager.get_recent(limit=20)):
            self._write_entry(entry)

    def _on_log_entry(self, entry) -> None:
        """Handle new log entry."""
        try:
            self._write_entry(entry)
        except Exception:
            pass

    def _write_entry(self, entry) -> None:
        """Write a log entry to the RichLog."""
        try:
            if self._is_progress_entry(entry):
                self._update_live_entry(entry, label="LIVE", style="cyan")
                return

            if self._is_final_entry(entry):
                label, style = self._final_label(entry.message)
                self._update_live_entry(entry, label=label, style=style)

            log_output = self.query_one("#log-output", RichLog)
            log_output.write(entry.to_rich_markup())
        except Exception:
            pass

    def _is_progress_entry(self, entry) -> bool:
        kind = str(getattr(entry, "data", {}).get("kind", ""))
        return kind == self._PROGRESS_KIND or bool(self._PROGRESS_RE.match(entry.message or ""))

    def _is_final_entry(self, entry) -> bool:
        kind = str(getattr(entry, "data", {}).get("kind", ""))
        return kind == self._FINAL_KIND

    def _update_live_entry(self, entry, label: str, style: str) -> None:
        try:
            live = self.query_one("#log-live", Static)
            live.update(
                f"[dim]{entry.time_str}[/dim] "
                f"[{style}]{label:>4}[/{style}] "
                f"[{entry.level_style}]{escape(entry.source)}[/{entry.level_style}] "
                f"{escape(self._shorten(entry.message, 220))}"
            )
        except Exception:
            pass

    @staticmethod
    def _final_label(message: str) -> tuple[str, str]:
        if message.startswith(("✓", "Saved file:")):
            return ("DONE", "green")
        return ("FAIL", "red")

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: limit - 3] + "..."

    def watch_visible(self, value: bool) -> None:
        self.set_class(not value, "hidden")

    def toggle(self) -> None:
        self.visible = not self.visible

    def clear(self) -> None:
        try:
            self.query_one("#log-output", RichLog).clear()
            self.query_one("#log-live", Static).update("[dim]Live progress: idle[/dim]")
        except Exception:
            pass


class WorkspaceArea(Vertical):
    """Central workspace that holds the active view content."""

    DEFAULT_CSS = """
    WorkspaceArea {
        width: 1fr;
        height: 1fr;
        padding: 0;
    }
    WorkspaceArea #workspace-content {
        height: 1fr;
    }
    WorkspaceArea #workspace-placeholder {
        content-align: center middle;
        height: 100%;
        color: $text-primary;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(
            "🎬 [bold]Universal Media Downloader[/bold]\n\n"
            "[dim]Select an option from the sidebar or press Ctrl+P for commands[/dim]",
            id="workspace-placeholder",
        )


class MainScreen(Widget):
    """
    Primary application screen with sidebar layout.

    Layout:
    ┌──────────┬─────────────────────────┐
    │          │                         │
    │ Sidebar  │      Workspace          │
    │          │                         │
    │          │                         │
    ├──────────┴─────────────────────────┤
    │            Log Panel               │
    ├────────────────────────────────────┤
    │            Status Bar              │
    └────────────────────────────────────┘
    """

    DEFAULT_CSS = """
    MainScreen {
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }
    MainScreen #main-layout {
        height: 1fr;
    }
    """

    BINDINGS = [
        ("ctrl+b", "toggle_sidebar", "Toggle Sidebar"),
        ("ctrl+l", "toggle_log", "Toggle Log Panel"),
    ]

    def compose(self) -> ComposeResult:
        with Horizontal(id="main-layout"):
            yield Sidebar(id="sidebar")
            yield WorkspaceArea(id="workspace")
        yield LogPanel(id="log-panel")
        yield StatusBar(id="status-bar")

    def on_mount(self) -> None:
        # Set initial sidebar selection
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.active_item = "download"

        # Subscribe to events
        event_bus.on("download.started", self._on_download_event)
        event_bus.on("download.completed", self._on_download_event)
        event_bus.on("download.failed", self._on_download_event)
        event_bus.on("mode.changed", self._on_mode_changed)
        event_bus.on("theme.changed", self._on_theme_changed)

    def on_sidebar_item_selected(self, event: Sidebar.ItemSelected) -> None:
        """Handle sidebar navigation."""
        self.app.handle_navigation(event.item_id)

    def action_toggle_sidebar(self) -> None:
        sidebar = self.query_one("#sidebar", Sidebar)
        sidebar.toggle()

    def action_toggle_log(self) -> None:
        log_panel = self.query_one("#log-panel", LogPanel)
        log_panel.toggle()

    def update_workspace(self, widget) -> None:
        """Replace workspace content with a new widget."""
        workspace = self.query_one("#workspace", WorkspaceArea)
        # Remove existing children
        for child in list(workspace.children):
            child.remove()
        workspace.mount(widget)

    def _on_download_event(self, event) -> None:
        """Update status bar on download events."""
        try:
            status_bar = self.query_one("#status-bar", StatusBar)
            if event.name == "download.started":
                status_bar.active_downloads += 1
                status_bar.status_text = f"Downloading: {event.get('url', '')[:40]}"
            elif event.name == "download.completed":
                status_bar.active_downloads = max(0, status_bar.active_downloads - 1)
                status_bar.status_text = "Download completed ✓"
            elif event.name == "download.failed":
                status_bar.active_downloads = max(0, status_bar.active_downloads - 1)
                status_bar.status_text = "Download failed ✗"
        except Exception:
            pass

    def _on_mode_changed(self, event) -> None:
        try:
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.mode_name = event.get("mode", "Build")
        except Exception:
            pass

    def _on_theme_changed(self, event) -> None:
        try:
            status_bar = self.query_one("#status-bar", StatusBar)
            status_bar.theme_name = event.get("new_theme", "dark").title()
        except Exception:
            pass

    def get_log_panel(self) -> Optional[LogPanel]:
        try:
            return self.query_one("#log-panel", LogPanel)
        except Exception:
            return None

    def get_status_bar(self) -> Optional[StatusBar]:
        try:
            return self.query_one("#status-bar", StatusBar)
        except Exception:
            return None
