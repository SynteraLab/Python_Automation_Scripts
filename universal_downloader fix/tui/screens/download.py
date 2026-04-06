"""
Download screen — handles single URL download with format selection.
"""

import re
import time

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Input, Button, RichLog
from textual.containers import Vertical, Horizontal
from textual.message import Message
from textual import work
from textual.timer import Timer
from typing import Any, List, Optional

from ..widgets.format_table import FormatTable
from ..engine.events import event_bus
from ..logging_ import log_manager


_PROGRESS_LOG_RE = re.compile(r"^(?:Progress:|HLS progress:|yt-dlp progress:|[A-Z-]+ progress:)")


def _is_restricted_format(fmt: Any) -> bool:
    label = f"{getattr(fmt, 'label', '')} {getattr(fmt, 'format_note', '')}".lower()
    url = str(getattr(fmt, 'url', '') or '').lower()
    return (
        'preview' in label
        or 'trailer' in label
        or 'litevideo/freepv' in url
        or ('dmm.co.jp' in url and 'freepv' in url)
    )


class DownloadView(Widget):
    """
    Download workflow view.
    URL input → extract → format selection → download.
    """

    DEFAULT_CSS = """
    DownloadView {
        height: 100%;
        padding: 1;
    }
    DownloadView #dl-header {
        text-style: bold;
        color: $primary;
        height: 2;
        padding: 0 1;
    }
    DownloadView #dl-url-input {
        margin: 1 0;
    }
    DownloadView #dl-status {
        margin: 1 0;
        height: auto;
        min-height: 3;
        max-height: 12;
        padding: 0 1;
    }
    DownloadView #dl-actions {
        margin: 1 0;
        height: 3;
    }
    DownloadView #dl-progress {
        margin: 0 1;
        height: 1;
        color: $text-primary;
    }
    """

    class DownloadStarted(Message):
        def __init__(self, url: str) -> None:
            self.url = url
            super().__init__()

    class DownloadFinished(Message):
        def __init__(self, success: bool, filepath: str = "") -> None:
            self.success = success
            self.filepath = filepath
            super().__init__()

    def __init__(self, audio_only: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.audio_only = audio_only
        self._formats: List = []
        self._selected_format: Any = None
        self._is_downloading = False
        self._progress_downloaded = 0
        self._progress_total = 0
        self._progress_started_at = 0.0
        self._spinner_index = 0
        self._progress_timer: Optional[Timer] = None
        self._spinner_frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def compose(self) -> ComposeResult:
        title = "🎵 Audio Download" if self.audio_only else "🎯 Smart Download"
        yield Static(title, id="dl-header")
        yield Input(
            placeholder="Paste URL here...",
            id="dl-url-input",
        )
        with Horizontal(id="dl-actions"):
            yield Button("Fetch Formats", variant="primary", id="btn-fetch")
            yield Button("Download Selected", id="btn-download", disabled=True)
            yield Button("Clear", id="btn-clear")
        yield Static("Idle", id="dl-progress")
        yield FormatTable(id="dl-formats")
        yield RichLog(id="dl-status", highlight=True, markup=True, wrap=True)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "dl-url-input":
            self._start_fetch(event.value.strip())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-fetch":
            url_input = self.query_one("#dl-url-input", Input)
            self._start_fetch(url_input.value.strip())
        elif event.button.id == "btn-download":
            url_input = self.query_one("#dl-url-input", Input)
            self._start_download_selected(url_input.value.strip())
        elif event.button.id == "btn-clear":
            self._clear()

    def on_format_table_format_selected(self, event: FormatTable.FormatSelected) -> None:
        self._selected_format = event.format_obj
        self._log_status(f"Selected format: {event.format_obj.format_note}")
        if _is_restricted_format(event.format_obj):
            self._set_download_enabled(False)
            self._log_status(
                "[yellow]This row looks like a preview/restricted media URL. Download is disabled.[/yellow]"
            )
            return

        self._set_download_enabled(True)

    def _start_fetch(self, url: str) -> None:
        if not url:
            self._log_status("[yellow]Please enter a URL[/yellow]")
            return

        self._selected_format = None
        self._set_download_enabled(False)
        self._log_status(f"[cyan]🔗 {url}[/cyan]")
        self._run_fetch_formats(url)

    def _start_download_selected(self, url: str) -> None:
        if not url:
            self._log_status("[yellow]Please enter a URL[/yellow]")
            return

        if not self._formats:
            self._log_status("[yellow]Fetch formats first[/yellow]")
            return

        if not self._selected_format:
            self._log_status("[yellow]Select one format from table first[/yellow]")
            return

        self.post_message(self.DownloadStarted(url))
        self._run_download(url)

    @work(thread=True)
    def _run_fetch_formats(self, url: str) -> None:
        from ..controllers.downloader import smart_download_interactive

        config = self.app.config

        def _on_status(msg: str) -> None:
            self.app.call_from_thread(self._log_status, msg)

        def _on_formats(formats: list) -> None:
            visible_formats = [fmt for fmt in formats if not _is_restricted_format(fmt)]
            self._formats = visible_formats
            self.app.call_from_thread(self._show_formats, visible_formats)
            if visible_formats:
                hidden_count = len(formats) - len(visible_formats)
                if hidden_count > 0:
                    self.app.call_from_thread(
                        self._log_status,
                        f"[yellow]{hidden_count} preview/restricted row(s) hidden from the table.[/yellow]",
                    )
            else:
                self.app.call_from_thread(
                    self._log_status,
                    "[yellow]No downloadable rows available. Only preview/restricted media URLs were found.[/yellow]",
                )

        result = smart_download_interactive(
            url=url,
            config=config,
            quality="best",
            audio_only=self.audio_only,
            selected_format=None,
            preview_only=True,
            on_status=_on_status,
            on_formats=_on_formats,
        )

        if self._formats:
            self.app.call_from_thread(self._log_status, "[green]Formats loaded. Choose one row.[/green]")
        else:
            error = result.get("error", "Could not fetch formats")
            self.app.call_from_thread(self._log_status, f"[red]✗ {error}[/red]")

    @work(thread=True)
    def _run_download(self, url: str) -> None:
        """Run download in background thread."""
        from ..controllers.downloader import smart_download_interactive

        config = self.app.config
        self.app.call_from_thread(self._start_progress_ui)

        def _on_status(msg: str) -> None:
            self.app.call_from_thread(self._log_status, msg)

        def _on_formats(formats: list) -> None:
            self._formats = formats
            self.app.call_from_thread(self._show_formats, formats)

        def _on_progress(downloaded: int, total: int) -> None:
            self.app.call_from_thread(self._update_progress, downloaded, total)

        try:
            result = smart_download_interactive(
                url=url,
                config=config,
                quality="best",
                audio_only=self.audio_only,
                selected_format=self._selected_format,
                preview_only=False,
                on_status=_on_status,
                on_formats=_on_formats,
                on_progress=_on_progress,
            )
        except Exception as exc:
            self.app.call_from_thread(self._log_status, f"\n[red]✗ Failed: {exc}[/red]")
            self.app.call_from_thread(self._stop_progress_ui, False)
            self.post_message(self.DownloadFinished(False, ""))
            return

        success = result.get("success", False)
        filepath = result.get("filepath", "")

        if success:
            self.app.call_from_thread(
                self._log_status,
                f"\n[green]✓ Downloaded: {filepath}[/green]"
            )
            self.app.call_from_thread(self.app.notify, f"Downloaded: {filepath}", title="✓ Success")
            self.app.call_from_thread(self._set_download_enabled, False)
            self.app.call_from_thread(self._stop_progress_ui, True)
        else:
            error = result.get("error", "Unknown error")
            self.app.call_from_thread(
                self._log_status,
                f"\n[red]✗ Failed: {error}[/red]"
            )
            self.app.call_from_thread(self._stop_progress_ui, False)

        self.post_message(self.DownloadFinished(success, filepath))

    def _show_formats(self, formats: list) -> None:
        try:
            ft = self.query_one("#dl-formats", FormatTable)
            ft.load_formats(formats)
        except Exception:
            pass

    def _log_status(self, msg: str) -> None:
        if _PROGRESS_LOG_RE.match(msg.strip()):
            return
        try:
            log = self.query_one("#dl-status", RichLog)
            log.write(msg)
        except Exception:
            pass

    def _clear(self) -> None:
        try:
            self.query_one("#dl-url-input", Input).value = ""
            self.query_one("#dl-status", RichLog).clear()
            self._formats = []
            self._selected_format = None
            self._set_download_enabled(False)
            self._stop_progress_ui(False, reset=True)
        except Exception:
            pass

    def _set_download_enabled(self, enabled: bool) -> None:
        try:
            self.query_one("#btn-download", Button).disabled = not enabled
        except Exception:
            pass

    def _start_progress_ui(self) -> None:
        self._is_downloading = True
        self._progress_downloaded = 0
        self._progress_total = 0
        self._progress_started_at = time.time()
        self._spinner_index = 0
        self._set_progress_text("Preparing download...")

        if self._progress_timer is not None:
            try:
                self._progress_timer.stop()
            except Exception:
                pass

        self._progress_timer = self.set_interval(0.15, self._tick_progress)

    def _stop_progress_ui(self, success: bool, reset: bool = False) -> None:
        self._is_downloading = False

        if self._progress_timer is not None:
            try:
                self._progress_timer.stop()
            except Exception:
                pass
            self._progress_timer = None

        if reset:
            self._set_progress_text("Idle")
            return

        if success:
            if self._progress_downloaded > 0:
                elapsed = max(time.time() - self._progress_started_at, 0.001)
                speed = self._progress_downloaded / elapsed
                text = (
                    f"Done • {self._human_bytes(self._progress_downloaded)} "
                    f"at {self._human_bytes(speed)}/s"
                )
            else:
                text = "Done • see logs for saved file"
        else:
            text = "Stopped"

        self._set_progress_text(text)

    def _update_progress(self, downloaded: int, total: int) -> None:
        self._progress_downloaded = max(0, int(downloaded))
        self._progress_total = max(0, int(total or 0))

    def _tick_progress(self) -> None:
        if not self._is_downloading:
            return

        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_frames)
        frame = self._spinner_frames[self._spinner_index]
        elapsed = max(time.time() - self._progress_started_at, 0.001)
        speed = self._progress_downloaded / elapsed

        if self._progress_total > 0:
            percent = min(100.0, (self._progress_downloaded / self._progress_total) * 100)
            text = (
                f"{frame} {self._human_bytes(self._progress_downloaded)} / "
                f"{self._human_bytes(self._progress_total)} ({percent:5.1f}%) "
                f"@ {self._human_bytes(speed)}/s"
            )
        else:
            text = (
                f"{frame} {self._human_bytes(self._progress_downloaded)} "
                f"downloaded @ {self._human_bytes(speed)}/s"
            )

        self._set_progress_text(text)

    def _set_progress_text(self, text: str) -> None:
        try:
            self.query_one("#dl-progress", Static).update(text)
        except Exception:
            pass

    @staticmethod
    def _human_bytes(value: float) -> str:
        if value < 1024:
            return f"{value:.0f} B"
        if value < 1024 ** 2:
            return f"{value / 1024:.1f} KB"
        if value < 1024 ** 3:
            return f"{value / (1024 ** 2):.1f} MB"
        return f"{value / (1024 ** 3):.2f} GB"
