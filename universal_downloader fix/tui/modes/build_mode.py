"""Build mode — node editor and workflow design view."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Button
from textual.containers import Vertical, Horizontal

from ..widgets.node_editor import NodeEditorCanvas
from ..nodes.base import NodeGraph
from ..nodes.renderer import GraphRenderer


class BuildModeView(Widget):
    """Build mode workspace: node editor + toolbar."""

    DEFAULT_CSS = """
    BuildModeView { height: 100%; }
    BuildModeView #build-toolbar {
        dock: top; height: 3; background: $surface;
        border-bottom: solid $border; padding: 0 1;
    }
    BuildModeView #build-toolbar Button {
        margin: 0 1; height: 3;
    }
    """

    def __init__(self, graph: NodeGraph = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._graph = graph or NodeGraph("New Workflow")

    def compose(self) -> ComposeResult:
        with Horizontal(id="build-toolbar"):
            yield Button("➕ Add Node", id="btn-add-node")
            yield Button("🔗 Connect", id="btn-connect")
            yield Button("💾 Save", id="btn-save")
            yield Button("📂 Load", id="btn-load")
            yield Button("▶️ Run", id="btn-run-wf")
            yield Button("🗑️ Clear", id="btn-clear-graph")
        yield NodeEditorCanvas(graph=self._graph, id="node-canvas")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        canvas = self.query_one("#node-canvas", NodeEditorCanvas)

        if event.button.id == "btn-add-node":
            canvas.post_message(NodeEditorCanvas.NodeAction("add"))
        elif event.button.id == "btn-connect":
            selected = canvas.graph.selected_node
            if selected:
                canvas.connect_mode = True
                canvas._connect_source = selected.id
                canvas.refresh_graph()
        elif event.button.id == "btn-save":
            canvas.post_message(NodeEditorCanvas.NodeAction("save"))
        elif event.button.id == "btn-load":
            canvas.post_message(NodeEditorCanvas.NodeAction("load"))
        elif event.button.id == "btn-run-wf":
            canvas.post_message(NodeEditorCanvas.NodeAction("run"))
        elif event.button.id == "btn-clear-graph":
            self._graph = NodeGraph("New Workflow")
            canvas.set_graph(self._graph)

    @property
    def graph(self) -> NodeGraph:
        try:
            return self.query_one("#node-canvas", NodeEditorCanvas).graph
        except Exception:
            return self._graph

    def set_graph(self, graph: NodeGraph) -> None:
        self._graph = graph
        try:
            self.query_one("#node-canvas", NodeEditorCanvas).set_graph(graph)
        except Exception:
            pass