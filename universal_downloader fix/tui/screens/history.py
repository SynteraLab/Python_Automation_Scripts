"""
History and dashboard screen.
"""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, DataTable, Button
from textual.containers import Vertical, Horizontal, ScrollableContainer

from ..widgets.charts import StatsOverview, ExtractorChart, ActivitySparkline, RecentDownloads
from core.history import DownloadHistory


class HistoryView(Widget):
    """Download history with stats."""

    DEFAULT_CSS = """
    HistoryView { height: 100%; padding: 1; }
    HistoryView #hist-header { text-style: bold; color: $primary; height: 2; }
    HistoryView DataTable { height: auto; max-height: 20; margin: 1 0; }
    """

    def compose(self) -> ComposeResult:
        yield Static("📊 Download History", id="hist-header")
        with Horizontal():
            yield Button("Refresh", variant="primary", id="btn-refresh-hist")
            yield Button("Dashboard", id="btn-show-dash")
        yield DataTable(id="hist-table")

    def on_mount(self) -> None:
        self._load_history()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-refresh-hist":
            self._load_history()
        elif event.button.id == "btn-show-dash":
            self.app.handle_navigation("dashboard")

    def _load_history(self) -> None:
        history = DownloadHistory()
        records = history.get_history(limit=20)
        stats = history.get_stats()

        dt = self.query_one("#hist-table", DataTable)
        dt.clear(columns=True)

        dt.add_column("#", key="no", width=4)
        dt.add_column("Title", key="title", width=40)
        dt.add_column("Extractor", key="ext", width=12)
        dt.add_column("Status", key="status", width=10)
        dt.add_column("Date", key="date", width=16)

        for idx, rec in enumerate(records, 1):
            status = rec.get("status", "")
            status_fmt = {"completed": "✓ Done", "failed": "✗ Failed"}.get(status, status)
            title = (rec.get("title", "") or "")[:38]
            date = (rec.get("created_at", "") or "")[:16]
            dt.add_row(str(idx), title, rec.get("extractor", ""), status_fmt, date)


class DashboardView(Widget):
    """Full statistics dashboard."""

    DEFAULT_CSS = """
    DashboardView { height: 100%; padding: 0; }
    DashboardView #dash-header {
        text-style: bold; color: $primary; height: 2; padding: 0 1;
    }
    DashboardView #dash-actions { height: 3; margin: 0 1; }
    DashboardView #dash-scroll { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        yield Static("📈 Dashboard", id="dash-header")
        with Horizontal(id="dash-actions"):
            yield Button("Refresh", variant="primary", id="btn-refresh-dash")
        with ScrollableContainer(id="dash-scroll"):
            yield StatsOverview(id="dash-stats")
            yield ExtractorChart(id="dash-extractors")
            yield ActivitySparkline(id="dash-activity")
            yield RecentDownloads(id="dash-recent")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-refresh-dash":
            self._refresh_all()

    def _refresh_all(self) -> None:
        try:
            self.query_one("#dash-stats", StatsOverview).refresh_stats()
            self.query_one("#dash-extractors", ExtractorChart).refresh_chart()
            self.query_one("#dash-activity", ActivitySparkline).refresh_chart()
            self.query_one("#dash-recent", RecentDownloads).refresh_table()
        except Exception:
            pass