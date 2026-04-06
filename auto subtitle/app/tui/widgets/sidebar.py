"""Navigation sidebar — fixed version."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Static


class Sidebar(Vertical):
    """Left navigation sidebar."""

    DEFAULT_CSS = """
    Sidebar {
        width: 30;
        background: $panel;
        border-right: thick $accent;
        padding: 1 0;
        dock: left;
    }

    Sidebar .sidebar-title {
        text-align: center;
        text-style: bold;
        color: $accent;
        padding: 1 2;
        width: 100%;
    }

    Sidebar .sidebar-version {
        text-align: center;
        color: $text-muted;
        padding: 0 2 1 2;
        width: 100%;
    }

    Sidebar .sidebar-divider {
        text-align: center;
        color: $text-muted;
        width: 100%;
    }

    Sidebar .nav-btn {
        width: 100%;
        margin: 0 1;
        min-height: 3;
    }

    Sidebar .nav-btn.-active {
        background: $accent 30%;
        text-style: bold;
    }

    Sidebar .sidebar-footer {
        text-align: center;
        color: $text-muted;
        padding: 1 2;
        width: 100%;
        dock: bottom;
    }
    """

    active_screen: reactive[str] = reactive("home")

    class Navigate(Message):
        """Emitted when user clicks a nav button."""
        def __init__(self, screen: str) -> None:
            super().__init__()
            self.screen = screen

    NAV_ITEMS = [
        ("home",     "🏠  Dashboard"),
        ("generate", "🎬  Generate"),
        ("batch",    "📁  Batch Process"),
        ("live",     "🎤  Live Mic"),
        ("jobs",     "📋  Job Monitor"),
        ("preview",  "👁  Preview Subs"),
        ("settings", "⚙   Settings"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("⚡ Auto Subtitle AI", classes="sidebar-title")
        yield Static("v1.0.0", classes="sidebar-version")
        yield Static("─" * 26, classes="sidebar-divider")

        for screen_id, label in self.NAV_ITEMS:
            yield Button(
                label,
                id=f"nav-{screen_id}",
                classes="nav-btn",
                variant="default",
            )

        yield Static("  [dim]q[/] quit  [dim]?[/] help", classes="sidebar-footer")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id.startswith("nav-"):
            screen = btn_id.removeprefix("nav-")
            self.active_screen = screen
            self.post_message(self.Navigate(screen))

    def watch_active_screen(self, value: str) -> None:
        """Highlight the active nav button."""
        for screen_id, _ in self.NAV_ITEMS:
            try:
                btn = self.query_one(f"#nav-{screen_id}", Button)
                if screen_id == value:
                    btn.add_class("-active")
                else:
                    btn.remove_class("-active")
            except Exception:
                pass