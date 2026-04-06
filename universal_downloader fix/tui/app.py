# pyright: reportOptionalMemberAccess=false, reportOptionalCall=false
"""
UltraApp — Main Textual application.

Integrates all subsystems:
- Sidebar navigation
- Multi-mode workspace (Build/Run/Monitor/Focus)
- Command palette with fuzzy search
- Status bar with live info
- Log panel
- Node editor + workflow engine
- Plugin system
- Adaptive UI + intelligence
- Theme switching
- Keybinding system
"""

import asyncio
import logging
import shutil
from typing import Any, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Static, Input, Header
from textual.containers import Vertical
from textual import work, events

# ── Engine ──────────────────────────────────────────────────
from .engine import event_bus, LifecycleManager, ConfigManager, ErrorBoundary, ErrorSeverity

# ── Themes ──────────────────────────────────────────────────
from .themes import theme_manager

# ── Logging ─────────────────────────────────────────────────
from .logging_ import log_manager

# ── Nodes & Workflow ────────────────────────────────────────
from .nodes import register_builtin_nodes, node_registry, NodeGraph
from .workflow import WorkflowExecutor, WorkflowStorage, WorkflowScheduler

# ── Modes ───────────────────────────────────────────────────
from .modes import mode_manager, AppMode

# ── Intelligence ────────────────────────────────────────────
from .intelligence import usage_tracker, advisor

# ── Plugins ─────────────────────────────────────────────────
from .plugins import PluginLoader, plugin_api

# ── Dashboard ───────────────────────────────────────────────
from .dashboard.metrics import metrics

# ── Keybindings ─────────────────────────────────────────────
from .keybindings import keybinding_manager

# ── Palette ─────────────────────────────────────────────────
from .palette.commander import command_palette

# ── Screens ─────────────────────────────────────────────────
from .screens.main_screen import MainScreen

# ── Config ──────────────────────────────────────────────────
from config import Config, setup_logging
import extractors  # Register built-in extractors

logger = logging.getLogger(__name__)


class CommandPaletteScreen:
    """Textual modal screen for fuzzy command palette."""
    pass  # Implemented inline below via push_screen


from textual.screen import ModalScreen


