"""
Plugin loader — discovers and loads plugins from directories.
"""

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from ..engine.events import event_bus
from ..engine.errors import ErrorBoundary, ErrorSeverity

logger = logging.getLogger(__name__)


class PluginInfo:
    def __init__(self, name: str, path: str, module=None):
        self.name = name
        self.path = path
        self.module = module
        self.loaded = False
        self.error: Optional[str] = None


class PluginLoader:
    """
    Discovers and loads plugin files from directories.

    Plugin files must have a `register(api)` function
    that receives the PluginAPI instance.
    """

    def __init__(self, plugin_dirs: Optional[List[str]] = None):
        self._plugin_dirs = [
            Path(d) for d in (plugin_dirs or [
                os.path.expanduser("~/.universal_downloader/plugins"),
                "./plugins",
            ])
        ]
        self._plugins: Dict[str, PluginInfo] = {}

    @property
    def loaded_plugins(self) -> List[PluginInfo]:
        return [p for p in self._plugins.values() if p.loaded]

    def discover(self) -> List[PluginInfo]:
        """Discover plugin files in plugin directories."""
        discovered = []
        for plugin_dir in self._plugin_dirs:
            if not plugin_dir.exists():
                continue
            for filepath in plugin_dir.glob("*.py"):
                if filepath.name.startswith("_"):
                    continue
                name = filepath.stem
                if name not in self._plugins:
                    info = PluginInfo(name=name, path=str(filepath))
                    self._plugins[name] = info
                    discovered.append(info)
                    logger.debug(f"Discovered plugin: {name} at {filepath}")

        return discovered

    def load_all(self) -> int:
        """Load all discovered plugins. Returns count loaded."""
        self.discover()
        count = 0
        for name, info in self._plugins.items():
            if info.loaded:
                continue
            if self.load_plugin(name):
                count += 1
        return count

    def load_plugin(self, name: str) -> bool:
        """Load a single plugin by name."""
        info = self._plugins.get(name)
        if not info:
            logger.warning(f"Plugin not found: {name}")
            return False

        if info.loaded:
            return True

        with ErrorBoundary(
            f"loading plugin '{name}'",
            severity=ErrorSeverity.LOW,
            reraise=False,
        ):
            spec = importlib.util.spec_from_file_location(
                f"tui_plugin_{name}", info.path
            )
            if not spec or not spec.loader:
                info.error = "Invalid module spec"
                return False

            module = importlib.util.module_from_spec(spec)

            # Add to sys.modules temporarily
            module_name = f"tui_plugin_{name}"
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # Call register() if it exists
            if hasattr(module, "register"):
                from .api import plugin_api
                module.register(plugin_api)

            info.module = module
            info.loaded = True
            logger.info(f"Plugin loaded: {name}")

            event_bus.emit(
                "plugin.loaded",
                source="PluginLoader",
                name=name,
                path=info.path,
            )
            return True

        info.error = "Load failed"
        return False

    def unload_plugin(self, name: str) -> bool:
        """Unload a plugin."""
        info = self._plugins.get(name)
        if not info or not info.loaded:
            return False

        # Call unregister() if it exists
        if info.module and hasattr(info.module, "unregister"):
            try:
                info.module.unregister()
            except Exception:
                pass

        # Remove from sys.modules
        module_name = f"tui_plugin_{name}"
        sys.modules.pop(module_name, None)

        info.loaded = False
        info.module = None

        event_bus.emit("plugin.unloaded", source="PluginLoader", name=name)
        logger.info(f"Plugin unloaded: {name}")
        return True

    def list_plugins(self) -> List[Dict]:
        """List all plugins with status."""
        self.discover()
        return [
            {
                "name": info.name,
                "path": info.path,
                "loaded": info.loaded,
                "error": info.error,
            }
            for info in self._plugins.values()
        ]