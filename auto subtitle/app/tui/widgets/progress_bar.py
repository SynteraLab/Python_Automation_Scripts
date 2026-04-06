"""Progress tracker widget — fixed version."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Label, ProgressBar, Static


class TaskProgress(Vertical):
    """Labelled progress bar with status text."""

    DEFAULT_CSS = """
    TaskProgress {
        height: auto;
        padding: 1 2;
        background: $panel;
        border: round $primary-background-lighten-2;
        margin: 1 0;
    }
    """

    progress: reactive[float] = reactive(0.0)
    status_text: reactive[str] = reactive("Idle")

    def __init__(self, task_name: str = "Task", **kwargs):
        super().__init__(**kwargs)
        self._task_name = task_name

    def compose(self) -> ComposeResult:
        yield Label(self._task_name, id="tp-label")
        yield ProgressBar(total=100, show_percentage=True, show_eta=False, id="tp-bar")
        yield Static("  ↳ Idle", id="tp-status")

    def watch_progress(self, value: float) -> None:
        try:
            bar = self.query_one("#tp-bar", ProgressBar)
            bar.update(progress=value)
        except Exception:
            pass

    def watch_status_text(self, value: str) -> None:
        try:
            lbl = self.query_one("#tp-status", Static)
            lbl.update(f"  ↳ {value}")
        except Exception:
            pass

    def reset(self) -> None:
        self.progress = 0.0
        self.status_text = "Idle"

    def complete(self, msg: str = "Complete ✓") -> None:
        self.progress = 100.0
        self.status_text = f"[green]{msg}[/green]"

    def fail(self, msg: str = "Failed ✗") -> None:
        self.status_text = f"[red]{msg}[/red]"