class PaletteModal(ModalScreen[str]):
    """Fuzzy search command palette modal."""

    DEFAULT_CSS = """
    PaletteModal {
        align: center top;
    }
    PaletteModal #palette-container {
        margin-top: 3;
        width: 70%;
        max-width: 80;
        height: auto;
        max-height: 60%;
        background: $panel;
        border: double $primary;
        padding: 0;
    }
    PaletteModal #palette-input {
        margin: 1;
    }
    PaletteModal #palette-results {
        height: auto;
        max-height: 20;
        padding: 0 1;
    }
    PaletteModal .palette-item {
        height: 2;
        padding: 0 1;
        content-align: left middle;
    }
    PaletteModal .palette-item:hover {
        background: $primary 15%;
    }
    PaletteModal .palette-item.--highlighted {
        background: $primary 20%;
        color: $primary;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._results = []
        self._highlighted = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-container"):
            yield Input(placeholder="Type a command...", id="palette-input")
            yield Vertical(id="palette-results")

    def on_mount(self) -> None:
        self._refresh_results("")
        self.query_one("#palette-input", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "palette-input":
            self._refresh_results(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "palette-input":
            self._execute_selected()

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.dismiss("")
            event.stop()
        elif event.key == "down":
            self._highlighted = min(self._highlighted + 1, len(self._results) - 1)
            self._update_highlight()
            event.stop()
        elif event.key == "up":
            self._highlighted = max(self._highlighted - 1, 0)
            self._update_highlight()
            event.stop()
        elif event.key == "enter":
            self._execute_selected()
            event.stop()

    def _refresh_results(self, query: str) -> None:
        """Update results based on query."""
        self._results = command_palette.search(query, limit=12)
        self._highlighted = 0

        try:
            container = self.query_one("#palette-results", Vertical)
            container.remove_children()

            for idx, cmd in enumerate(self._results):
                icon = cmd.icon or "⚡"
                text = f" {icon}  [bold]{cmd.name}[/bold]  [dim]{cmd.description}[/dim]"
                item = Static(text, classes="palette-item")
                item.name = cmd.name
                if idx == 0:
                    item.add_class("--highlighted")
                container.mount(item)
        except Exception:
            pass

    def _update_highlight(self) -> None:
        """Update visual highlight on results."""
        try:
            items = self.query(".palette-item")
            for idx, item in enumerate(items):
                item.set_class(idx == self._highlighted, "--highlighted")
        except Exception:
            pass

    def _execute_selected(self) -> None:
        """Execute the highlighted command."""
        if 0 <= self._highlighted < len(self._results):
            cmd = self._results[self._highlighted]
            self.dismiss(cmd.name)


class UltraApp(App):
    """
    Main Textual application.
    Orchestrates all subsystems and provides the entry point.
    """

    TITLE = "Universal Media Downloader"
    SUB_TITLE = "Ultra TUI"

    BINDINGS = [
        Binding("ctrl+p", "command_palette", "Commands", show=True, priority=True),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", show=True),
        Binding("ctrl+d", "quick_download", "Download", show=True),
        Binding("ctrl+l", "toggle_log", "Logs", show=True),
        Binding("ctrl+t", "cycle_theme", "Theme", show=True),
        Binding("ctrl+c", "quit_app", "Quit", show=True, priority=True),
        Binding("f1", "mode_build", "Build"),
        Binding("f2", "mode_run", "Run"),
        Binding("f3", "mode_monitor", "Monitor"),
        Binding("f4", "mode_focus", "Focus"),
        Binding("ctrl+q", "quit_app", "Quit", show=True),
        Binding("question_mark", "show_help", "Help"),
    ]

    CSS = ""  # Will be set dynamically from theme

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

        # Load config (untouched config.py)
        self._config = Config.load()
        setup_logging(self._config, use_console=False)

        # Systems
        self._lifecycle = LifecycleManager()
        self._config_manager = ConfigManager(self._config)
        self._workflow_storage = WorkflowStorage()
        self._scheduler = WorkflowScheduler()
        self._plugin_loader = PluginLoader()
        self._current_graph = NodeGraph("Default Workflow")

        # Apply theme CSS
        self.CSS = theme_manager.get_css()

    @property
    def config(self) -> Config:
        return self._config

    @property
    def current_graph(self) -> NodeGraph:
        return self._current_graph

    # ── Lifecycle ──────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield MainScreen(id="main-screen")

    async def on_mount(self) -> None:
        """Initialize all subsystems on app mount."""
        # Register startup hooks
        self._lifecycle.register_startup("nodes", self._init_nodes, priority=100)
        self._lifecycle.register_startup("commands", self._init_commands, priority=90)
        self._lifecycle.register_startup("plugins", self._init_plugins, priority=80)
        self._lifecycle.register_startup("intelligence", self._init_intelligence, priority=70)
        self._lifecycle.register_startup("logging", self._init_logging, priority=60)

        # Register shutdown hooks
        self._lifecycle.register_shutdown("usage_save", self._save_usage, priority=100)
        self._lifecycle.register_shutdown("scheduler_stop", self._stop_scheduler, priority=90)

        # Register services
        self._lifecycle.register_service("config", self._config)
        self._lifecycle.register_service("config_manager", self._config_manager)
        self._lifecycle.register_service("workflow_storage", self._workflow_storage)
        self._lifecycle.register_service("scheduler", self._scheduler)

        # Run startup
        await self._lifecycle.startup()

        # Log startup info
        self._log_startup_info()

        # Subscribe to events
        event_bus.on("theme.changed", self._on_theme_changed)
        event_bus.on("plugin.command.registered", self._on_plugin_command)

    async def on_unmount(self) -> None:
        """Cleanup on app exit."""
        await self._lifecycle.shutdown()

    # ── Startup Hooks ──────────────────────────────────────

    def _init_nodes(self) -> None:
        """Register built-in node types."""
        register_builtin_nodes()
        log_manager.info(
            f"Registered {node_registry.type_count} node types",
            source="startup",
        )

    def _init_commands(self) -> None:
        """Register all palette commands."""
        self._register_palette_commands()

    def _init_plugins(self) -> None:
        """Load plugins."""
        with ErrorBoundary("loading plugins", severity=ErrorSeverity.LOW):
            count = self._plugin_loader.load_all()
            if count > 0:
                log_manager.info(f"Loaded {count} plugin(s)", source="startup")

    def _init_intelligence(self) -> None:
        """Initialize usage tracking."""
        log_manager.debug("Intelligence layer initialized", source="startup")

    def _init_logging(self) -> None:
        """Setup log manager integration."""
        log_manager.install_python_handler(level=logging.INFO)

    def _save_usage(self) -> None:
        usage_tracker.save()

    async def _stop_scheduler(self) -> None:
        if self._scheduler.is_running:
            await self._scheduler.stop()

    # ── Startup Info ───────────────────────────────────────

    def _log_startup_info(self) -> None:
        """Log environment info."""
        deps = []
        deps.append(("FFmpeg", shutil.which("ffmpeg") is not None))
        deps.append(("aria2c", shutil.which("aria2c") is not None))

        from extractors.ytdlp import YTDLP_AVAILABLE
        deps.append(("yt-dlp", YTDLP_AVAILABLE))

        for name, available in deps:
            status = "✓" if available else "✗"
            log_manager.info(f"{status} {name}", source="env")

        log_manager.info(
            f"Nodes: {node_registry.type_count} | "
            f"Plugins: {len(self._plugin_loader.loaded_plugins)} | "
            f"Theme: {theme_manager.current_name}",
            source="startup",
        )

    # ── Navigation ─────────────────────────────────────────

    def handle_navigation(self, item_id: str) -> None:
        """Route sidebar navigation to the correct view."""
        usage_tracker.track_navigation(item_id)

        try:
            main = self.query_one("#main-screen", MainScreen)
        except Exception:
            return

        view = self._create_view(item_id)
        if view:
            main.update_workspace(view)

    def _create_view(self, item_id: str):
        """Factory: create the correct view widget for a nav item."""
        from .screens.download import DownloadView
        from .screens.batch import BatchDownloadView
        from .screens.erome import EromeView
        from .screens.info import VideoInfoView, ExtractorsView, SupportedSitesView
        from .screens.history import HistoryView, DashboardView
        from .screens.settings import SettingsView
        from .modes.build_mode import BuildModeView
        from .modes.run_mode import RunModeView
        from .modes.monitor_mode import MonitorModeView
        from .modes.focus_mode import FocusModeView

        views = {
            "download": lambda: DownloadView(),
            "audio": lambda: DownloadView(audio_only=True),
            "batch": lambda: BatchDownloadView(),
            "batch_file": lambda: BatchDownloadView(from_file=True),
            "erome": lambda: EromeView(),
            "info": lambda: VideoInfoView(),
            "extractors": lambda: ExtractorsView(),
            "sites": lambda: SupportedSitesView(),
            "history": lambda: HistoryView(),
            "dashboard": lambda: DashboardView(),
            "settings": lambda: SettingsView(),
            "workflows": lambda: self._create_workflow_view(),
            "build": lambda: BuildModeView(graph=self._current_graph),
            "run": lambda: self._create_run_view(),
            "monitor": lambda: MonitorModeView(),
            "focus": lambda: FocusModeView(),
        }

        factory = views.get(item_id)
        if factory:
            return factory()

        log_manager.warning(f"Unknown navigation: {item_id}", source="nav")
        return None

    def _create_workflow_view(self):
        from .modes.build_mode import BuildModeView
        return BuildModeView(graph=self._current_graph)

    def _create_run_view(self):
        from .modes.run_mode import RunModeView
        view = RunModeView()
        view.set_graph(self._current_graph)
        return view

    # ── Palette Commands ───────────────────────────────────

    def _register_palette_commands(self) -> None:
        """Register all built-in palette commands."""
        # Navigation commands
        nav_items = [
            ("download", "🎯", "Smart Download"),
            ("audio", "🎵", "Audio Only Download"),
            ("batch", "📦", "Batch Download"),
            ("batch_file", "📄", "Batch from File"),
            ("erome", "📸", "EroMe Album"),
            ("info", "ℹ️", "Video Info"),
            ("extractors", "🔧", "Show Extractors"),
            ("sites", "🌐", "Supported Sites"),
            ("history", "📊", "Download History"),
            ("dashboard", "📈", "Dashboard"),
            ("settings", "⚙️", "Settings"),
            ("workflows", "🔄", "Workflow Editor"),
        ]

        for item_id, icon, desc in nav_items:
            command_palette.register(
                name=desc,
                handler=lambda _id=item_id: self.handle_navigation(_id),
                description=f"Navigate to {desc}",
                category="Navigation",
                icon=icon,
                aliases=[item_id],
            )

        # Mode commands
        for mode_info in mode_manager.list_modes():
            command_palette.register(
                name=f"Mode: {mode_info['name']}",
                handler=lambda m=mode_info['mode']: self._switch_mode(m),
                description=mode_info['description'],
                category="Mode",
                icon=mode_info['icon'],
            )

        # Theme commands
        for theme_info in theme_manager.list_themes():
            command_palette.register(
                name=f"Theme: {theme_info['display_name']}",
                handler=lambda t=theme_info['name']: self._apply_theme(t),
                description=theme_info['description'],
                category="Theme",
                icon="🎨",
            )

        # Workflow commands
        command_palette.register(
            name="New Workflow",
            handler=lambda: self._new_workflow(),
            description="Create a new workflow",
            category="Workflow",
            icon="➕",
        )
        command_palette.register(
            name="Save Workflow",
            handler=lambda: self._save_current_workflow(),
            description="Save current workflow",
            category="Workflow",
            icon="💾",
        )
        command_palette.register(
            name="Load Workflow",
            handler=lambda: self._show_load_workflow(),
            description="Load a saved workflow",
            category="Workflow",
            icon="📂",
        )

        # System commands
        command_palette.register(
            name="Toggle Sidebar",
            handler=lambda: self.action_toggle_sidebar(),
            description="Show/hide sidebar",
            category="View",
            icon="📌",
        )
        command_palette.register(
            name="Toggle Logs",
            handler=lambda: self.action_toggle_log(),
            description="Show/hide log panel",
            category="View",
            icon="📜",
        )
        command_palette.register(
            name="Clear Logs",
            handler=lambda: self._clear_logs(),
            description="Clear the log panel",
            category="View",
            icon="🗑️",
        )
        command_palette.register(
            name="Reload Config",
            handler=lambda: self._reload_config(),
            description="Hot-reload configuration",
            category="System",
            icon="🔄",
        )
        command_palette.register(
            name="Quit",
            handler=lambda: self.exit(),
            description="Exit application",
            category="System",
            icon="🚪",
        )

    # ── Actions ────────────────────────────────────────────

    def action_command_palette(self) -> None:
        """Open the command palette modal."""

        def _on_result(result: str) -> None:
            if result:
                command_palette.execute(result)

        self.push_screen(PaletteModal(), _on_result)

    def action_toggle_sidebar(self) -> None:
        try:
            from .widgets.sidebar import Sidebar
            sidebar = self.query_one("#sidebar", Sidebar)
            sidebar.toggle()
        except Exception:
            pass

    def action_toggle_log(self) -> None:
        try:
            main = self.query_one("#main-screen", MainScreen)
            log_panel = main.get_log_panel()
            if log_panel:
                log_panel.toggle()
        except Exception:
            pass

    def action_quick_download(self) -> None:
        """Quick download via input modal."""
        from .widgets.modal import InputModal

        def _on_url(url: str) -> None:
            if url.strip():
                self.handle_navigation("download")
                # Give workspace time to mount, then set URL
                self.set_timer(0.3, lambda: self._inject_url(url.strip()))

        self.push_screen(
            InputModal(
                title="🎯 Quick Download",
                prompt="Enter URL to download:",
                placeholder="https://...",
            ),
            _on_url,
        )

    def _inject_url(self, url: str) -> None:
        """Inject URL into the download view input."""
        try:
            url_input = self.query_one("#dl-url-input", Input)
            url_input.value = url
            url_input.action_submit()
        except Exception:
            pass

    def action_cycle_theme(self) -> None:
        theme_manager.cycle()

    def action_mode_build(self) -> None:
        self._switch_mode("build_mode")

    def action_mode_run(self) -> None:
        self._switch_mode("run_mode")

    def action_mode_monitor(self) -> None:
        self._switch_mode("monitor_mode")

    def action_mode_focus(self) -> None:
        self._switch_mode("focus_mode")

    def action_quit_app(self) -> None:
        self.exit()

    def action_show_help(self) -> None:
        """Show keybindings help."""
        help_text = keybinding_manager.generate_help_text()
        self.notify(help_text, title="Keybindings", timeout=10)

    # ── Mode Switching ─────────────────────────────────────

    def _switch_mode(self, mode_value: str) -> None:
        """Switch application mode and update workspace."""
        mode_map = {
            "build_mode": ("build", AppMode.BUILD),
            "run_mode": ("run", AppMode.RUN),
            "monitor_mode": ("monitor", AppMode.MONITOR),
            "focus_mode": ("focus", AppMode.FOCUS),
        }

        entry = mode_map.get(mode_value)
        if not entry:
            return

        nav_id, mode = entry
        mode_manager.switch(mode)
        self.handle_navigation(nav_id)

        # Update status bar
        try:
            main = self.query_one("#main-screen", MainScreen)
            sb = main.get_status_bar()
            if sb:
                sb.mode_name = mode_manager.current_name
        except Exception:
            pass

    # ── Theme ──────────────────────────────────────────────

    def _apply_theme(self, theme_name: str) -> None:
        theme_manager.switch(theme_name)

    def _on_theme_changed(self, event) -> None:
        """Reapply CSS when theme changes."""
        new_css = theme_manager.get_css()
        self.stylesheet.source = new_css
        try:
            self.stylesheet.reparse()
            self.refresh(layout=True)
        except Exception:
            pass

        self.notify(
            f"Theme: {theme_manager.current_theme.display_name}",
            title="🎨 Theme",
            timeout=3,
        )

    # ── Workflow Helpers ───────────────────────────────────

    def _new_workflow(self) -> None:
        self._current_graph = NodeGraph("New Workflow")
        self.handle_navigation("build")
        self.notify("New workflow created", title="Workflow")

    def _save_current_workflow(self) -> None:
        try:
            path = self._workflow_storage.save(self._current_graph)
            self.notify(f"Saved: {path}", title="💾 Workflow")
            log_manager.info(f"Workflow saved: {path}", source="workflow")
        except Exception as e:
            self.notify(f"Save failed: {e}", title="Error", severity="error")

    def _show_load_workflow(self) -> None:
        """Show workflow selection modal."""
        workflows = self._workflow_storage.list_workflows()
        if not workflows:
            self.notify("No saved workflows", title="Workflow")
            return

        from .widgets.modal import SelectionModal

        items = [
            (wf["name"], f"{wf.get('graph_name', wf['name'])} ({wf.get('node_count', 0)} nodes)")
            for wf in workflows
            if "error" not in wf
        ]

        def _on_select(name: str) -> None:
            if name:
                graph = self._workflow_storage.load(name)
                if graph:
                    self._current_graph = graph
                    self.handle_navigation("build")
                    self.notify(f"Loaded: {name}", title="📂 Workflow")

        self.push_screen(
            SelectionModal(title="📂 Load Workflow", items=items),
            _on_select,
        )

    # ── Node Editor Events ─────────────────────────────────

    def on_node_editor_canvas_node_action(self, event) -> None:
        """Handle node editor actions."""
        from .widgets.node_editor import NodeEditorCanvas
        from .widgets.modal import SelectionModal, InputModal, ConfirmModal
        from .nodes.renderer import GraphRenderer

        action = event.action

        if action == "add":
            # Show node type selector
            types = node_registry.list_types()
            items = [(t.type_name, f"{t.icon} {t.display_name} — {t.description}") for t in types]

            def _on_select(type_name: str) -> None:
                if type_name:
                    try:
                        canvas = self.query_one("#node-canvas", NodeEditorCanvas)
                        node = canvas.add_node_by_type(type_name)
                        if node:
                            self.notify(f"Added: {node.name}", title="Node")
                    except Exception:
                        pass

            self.push_screen(SelectionModal(title="➕ Add Node", items=items), _on_select)

        elif action == "delete":
            def _on_confirm(yes: bool) -> None:
                if yes:
                    try:
                        canvas = self.query_one("#node-canvas", NodeEditorCanvas)
                        canvas.remove_selected_node()
                    except Exception:
                        pass

            self.push_screen(ConfirmModal(message="Delete this node?"), _on_confirm)

        elif action == "save":
            self._save_current_workflow()

        elif action == "load":
            self._show_load_workflow()

        elif action == "run":
            self._switch_mode("run_mode")

    # ── Plugin Events ──────────────────────────────────────

    def _on_plugin_command(self, event) -> None:
        """Register plugin commands into palette."""
        name = event.get("name", "")
        handler = event.get("handler")
        desc = event.get("description", "")
        aliases = event.get("aliases", [])

        if name and handler:
            command_palette.register(
                name=name,
                handler=handler,
                description=desc,
                category="Plugin",
                icon="🔌",
                aliases=aliases,
            )

    # ── Config ─────────────────────────────────────────────

    def _reload_config(self) -> None:
        if self._config_manager.reload():
            self.notify("Config reloaded ✓", title="System")
        else:
            self.notify("No config file found", title="System", severity="warning")

    def _clear_logs(self) -> None:
        try:
            main = self.query_one("#main-screen", MainScreen)
            log_panel = main.get_log_panel()
            if log_panel:
                log_panel.clear()
        except Exception:
            pass
