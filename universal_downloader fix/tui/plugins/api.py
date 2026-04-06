"""
Plugin API — public interface for plugin developers.

Usage in plugin file:
    from tui.plugins import plugin_api

    plugin_api.register_node("my_node", factory_fn, ...)
    plugin_api.register_command("my_cmd", handler_fn, ...)
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from ..engine.events import event_bus
from ..nodes.registry import node_registry
from ..nodes.base import NodeCategory

logger = logging.getLogger(__name__)


class PluginAPI:
    """Public API for plugin registration."""

    def __init__(self):
        self._registered_nodes: List[str] = []
        self._registered_commands: List[str] = []
        self._hooks: Dict[str, List[Callable]] = {}

    def register_node(
        self,
        type_name: str,
        factory: Callable,
        display_name: str = "",
        description: str = "",
        category: str = "processing",
        icon: str = "🔌",
        tags: Optional[List[str]] = None,
    ) -> None:
        """Register a custom node type."""
        cat = NodeCategory(category) if category in [c.value for c in NodeCategory] else NodeCategory.PROCESSING

        node_registry.register(
            type_name=type_name,
            factory=factory,
            display_name=display_name or type_name,
            description=description,
            category=cat,
            icon=icon,
            tags=tags or ["plugin"],
        )
        self._registered_nodes.append(type_name)
        event_bus.emit("plugin.node.registered", source="PluginAPI", type_name=type_name)
        logger.info(f"Plugin node registered: {type_name}")

    def register_command(
        self,
        name: str,
        handler: Callable,
        description: str = "",
        aliases: Optional[List[str]] = None,
    ) -> None:
        """Register a custom command."""
        self._registered_commands.append(name)
        event_bus.emit(
            "plugin.command.registered",
            source="PluginAPI",
            name=name,
            handler=handler,
            description=description,
            aliases=aliases or [],
        )
        logger.info(f"Plugin command registered: {name}")

    def on_event(self, event_name: str, handler: Callable) -> None:
        """Subscribe to an event."""
        event_bus.on(event_name, handler)
        if event_name not in self._hooks:
            self._hooks[event_name] = []
        self._hooks[event_name].append(handler)

    def emit_event(self, event_name: str, **data) -> None:
        """Emit a custom event."""
        event_bus.emit(event_name, source="plugin", **data)

    @property
    def registered_nodes(self) -> List[str]:
        return list(self._registered_nodes)

    @property
    def registered_commands(self) -> List[str]:
        return list(self._registered_commands)


# ── Singleton ──────────────────────────────────────────────
plugin_api = PluginAPI()