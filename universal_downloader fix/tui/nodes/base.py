"""
Node, Edge, Port data models and NodeGraph container.

Design:
- Node = functional unit with input/output ports
- Edge = directed connection between ports
- Port = typed data endpoint on a node
- NodeGraph = container managing nodes + edges

All models are serializable (to_dict / from_dict) for workflow persistence.
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Set

from ..engine.events import event_bus


class PortType(Enum):
    """Data type flowing through ports."""
    ANY = "any"
    URL = "url"
    URL_LIST = "url_list"
    MEDIA_INFO = "media_info"
    FORMAT = "format"
    FILE_PATH = "file_path"
    TEXT = "text"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DICT = "dict"
    LIST = "list"


class NodeStatus(Enum):
    """Execution status of a node."""
    IDLE = "idle"
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class NodeCategory(Enum):
    """Node classification for UI grouping."""
    INPUT = "input"
    PROCESSING = "processing"
    OUTPUT = "output"
    CONDITIONAL = "conditional"
    UTILITY = "utility"
    DOWNLOAD = "download"
    TRANSFORM = "transform"


@dataclass
class Port:
    """
    Data endpoint on a node.
    Input ports receive data; output ports emit data.
    """
    id: str = ""
    name: str = ""
    port_type: PortType = PortType.ANY
    is_input: bool = True
    required: bool = True
    default_value: Any = None
    description: str = ""
    value: Any = None       # Current runtime value
    connected: bool = False  # Whether an edge connects to this port

    def __post_init__(self):
        if not self.id:
            self.id = f"port_{uuid.uuid4().hex[:8]}"

    def accepts(self, other_type: PortType) -> bool:
        """Check if this port can accept data of given type."""
        if self.port_type == PortType.ANY or other_type == PortType.ANY:
            return True
        return self.port_type == other_type

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "port_type": self.port_type.value,
            "is_input": self.is_input,
            "required": self.required,
            "default_value": self.default_value,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Port":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            port_type=PortType(data.get("port_type", "any")),
            is_input=data.get("is_input", True),
            required=data.get("required", True),
            default_value=data.get("default_value"),
            description=data.get("description", ""),
        )


@dataclass
class Node:
    """
    Functional unit in the visual workflow.

    Each node type defines:
    - Input/output ports (data interface)
    - execute() method (business logic)
    - Visual properties (position, icon, color)
    """
    id: str = ""
    node_type: str = ""          # Registry type name
    name: str = ""               # User-visible label
    description: str = ""
    category: NodeCategory = NodeCategory.PROCESSING
    icon: str = "⚙️"

    # Ports
    inputs: List[Port] = field(default_factory=list)
    outputs: List[Port] = field(default_factory=list)

    # Configuration
    config: Dict[str, Any] = field(default_factory=dict)

    # Visual position (grid coordinates)
    x: int = 0
    y: int = 0
    width: int = 24
    height: int = 6

    # Runtime state
    status: NodeStatus = NodeStatus.IDLE
    error_message: str = ""
    execution_time: float = 0.0
    last_run: float = 0.0

    # Internal
    _execute_fn: Optional[Callable] = field(default=None, repr=False)

    def __post_init__(self):
        if not self.id:
            self.id = f"node_{uuid.uuid4().hex[:8]}"

    # ── Port Access ────────────────────────────────────────

    def get_input(self, name: str) -> Optional[Port]:
        """Get input port by name."""
        for port in self.inputs:
            if port.name == name:
                return port
        return None

    def get_output(self, name: str) -> Optional[Port]:
        """Get output port by name."""
        for port in self.outputs:
            if port.name == name:
                return port
        return None

    def get_input_value(self, name: str, default: Any = None) -> Any:
        """Get value from input port."""
        port = self.get_input(name)
        if port is None:
            return default
        return port.value if port.value is not None else (port.default_value or default)

    def set_output_value(self, name: str, value: Any) -> None:
        """Set value on output port."""
        port = self.get_output(name)
        if port:
            port.value = value

    # ── Execution ──────────────────────────────────────────

    async def execute(self, context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Execute node logic. Override in subclasses or set _execute_fn.

        Args:
            context: Shared execution context (config, services, etc.)

        Returns:
            Dict of output port name → value
        """
        if self._execute_fn:
            start = time.time()
            self.status = NodeStatus.RUNNING
            self.error_message = ""

            event_bus.emit(
                "workflow.node.started",
                source="Node",
                node_id=self.id,
                node_type=self.node_type,
                node_name=self.name,
            )

            try:
                # Collect input values
                input_data = {
                    port.name: port.value if port.value is not None else port.default_value
                    for port in self.inputs
                }

                result = self._execute_fn(input_data, self.config, context or {})

                # Handle async results
                import asyncio
                if asyncio.iscoroutine(result):
                    result = await result

                if not isinstance(result, dict):
                    result = {"output": result}

                # Set output values
                for port in self.outputs:
                    if port.name in result:
                        port.value = result[port.name]

                self.status = NodeStatus.COMPLETED
                self.execution_time = time.time() - start
                self.last_run = time.time()

                event_bus.emit(
                    "workflow.node.done",
                    source="Node",
                    node_id=self.id,
                    node_type=self.node_type,
                    status="completed",
                    execution_time=self.execution_time,
                )

                return result

            except Exception as e:
                self.status = NodeStatus.FAILED
                self.error_message = str(e)
                self.execution_time = time.time() - start

                event_bus.emit(
                    "workflow.node.done",
                    source="Node",
                    node_id=self.id,
                    node_type=self.node_type,
                    status="failed",
                    error=str(e),
                )

                raise

        return {}

    def reset(self) -> None:
        """Reset node to idle state."""
        self.status = NodeStatus.IDLE
        self.error_message = ""
        self.execution_time = 0.0
        for port in self.inputs + self.outputs:
            port.value = None

    # ── Validation ─────────────────────────────────────────

    def validate_inputs(self) -> List[str]:
        """Check if all required inputs have values. Returns list of errors."""
        errors = []
        for port in self.inputs:
            if port.required and port.value is None and port.default_value is None:
                if not port.connected:
                    errors.append(f"Missing required input: {port.name}")
        return errors

    # ── Serialization ──────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "node_type": self.node_type,
            "name": self.name,
            "description": self.description,
            "category": self.category.value,
            "icon": self.icon,
            "inputs": [p.to_dict() for p in self.inputs],
            "outputs": [p.to_dict() for p in self.outputs],
            "config": self.config,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Node":
        return cls(
            id=data.get("id", ""),
            node_type=data.get("node_type", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            category=NodeCategory(data.get("category", "processing")),
            icon=data.get("icon", "⚙️"),
            inputs=[Port.from_dict(p) for p in data.get("inputs", [])],
            outputs=[Port.from_dict(p) for p in data.get("outputs", [])],
            config=data.get("config", {}),
            x=data.get("x", 0),
            y=data.get("y", 0),
            width=data.get("width", 24),
            height=data.get("height", 6),
        )


@dataclass
class Edge:
    """
    Directed connection between two ports on different nodes.
    Data flows from source_port → target_port.
    """
    id: str = ""
    source_node_id: str = ""
    source_port_id: str = ""
    target_node_id: str = ""
    target_port_id: str = ""
    active: bool = False  # Highlighted during execution

    def __post_init__(self):
        if not self.id:
            self.id = f"edge_{uuid.uuid4().hex[:8]}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_node_id": self.source_node_id,
            "source_port_id": self.source_port_id,
            "target_node_id": self.target_node_id,
            "target_port_id": self.target_port_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Edge":
        return cls(
            id=data.get("id", ""),
            source_node_id=data.get("source_node_id", ""),
            source_port_id=data.get("source_port_id", ""),
            target_node_id=data.get("target_node_id", ""),
            target_port_id=data.get("target_port_id", ""),
        )


class NodeGraph:
    """
    Container managing a collection of nodes and edges.

    Provides:
    - Add/remove nodes and edges
    - Topological sort for execution ordering
    - Connection validation
    - Data flow propagation
    - Serialization
    """

    def __init__(self, name: str = "Untitled Workflow"):
        self.name = name
        self.id = f"graph_{uuid.uuid4().hex[:8]}"
        self._nodes: Dict[str, Node] = {}
        self._edges: Dict[str, Edge] = {}
        self._selected_node_id: Optional[str] = None

    # ── Node Management ────────────────────────────────────

    @property
    def nodes(self) -> List[Node]:
        return list(self._nodes.values())

    @property
    def edges(self) -> List[Edge]:
        return list(self._edges.values())

    @property
    def selected_node(self) -> Optional[Node]:
        if self._selected_node_id:
            return self._nodes.get(self._selected_node_id)
        return None

    def add_node(self, node: Node) -> None:
        """Add a node to the graph."""
        self._nodes[node.id] = node
        event_bus.emit(
            "graph.node.added",
            source="NodeGraph",
            node_id=node.id,
            node_type=node.node_type,
        )

    def remove_node(self, node_id: str) -> bool:
        """Remove a node and its connected edges."""
        if node_id not in self._nodes:
            return False

        # Remove connected edges
        edges_to_remove = [
            e.id for e in self._edges.values()
            if e.source_node_id == node_id or e.target_node_id == node_id
        ]
        for edge_id in edges_to_remove:
            self.remove_edge(edge_id)

        del self._nodes[node_id]

        if self._selected_node_id == node_id:
            self._selected_node_id = None

        event_bus.emit("graph.node.removed", source="NodeGraph", node_id=node_id)
        return True

    def get_node(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    def select_node(self, node_id: Optional[str]) -> None:
        """Set selected node for UI focus."""
        old = self._selected_node_id
        self._selected_node_id = node_id
        if old != node_id:
            event_bus.emit(
                "graph.node.selected",
                source="NodeGraph",
                node_id=node_id,
                old_node_id=old,
            )

    def select_next(self) -> Optional[str]:
        """Select the next node in order."""
        nodes = self.nodes
        if not nodes:
            return None
        if self._selected_node_id is None:
            self.select_node(nodes[0].id)
            return nodes[0].id

        ids = [n.id for n in nodes]
        try:
            idx = ids.index(self._selected_node_id)
            next_idx = (idx + 1) % len(ids)
        except ValueError:
            next_idx = 0
        self.select_node(ids[next_idx])
        return ids[next_idx]

    def select_prev(self) -> Optional[str]:
        """Select the previous node in order."""
        nodes = self.nodes
        if not nodes:
            return None
        if self._selected_node_id is None:
            self.select_node(nodes[-1].id)
            return nodes[-1].id

        ids = [n.id for n in nodes]
        try:
            idx = ids.index(self._selected_node_id)
            prev_idx = (idx - 1) % len(ids)
        except ValueError:
            prev_idx = 0
        self.select_node(ids[prev_idx])
        return ids[prev_idx]

    # ── Edge Management ────────────────────────────────────

    def add_edge(self, edge: Edge) -> bool:
        """
        Add an edge. Validates port compatibility and prevents cycles.
        Returns True if edge was added successfully.
        """
        src_node = self._nodes.get(edge.source_node_id)
        tgt_node = self._nodes.get(edge.target_node_id)

        if not src_node or not tgt_node:
            return False

        # Cannot connect to self
        if edge.source_node_id == edge.target_node_id:
            return False

        # Find ports
        src_port = None
        for p in src_node.outputs:
            if p.id == edge.source_port_id:
                src_port = p
                break

        tgt_port = None
        for p in tgt_node.inputs:
            if p.id == edge.target_port_id:
                tgt_port = p
                break

        if not src_port or not tgt_port:
            return False

        # Type compatibility
        if not tgt_port.accepts(src_port.port_type):
            return False

        # Cycle detection
        if self._would_create_cycle(edge.source_node_id, edge.target_node_id):
            return False

        # Check for duplicate edge
        for existing in self._edges.values():
            if (existing.source_node_id == edge.source_node_id
                    and existing.target_node_id == edge.target_node_id
                    and existing.source_port_id == edge.source_port_id
                    and existing.target_port_id == edge.target_port_id):
                return False

        # Mark ports as connected
        src_port.connected = True
        tgt_port.connected = True

        self._edges[edge.id] = edge
        event_bus.emit(
            "graph.edge.added",
            source="NodeGraph",
            edge_id=edge.id,
            source_node=edge.source_node_id,
            target_node=edge.target_node_id,
        )
        return True

    def remove_edge(self, edge_id: str) -> bool:
        """Remove an edge."""
        edge = self._edges.get(edge_id)
        if not edge:
            return False

        del self._edges[edge_id]

        # Update port connected status
        self._refresh_port_connections()

        event_bus.emit("graph.edge.removed", source="NodeGraph", edge_id=edge_id)
        return True

    def _refresh_port_connections(self) -> None:
        """Recalculate connected status on all ports."""
        connected_ports: Set[str] = set()
        for edge in self._edges.values():
            connected_ports.add(edge.source_port_id)
            connected_ports.add(edge.target_port_id)

        for node in self._nodes.values():
            for port in node.inputs + node.outputs:
                port.connected = port.id in connected_ports

    def get_incoming_edges(self, node_id: str) -> List[Edge]:
        """Get all edges pointing to a node."""
        return [e for e in self._edges.values() if e.target_node_id == node_id]

    def get_outgoing_edges(self, node_id: str) -> List[Edge]:
        """Get all edges leaving a node."""
        return [e for e in self._edges.values() if e.source_node_id == node_id]

    def get_predecessors(self, node_id: str) -> List[Node]:
        """Get all nodes feeding into a given node."""
        pred_ids = {e.source_node_id for e in self.get_incoming_edges(node_id)}
        return [self._nodes[nid] for nid in pred_ids if nid in self._nodes]

    def get_successors(self, node_id: str) -> List[Node]:
        """Get all nodes receiving data from a given node."""
        succ_ids = {e.target_node_id for e in self.get_outgoing_edges(node_id)}
        return [self._nodes[nid] for nid in succ_ids if nid in self._nodes]

    # ── Cycle Detection ────────────────────────────────────

    def _would_create_cycle(self, source_id: str, target_id: str) -> bool:
        """Check if adding source→target edge would create a cycle."""
        visited: Set[str] = set()
        stack = [target_id]

        while stack:
            current = stack.pop()
            if current == source_id:
                return True
            if current in visited:
                continue
            visited.add(current)

            for edge in self.get_outgoing_edges(current):
                stack.append(edge.target_node_id)

        return False

    # ── Topological Sort ───────────────────────────────────

    def topological_sort(self) -> List[Node]:
        """
        Return nodes in topological order (dependencies first).
        Raises ValueError if graph has cycles.
        """
        in_degree: Dict[str, int] = {nid: 0 for nid in self._nodes}

        for edge in self._edges.values():
            if edge.target_node_id in in_degree:
                in_degree[edge.target_node_id] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        result = []

        while queue:
            nid = queue.pop(0)
            result.append(self._nodes[nid])

            for edge in self.get_outgoing_edges(nid):
                target = edge.target_node_id
                if target in in_degree:
                    in_degree[target] -= 1
                    if in_degree[target] == 0:
                        queue.append(target)

        if len(result) != len(self._nodes):
            raise ValueError("Graph contains cycles — cannot determine execution order")

        return result

    # ── Data Propagation ───────────────────────────────────

    def propagate_data(self, source_node_id: str) -> None:
        """Propagate output data from a node to connected inputs."""
        src_node = self._nodes.get(source_node_id)
        if not src_node:
            return

        for edge in self.get_outgoing_edges(source_node_id):
            tgt_node = self._nodes.get(edge.target_node_id)
            if not tgt_node:
                continue

            # Find source output port
            src_port = None
            for p in src_node.outputs:
                if p.id == edge.source_port_id:
                    src_port = p
                    break

            # Find target input port
            tgt_port = None
            for p in tgt_node.inputs:
                if p.id == edge.target_port_id:
                    tgt_port = p
                    break

            if src_port and tgt_port:
                tgt_port.value = src_port.value

    # ── Reset ──────────────────────────────────────────────

    def reset_all(self) -> None:
        """Reset all nodes to idle state."""
        for node in self._nodes.values():
            node.reset()
        for edge in self._edges.values():
            edge.active = False

    # ── Serialization ──────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges.values()],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], node_factory: Optional[Callable] = None) -> "NodeGraph":
        """
        Deserialize from dict.

        Args:
            data: Serialized graph dict
            node_factory: Optional callable(node_type, node_dict) → Node
                          Used to restore execute functions from registry
        """
        graph = cls(name=data.get("name", "Untitled"))
        graph.id = data.get("id", graph.id)

        for node_data in data.get("nodes", []):
            if node_factory:
                node = node_factory(node_data.get("node_type", ""), node_data)
            else:
                node = Node.from_dict(node_data)
            graph.add_node(node)

        for edge_data in data.get("edges", []):
            edge = Edge.from_dict(edge_data)
            graph.add_edge(edge)

        return graph

    # ── Stats ──────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        """Return graph statistics."""
        status_counts: Dict[str, int] = {}
        for node in self._nodes.values():
            s = node.status.value
            status_counts[s] = status_counts.get(s, 0) + 1

        return {
            "node_count": len(self._nodes),
            "edge_count": len(self._edges),
            "status": status_counts,
            "has_cycles": False,  # If topological sort succeeds, no cycles
        }