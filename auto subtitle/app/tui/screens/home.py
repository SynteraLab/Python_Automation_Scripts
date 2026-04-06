"""Dashboard — fixed: uses Container instead of Screen."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Label, Static


class HomePanel(VerticalScroll):
    """Main dashboard showing system status and quick actions."""

    DEFAULT_CSS = """
    HomePanel {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }
    """

    def compose(self) -> ComposeResult:
        # Load settings safely
        try:
            from app.core.config import get_settings
            settings = get_settings()
            model = settings.whisper_model_size.upper()
            device = settings.whisper_device.upper()
            upload_count = self._count_files(settings.upload_dir)
            output_count = self._count_files(settings.output_dir)
            upload_dir = str(settings.upload_dir)
            output_dir = str(settings.output_dir)
            max_upload = settings.max_upload_size_mb
            default_fmt = settings.default_format.upper()
            style_preset = settings.default_style_preset
            redis_url = settings.redis_url
            compute = settings.whisper_compute_type
        except Exception:
            model = "N/A"
            device = "N/A"
            upload_count = 0
            output_count = 0
            upload_dir = "storage/uploads"
            output_dir = "storage/outputs"
            max_upload = 500
            default_fmt = "SRT"
            style_preset = "netflix"
            redis_url = "redis://localhost:6379/0"
            compute = "int8"

        # ── Title ────────────────────────────────────────────
        yield Static(
            "🏠  [bold]Dashboard[/bold]",
            classes="screen-title",
        )

        # ── Stats ────────────────────────────────────────────
        yield Static("")
        with Horizontal(classes="stats-row"):
            yield self._stat_card("Model", model)
            yield self._stat_card("Device", device)
            yield self._stat_card("Uploads", str(upload_count))
            yield self._stat_card("Outputs", str(output_count))

        # ── Quick actions ────────────────────────────────────
        yield Static("\n[bold]Quick Actions[/bold]")
        with Horizontal():
            yield Button("🎬 Generate Subtitle", id="qa-generate", variant="primary")
            yield Button("📁 Batch Process", id="qa-batch", variant="primary")
            yield Button("🎤 Live Mic", id="qa-live", variant="success")
            yield Button("👁 Preview", id="qa-preview", variant="default")

        # ── System info ──────────────────────────────────────
        yield Static("\n[bold]System Info[/bold]")
        yield Static(
            f"  Whisper Model:  [cyan]{model}[/cyan]\n"
            f"  Compute Type:   [cyan]{compute}[/cyan]\n"
            f"  Device:         [cyan]{device}[/cyan]\n"
            f"  Upload Dir:     [dim]{upload_dir}[/dim]\n"
            f"  Output Dir:     [dim]{output_dir}[/dim]\n"
            f"  Max Upload:     {max_upload} MB\n"
            f"  Default Format: {default_fmt}\n"
            f"  Style Preset:   {style_preset}\n"
            f"  Redis:          [dim]{redis_url}[/dim]"
        )

        # ── Recent outputs ───────────────────────────────────
        yield Static("\n[bold]Recent Outputs[/bold]")
        try:
            out_path = Path(output_dir)
            if out_path.exists():
                files = sorted(
                    [f for f in out_path.iterdir() if f.is_file()],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[:8]
                if files:
                    lines = []
                    for f in files:
                        sz = f.stat().st_size / 1024
                        lines.append(f"  📄 {f.name:40s} {sz:>8.1f} KB")
                    yield Static("\n".join(lines))
                else:
                    yield Static("  [dim]No outputs yet. Generate your first subtitle![/dim]")
            else:
                yield Static("  [dim]Output directory not found.[/dim]")
        except Exception:
            yield Static("  [dim]Could not read output directory.[/dim]")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        nav_map = {
            "qa-generate": "generate",
            "qa-batch": "batch",
            "qa-live": "live",
            "qa-preview": "preview",
        }
        target = nav_map.get(event.button.id or "")
        if target and hasattr(self.app, "switch_panel"):
            self.app.switch_panel(target)

    @staticmethod
    def _stat_card(label: str, value: str) -> Vertical:
        return Vertical(
            Static(f"[bold cyan]{value}[/bold cyan]", classes="stat-value"),
            Static(f"[dim]{label}[/dim]", classes="stat-label"),
            classes="stat-card",
        )

    @staticmethod
    def _count_files(directory: Path) -> int:
        try:
            return sum(1 for f in directory.iterdir() if f.is_file())
        except Exception:
            return 0