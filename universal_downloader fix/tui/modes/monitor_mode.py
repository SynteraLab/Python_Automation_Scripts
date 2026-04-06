"""Monitor mode — dashboard + logs view."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Button
from textual.containers import ScrollableContainer, Horizontal

from ..widgets.charts import StatsOverview, ExtractorChart, ActivitySparkline, RecentDownloads


class MonitorModeView(Widget):
    """Monitor mode: live dashboard."""

    DEFAULT_CSS = """
    MonitorModeView { height: 100%; }
    MonitorModeView #monitor-toolbar {
        dock: top; height: 3; background: $surface;
        border-bottom: solid $border; padding: 0 1;
    }
    MonitorModeView #monitor-scroll { height: 1fr; }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="monitor-toolbar"):
            yield Button("🔄 Refresh", variant="primary", id="btn-refresh-mon")
        with ScrollableContainer(id="monitor-scroll"):
            yield StatsOverview(id="mon-stats")
            yield ExtractorChart(id="mon-ext")
            yield ActivitySparkline(id="mon-activity")
            yield RecentDownloads(id="mon-recent")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-refresh-mon":
            self.refresh_all()

    def refresh_all(self) -> None:
        try:
            self.query_one("#mon-stats", StatsOverview).refresh_stats()
            self.query_one("#mon-ext", ExtractorChart).refresh_chart()
            self.query_one("#mon-activity", ActivitySparkline).refresh_chart()
            self.query_one("#mon-recent", RecentDownloads).refresh_table()
        except Exception:
            pass