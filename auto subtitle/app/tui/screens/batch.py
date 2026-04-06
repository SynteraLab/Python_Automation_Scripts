"""Batch folder processing — fixed layout."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import List

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Input,
    Label,
    Select,
    Static,
)

from app.tui.widgets.log_panel import LogPanel
from app.tui.widgets.progress_bar import TaskProgress


class BatchFormRow(Horizontal):
    """Satu baris form untuk Batch screen."""

    DEFAULT_CSS = """
    BatchFormRow {
        width: 100%;
        height: 3;
        min-height: 3;
        max-height: 4;
        margin: 0 0 1 0;
        align: left middle;
    }

    BatchFormRow > Label {
        width: 18;
        height: 3;
        padding: 1 1 0 2;
        text-style: bold;
    }

    BatchFormRow > Select {
        width: 1fr;
        height: 3;
    }

    BatchFormRow > Input {
        width: 1fr;
        height: 3;
    }

    BatchFormRow > Button {
        margin: 0 0 0 1;
    }
    """


class BatchPanel(VerticalScroll):
    """Batch-process all media files in a directory."""

    DEFAULT_CSS = """
    BatchPanel {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }

    .batch-section {
        width: 100%;
        height: auto;
        background: $panel;
        border: round $primary-background-lighten-2;
        padding: 1 2;
        margin: 1 0;
    }

    .batch-header {
        text-style: bold;
        color: $accent;
        width: 100%;
        padding: 0 0 1 0;
    }

    .batch-actions {
        width: 100%;
        height: auto;
        margin: 1 0;
    }

    .batch-actions > Button {
        margin: 0 2 0 0;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._files: List[Path] = []
        self._is_running = False

    def compose(self) -> ComposeResult:
        yield Static("📁  [bold]Batch Processing[/bold]\n")

        # ═══════════════════════════════════════════════════
        # SECTION 1: Folder
        # ═══════════════════════════════════════════════════
        with Vertical(classes="batch-section"):
            yield Static("📂 Step 1: Select Folder", classes="batch-header")
            with BatchFormRow():
                yield Label("Folder Path:")
                yield Input(
                    placeholder="e.g. ~/Videos or /Users/you/anime/",
                    id="batch-folder",
                )
                yield Button("🔍 Scan", id="btn-scan", variant="primary")

        # ═══════════════════════════════════════════════════
        # File Table
        # ═══════════════════════════════════════════════════
        with Vertical(classes="batch-section"):
            yield Static("📋 Discovered Files", classes="batch-header")
            yield Static("[dim]Click 'Scan' to find media files[/dim]", id="batch-file-count")
            yield DataTable(id="batch-table")

        # ═══════════════════════════════════════════════════
        # SECTION 2: Options
        # ═══════════════════════════════════════════════════
        with Vertical(classes="batch-section"):
            yield Static("⚙️ Step 2: Configure", classes="batch-header")

            with BatchFormRow():
                yield Label("Output Dir:")
                yield Input(
                    placeholder="Leave empty → same as source folder",
                    id="batch-outdir",
                )

            with BatchFormRow():
                yield Label("Language:")
                yield Input(
                    placeholder="auto-detect (or: ja, en, zh, ko…)",
                    id="batch-lang",
                )

            with BatchFormRow():
                yield Label("Format:")
                yield Select(
                    [("SRT", "srt"), ("ASS (styled)", "ass")],
                    value="srt",
                    id="batch-fmt",
                    allow_blank=False,
                )

            with BatchFormRow():
                yield Label("Model:")
                yield Select(
                    [
                        ("Tiny (fastest)", "tiny"),
                        ("Base", "base"),
                        ("Small", "small"),
                        ("Medium", "medium"),
                    ],
                    value="tiny",
                    id="batch-model",
                    allow_blank=False,
                )

            yield Static("")
            yield Checkbox(
                "Enable Translation",
                value=False,
                id="batch-translate-enable",
            )

            with BatchFormRow():
                yield Label("Translate to:")
                yield Select(
                    [
                        ("Indonesian", "id"),
                        ("English", "en"),
                        ("Malay", "ms"),
                        ("Japanese", "ja"),
                        ("Chinese", "zh-CN"),
                        ("Korean", "ko"),
                    ],
                    value="id",
                    id="batch-target-lang",
                    allow_blank=False,
                )

            yield Static("")
            yield Checkbox(
                "Sync correction",
                value=True,
                id="batch-sync",
            )

        # ═══════════════════════════════════════════════════
        # SECTION 3: Process
        # ═══════════════════════════════════════════════════
        with Vertical(classes="batch-section"):
            yield Static("▶️ Step 3: Process", classes="batch-header")
            with Horizontal(classes="batch-actions"):
                yield Button(
                    "▶  Start Batch",
                    id="btn-batch-start",
                    variant="success",
                )
                yield Button(
                    "⏹  Stop",
                    id="btn-batch-stop",
                    variant="error",
                    disabled=True,
                )

            yield TaskProgress(
                task_name="Batch Progress",
                id="batch-progress",
            )

        # ═══════════════════════════════════════════════════
        # LOG
        # ═══════════════════════════════════════════════════
        yield Static("\n[bold]Log[/bold]")
        yield LogPanel(id="batch-log")

    # ══════════════════════════════════════════════════════
    # EVENTS
    # ══════════════════════════════════════════════════════

    def on_mount(self) -> None:
        table = self.query_one("#batch-table", DataTable)
        table.add_columns("#", "Filename", "Size", "Status")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-scan":
            self._scan_folder()
        elif event.button.id == "btn-batch-start":
            self._start_batch()
        elif event.button.id == "btn-batch-stop":
            self._is_running = False

    # ══════════════════════════════════════════════════════
    # SCAN
    # ══════════════════════════════════════════════════════

    def _scan_folder(self) -> None:
        folder_str = self.query_one("#batch-folder", Input).value.strip()
        if not folder_str:
            self.notify("Enter a folder path first.", severity="warning")
            return

        folder = Path(folder_str).expanduser().resolve()
        if not folder.is_dir():
            self.notify(f"Not a directory: {folder}", severity="error")
            return

        from app.utils.file_manager import FileManager

        fm = FileManager()
        self._files = fm.scan_folder(folder)

        table = self.query_one("#batch-table", DataTable)
        table.clear()
        for i, f in enumerate(self._files, 1):
            size = f"{f.stat().st_size / (1024 * 1024):.1f} MB"
            table.add_row(str(i), f.name, size, "⏳ Pending")

        count_label = self.query_one("#batch-file-count", Static)
        count_label.update(
            f"[green]Found {len(self._files)} media files[/green]"
        )

        log = self.query_one("#batch-log", LogPanel)
        log.write(f"Found {len(self._files)} files in {folder}", "success")

    # ══════════════════════════════════════════════════════
    # BATCH PROCESS
    # ══════════════════════════════════════════════════════

    def _start_batch(self) -> None:
        if self._is_running:
            self.notify("Already running!", severity="warning")
            return
        if not self._files:
            self.notify("Scan a folder first.", severity="warning")
            return

        self._is_running = True
        self.query_one("#btn-batch-start", Button).disabled = True
        self.query_one("#btn-batch-stop", Button).disabled = False
        self.query_one("#batch-progress", TaskProgress).reset()

        thread = threading.Thread(target=self._run_batch, daemon=True)
        thread.start()

    def _run_batch(self) -> None:
        from app.models.schemas import StylePreset, SubtitleFormat
        from app.services.subtitle_service import SubtitleService

        progress = self.query_one("#batch-progress", TaskProgress)
        log = self.query_one("#batch-log", LogPanel)
        table = self.query_one("#batch-table", DataTable)

        outdir_str = self.query_one("#batch-outdir", Input).value.strip()
        lang_str = self.query_one("#batch-lang", Input).value.strip() or None
        fmt = SubtitleFormat(
            str(self.query_one("#batch-fmt", Select).value)
        )
        model = str(self.query_one("#batch-model", Select).value)
        sync_on = self.query_one("#batch-sync", Checkbox).value

        translate_on = self.query_one(
            "#batch-translate-enable", Checkbox
        ).value
        target_val = self.query_one("#batch-target-lang", Select).value
        translate_to = str(target_val) if translate_on else None

        svc = SubtitleService(model_size=model)
        total = len(self._files)
        ok, fail = 0, 0

        row_keys = list(table.rows.keys())

        for idx, fpath in enumerate(self._files):
            if not self._is_running:
                self.app.call_from_thread(
                    log.write, "Stopped by user.", "warning"
                )
                break

            num = idx + 1
            self.app.call_from_thread(
                log.write, f"[{num}/{total}] {fpath.name}…", "info"
            )

            def _prog(pct: float, msg: str) -> None:
                overall = ((idx + pct / 100.0) / total) * 100.0
                self.app.call_from_thread(
                    setattr, progress, "progress", overall
                )
                self.app.call_from_thread(
                    setattr,
                    progress,
                    "status_text",
                    f"[{num}/{total}] {msg}",
                )

            try:
                out_dir = (
                    Path(outdir_str).expanduser()
                    if outdir_str
                    else fpath.parent
                )
                out_dir.mkdir(parents=True, exist_ok=True)
                suffix = f"_{translate_to}" if translate_to else ""
                out_path = out_dir / f"{fpath.stem}{suffix}.{fmt.value}"

                svc.generate_from_file(
                    input_path=fpath,
                    output_path=out_path,
                    language=lang_str,
                    fmt=fmt,
                    style_preset=StylePreset.NETFLIX,
                    apply_sync=sync_on,
                    translate_to=translate_to,
                    progress_callback=_prog,
                )
                ok += 1
                self.app.call_from_thread(
                    log.write, f"  ✅ → {out_path.name}", "success"
                )
                if idx < len(row_keys):
                    self.app.call_from_thread(
                        table.update_cell,
                        row_keys[idx],
                        "Status",
                        "✅ Done",
                    )
            except Exception as exc:
                fail += 1
                self.app.call_from_thread(
                    log.write, f"  ❌ {exc}", "error"
                )
                if idx < len(row_keys):
                    self.app.call_from_thread(
                        table.update_cell,
                        row_keys[idx],
                        "Status",
                        "❌ Failed",
                    )

        summary = f"Done: {ok} succeeded, {fail} failed"
        self.app.call_from_thread(
            setattr, progress, "status_text", summary
        )
        self.app.call_from_thread(
            log.write, f"🏁 {summary}", "success"
        )
        self._is_running = False
        self.app.call_from_thread(
            setattr,
            self.query_one("#btn-batch-start", Button),
            "disabled",
            False,
        )
        self.app.call_from_thread(
            setattr,
            self.query_one("#btn-batch-stop", Button),
            "disabled",
            True,
        )