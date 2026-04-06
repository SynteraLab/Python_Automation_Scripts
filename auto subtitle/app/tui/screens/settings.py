"""Settings editor — fixed layout."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Label, Select, Static


class SettingsFormRow(Horizontal):
    """Satu baris form untuk Settings."""

    DEFAULT_CSS = """
    SettingsFormRow {
        width: 100%;
        height: 3;
        min-height: 3;
        max-height: 4;
        margin: 0 0 1 0;
        align: left middle;
    }

    SettingsFormRow > Label {
        width: 20;
        height: 3;
        padding: 1 1 0 2;
        text-style: bold;
    }

    SettingsFormRow > Select {
        width: 1fr;
        height: 3;
    }

    SettingsFormRow > Input {
        width: 1fr;
        height: 3;
    }
    """


class SettingsPanel(VerticalScroll):
    """View and modify runtime settings."""

    DEFAULT_CSS = """
    SettingsPanel {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }

    .settings-section {
        width: 100%;
        height: auto;
        background: $panel;
        border: round $primary-background-lighten-2;
        padding: 1 2;
        margin: 1 0;
    }

    .settings-header {
        text-style: bold;
        color: $accent;
        width: 100%;
        padding: 0 0 1 0;
    }

    .settings-actions {
        width: 100%;
        height: auto;
        margin: 1 0;
    }

    .settings-actions > Button {
        margin: 0 2 0 0;
    }
    """

    def compose(self) -> ComposeResult:
        try:
            from app.core.config import get_settings
            s = get_settings()
            model = s.whisper_model_size
            device = s.whisper_device
            compute = s.whisper_compute_type
            beam = str(s.whisper_beam_size)
            fmt = s.default_format
            style = s.default_style_preset
            maxchars = str(s.max_chars_per_line)
            maxlines = str(s.max_lines)
            upload_dir = str(s.upload_dir)
            output_dir = str(s.output_dir)
            max_upload = str(s.max_upload_size_mb)
            redis_url = s.redis_url
            concurrency = str(s.worker_concurrency)
        except Exception:
            model = device = compute = "auto"
            beam = "5"
            fmt = "srt"
            style = "netflix"
            maxchars = "42"
            maxlines = "2"
            upload_dir = "storage/uploads"
            output_dir = "storage/outputs"
            max_upload = "500"
            redis_url = "redis://localhost:6379/0"
            concurrency = "2"

        yield Static("⚙   [bold]Settings[/bold]\n")

        # ── Whisper ──────────────────────────────────────
        with Vertical(classes="settings-section"):
            yield Static("🧠 Whisper Engine", classes="settings-header")

            with SettingsFormRow():
                yield Label("Model Size:")
                yield Select(
                    [
                        ("Tiny (~1GB RAM)", "tiny"),
                        ("Base (~1GB RAM)", "base"),
                        ("Small (~2GB RAM)", "small"),
                        ("Medium (~5GB RAM)", "medium"),
                        ("Large-v3 (~10GB)", "large-v3"),
                    ],
                    value=model,
                    id="set-model",
                    allow_blank=False,
                )

            with SettingsFormRow():
                yield Label("Device:")
                yield Select(
                    [
                        ("Auto", "auto"),
                        ("CPU", "cpu"),
                        ("CUDA (GPU)", "cuda"),
                    ],
                    value=device,
                    id="set-device",
                    allow_blank=False,
                )

            with SettingsFormRow():
                yield Label("Compute Type:")
                yield Select(
                    [
                        ("Auto", "auto"),
                        ("int8 (fastest)", "int8"),
                        ("float16", "float16"),
                        ("float32", "float32"),
                    ],
                    value=compute,
                    id="set-compute",
                    allow_blank=False,
                )

            with SettingsFormRow():
                yield Label("Beam Size:")
                yield Input(beam, id="set-beam")

        # ── Subtitle ─────────────────────────────────────
        with Vertical(classes="settings-section"):
            yield Static("📝 Subtitle Defaults", classes="settings-header")

            with SettingsFormRow():
                yield Label("Default Format:")
                yield Select(
                    [("SRT", "srt"), ("ASS", "ass")],
                    value=fmt,
                    id="set-fmt",
                    allow_blank=False,
                )

            with SettingsFormRow():
                yield Label("Default Style:")
                yield Select(
                    [
                        ("Netflix", "netflix"),
                        ("Minimal", "minimal"),
                        ("Custom", "custom"),
                    ],
                    value=style,
                    id="set-style",
                    allow_blank=False,
                )

            with SettingsFormRow():
                yield Label("Max Chars/Line:")
                yield Input(maxchars, id="set-maxchars")

            with SettingsFormRow():
                yield Label("Max Lines:")
                yield Input(maxlines, id="set-maxlines")

        # ── Storage ──────────────────────────────────────
        with Vertical(classes="settings-section"):
            yield Static("📁 Storage", classes="settings-header")

            with SettingsFormRow():
                yield Label("Upload Dir:")
                yield Input(upload_dir, id="set-upload-dir")

            with SettingsFormRow():
                yield Label("Output Dir:")
                yield Input(output_dir, id="set-output-dir")

            with SettingsFormRow():
                yield Label("Max Upload MB:")
                yield Input(max_upload, id="set-max-upload")

        # ── Workers ──────────────────────────────────────
        with Vertical(classes="settings-section"):
            yield Static("⚡ Workers & Redis", classes="settings-header")

            with SettingsFormRow():
                yield Label("Redis URL:")
                yield Input(redis_url, id="set-redis")

            with SettingsFormRow():
                yield Label("Concurrency:")
                yield Input(concurrency, id="set-concurrency")

        # ── Actions ──────────────────────────────────────
        with Horizontal(classes="settings-actions"):
            yield Button(
                "💾  Save Settings",
                id="btn-save",
                variant="success",
            )
            yield Button(
                "🗑  Clean Temp Files",
                id="btn-clean",
                variant="warning",
            )

        yield Static(
            "\n[dim]Changes apply to this session only.\n"
            "To persist, edit the .env file and restart.[/dim]"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-save":
            self._save()
        elif event.button.id == "btn-clean":
            self._clean_temp()

    def _save(self) -> None:
        try:
            from app.core.config import get_settings
            s = get_settings()

            s.whisper_model_size = str(
                self.query_one("#set-model", Select).value
            )
            s.whisper_device = str(
                self.query_one("#set-device", Select).value
            )
            s.whisper_compute_type = str(
                self.query_one("#set-compute", Select).value
            )

            beam = self.query_one("#set-beam", Input).value.strip()
            if beam.isdigit():
                s.whisper_beam_size = int(beam)

            s.default_format = str(
                self.query_one("#set-fmt", Select).value
            )
            s.default_style_preset = str(
                self.query_one("#set-style", Select).value
            )

            mc = self.query_one("#set-maxchars", Input).value.strip()
            if mc.isdigit():
                s.max_chars_per_line = int(mc)

            ml = self.query_one("#set-maxlines", Input).value.strip()
            if ml.isdigit():
                s.max_lines = int(ml)

            ud = self.query_one("#set-upload-dir", Input).value.strip()
            if ud:
                s.upload_dir = Path(ud)

            od = self.query_one("#set-output-dir", Input).value.strip()
            if od:
                s.output_dir = Path(od)

            s.ensure_dirs()
            self.notify("Settings saved ✓", severity="information")
        except Exception as exc:
            self.notify(f"Error: {exc}", severity="error")

    def _clean_temp(self) -> None:
        try:
            from app.utils.file_manager import FileManager
            fm = FileManager()
            fm.cleanup_all_temp()
            self.notify("Temp files cleaned ✓", severity="information")
        except Exception as exc:
            self.notify(f"Error: {exc}", severity="error")