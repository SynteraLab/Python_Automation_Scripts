"""Focus mode — minimal distraction-free download interface."""

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Input, Button, RichLog
from textual.containers import Vertical, Center
from textual import work


class FocusModeView(Widget):
    """Focus mode: minimal single-task interface."""

    DEFAULT_CSS = """
    FocusModeView { height: 100%; align: center middle; }
    FocusModeView #focus-container {
        width: 70%; max-width: 80; height: auto;
        padding: 2; margin: 2;
        border: double $primary;
    }
    FocusModeView #focus-title {
        text-align: center; text-style: bold; color: $primary;
        height: 3;
    }
    FocusModeView Input { margin: 1 0; }
    FocusModeView #focus-log { height: auto; max-height: 20; margin: 1 0; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="focus-container"):
            yield Static("🎯 Focus Download", id="focus-title")
            yield Input(placeholder="Paste URL and press Enter...", id="focus-url")
            yield RichLog(id="focus-log", highlight=True, markup=True, wrap=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "focus-url":
            url = event.value.strip()
            if url:
                self._download(url)

    @work(thread=True)
    def _download(self, url: str) -> None:
        from ..controllers.downloader import smart_download_headless

        self.app.call_from_thread(self._log, f"[cyan]🔗 {url}[/cyan]")

        result = smart_download_headless(
            url=url,
            config=self.app.config,
            quality="best",
            progress_callback=lambda msg: self.app.call_from_thread(self._log, f"  {msg}"),
        )

        if result.get("success"):
            self.app.call_from_thread(
                self._log, f"\n[green]✓ {result.get('filepath', '')}[/green]"
            )
            self.app.call_from_thread(
                self.app.notify, "Download complete ✓", title="Focus"
            )
        else:
            self.app.call_from_thread(
                self._log, f"\n[red]✗ {result.get('error', 'Failed')}[/red]"
            )

    def _log(self, msg: str) -> None:
        try:
            self.query_one("#focus-log", RichLog).write(msg)
        except Exception:
            pass