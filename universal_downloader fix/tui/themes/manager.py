"""
Theme manager for Textual CSS theme switching.

Features:
- Dark/Light theme registry
- Runtime switching via event bus
- Custom theme registration
- CSS variable system for consistent colors

Usage:
    from tui.themes import theme_manager

    theme_manager.switch("light")
    current = theme_manager.current_theme
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional

from ..engine.events import event_bus

logger = logging.getLogger(__name__)


@dataclass
class Theme:
    """Theme definition with color palette and metadata."""

    name: str
    display_name: str
    description: str = ""
    is_dark: bool = True

    # Core colors
    primary: str = "#00d4ff"
    secondary: str = "#7c3aed"
    accent: str = "#10b981"
    background: str = "#0d1117"
    surface: str = "#161b22"
    panel: str = "#1c2333"
    text: str = "#e6edf3"
    text_dim: str = "#8b949e"
    border: str = "#30363d"

    # Semantic colors
    success: str = "#3fb950"
    warning: str = "#d29922"
    error: str = "#f85149"
    info: str = "#58a6ff"

    # Node editor colors
    node_bg: str = "#1c2333"
    node_border: str = "#30363d"
    node_selected: str = "#58a6ff"
    edge_color: str = "#8b949e"
    edge_active: str = "#3fb950"

    # Status colors
    status_running: str = "#3fb950"
    status_pending: str = "#d29922"
    status_failed: str = "#f85149"
    status_idle: str = "#8b949e"

    def to_css_variables(self) -> str:
        """Generate Textual CSS variable declarations."""
        return "\n".join([
            f"$primary: {self.primary};",
            f"$secondary: {self.secondary};",
            f"$accent: {self.accent};",
            f"$background: {self.background};",
            f"$surface: {self.surface};",
            f"$panel: {self.panel};",
            f"$text: {self.text};",
            f"$text-dim: {self.text_dim};",
            f"$border: {self.border};",
            f"$success: {self.success};",
            f"$warning: {self.warning};",
            f"$error: {self.error};",
            f"$info: {self.info};",
            f"$node-bg: {self.node_bg};",
            f"$node-border: {self.node_border};",
            f"$node-selected: {self.node_selected};",
            f"$edge-color: {self.edge_color};",
            f"$edge-active: {self.edge_active};",
        ])


# ── Built-in Themes ───────────────────────────────────────

DARK_THEME = Theme(
    name="dark",
    display_name="🌙 Dark",
    description="Default dark theme",
    is_dark=True,
)

LIGHT_THEME = Theme(
    name="light",
    display_name="☀️ Light",
    description="Light theme for bright environments",
    is_dark=False,
    primary="#0969da",
    secondary="#8250df",
    accent="#1a7f37",
    background="#ffffff",
    surface="#f6f8fa",
    panel="#f0f3f6",
    text="#1f2328",
    text_dim="#656d76",
    border="#d0d7de",
    success="#1a7f37",
    warning="#9a6700",
    error="#cf222e",
    info="#0969da",
    node_bg="#f6f8fa",
    node_border="#d0d7de",
    node_selected="#0969da",
    edge_color="#656d76",
    edge_active="#1a7f37",
    status_running="#1a7f37",
    status_pending="#9a6700",
    status_failed="#cf222e",
    status_idle="#656d76",
)

CYBERPUNK_THEME = Theme(
    name="cyberpunk",
    display_name="🌆 Cyberpunk",
    description="Neon cyberpunk aesthetic",
    is_dark=True,
    primary="#ff00ff",
    secondary="#00ffff",
    accent="#ff6600",
    background="#0a0a0a",
    surface="#1a0a2e",
    panel="#16213e",
    text="#e0e0ff",
    text_dim="#7070a0",
    border="#4a0080",
    success="#00ff41",
    warning="#ffff00",
    error="#ff0040",
    info="#00d4ff",
    node_bg="#1a0a2e",
    node_border="#4a0080",
    node_selected="#ff00ff",
    edge_color="#7070a0",
    edge_active="#00ff41",
)


class ThemeManager:
    """
    Manages theme registration, switching, and CSS generation.

    Thread-safe. Emits events on theme change.
    """

    def __init__(self):
        self._themes: Dict[str, Theme] = {}
        self._current: str = "dark"

        # Register built-in themes
        self.register(DARK_THEME)
        self.register(LIGHT_THEME)
        self.register(CYBERPUNK_THEME)

    @property
    def current_theme(self) -> Theme:
        """Get current active theme."""
        return self._themes.get(self._current, DARK_THEME)

    @property
    def current_name(self) -> str:
        return self._current

    @property
    def is_dark(self) -> bool:
        return self.current_theme.is_dark

    def register(self, theme: Theme) -> None:
        """Register a theme."""
        self._themes[theme.name] = theme
        logger.debug(f"Theme registered: {theme.name}")

    def switch(self, name: str) -> bool:
        """
        Switch to a named theme.
        Emits 'theme.changed' event.
        Returns True if switched successfully.
        """
        if name not in self._themes:
            logger.warning(f"Theme not found: {name}")
            return False

        old_name = self._current
        if old_name == name:
            return True

        self._current = name
        event_bus.emit(
            "theme.changed",
            source="ThemeManager",
            old_theme=old_name,
            new_theme=name,
            is_dark=self._themes[name].is_dark,
        )
        logger.info(f"Theme switched: {old_name} → {name}")
        return True

    def cycle(self) -> str:
        """Cycle to next theme. Returns new theme name."""
        names = list(self._themes.keys())
        if not names:
            return self._current

        try:
            idx = names.index(self._current)
            next_idx = (idx + 1) % len(names)
        except ValueError:
            next_idx = 0

        self.switch(names[next_idx])
        return names[next_idx]

    def list_themes(self) -> list:
        """List all registered themes."""
        return [
            {
                "name": t.name,
                "display_name": t.display_name,
                "description": t.description,
                "is_dark": t.is_dark,
                "active": t.name == self._current,
            }
            for t in self._themes.values()
        ]

    def get_css(self) -> str:
        """Generate complete Textual CSS for current theme."""
        theme = self.current_theme
        return _generate_tcss(theme)


def _generate_tcss(theme: Theme) -> str:
    """Generate complete Textual CSS from a Theme object."""
    return f"""
