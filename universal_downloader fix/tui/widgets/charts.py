"""
Dashboard chart widgets for Textual.
Wraps Rich renderables in Textual widgets.
"""

import time
from collections import Counter
from typing import Any, Dict, List, Optional

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static
from textual.containers import Vertical, Horizontal
from textual.reactive import reactive

from rich.text import Text
from rich.table import Table
from rich.panel import Panel
from rich import box

from core.history import DownloadHistory


# ── Unicode Constants ──────────────────────────────────────

SPARK_BLOCKS = " ▁▂▃▄▅▆▇█"
BAR_FULL = "█"
BAR_HALF = "▌"
BAR_EMPTY = "░"


class StatsOverview(Widget):
    """Compact download statistics overview."""

    DEFAULT_CSS = """
    StatsOverview {
        height: auto;
        margin: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="stats-content")

    def on_mount(self) -> None:
        self.refresh_stats()

    def refresh_stats(self) -> None:
        history = DownloadHistory()
        stats = history.get_stats()

        total = stats.get("total", 0)
        successful = stats.get("successful", 0)
        failed = stats.get("failed", 0)
        size_gb = stats.get("total_gb", 0)

        # Build display
        content = Text()
        content.append("📊 Download Statistics\n\n", style="bold")
        content.append(f"  Total:    ", style="dim")
        content.append(f"{total}\n", style="bold white")
        content.append(f"  Success:  ", style="dim")
        content.append(f"{successful}\n", style="bold green")
        content.append(f"  Failed:   ", style="dim")
        content.append(f"{failed}\n", style="bold red")
        content.append(f"  Size:     ", style="dim")
        content.append(f"{size_gb}GB\n", style="bold cyan")

        # Ratio bar
        if total > 0:
            ratio = successful / total
            width = 30
            filled = int(ratio * width)
            empty = width - filled
            content.append(f"\n  ")
            content.append(BAR_FULL * filled, style="bold green")
            content.append(BAR_FULL * empty, style="bold red")
            content.append(f"  {ratio * 100:.0f}%\n", style="bold")

        panel = Panel(
            content,
            border_style="cyan",
            box=box.ROUNDED,
            padding=(0, 1),
        )

        try:
            self.query_one("#stats-content", Static).update(panel)
        except Exception:
            pass


class ExtractorChart(Widget):
    """Bar chart of downloads by extractor."""

    DEFAULT_CSS = """
    ExtractorChart {
        height: auto;
        margin: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="extractor-content")

    def on_mount(self) -> None:
        self.refresh_chart()

    def refresh_chart(self) -> None:
        history = DownloadHistory()
        records = history.get_history(limit=200)

        counts: Counter = Counter()
        for rec in records:
            ext = rec.get("extractor", "unknown") or "unknown"
            counts[ext] += 1

        top = counts.most_common(8)
        if not top:
            try:
                self.query_one("#extractor-content", Static).update("[dim]No data[/dim]")
            except Exception:
                pass
            return

        max_val = max(c for _, c in top)
        colors = ["cyan", "green", "yellow", "magenta", "blue", "red", "bright_cyan", "bright_green"]

        content = Text()
        content.append("🔧 By Extractor\n\n", style="bold")

        max_label = max(len(name) for name, _ in top)
        bar_width = 24

        for idx, (name, count) in enumerate(top):
            color = colors[idx % len(colors)]
            ratio = count / max_val if max_val > 0 else 0
            filled = int(ratio * bar_width)

            content.append(f"  {name:<{max_label + 1}} ", style="bold")
            content.append(BAR_FULL * filled, style=f"bold {color}")
            content.append(BAR_EMPTY * (bar_width - filled), style="dim")
            content.append(f" {count}\n", style="dim")

        panel = Panel(
            content,
            border_style="blue",
            box=box.ROUNDED,
            padding=(0, 1),
        )

        try:
            self.query_one("#extractor-content", Static).update(panel)
        except Exception:
            pass


class ActivitySparkline(Widget):
    """Activity sparkline over last 14 days."""

    DEFAULT_CSS = """
    ActivitySparkline {
        height: auto;
        margin: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="activity-content")

    def on_mount(self) -> None:
        self.refresh_chart()

    def refresh_chart(self) -> None:
        history = DownloadHistory()
        records = history.get_history(limit=500)

        day_counts: Counter = Counter()
        for rec in records:
            raw = rec.get("created_at", "") or ""
            date_key = raw[:10] if len(raw) >= 10 else ""
            if date_key:
                day_counts[date_key] += 1

        from datetime import datetime, timedelta

        today = datetime.now()
        values = []
        labels = []
        for i in range(13, -1, -1):
            day = today - timedelta(days=i)
            key = day.strftime("%Y-%m-%d")
            values.append(day_counts.get(key, 0))
            labels.append(day.strftime("%d"))

        content = Text()
        content.append("📈 Activity (14 days)\n\n  ", style="bold")

        if values:
            max_v = max(values) if max(values) > 0 else 1
            for v in values:
                idx = int((v / max_v) * 8) if max_v > 0 else 0
                content.append(SPARK_BLOCKS[min(idx, 8)], style="bright_green")

            content.append(f"  min={min(values)} max={max(values)}", style="dim")
            content.append("\n  ", style="dim")
            for lbl in labels:
                content.append(f"{lbl} ", style="dim")

        panel = Panel(
            content,
            border_style="yellow",
            box=box.ROUNDED,
            padding=(0, 1),
        )

        try:
            self.query_one("#activity-content", Static).update(panel)
        except Exception:
            pass


class RecentDownloads(Widget):
    """Compact recent downloads table."""

    DEFAULT_CSS = """
    RecentDownloads {
        height: auto;
        margin: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static(id="recent-content")

    def on_mount(self) -> None:
        self.refresh_table()

    def refresh_table(self) -> None:
        history = DownloadHistory()
        records = history.get_history(limit=10)

        table = Table(
            show_header=True,
            header_style="bold",
            box=box.SIMPLE_HEAVY,
            expand=True,
            title="📋 Recent Downloads",
            title_style="bold",
        )
        table.add_column("#", width=3, justify="right", style="dim")
        table.add_column("Title", max_width=30, overflow="ellipsis")
        table.add_column("Ext", width=10, style="cyan")
        table.add_column("Status", width=6, justify="center")
        table.add_column("Date", width=10, style="dim")

        for idx, rec in enumerate(records, 1):
            status = rec.get("status", "")
            status_fmt = {"completed": "[green]✓[/green]", "failed": "[red]✗[/red]"}.get(status, "?")
            title = (rec.get("title", "") or "")[:28]
            date = (rec.get("created_at", "") or "")[:10]
            table.add_row(str(idx), title, rec.get("extractor", ""), status_fmt, date)

        try:
            self.query_one("#recent-content", Static).update(table)
        except Exception:
            pass