"""Plugin system for extending the TUI."""

from .api import PluginAPI, plugin_api
from .loader import PluginLoader

__all__ = ["PluginAPI", "plugin_api", "PluginLoader"]