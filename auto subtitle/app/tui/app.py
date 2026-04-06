"""
Auto Subtitle AI — Main TUI Application (FIXED).

Key fix: Screens are now Container widgets, not Screen objects.
They get mounted/unmounted inside #content-area.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Footer, Header

from app.tui.widgets.sidebar import Sidebar
from app.tui.screens.home import HomePanel
from app.tui.screens.generate import GeneratePanel
from app.tui.screens.batch import BatchPanel
from app.tui.screens.live import LivePanel
from app.tui.screens.jobs import JobsPanel
from app.tui.screens.preview import PreviewPanel
from app.tui.screens.settings import SettingsPanel


# ── Panel registry ───────────────────────────────────────────
# Maps name → class. Each class is a Container, NOT a Screen.
PANELS = {
    "home":     HomePanel,
    "generate": GeneratePanel,
    "batch":    BatchPanel,
    "live":     LivePanel,
    "jobs":     JobsPanel,
    "preview":  PreviewPanel,
    "settings": SettingsPanel,
}


class SubtitleApp(App):
    """Root TUI application."""

    TITLE = "Auto Subtitle AI"
    SUB_TITLE = "v1.0.0"

    # Inline CSS — guaranteed to work (no external file needed)
    DEFAULT_CSS = """
    Screen {
        background: $surface;
    }

    #app-layout {
        width: 100%;
        height: 100%;
    }

    #content-area {
        width: 1fr;
        height: 1fr;
        overflow-y: auto;
    }

    /* ── Stat cards on home screen ─────────────────────────── */
    .stats-row {
        height: auto;
        margin: 1 0;
    }

    .stat-card {
        width: 1fr;
        height: 5;
        background: $panel;
        border: round $primary-background-lighten-2;
        margin: 0 1;
        padding: 0 1;
        content-align: center middle;
    }

    .stat-value {
        text-align: center;
    }

    .stat-label {
        text-align: center;
    }

    /* ── DataTable ────────────────────────────────────────── */
    DataTable {
        height: auto;
        max-height: 16;
        margin: 1 0;
    }

    /* ── Buttons in a row ─────────────────────────────────── */
    Horizontal > Button {
        margin: 0 1;
    }

    /* ── Form layout ──────────────────────────────────────── */
    Horizontal > Label {
        width: 16;
        padding: 1 0;
    }

    Horizontal > Input {
        width: 1fr;
    }

    Horizontal > Select {
        width: 1fr;
    }
    """

    BINDINGS = [
        Binding("q",              "quit",                  "Quit",      priority=True),
        Binding("d",              "go_panel('home')",      "Dashboard", show=True),
        Binding("g",              "go_panel('generate')",  "Generate",  show=True),
        Binding("b",              "go_panel('batch')",     "Batch",     show=True),
        Binding("l",              "go_panel('live')",      "Live Mic",  show=True),
        Binding("j",              "go_panel('jobs')",      "Jobs",      show=True),
        Binding("p",              "go_panel('preview')",   "Preview",   show=True),
        Binding("s",              "go_panel('settings')",  "Settings",  show=True),
        Binding("question_mark",  "show_help",             "Help",      show=True),
    ]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._current_panel = "home"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="app-layout"):
            yield Sidebar()
            yield Container(id="content-area")
        yield Footer()

    def on_mount(self) -> None:
        """Show home panel on startup."""
        self.switch_panel("home")

    # ── Panel switching ──────────────────────────────────────

    def switch_panel(self, name: str) -> None:
        """Replace content area with the named panel."""
        if name not in PANELS:
            self.notify(f"Unknown panel: {name}", severity="error")
            return

        container = self.query_one("#content-area", Container)

        # Remove current content
        container.remove_children()

        # Create and mount the new panel
        panel_cls = PANELS[name]
        panel = panel_cls()
        container.mount(panel)

        self._current_panel = name

        # Update sidebar highlight
        try:
            sidebar = self.query_one(Sidebar)
            sidebar.active_screen = name
        except Exception:
            pass

    # ── Action handlers (called by key bindings) ─────────────

    def action_go_panel(self, name: str) -> None:
        self.switch_panel(name)

    def action_show_help(self) -> None:
        self.notify(
            "[d] Dashboard  [g] Generate  [b] Batch  [l] Live  "
            "[j] Jobs  [p] Preview  [s] Settings  [q] Quit",
            severity="information",
            timeout=8,
        )

    # ── Sidebar navigation ───────────────────────────────────

    def on_sidebar_navigate(self, event: Sidebar.Navigate) -> None:
        self.switch_panel(event.screen)