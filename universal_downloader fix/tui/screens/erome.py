"""
EroMe album download screen.
"""

from pathlib import Path
from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Input, Button, DataTable, RichLog, Select
from textual.containers import Vertical, Horizontal
from textual import work
from typing import Any, Dict, List, Optional

from ..controllers.downloader import build_erome_table_data, _parse_selection_ranges
from ..engine.events import event_bus


class EromeView(Widget):
    """EroMe album download view."""

    DEFAULT_CSS = """
    EromeView {
        height: 100%;
        padding: 1;
    }
    EromeView #erome-header {
        text-style: bold;
        color: $primary;
        height: 2;
    }
    EromeView #erome-actions {
        height: 3;
        margin: 1 0;
    }
    EromeView DataTable {
        height: auto;
        max-height: 14;
        margin: 1 0;
    }
    EromeView #erome-log {
        height: 1fr;
        min-height: 5;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._album: Optional[Dict] = None
        self._items: List = []
        self._filtered_items: List = []

    def compose(self) -> ComposeResult:
        yield Static("📸 EroMe Album Download", id="erome-header")
        with Horizontal():
            yield Input(placeholder="EroMe album URL...", id="erome-url")
            yield Select(
                [("All", "all"), ("Video", "video"), ("Photo", "photo")],
                value="all",
                id="erome-filter",
            )
        with Horizontal(id="erome-actions"):
            yield Button("Load Album", variant="primary", id="btn-load-album")
            yield Button("Download All", id="btn-dl-all")
            yield Button("Download Selected", id="btn-dl-selected")
        yield DataTable(id="erome-table")
        yield Input(placeholder="Select items (e.g. 1,3-5 or all)...", id="erome-selection")
        yield RichLog(id="erome-log", highlight=True, markup=True, wrap=True)

    def on_mount(self) -> None:
        try:
            dt = self.query_one("#erome-table", DataTable)
            dt.add_column("No", key="no", width=4)
            dt.add_column("Type", key="type", width=8)
            dt.add_column("Title", key="title")
            dt.add_column("Quality", key="quality", width=10)
            dt.add_column("Ext", key="ext", width=6)
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-load-album":
            self._load_album()
        elif event.button.id == "btn-dl-all":
            self._download_items(self._filtered_items)
        elif event.button.id == "btn-dl-selected":
            self._download_selected()

    @work(thread=True)
    def _load_album(self) -> None:
        try:
            url = self.app.call_from_thread(
                lambda: self.query_one("#erome-url", Input).value.strip()
            )
            filter_val = self.app.call_from_thread(
                lambda: self.query_one("#erome-filter", Select).value
            )
        except Exception:
            return

        if not url:
            self.app.call_from_thread(self._log, "[yellow]Enter a URL[/yellow]")
            return

        self.app.call_from_thread(self._log, f"[cyan]Loading album: {url}[/cyan]")

        from extractors.erome import EromeExtractor
        from utils.network import SessionManager

        config = self.app.config
        session = SessionManager(
            user_agent=config.extractor.user_agent,
            proxy=config.proxy.to_dict(),
            cookies_file=config.cookies_file,
            cookies_from_browser=config.cookies_from_browser,
        )

        try:
            extractor = EromeExtractor(session, config=vars(config))
            album = extractor.extract_album_items(url)
        except Exception as e:
            self.app.call_from_thread(self._log, f"[red]Failed: {e}[/red]")
            return
        finally:
            session.close()

        self._album = album
        self._items = album.get("items", [])

        if filter_val and filter_val != "all":
            self._filtered_items = [i for i in self._items if i.media_type == filter_val]
        else:
            self._filtered_items = list(self._items)

        self.app.call_from_thread(self._log, f"[bold]{album.get('title', '')}[/bold]")
        if album.get("uploader"):
            self.app.call_from_thread(self._log, f"[dim]Uploader: {album['uploader']}[/dim]")
        self.app.call_from_thread(
            self._log, f"[dim]Found {len(self._filtered_items)} items[/dim]"
        )
        self.app.call_from_thread(self._populate_table)

    def _populate_table(self) -> None:
        rows = build_erome_table_data(self._filtered_items)
        try:
            dt = self.query_one("#erome-table", DataTable)
            dt.clear()
            for row in rows:
                dt.add_row(row["no"], row["type"], row["title"], row["quality"], row["ext"])
        except Exception:
            pass

    def _download_selected(self) -> None:
        try:
            selection = self.query_one("#erome-selection", Input).value.strip()
        except Exception:
            return

        if not selection or not self._filtered_items:
            self._log("[yellow]No selection[/yellow]")
            return

        try:
            indices = _parse_selection_ranges(selection, len(self._filtered_items))
            selected = [self._filtered_items[i - 1] for i in indices]
            self._download_items(selected)
        except ValueError as e:
            self._log(f"[red]Invalid selection: {e}[/red]")

    @work(thread=True)
    def _download_items(self, items: list) -> None:
        if not items or not self._album:
            self.app.call_from_thread(self._log, "[yellow]Nothing to download[/yellow]")
            return

        from utils.helpers import sanitize_filename
        from ..controllers.downloader import erome_download_execute

        config = self.app.config
        base_dir = Path(config.download.output_dir)
        album_dir = base_dir / sanitize_filename(self._album["title"])
        album_dir.mkdir(parents=True, exist_ok=True)

        self.app.call_from_thread(
            self._log, f"[dim]Downloading {len(items)} item(s) to: {album_dir}[/dim]"
        )

        result = erome_download_execute(
            album=self._album,
            selected_items=items,
            album_dir=album_dir,
            config=config,
            on_status=lambda msg: self.app.call_from_thread(self._log, msg),
        )

        self.app.call_from_thread(
            self._log,
            f"\n[bold]EroMe Summary:[/bold] "
            f"[green]{result['success_count']} success[/green], "
            f"[red]{result['failed_count']} failed[/red]"
        )

    def _log(self, msg: str) -> None:
        try:
            self.query_one("#erome-log", RichLog).write(msg)
        except Exception:
            pass