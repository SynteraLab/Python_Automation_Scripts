"""
Terminal-based node graph renderer.

Renders nodes and edges as Rich renderables for display
inside Textual widgets. Supports:
- ASCII box drawing for nodes
- Line drawing for edges
- Status indicators
- Selection highlighting
- Port labels
"""

from typing import Dict, List, Optional, Tuple

from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich import box

from .base import Node, Edge, NodeGraph, NodeStatus, NodeCategory


# ── Status Icons ───────────────────────────────────────────

STATUS_ICONS: Dict[NodeStatus, Tuple[str, str]] = {
    NodeStatus.IDLE: ("○", "dim"),
    NodeStatus.PENDING: ("◷", "yellow"),
    NodeStatus.RUNNING: ("◉", "bright_cyan"),
    NodeStatus.COMPLETED: ("✓", "green"),
    NodeStatus.FAILED: ("✗", "red"),
    NodeStatus.SKIPPED: ("⊘", "dim"),
    NodeStatus.CANCELLED: ("⊗", "yellow"),
}

CATEGORY_COLORS: Dict[NodeCategory, str] = {
    NodeCategory.INPUT: "bright_green",
    NodeCategory.PROCESSING: "bright_cyan",
    NodeCategory.OUTPUT: "bright_magenta",
    NodeCategory.CONDITIONAL: "bright_yellow",
    NodeCategory.UTILITY: "grey70",
    NodeCategory.DOWNLOAD: "bright_blue",
    NodeCategory.TRANSFORM: "bright_red",
}


class NodeRenderer:
    """Renders individual nodes as Rich Text objects."""

    @staticmethod
    def render_node(node: Node, selected: bool = False, compact: bool = False) -> Panel:
        """Render a single node as a Rich Panel."""
        status_icon, status_style = STATUS_ICONS.get(
            node.status, ("?", "dim")
        )
        cat_color = CATEGORY_COLORS.get(node.category, "white")

        # Title line
        title_text = Text()
        title_text.append(f"{node.icon} ", style="bold")
        title_text.append(node.name, style=f"bold {cat_color}")
        title_text.append(f" [{status_icon}]", style=status_style)

        if compact:
            border = "double" if selected else "round"
            border_color = "bright_cyan" if selected else "dim"
            return Panel(
                title_text,
                border_style=border_color,
                box=getattr(box, "DOUBLE" if selected else "ROUNDED"),
                padding=(0, 1),
                width=node.width,
            )

        # Build content
        content = Text()

        # Type label
        content.append(f"[{node.node_type}]", style="dim")
        content.append("\n")

        # Input ports
        if node.inputs:
            for port in node.inputs:
                connector = "●" if port.connected else "○"
                port_style = "green" if port.connected else "dim"
                content.append(f" {connector} ", style=port_style)
                content.append(f"{port.name}", style="bold")
                if port.value is not None:
                    val_str = str(port.value)[:20]
                    content.append(f" = {val_str}", style="dim")
                content.append("\n")

        # Separator
        if node.inputs and node.outputs:
            content.append("─" * (node.width - 4), style="dim")
            content.append("\n")

        # Output ports
        if node.outputs:
            for port in node.outputs:
                connector = "●" if port.connected else "○"
                port_style = "blue" if port.connected else "dim"
                content.append(f" {connector} ", style=port_style)
                content.append(f"{port.name}", style="bold")
                if port.value is not None:
                    val_str = str(port.value)[:20]
                    content.append(f" = {val_str}", style="dim")
                content.append("\n")

        # Error message
        if node.status == NodeStatus.FAILED and node.error_message:
            content.append(f"⚠ {node.error_message[:40]}", style="red")
            content.append("\n")

        # Execution time
        if node.execution_time > 0:
            content.append(f"⏱ {node.execution_time:.2f}s", style="dim")

        border_style = "bright_cyan" if selected else CATEGORY_COLORS.get(node.category, "dim")
        box_type = box.DOUBLE if selected else box.ROUNDED

        return Panel(
            content,
            title=f" {title_text} ",
            border_style=border_style,
            box=box_type,
            padding=(0, 1),
            width=node.width,
        )


