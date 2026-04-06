"""Subtitle preview — fixed version."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label, Static

from app.tui.widgets.log_panel import LogPanel
from app.tui.widgets.subtitle_viewer import SubtitleViewer


class PreviewPanel(VerticalScroll):
    """Browse and preview generated subtitle files."""

    DEFAULT_CSS = """
    PreviewPanel {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("👁  [bold]Subtitle Preview[/bold]")

        # ── File input ───────────────────────────────────────
        yield Static("\n[bold]Open Subtitle File[/bold]")
        with Horizontal():
            yield Label("  Path: ")
            yield Input(
                placeholder="Enter subtitle file path (.srt or .ass)",
                id="preview-path",
            )
            yield Button("📂 Open", id="btn-preview-open", variant="primary")

        # ── Quick buttons ────────────────────────────────────
        with Horizontal():
            yield Button("📂 List Output Files", id="btn-list-outputs", variant="default")
            yield Button("🗑 Clear", id="btn-preview-clear", variant="default")

        # ── File list ────────────────────────────────────────
        yield Static("\n[bold]Available Files[/bold]")
        yield Static("[dim]Click 'List Output Files' to see generated subtitles[/dim]", id="file-list")

        # ── Viewer ───────────────────────────────────────────
        yield Static("\n[bold]File Content[/bold]")
        yield SubtitleViewer(id="preview-viewer")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-preview-open":
            self._open_file()
        elif event.button.id == "btn-list-outputs":
            self._list_outputs()
        elif event.button.id == "btn-preview-clear":
            self.query_one("#preview-viewer", SubtitleViewer).clear_viewer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "preview-path":
            self._open_file()

    def _open_file(self) -> None:
        raw = self.query_one("#preview-path", Input).value.strip()
        if not raw:
            self.notify("Enter a file path.", severity="warning")
            return

        path = Path(raw).expanduser().resolve()
        if not path.exists():
            self.notify(f"File not found: {path}", severity="error")
            return

        viewer = self.query_one("#preview-viewer", SubtitleViewer)
        viewer.load_file(path)
        self.notify(f"Loaded: {path.name}")

    def _list_outputs(self) -> None:
        file_list = self.query_one("#file-list", Static)

        try:
            from app.core.config import get_settings
            settings = get_settings()
            out_dir = settings.output_dir

            if not out_dir.exists():
                file_list.update("[yellow]Output directory does not exist yet.[/yellow]")
                return

            files = sorted(
                [f for f in out_dir.iterdir() if f.is_file()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )

            if not files:
                file_list.update("[dim]No files found. Generate subtitles first![/dim]")
                return

            lines = []
            for f in files[:20]:
                sz = f.stat().st_size / 1024
                lines.append(f"  📄 {f.name:45s} {sz:>8.1f} KB")
            lines.append(f"\n  [dim]Total: {len(files)} files in {out_dir}[/dim]")
            lines.append("  [dim]Copy a filename above and paste into the Path input to view it.[/dim]")

            file_list.update("\n".join(lines))

        except Exception as exc:
            file_list.update(f"[red]Error: {exc}[/red]")