"""Run mode — workflow execution with live progress."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Button, RichLog, ProgressBar, DataTable
from textual.containers import Vertical, Horizontal
from textual.reactive import reactive
from textual import work

from rich.text import Text

from ..nodes.base import NodeGraph, NodeStatus
from ..engine.events import event_bus


class RunModeView(Widget):
    """Run mode workspace: execution progress + controls."""

    DEFAULT_CSS = """
    RunModeView { height: 100%; padding: 1; }
    RunModeView #run-header {
        text-style: bold; color: $primary; height: 2;
    }
    RunModeView #run-controls {
        height: 3; margin: 1 0;
    }
    RunModeView #run-progress-bar {
        margin: 1 0; height: 3;
    }
    RunModeView DataTable {
        height: auto; max-height: 14; margin: 1 0;
    }
    RunModeView #run-log {
        height: 1fr; min-height: 8;
    }
    """

    execution_status: reactive[str] = reactive("Idle")
    progress_value: reactive[float] = reactive(0.0)

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._graph = None
        self._executor = None

    def compose(self) -> ComposeResult:
        yield Static("▶️ Workflow Execution", id="run-header")
        with Horizontal(id="run-controls"):
            yield Button("▶ Run", variant="primary", id="btn-run")
            yield Button("⏸ Pause", id="btn-pause")
            yield Button("⏹ Cancel", variant="error", id="btn-cancel")
            yield Button("⏭ Step", id="btn-step")
        yield Static(id="run-status")
        yield ProgressBar(total=100, id="run-progress-bar")
        yield DataTable(id="run-nodes")
        yield RichLog(id="run-log", highlight=True, markup=True, wrap=True)

    def on_mount(self) -> None:
        dt = self.query_one("#run-nodes", DataTable)
        dt.add_column("Node", key="name", width=20)
        dt.add_column("Type", key="type", width=15)
        dt.add_column("Status", key="status", width=12)
        dt.add_column("Time", key="time", width=8)

        event_bus.on("workflow.node.executing", self._on_node_executing)
        event_bus.on("workflow.node.done", self._on_node_done)

    def set_graph(self, graph: NodeGraph) -> None:
        """Set the graph to execute."""
        self._graph = graph
        self._populate_nodes()

    def _populate_nodes(self) -> None:
        if not self._graph:
            return
        try:
            dt = self.query_one("#run-nodes", DataTable)
            dt.clear()
            for node in self._graph.nodes:
                dt.add_row(
                    node.name, node.node_type,
                    node.status.value, "",
                    key=node.id,
                )
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-run":
            self._run_workflow()
        elif event.button.id == "btn-pause":
            if self._executor:
                from ..workflow import ExecutionStatus
                if self._executor.status == ExecutionStatus.PAUSED:
                    self._executor.resume()
                    self._log("[cyan]Resumed[/cyan]")
                else:
                    self._executor.pause()
                    self._log("[yellow]Paused[/yellow]")
        elif event.button.id == "btn-cancel":
            if self._executor:
                self._executor.cancel()
                self._log("[red]Cancelled[/red]")
        elif event.button.id == "btn-step":
            self._step_workflow()

    @work(thread=True)
    def _run_workflow(self) -> None:
        if not self._graph:
            self.app.call_from_thread(self._log, "[yellow]No workflow loaded[/yellow]")
            return

        import asyncio
        from ..workflow import WorkflowExecutor, ExecutionMode

        self.app.call_from_thread(self._log, "[cyan]Starting workflow...[/cyan]")

        self._executor = WorkflowExecutor(
            on_node_start=lambda n: self.app.call_from_thread(
                self._log, f"[dim]Running: {n.name}[/dim]"
            ),
            on_node_complete=lambda n, r: self.app.call_from_thread(
                self._log, f"[green]✓ {n.name} ({r.execution_time:.2f}s)[/green]"
            ),
            on_node_error=lambda n, e: self.app.call_from_thread(
                self._log, f"[red]✗ {n.name}: {e}[/red]"
            ),
        )

        context = {"config": self.app.config}
        result = asyncio.run(
            self._executor.execute(self._graph, context=context, mode=ExecutionMode.SEQUENTIAL)
        )

        self.app.call_from_thread(self._log, f"\n[bold]{result.summary()}[/bold]")
        self.app.call_from_thread(self._populate_nodes)

    @work(thread=True)
    def _step_workflow(self) -> None:
        if not self._graph:
            return

        import asyncio
        from ..workflow import WorkflowExecutor

        if not self._executor:
            self._executor = WorkflowExecutor()
            context = {"config": self.app.config}
            self._executor.prepare(self._graph, context)

        node_result = asyncio.run(self._executor.step())
        if node_result:
            status = "✓" if node_result.status == NodeStatus.COMPLETED else "✗"
            self.app.call_from_thread(
                self._log,
                f"[{'green' if status == '✓' else 'red'}]{status} {node_result.node_name}[/]"
            )
        else:
            self.app.call_from_thread(self._log, "[dim]No more nodes[/dim]")

        self.app.call_from_thread(self._populate_nodes)

    def _on_node_executing(self, event) -> None:
        try:
            dt = self.query_one("#run-nodes", DataTable)
            node_id = event.get("node_id", "")
            dt.update_cell(node_id, "status", "⏳ Running")
        except Exception:
            pass

    def _on_node_done(self, event) -> None:
        try:
            dt = self.query_one("#run-nodes", DataTable)
            node_id = event.get("node_id", "")
            status = event.get("status", "")
            exec_time = event.get("execution_time", 0)
            icon = "✓" if status == "completed" else "✗"
            dt.update_cell(node_id, "status", f"{icon} {status}")
            dt.update_cell(node_id, "time", f"{exec_time:.2f}s")
        except Exception:
            pass

    def _log(self, msg: str) -> None:
        try:
            self.query_one("#run-log", RichLog).write(msg)
        except Exception:
            pass