class GraphRenderer:
    """Renders a complete node graph as a Rich table/layout."""

    @staticmethod
    def render_graph(graph: NodeGraph, compact: bool = False) -> Table:
        """Render the entire node graph as a table of node panels."""
        nodes = graph.nodes
        if not nodes:
            return Panel(
                "[dim]No nodes in graph. Press [bold]N[/bold] to add a node.[/dim]",
                border_style="dim",
                box=box.ROUNDED,
            )

        # Sort nodes by position
        sorted_nodes = sorted(nodes, key=lambda n: (n.y, n.x))

        # Group into rows by y coordinate
        rows: Dict[int, List[Node]] = {}
        for node in sorted_nodes:
            row_key = node.y
            if row_key not in rows:
                rows[row_key] = []
            rows[row_key].append(node)

        selected_id = graph._selected_node_id
        renderer = NodeRenderer()

        # Build table
        table = Table(
            show_header=False,
            box=None,
            padding=(0, 1),
            expand=True,
        )

        # Determine max columns
        max_cols = max(len(row) for row in rows.values()) if rows else 1
        for _ in range(max_cols):
            table.add_column(width=28, no_wrap=True)

        for row_y in sorted(rows.keys()):
            row_nodes = rows[row_y]
            rendered = []
            for node in row_nodes:
                is_selected = node.id == selected_id
                panel = renderer.render_node(node, selected=is_selected, compact=compact)
                rendered.append(panel)

            # Pad row to max columns
            while len(rendered) < max_cols:
                rendered.append("")

            table.add_row(*rendered)

        return table

    @staticmethod
    def render_edges_summary(graph: NodeGraph) -> Text:
        """Render edge connections as text summary."""
        edges = graph.edges
        if not edges:
            return Text("No connections", style="dim")

        text = Text()
        for edge in edges:
            src = graph.get_node(edge.source_node_id)
            tgt = graph.get_node(edge.target_node_id)

            src_name = src.name if src else "?"
            tgt_name = tgt.name if tgt else "?"

            style = "bright_green" if edge.active else "dim"
            arrow = "━━▶" if edge.active else "──→"

            text.append(f"  {src_name} ", style="cyan")
            text.append(arrow, style=style)
            text.append(f" {tgt_name}", style="cyan")
            text.append("\n")

        return text

    @staticmethod
    def render_graph_stats(graph: NodeGraph) -> Panel:
        """Render graph statistics panel."""
        stats = graph.stats()

        content = Text()
        content.append(f"Nodes: {stats['node_count']}  ", style="bold")
        content.append(f"Edges: {stats['edge_count']}  ", style="bold")

        for status, count in stats.get("status", {}).items():
            icon, style = STATUS_ICONS.get(NodeStatus(status), ("?", "dim"))
            content.append(f" {icon}{count}", style=style)

        return Panel(
            content,
            border_style="dim",
            box=box.SQUARE,
            padding=(0, 1),
        )

    @staticmethod
    def render_node_palette(registry) -> Table:
        """Render available node types for the 'add node' picker."""
        types = registry.list_types()
        if not types:
            return Text("No node types registered", style="dim")

        table = Table(
            title="Available Nodes",
            show_header=True,
            header_style="bold",
            border_style="bright_cyan",
            box=box.ROUNDED,
            padding=(0, 1),
        )
        table.add_column("#", width=3, justify="right", style="dim")
        table.add_column("Icon", width=4, justify="center")
        table.add_column("Name", width=20, style="bold")
        table.add_column("Category", width=12)
        table.add_column("Description", overflow="fold")

        # Group by category
        by_cat: Dict[str, List] = {}
        for info in types:
            cat = info.category.value
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(info)

        idx = 1
        for cat_name in sorted(by_cat.keys()):
            for info in by_cat[cat_name]:
                cat_color = CATEGORY_COLORS.get(info.category, "white")
                table.add_row(
                    str(idx),
                    info.icon,
                    info.display_name,
                    f"[{cat_color}]{cat_name}[/{cat_color}]",
                    info.description,
                )
                idx += 1

        return table