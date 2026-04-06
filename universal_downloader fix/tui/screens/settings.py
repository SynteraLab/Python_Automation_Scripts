"""
Settings screen.
"""

import os
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Input, Button, Switch, Label
from textual.containers import Vertical, Horizontal

from ..engine.events import event_bus


class SettingsView(Widget):
    """Settings panel with live editing."""

    DEFAULT_CSS = """
    SettingsView { height: 100%; padding: 1; }
    SettingsView #settings-header {
        text-style: bold; color: $primary; height: 2;
    }
    SettingsView .setting-row {
        height: 3; margin: 0 0 1 0; padding: 0 1;
    }
    SettingsView .setting-label {
        width: 20; padding: 0 1; content-align: left middle;
    }
    SettingsView .setting-input {
        width: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("⚙️ Settings", id="settings-header")

        with Vertical():
            # Download directory
            with Horizontal(classes="setting-row"):
                yield Label("Download Dir:", classes="setting-label")
                yield Input(id="set-output-dir", classes="setting-input")

            # Max concurrent
            with Horizontal(classes="setting-row"):
                yield Label("Max Workers:", classes="setting-label")
                yield Input(id="set-max-concurrent", classes="setting-input")

            # aria2c toggle
            with Horizontal(classes="setting-row"):
                yield Label("Use aria2c:", classes="setting-label")
                yield Switch(id="set-aria2", value=True)

            # Connections
            with Horizontal(classes="setting-row"):
                yield Label("Connections:", classes="setting-label")
                yield Input(id="set-connections", classes="setting-input")

            # Proxy
            with Horizontal(classes="setting-row"):
                yield Label("Proxy:", classes="setting-label")
                yield Input(id="set-proxy", placeholder="http://... or socks5://...", classes="setting-input")

            with Horizontal():
                yield Button("Save", variant="primary", id="btn-save-settings")
                yield Button("Reset", id="btn-reset-settings")

    def on_mount(self) -> None:
        self._load_current()

    def _load_current(self) -> None:
        config = self.app.config
        try:
            self.query_one("#set-output-dir", Input).value = config.download.output_dir
            self.query_one("#set-max-concurrent", Input).value = str(config.download.max_concurrent)
            self.query_one("#set-aria2", Switch).value = config.download.use_aria2
            self.query_one("#set-connections", Input).value = str(config.download.aria2_connections)
            self.query_one("#set-proxy", Input).value = config.proxy.http or config.proxy.socks5 or ""
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save-settings":
            self._save_settings()
        elif event.button.id == "btn-reset-settings":
            self._load_current()

    def _save_settings(self) -> None:
        config = self.app.config
        try:
            output_dir = self.query_one("#set-output-dir", Input).value.strip()
            if output_dir:
                config.download.output_dir = output_dir
                os.makedirs(output_dir, exist_ok=True)

            max_conc = self.query_one("#set-max-concurrent", Input).value.strip()
            if max_conc.isdigit():
                config.download.max_concurrent = int(max_conc)

            config.download.use_aria2 = self.query_one("#set-aria2", Switch).value

            connections = self.query_one("#set-connections", Input).value.strip()
            if connections.isdigit():
                config.download.aria2_connections = int(connections)

            proxy = self.query_one("#set-proxy", Input).value.strip()
            config.proxy.http = proxy or None
            config.proxy.https = proxy or None

            event_bus.emit("config.changed", source="settings")
            self.app.notify("Settings saved ✓", title="Settings")
        except Exception as e:
            self.app.notify(f"Error: {e}", title="Settings", severity="error")