/* ── Ultra TUI Theme: {theme.display_name} ── */

Screen {{
    background: {theme.background};
    color: {theme.text};
}}

/* ── Sidebar ── */
#sidebar {{
    width: 28;
    background: {theme.surface};
    border-right: solid {theme.border};
    padding: 1;
}}

#sidebar.hidden {{
    display: none;
}}

#sidebar .nav-item {{
    padding: 0 1;
    height: 3;
    content-align: left middle;
}}

#sidebar .nav-item:hover {{
    background: {theme.panel};
}}

#sidebar .nav-item.--active {{
    background: {theme.primary} 15%;
    color: {theme.primary};
    text-style: bold;
}}

/* ── Workspace ── */
#workspace {{
    background: {theme.background};
}}

/* ── Status Bar ── */
#status-bar {{
    dock: bottom;
    height: 1;
    background: {theme.surface};
    color: {theme.text_dim};
    padding: 0 1;
}}

#status-bar .status-item {{
    margin: 0 2;
}}

/* ── Panels ── */
.panel {{
    background: {theme.panel};
    border: solid {theme.border};
    padding: 1;
    margin: 0 1 1 1;
}}

.panel-title {{
    text-style: bold;
    color: {theme.primary};
    padding: 0 1;
}}

/* ── Node Editor ── */
.node {{
    background: {theme.node_bg};
    border: solid {theme.node_border};
    padding: 0 1;
    margin: 1;
    min-width: 20;
    height: auto;
}}

.node:focus {{
    border: solid {theme.node_selected};
}}

.node.--selected {{
    border: double {theme.node_selected};
    background: {theme.node_selected} 10%;
}}

.node-title {{
    text-style: bold;
    color: {theme.primary};
}}

.node-port {{
    color: {theme.text_dim};
}}

.edge {{
    color: {theme.edge_color};
}}

.edge.--active {{
    color: {theme.edge_active};
    text-style: bold;
}}

/* ── Buttons ── */
Button {{
    background: {theme.surface};
    color: {theme.text};
    border: solid {theme.border};
    padding: 0 2;
    margin: 0 1;
    height: 3;
}}

