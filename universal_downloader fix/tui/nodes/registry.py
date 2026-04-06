"""
Node type registry.

Allows registration of node types with their factory functions.
Nodes are created from the registry when building workflows.

Usage:
    from tui.nodes import node_registry

    # Register a node type
    node_registry.register(
        type_name="url_input",
        factory=create_url_input_node,
        category=NodeCategory.INPUT,
        description="URL input source",
    )

    # Create instance
    node = node_registry.create("url_input")
"""

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .base import Node, NodeCategory, Port

logger = logging.getLogger(__name__)


@dataclass
class NodeTypeInfo:
    """Metadata about a registered node type."""
    type_name: str
    display_name: str
    description: str
    category: NodeCategory
    icon: str
    factory: Callable[..., Node]
    input_ports: List[Dict[str, Any]]
    output_ports: List[Dict[str, Any]]
    default_config: Dict[str, Any]
    tags: List[str]


class NodeRegistry:
    """
    Central registry for all available node types.

    Node types can be registered by:
    - Built-in nodes (tui/nodes/builtin.py)
    - Plugins (tui/plugins/)
    - User scripts
    """

    def __init__(self):
        self._types: Dict[str, NodeTypeInfo] = {}

    def register(
        self,
        type_name: str,
        factory: Callable[..., Node],
        display_name: str = "",
        description: str = "",
        category: NodeCategory = NodeCategory.PROCESSING,
        icon: str = "⚙️",
        input_ports: Optional[List[Dict[str, Any]]] = None,
        output_ports: Optional[List[Dict[str, Any]]] = None,
        default_config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        """Register a node type."""
        info = NodeTypeInfo(
            type_name=type_name,
            display_name=display_name or type_name.replace("_", " ").title(),
            description=description,
            category=category,
            icon=icon,
            factory=factory,
            input_ports=input_ports or [],
            output_ports=output_ports or [],
            default_config=default_config or {},
            tags=tags or [],
        )
        self._types[type_name] = info
        logger.debug(f"Node type registered: {type_name}")

    def unregister(self, type_name: str) -> bool:
        """Remove a node type."""
        return self._types.pop(type_name, None) is not None

    def create(self, type_name: str, **overrides: Any) -> Optional[Node]:
        """
        Create a node instance from a registered type.

        Args:
            type_name: Registered type name
            **overrides: Override node properties (name, x, y, config, etc.)
        """
        info = self._types.get(type_name)
        if not info:
            logger.warning(f"Unknown node type: {type_name}")
            return None

        try:
            node = info.factory(**overrides)
            node.node_type = type_name
            if not node.name:
                node.name = info.display_name
            if not node.icon or node.icon == "⚙️":
                node.icon = info.icon
            node.category = info.category
            return node
        except Exception as e:
            logger.error(f"Failed to create node '{type_name}': {e}")
            return None

    def create_from_dict(self, node_data: Dict[str, Any]) -> Node:
        """
        Create node from serialized dict, restoring execute function from registry.
        Falls back to base Node if type not found.
        """
        type_name = node_data.get("node_type", "")
        info = self._types.get(type_name)

        if info:
            node = info.factory()
            # Restore serialized state
            node.id = node_data.get("id", node.id)
            node.name = node_data.get("name", node.name)
            node.x = node_data.get("x", 0)
            node.y = node_data.get("y", 0)
            node.config = node_data.get("config", {})
            # Restore ports from serialized data (preserve IDs for edge reconnection)
            if node_data.get("inputs"):
                node.inputs = [Port.from_dict(p) for p in node_data["inputs"]]
            if node_data.get("outputs"):
                node.outputs = [Port.from_dict(p) for p in node_data["outputs"]]
            return node

        return Node.from_dict(node_data)

    def get_type(self, type_name: str) -> Optional[NodeTypeInfo]:
        """Get type info."""
        return self._types.get(type_name)

    def list_types(self) -> List[NodeTypeInfo]:
        """List all registered types."""
        return list(self._types.values())

    def list_by_category(self, category: NodeCategory) -> List[NodeTypeInfo]:
        """List types filtered by category."""
        return [t for t in self._types.values() if t.category == category]

    def search(self, query: str) -> List[NodeTypeInfo]:
        """Search types by name, description, or tags."""
        q = query.lower()
        results = []
        for info in self._types.values():
            if (
                q in info.type_name.lower()
                or q in info.display_name.lower()
                or q in info.description.lower()
                or any(q in tag.lower() for tag in info.tags)
            ):
                results.append(info)
        return results

    @property
    def type_count(self) -> int:
        return len(self._types)


# ── Singleton ──────────────────────────────────────────────
node_registry = NodeRegistry()