"""
Batch download screen — multiple URLs with progress tracking.
"""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple, cast

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static, Input, TextArea, Button, DataTable, RichLog
from textual.containers import Horizontal
from textual import work


class BatchDownloadView(Widget):
    """
    Batch download view.
    Supports URL list input or file import.
    Shows per-URL progress tracking.
    """

    DEFAULT_CSS = """
    BatchDownloadView {
        height: 100%;
        padding: 1;
    }
    BatchDownloadView #batch-header {
        text-style: bold;
        color: $primary;
        height: 2;
        padding: 0 1;
    }
    BatchDownloadView TextArea {
        height: 8;
        margin: 1 0;
    }
    BatchDownloadView #batch-help {
        margin: 0 1 1 1;
        height: 1;
    }
    BatchDownloadView #batch-file-row {
        height: 3;
        margin: 0 0 1 0;
    }
    BatchDownloadView #batch-options {
        height: 3;
        margin: 0 0 1 0;
    }
    BatchDownloadView #batch-workers {
        width: 12;
    }
    BatchDownloadView #batch-actions {
        height: 3;
        margin: 0 0 1 0;
    }
    BatchDownloadView #batch-summary {
        margin: 0 0 1 0;
        height: 1;
        padding: 0 1;
        color: $text-primary;
    }
    BatchDownloadView DataTable {
        height: auto;
        max-height: 14;
        margin: 1 0;
    }
    BatchDownloadView #batch-log {
        height: 1fr;
        min-height: 5;
    }
    """

    def __init__(self, from_file: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self.from_file = from_file
        self._urls: List[str] = []
        self._results: List[Optional[Dict[str, Any]]] = []
        self._is_running = False
        self._active_count = 0
        self._completed_count = 0
        self._success_count = 0
        self._failed_count = 0
        self._batch_workers = 1

    def compose(self) -> ComposeResult:
        title = "📄 Multiple Download from File" if self.from_file else "📦 Multiple Download"
        yield Static(title, id="batch-header")

        if self.from_file:
            with Horizontal(id="batch-file-row"):
                yield Input(
                    placeholder="Path to URL file...",
                    id="batch-file-input",
                )
                yield Button("Load", variant="primary", id="btn-load-file")
        else:
            yield TextArea(id="batch-urls-input")
            yield Static(
                "[dim]Enter one URL per line. Empty lines, comments, and duplicates are ignored.[/dim]",
                id="batch-help",
            )

        with Horizontal(id="batch-options"):
            yield Static("Workers", id="batch-workers-label")
            yield Input(placeholder="2", id="batch-workers")
            yield Static(
                "[dim]How many URLs run in parallel[/dim]",
                id="batch-workers-help",
            )

        with Horizontal(id="batch-actions"):
            yield Button("Start Batch", variant="primary", id="btn-start-batch")
            yield Button("Clear", id="btn-clear-batch")

        yield Static("Idle", id="batch-summary")
        yield DataTable(id="batch-progress")
        yield RichLog(id="batch-log", highlight=True, markup=True, wrap=True)

    def on_mount(self) -> None:
        try:
            dt = self.query_one("#batch-progress", DataTable)
            dt.add_column("#", key="no", width=4)
            dt.add_column("Item", key="item", width=42)
            dt.add_column("Status", key="status", width=12)
            dt.add_column("Progress", key="progress", width=18)
            dt.add_column("Detail", key="detail")
        except Exception:
            pass

        try:
            self.query_one("#batch-workers", Input).value = str(self._default_workers())
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-load-file":
            self._load_file()
        elif event.button.id == "btn-start-batch":
            self._start_batch()
        elif event.button.id == "btn-clear-batch":
            self._clear()

    def _default_workers(self) -> int:
        app = cast(Any, self.app)
        app_config = getattr(app, "config", None)
        try:
            configured = int(getattr(getattr(app_config, "download", None), "max_concurrent", 2))
        except Exception:
            configured = 2
        return max(1, min(3, configured))

    def _load_file(self) -> None:
        urls, duplicates = self._read_urls_from_file()
        if urls is None:
            return

        self._urls = urls
        self._populate_table()
        self._set_summary_text(f"Ready • {len(urls)} URL(s) loaded")
        self._log(f"[green]Loaded {len(urls)} URL(s) from file[/green]")
        if duplicates:
            self._log(f"[yellow]Ignored {duplicates} duplicate URL(s)[/yellow]")

    def _start_batch(self) -> None:
        if self._is_running:
            self._log("[yellow]A batch is already running[/yellow]")
            return

        urls, duplicates = self._collect_urls()
        if not urls:
            if not self.from_file:
                self._log("[yellow]No URLs to download[/yellow]")
            return

        self._urls = urls
        self._results = [None] * len(urls)
        self._active_count = 0
        self._completed_count = 0
        self._success_count = 0
        self._failed_count = 0
        self._batch_workers = self._resolve_workers(len(urls))

        self._populate_table(status="Queued", progress="-", detail="Waiting")
        self._set_running_state(True)
        self._update_summary()

        self._log(
            f"[cyan]Starting multiple download: {len(urls)} URL(s) with {self._batch_workers} worker(s)[/cyan]"
        )
        if duplicates:
            self._log(f"[yellow]Ignored {duplicates} duplicate URL(s)[/yellow]")

        self._run_batch(self._urls, self._batch_workers)

    def _collect_urls(self) -> Tuple[List[str], int]:
        if self.from_file:
            urls, duplicates = self._read_urls_from_file()
            return urls or [], duplicates

        try:
            text = self.query_one("#batch-urls-input", TextArea).text
        except Exception:
            return [], 0
        return self._parse_urls(text)

    def _read_urls_from_file(self) -> Tuple[Optional[List[str]], int]:
        try:
            filepath = self.query_one("#batch-file-input", Input).value.strip().strip("'\"")
        except Exception:
            filepath = ""

        if not filepath:
            self._log("[yellow]Enter a file path first[/yellow]")
            return None, 0

        if not os.path.exists(filepath):
            self._log(f"[red]File not found: {filepath}[/red]")
            return None, 0

        try:
            with open(filepath, "r", encoding="utf-8") as handle:
                text = handle.read()
        except Exception as exc:
            self._log(f"[red]Error loading file: {exc}[/red]")
            return None, 0

        urls, duplicates = self._parse_urls(text)
        if not urls:
            self._log("[yellow]No valid URLs found in file[/yellow]")
        return urls, duplicates

    @staticmethod
    def _parse_urls(text: str) -> Tuple[List[str], int]:
        urls: List[str] = []
        seen = set()
        duplicates = 0

        for raw_line in text.splitlines():
            url = raw_line.strip()
            if not url or url.startswith("#"):
                continue
            if url in seen:
                duplicates += 1
                continue
            seen.add(url)
            urls.append(url)

        return urls, duplicates

    def _resolve_workers(self, url_count: int) -> int:
        fallback = self._default_workers()
        raw_value = ""

        try:
            raw_value = self.query_one("#batch-workers", Input).value.strip()
        except Exception:
            pass

        workers = fallback
        if raw_value:
            try:
                workers = int(raw_value)
            except ValueError:
                self._log(f"[yellow]Invalid worker count '{raw_value}', using {fallback}[/yellow]")
                workers = fallback

        workers = max(1, min(workers, url_count))

        try:
            self.query_one("#batch-workers", Input).value = str(workers)
        except Exception:
            pass

        return workers

    @work(thread=True)
    def _run_batch(self, urls: List[str], workers: int) -> None:
        """Run batch download in background threads."""
        results: List[Optional[Dict[str, Any]]] = [None] * len(urls)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(self._download_single_url, idx, url): idx
                for idx, url in enumerate(urls)
            }

            for future in as_completed(future_map):
                idx = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "success": False,
                        "filepath": "",
                        "error": str(exc),
                        "title": "",
                    }

                results[idx] = result
                self.app.call_from_thread(self._handle_item_result, idx, result)

        self.app.call_from_thread(self._finish_batch, results)

    def _download_single_url(self, idx: int, url: str) -> Dict[str, Any]:
        from ..controllers.downloader import smart_download_interactive

        app = cast(Any, self.app)
        config = getattr(app, "config", None)
        if config is None:
            return {
                "success": False,
                "filepath": "",
                "error": "Application config is unavailable",
                "title": "",
            }

        self.app.call_from_thread(self._mark_row_started, idx)

        def _on_status(message: str) -> None:
            self.app.call_from_thread(self._handle_item_status, idx, message)

        def _on_progress(downloaded: int, total: int) -> None:
            self.app.call_from_thread(self._handle_item_progress, idx, downloaded, total)

        try:
            return smart_download_interactive(
                url=url,
                config=config,
                quality="best",
                on_status=_on_status,
                on_progress=_on_progress,
            )
        except Exception as exc:
            return {
                "success": False,
                "filepath": "",
                "error": str(exc),
                "title": "",
            }

    def _populate_table(
        self,
        status: str = "Pending",
        progress: str = "-",
        detail: str = "Ready",
    ) -> None:
        try:
            dt = self.query_one("#batch-progress", DataTable)
            dt.clear()
            for idx, url in enumerate(self._urls):
                dt.add_row(
                    str(idx + 1),
                    self._shorten(url, 42),
                    status,
                    progress,
                    detail,
                    key=self._row_key(idx),
                )
        except Exception:
            pass

    def _mark_row_started(self, idx: int) -> None:
        self._active_count += 1
        self._set_row(idx, status="Starting", progress="0%", detail="Queued")
        self._update_summary()

    def _handle_item_status(self, idx: int, message: str) -> None:
        if idx < len(self._results) and self._results[idx] is not None:
            return

        self._log(f"[dim][{idx + 1}/{len(self._urls)}][/dim] {message}")

        status, progress, detail = self._classify_status(message)
        updates: Dict[str, str] = {"detail": detail}
        if status:
            updates["status"] = status
        if progress:
            updates["progress"] = progress
        self._set_row(idx, **updates)

    def _handle_item_progress(self, idx: int, downloaded: int, total: int) -> None:
        if idx < len(self._results) and self._results[idx] is not None:
            return

        self._set_row(
            idx,
            status="Downloading",
            progress=self._format_progress(downloaded, total),
        )

    def _handle_item_result(self, idx: int, result: Dict[str, Any]) -> None:
        if idx < len(self._results):
            self._results[idx] = result

        self._active_count = max(0, self._active_count - 1)
        self._completed_count += 1

        title = result.get("title") or self._urls[idx]
        if result.get("success"):
            self._success_count += 1
            filepath = result.get("filepath", "")
            filename = os.path.basename(filepath) or filepath or "Completed"
            self._set_row(
                idx,
                item=self._shorten(title, 42),
                status="Done",
                progress="100%",
                detail=self._shorten(filename, 40),
            )
        else:
            self._failed_count += 1
            error = result.get("error", "Unknown error")
            self._set_row(
                idx,
                item=self._shorten(title, 42),
                status="Failed",
                detail=self._shorten(error, 40),
            )

        self._update_summary()

    def _finish_batch(self, results: List[Optional[Dict[str, Any]]]) -> None:
        success_count = sum(1 for result in results if result and result.get("success"))
        fail_count = sum(1 for result in results if result and not result.get("success"))

        self._set_running_state(False)
        self._set_summary_text(
            f"Complete • {success_count} success • {fail_count} failed • {self._batch_workers} worker(s)"
        )
        self._log(
            f"\n[bold]Batch Complete:[/bold] "
            f"[green]{success_count} success[/green], "
            f"[red]{fail_count} failed[/red]"
        )

    @staticmethod
    def _classify_status(message: str) -> Tuple[Optional[str], Optional[str], str]:
        text = message.strip()
        lowered = text.lower()
        status: Optional[str] = None
        progress: Optional[str] = None

        if lowered.startswith("using extractor:") or lowered.startswith("extracting with"):
            status = "Extracting"
        elif lowered.startswith("found ") or lowered.startswith("format:") or lowered.startswith("mode:"):
            status = "Preparing"
        elif lowered.startswith("trying yt-dlp") or lowered.startswith("trying generic"):
            status = "Retrying"
        elif lowered.startswith("custom extractor failed") or lowered.startswith("yt-dlp failed"):
            status = "Retrying"
        elif lowered.startswith("downloading..."):
            status = "Downloading"
            progress = text.replace("Downloading...", "", 1).strip() or None
        elif "already downloaded" in lowered:
            status = "Working"
        elif lowered.startswith("✓ downloaded"):
            status = "Done"
            progress = "100%"
        elif lowered.startswith("✗") or "could not download" in lowered:
            status = "Failed"
        else:
            status = "Working"

        return status, progress, BatchDownloadView._shorten(text, 40)

    def _set_row(
        self,
        idx: int,
        item: Optional[str] = None,
        status: Optional[str] = None,
        progress: Optional[str] = None,
        detail: Optional[str] = None,
    ) -> None:
        try:
            dt = self.query_one("#batch-progress", DataTable)
            row_key = self._row_key(idx)
            if item is not None:
                dt.update_cell(row_key, "item", item)
            if status is not None:
                dt.update_cell(row_key, "status", status)
            if progress is not None:
                dt.update_cell(row_key, "progress", progress)
            if detail is not None:
                dt.update_cell(row_key, "detail", detail)
        except Exception:
            pass

    def _set_running_state(self, running: bool) -> None:
        self._is_running = running

        widget_ids = ["btn-start-batch", "btn-clear-batch", "batch-workers"]
        if self.from_file:
            widget_ids.extend(["btn-load-file", "batch-file-input"])
        else:
            widget_ids.append("batch-urls-input")

        for widget_id in widget_ids:
            try:
                self.query_one(f"#{widget_id}").disabled = running
            except Exception:
                pass

    def _update_summary(self) -> None:
        total = len(self._urls)
        if total == 0:
            text = "Idle"
        elif self._is_running:
            queued = max(0, total - self._completed_count - self._active_count)
            percent = (self._completed_count / total) * 100
            text = (
                f"Running {self._completed_count}/{total} ({percent:.0f}%) • "
                f"{self._success_count} success • {self._failed_count} failed • "
                f"{self._active_count} active • {queued} queued • "
                f"{self._batch_workers} worker(s)"
            )
        else:
            text = f"Ready • {total} URL(s) queued"

        self._set_summary_text(text)

    def _set_summary_text(self, text: str) -> None:
        try:
            self.query_one("#batch-summary", Static).update(text)
        except Exception:
            pass

    @staticmethod
    def _format_progress(downloaded: int, total: int) -> str:
        downloaded = max(0, int(downloaded))
        total = max(0, int(total or 0))
        if total > 0:
            percent = min(100.0, (downloaded / total) * 100)
            return (
                f"{percent:5.1f}% • "
                f"{BatchDownloadView._human_bytes(downloaded)}/"
                f"{BatchDownloadView._human_bytes(total)}"
            )
        if downloaded > 0:
            return BatchDownloadView._human_bytes(downloaded)
        return "0%"

    @staticmethod
    def _human_bytes(value: float) -> str:
        if value < 1024:
            return f"{value:.0f} B"
        if value < 1024 ** 2:
            return f"{value / 1024:.1f} KB"
        if value < 1024 ** 3:
            return f"{value / (1024 ** 2):.1f} MB"
        return f"{value / (1024 ** 3):.2f} GB"

    @staticmethod
    def _shorten(text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        if limit <= 3:
            return text[:limit]
        return text[: limit - 3] + "..."

    @staticmethod
    def _row_key(idx: int) -> str:
        return f"row-{idx}"

    def _log(self, msg: str) -> None:
        try:
            self.query_one("#batch-log", RichLog).write(msg)
        except Exception:
            pass

    def _clear(self) -> None:
        if self._is_running:
            self._log("[yellow]Wait for the current batch to finish before clearing[/yellow]")
            return

        self._urls = []
        self._results = []
        self._active_count = 0
        self._completed_count = 0
        self._success_count = 0
        self._failed_count = 0

        try:
            self.query_one("#batch-progress", DataTable).clear()
            self.query_one("#batch-log", RichLog).clear()
            self.query_one("#batch-summary", Static).update("Idle")
        except Exception:
            pass

        try:
            self.query_one("#batch-file-input", Input).value = ""
        except Exception:
            pass

        try:
            self.query_one("#batch-urls-input", TextArea).text = ""
        except Exception:
            pass