Button:hover {{
    background: {theme.primary} 20%;
    border: solid {theme.primary};
}}

Button:focus {{
    border: solid {theme.primary};
    text-style: bold;
}}

Button.-primary {{
    background: {theme.primary};
    color: {theme.background};
    text-style: bold;
}}

/* ── Input ── */
Input {{
    background: {theme.surface};
    color: {theme.text};
    border: solid {theme.border};
    padding: 0 1;
}}

Input:focus {{
    border: solid {theme.primary};
}}

/* ── DataTable ── */
DataTable {{
    background: {theme.panel};
}}

DataTable > .datatable--header {{
    background: {theme.surface};
    color: {theme.primary};
    text-style: bold;
}}

DataTable > .datatable--cursor {{
    background: {theme.primary} 20%;
}}

/* ── Modal ── */
.modal-overlay {{
    align: center middle;
    background: {theme.background} 80%;
}}

.modal-container {{
    background: {theme.panel};
    border: double {theme.primary};
    padding: 1 2;
    width: 70%;
    max-width: 90;
    height: auto;
    max-height: 80%;
}}

.modal-title {{
    text-style: bold;
    color: {theme.primary};
    text-align: center;
    padding: 0 0 1 0;
}}

/* ── Log Panel ── */
#log-panel {{
    height: 12;
    background: {theme.surface};
    border-top: solid {theme.border};
}}

#log-panel.hidden {{
    display: none;
}}

#log-panel RichLog {{
    background: {theme.surface};
    padding: 0 1;
}}

/* ── Dashboard Charts ── */
.chart-panel {{
    background: {theme.panel};
    border: solid {theme.border};
    padding: 1;
    margin: 1;
}}

/* ── Status Indicators ── */
.status-running {{
    color: {theme.status_running};
}}

.status-pending {{
    color: {theme.status_pending};
}}

.status-failed {{
    color: {theme.status_failed};
}}

.status-idle {{
    color: {theme.status_idle};
}}

/* ── Toast / Notifications ── */
Toast {{
    background: {theme.surface};
    border: solid {theme.border};
    padding: 1 2;
}}

/* ── Command Palette ── */
#command-palette {{
    align: center top;
    margin-top: 3;
    width: 70%;
    max-width: 80;
    height: auto;
    max-height: 60%;
    background: {theme.panel};
    border: double {theme.primary};
    padding: 0;
}}

#command-palette Input {{
    margin: 1;
    border: solid {theme.border};
}}

#command-palette .command-item {{
    padding: 0 2;
    height: 2;
}}

#command-palette .command-item:hover {{
    background: {theme.primary} 15%;
}}

#command-palette .command-item.--highlighted {{
    background: {theme.primary} 20%;
    color: {theme.primary};
}}

/* ── Scrollbar ── */
Scrollbar {{
    background: {theme.surface};
}}

ScrollbarSlider {{
    background: {theme.border};
}}

ScrollbarSlider:hover {{
    background: {theme.text_dim};
}}

/* ── Tabs ── */
Tabs {{
    background: {theme.surface};
}}

Tab {{
    background: {theme.surface};
    color: {theme.text_dim};
    padding: 0 2;
}}

Tab:hover {{
    color: {theme.text};
    background: {theme.panel};
}}

Tab.-active {{
    color: {theme.primary};
    text-style: bold;
    background: {theme.panel};
}}

/* ── Progress Bar ── */
ProgressBar {{
    padding: 0 1;
}}

Bar > .bar--bar {{
    color: {theme.primary};
    background: {theme.surface};
}}

Bar > .bar--complete {{
    color: {theme.success};
}}

/* ── Mode Indicator ── */
.mode-badge {{
    padding: 0 1;
    text-style: bold;
}}

.mode-badge.--build {{
    color: {theme.info};
}}

.mode-badge.--run {{
    color: {theme.success};
}}

.mode-badge.--monitor {{
    color: {theme.warning};
}}

.mode-badge.--focus {{
    color: {theme.secondary};
}}
"""


# ── Singleton ──────────────────────────────────────────────
theme_manager = ThemeManager()