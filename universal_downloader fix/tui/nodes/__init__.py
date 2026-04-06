"""
Visual Node Engine — core differentiator.
Provides node data model, registry, built-in nodes, and terminal renderer.
"""

from .base import Node, Edge, Port, PortType, NodeStatus, NodeGraph
from .registry import NodeRegistry, node_registry
from .builtin import register_builtin_nodes

__all__ = [
    "Node",
    "Edge",
    "Port",
    "PortType",
    "NodeStatus",
    "NodeGraph",
    "NodeRegistry",
    "node_registry",
    "register_builtin_nodes",
]