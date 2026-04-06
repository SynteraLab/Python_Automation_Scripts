"""Simple file input widget — beginner-friendly version.

Uses a text input + scan button instead of DirectoryTree
(more reliable across all terminals).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Input, Label, Static

MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv",
    ".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a",
}


class FileBrowserWidget(Vertical):
    """Simple file path input with validation."""

    DEFAULT_CSS = """
    FileBrowserWidget {
        height: auto;
        padding: 1 2;
        background: $panel;
        border: round $primary-background-lighten-2;
        margin: 1 0;
    }
    """

    class FileSelected(Message):
        def __init__(self, path: Path) -> None:
            super().__init__()
            self.path = path

    def __init__(self, placeholder: str = "Enter file path…", **kwargs):
        super().__init__(**kwargs)
        self._placeholder = placeholder
        self._selected: Optional[Path] = None

    def compose(self) -> ComposeResult:
        yield Label("📂 File Path:")
        with Horizontal():
            yield Input(
                placeholder=self._placeholder,
                id="fb-input",
            )
            yield Button("✓ Select", id="fb-select", variant="primary")
        yield Static("[dim]No file selected[/dim]", id="fb-info")

    @property
    def selected_path(self) -> Optional[Path]:
        return self._selected

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "fb-select":
            self._validate_and_select()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """User pressed Enter in the input field."""
        self._validate_and_select()

    def _validate_and_select(self) -> None:
        raw = self.query_one("#fb-input", Input).value.strip()
        info = self.query_one("#fb-info", Static)

        if not raw:
            info.update("[red]Please enter a file path[/red]")
            return

        path = Path(raw).expanduser().resolve()

        if not path.exists():
            info.update(f"[red]File not found: {path}[/red]")
            return

        if path.is_dir():
            info.update(f"[red]That's a folder, not a file. Use Batch mode for folders.[/red]")
            return

        if path.suffix.lower() not in MEDIA_EXTENSIONS:
            info.update(
                f"[yellow]Warning: '{path.suffix}' may not be supported. "
                f"Supported: {', '.join(sorted(MEDIA_EXTENSIONS))}[/yellow]"
            )

        size = path.stat().st_size
        size_str = self._fmt_size(size)
        info.update(
            f"[green]✓[/green] [bold]{path.name}[/bold]  │  {size_str}  │  {path.suffix.upper()}"
        )
        self._selected = path
        self.post_message(self.FileSelected(path))

    @staticmethod
    def _fmt_size(n: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"