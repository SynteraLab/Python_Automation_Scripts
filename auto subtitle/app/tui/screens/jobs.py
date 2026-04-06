"""Job monitor — fixed version."""

from __future__ import annotations

import asyncio
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Input, Label, Static

from app.tui.widgets.log_panel import LogPanel


class JobsPanel(VerticalScroll):
    """Monitor background subtitle generation jobs."""

    DEFAULT_CSS = """
    JobsPanel {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._polling = False

    def compose(self) -> ComposeResult:
        yield Static("📋  [bold]Job Monitor[/bold]")

        yield Static("\n[bold]Check Job Status[/bold]")
        with Horizontal():
            yield Label("  Job ID: ")
            yield Input(placeholder="Enter job ID…", id="job-id-input")
            yield Button("🔍 Check", id="btn-check-job", variant="primary")

        yield Static("\n[bold]Job Details[/bold]")
        yield Static("[dim]Enter a job ID and click Check[/dim]", id="job-details")

        yield Static("\n[bold]Tracked Jobs[/bold]")
        yield DataTable(id="jobs-table")

        yield Static("\n[bold]Log[/bold]")
        yield LogPanel(id="jobs-log")

        yield Static(
            "\n[dim]Tip: Jobs are created when you use the API "
            "(POST /api/v1/generate-subtitle).\n"
            "For direct TUI generation, results appear immediately "
            "in the Generate screen.[/dim]"
        )

    def on_mount(self) -> None:
        table = self.query_one("#jobs-table", DataTable)
        table.add_columns("Job ID", "Status", "Progress", "Message")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-check-job":
            self.run_worker(self._check_job_async(), exclusive=False)

    async def _check_job_async(self) -> None:
        job_id = self.query_one("#job-id-input", Input).value.strip()
        log = self.query_one("#jobs-log", LogPanel)
        details = self.query_one("#job-details", Static)

        if not job_id:
            self.notify("Enter a job ID.", severity="warning")
            return

        try:
            import redis.asyncio as aioredis
            from app.core.config import get_settings
            from app.services.job_service import JobService

            settings = get_settings()
            r = aioredis.from_url(settings.redis_url, decode_responses=True)
            js = JobService(r)

            status = await js.get(job_id)
            await r.aclose()

            icons = {"pending": "⏳", "processing": "⚙️", "completed": "✅", "failed": "❌"}
            icon = icons.get(status.status.value, "❓")

            details.update(
                f"  [bold]Job ID:[/bold]    {status.job_id}\n"
                f"  [bold]Status:[/bold]    {icon} {status.status.value}\n"
                f"  [bold]Progress:[/bold]  {status.progress:.1f}%\n"
                f"  [bold]Message:[/bold]   {status.message}\n"
                f"  [bold]Error:[/bold]     {status.error or '—'}\n"
                f"  [bold]Created:[/bold]   {status.created_at or '—'}\n"
                f"  [bold]Updated:[/bold]   {status.updated_at or '—'}"
            )

            log.write(f"Job {job_id}: {status.status.value}", "info")

            # Add to table
            table = self.query_one("#jobs-table", DataTable)
            table.add_row(
                status.job_id,
                f"{icon} {status.status.value}",
                f"{status.progress:.0f}%",
                status.message[:50],
            )

        except Exception as exc:
            details.update(f"[red]Error: {exc}[/red]")
            log.write(f"Error: {exc}", "error")