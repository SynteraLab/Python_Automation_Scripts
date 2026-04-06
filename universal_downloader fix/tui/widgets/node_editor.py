"""
Node editor canvas widget.
Keyboard-driven node graph editor inside terminal.
"""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
from textual.containers import Vertical, ScrollableContainer
from textual.reactive import reactive
from textual.message import Message
from textual import events
from typing import Optional

from ..nodes.base import NodeGraph, Node, Edge
from ..nodes.registry import node_registry
from ..nodes.renderer import NodeRenderer, GraphRenderer


class NodeEditorCanvas(Widget):
    """
    Visual node graph editor canvas.

    Renders nodes as Rich panels in a grid.
    Keyboard-driven: Tab/Shift+Tab to navigate, Enter to edit,
    N to add, Delete to remove, C to connect.
    """

    DEFAULT_CSS = """
    NodeEditorCanvas {
        height: 100%;
        padding: 1;
    }
    NodeEditorCanvas #editor-graph {
        height: auto;
        min-height: 20;
    }
    NodeEditorCanvas #editor-info {
        dock: bottom;
        height: 3;
        background: $surface;
        border-top: solid $border;
        padding: 0 1;
    }
    """

    class NodeAction(Message):
        """Emitted when user wants to act on a node."""
        def __init__(self, action: str, node_id: Optional[str] = None) -> None:
            self.action = action  # add, delete, connect, edit, run
            self.node_id = node_id
            super().__init__()

    connect_mode: reactive[bool] = reactive(False)
    _connect_source: Optional[str] = None

    def __init__(self, graph: Optional[NodeGraph] = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self.graph = graph or NodeGraph("New Workflow")

    def compose(self) -> ComposeResult:
        with ScrollableContainer(id="editor-graph"):
            yield Static(id="graph-content")
        yield Static(id="editor-info")

    def on_mount(self) -> None:
        self.refresh_graph()

    def refresh_graph(self) -> None:
        """Re-render the graph display."""
        try:
            content = self.query_one("#graph-content", Static)
            rendered = GraphRenderer.render_graph(self.graph, compact=False)
            content.update(rendered)
        except Exception:
            pass

        self._update_info()

    def _update_info(self) -> None:
        """Update the bottom info bar."""
        try:
            info = self.query_one("#editor-info", Static)
            node = self.graph.selected_node

            if self.connect_mode:
                info.update(
                    "[yellow]CONNECT MODE[/yellow] — "
                    "Select target node, Enter to connect, Esc to cancel"
                )
            elif node:
                info.update(
                    f"[cyan]{node.icon} {node.name}[/cyan] "
                    f"[dim]({node.node_type}) "
                    f"In:{len(node.inputs)} Out:{len(node.outputs)} "
                    f"Status:{node.status.value}[/dim]"
                )
            else:
                stats = self.graph.stats()
                info.update(
                    f"[dim]Nodes: {stats['node_count']} | "
                    f"Edges: {stats['edge_count']} | "
                    f"N=Add Tab=Navigate C=Connect Del=Remove R=Run[/dim]"
                )
        except Exception:
            pass

    def on_key(self, event: events.Key) -> None:
        """Handle keyboard input for node editing."""
        key = event.key

        if key == "tab":
            self.graph.select_next()
            self.refresh_graph()
            event.stop()

        elif key == "shift+tab":
            self.graph.select_prev()
            self.refresh_graph()
            event.stop()

        elif key == "n":
            self.post_message(self.NodeAction("add"))
            event.stop()

        elif key == "delete":
            selected = self.graph.selected_node
            if selected:
                self.post_message(self.NodeAction("delete", selected.id))
            event.stop()

        elif key == "c":
            if not self.connect_mode:
                selected = self.graph.selected_node
                if selected:
                    self.connect_mode = True
                    self._connect_source = selected.id
                    self.refresh_graph()
            event.stop()

        elif key == "escape":
            if self.connect_mode:
                self.connect_mode = False
                self._connect_source = None
                self.refresh_graph()
                event.stop()

        elif key == "enter":
            if self.connect_mode and self._connect_source:
                target = self.graph.selected_node
                if target and target.id != self._connect_source:
                    self._auto_connect(self._connect_source, target.id)
                self.connect_mode = False
                self._connect_source = None
                self.refresh_graph()
                event.stop()
            elif self.graph.selected_node:
                self.post_message(self.NodeAction("edit", self.graph.selected_node.id))
                event.stop()

        elif key == "x":
            selected = self.graph.selected_node
            if selected:
                self._disconnect_node(selected.id)
                self.refresh_graph()
            event.stop()

        elif key == "r":
            self.post_message(self.NodeAction("run"))
            event.stop()

        elif key == "s":
            self.post_message(self.NodeAction("save"))
            event.stop()

        elif key == "l":
            self.post_message(self.NodeAction("load"))
            event.stop()

    def _auto_connect(self, source_id: str, target_id: str) -> bool:
        """Auto-connect first compatible output→input between two nodes."""
        src = self.graph.get_node(source_id)
        tgt = self.graph.get_node(target_id)
        if not src or not tgt:
            return False

        for out_port in src.outputs:
            for in_port in tgt.inputs:
                if in_port.accepts(out_port.port_type):
                    edge = Edge(
                        source_node_id=source_id,
                        source_port_id=out_port.id,
                        target_node_id=target_id,
                        target_port_id=in_port.id,
                    )
                    if self.graph.add_edge(edge):
                        return True
        return False

    def _disconnect_node(self, node_id: str) -> None:
        """Remove all edges connected to a node."""
        edges_to_remove = [
            e.id for e in self.graph.edges
            if e.source_node_id == node_id or e.target_node_id == node_id
        ]
        for eid in edges_to_remove:
            self.graph.remove_edge(eid)

    def add_node_by_type(self, type_name: str) -> Optional[Node]:
        """Add a new node of given type to the graph."""
        node = node_registry.create(type_name)
        if node:
            # Position after last node
            existing = self.graph.nodes
            if existing:
                max_x = max(n.x for n in existing)
                max_y = max(n.y for n in existing)
                node.x = max_x + 1 if len(existing) % 3 != 0 else 0
                node.y = max_y if len(existing) % 3 != 0 else max_y + 1
            self.graph.add_node(node)
            self.graph.select_node(node.id)
            self.refresh_graph()
        return node

    def remove_selected_node(self) -> bool:
        """Remove the currently selected node."""
        selected = self.graph.selected_node
        if selected:
            self.graph.remove_node(selected.id)
            self.refresh_graph()
            return True
        return False

    def set_graph(self, graph: NodeGraph) -> None:
        """Replace the graph and refresh."""
        self.graph = graph
        self.refresh_graph()