"""Subtitle file viewer — fixed version."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static


class SubtitleViewer(VerticalScroll):
    """Displays subtitle file contents."""

    DEFAULT_CSS = """
    SubtitleViewer {
        height: 1fr;
        min-height: 10;
        border: round $primary-background-lighten-2;
        background: $panel;
        margin: 1 0;
        padding: 1 2;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._current: Optional[Path] = None

    def compose(self) -> ComposeResult:
        yield Static(
            "[dim]No subtitle file loaded.\nGenerate or select one to preview.[/dim]",
            id="sv-content",
        )

    def load_file(self, path: Path) -> None:
        self._current = path
        content = self.query_one("#sv-content", Static)

        if not path.exists():
            content.update(f"[red]File not found: {path}[/red]")
            return

        text = path.read_text(encoding="utf-8", errors="replace")
        suffix = path.suffix.lower()

        if suffix == ".srt":
            formatted = self._format_srt(text)
        elif suffix in (".ass", ".ssa"):
            formatted = self._format_ass(text)
        else:
            formatted = text

        header = (
            f"[bold]📄 {path.name}[/bold]  │  "
            f"{len(text)} chars  │  "
            f"{text.count(chr(10)) + 1} lines\n"
            f"{'─' * 60}\n\n"
        )
        content.update(header + formatted)
        self.scroll_home(animate=False)

    def clear_viewer(self) -> None:
        self._current = None
        self.query_one("#sv-content", Static).update(
            "[dim]No subtitle file loaded.[/dim]"
        )

    @staticmethod
    def _format_srt(text: str) -> str:
        lines = []
        for line in text.split("\n"):
            s = line.strip()
            if s.isdigit():
                lines.append(f"[bold cyan]{s}[/bold cyan]")
            elif "-->" in s:
                lines.append(f"[dim green]{s}[/dim green]")
            elif s:
                lines.append(f"[white]{s}[/white]")
            else:
                lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _format_ass(text: str) -> str:
        lines = []
        for line in text.split("\n"):
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                lines.append(f"[bold magenta]{s}[/bold magenta]")
            elif s.startswith("Dialogue:"):
                lines.append(f"[white]{s}[/white]")
            elif s.startswith("Style:"):
                lines.append(f"[cyan]{s}[/cyan]")
            elif ":" in s:
                k, _, v = s.partition(":")
                lines.append(f"[yellow]{k}:[/yellow]{v}")
            else:
                lines.append(s)
        return "\n".join(lines)