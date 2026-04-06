"""Single file subtitle generation — fixed layout, no overlapping."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    Select,
    Static,
)

from app.tui.widgets.file_tree import FileBrowserWidget
from app.tui.widgets.log_panel import LogPanel
from app.tui.widgets.progress_bar import TaskProgress


class FormRow(Horizontal):
    """Satu baris form: Label + Input/Select, tinggi fix supaya tidak tumpuk."""

    DEFAULT_CSS = """
    FormRow {
        width: 100%;
        height: 3;
        min-height: 3;
        max-height: 4;
        margin: 0 0 1 0;
        align: left middle;
    }

    FormRow > Label {
        width: 18;
        height: 3;
        padding: 1 1 0 2;
        text-style: bold;
    }

    FormRow > Select {
        width: 1fr;
        height: 3;
    }

    FormRow > Input {
        width: 1fr;
        height: 3;
    }
    """


class GeneratePanel(VerticalScroll):
    """Generate subtitles for a single file — with translation support."""

    RESOLUTION_PRESETS = {
        "source": None,
        "1080p": (1920, 1080),
        "720p": (1280, 720),
        "480p": (854, 480),
    }

    DEFAULT_CSS = """
    GeneratePanel {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }

    .section-box {
        width: 100%;
        height: auto;
        background: $panel;
        border: round $primary-background-lighten-2;
        padding: 1 2;
        margin: 1 0;
    }

    .section-header {
        text-style: bold;
        color: $accent;
        width: 100%;
        padding: 0 0 1 0;
    }

    .action-row {
        width: 100%;
        height: auto;
        margin: 1 0;
        align: left middle;
    }

    .action-row > Button {
        margin: 0 2 0 0;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._selected_file: Optional[Path] = None
        self._is_running = False

    def compose(self) -> ComposeResult:
        try:
            from app.core.config import get_settings

            settings = get_settings()
            default_model = settings.whisper_model_size
            default_beam = str(min(settings.whisper_beam_size, 3))
            default_format = settings.default_format
            default_style = settings.default_style_preset
            default_encoder = (
                "libx264"
                if settings.hard_subtitle_encoder == "auto"
                else settings.hard_subtitle_encoder
            )
            default_crf = str(settings.hard_subtitle_crf)
            default_preset = settings.hard_subtitle_preset
        except Exception:
            default_model = "base"
            default_beam = "5"
            default_format = "srt"
            default_style = "netflix"
            default_encoder = "libx264"
            default_crf = "18"
            default_preset = "medium"

        yield Static("🎬  [bold]Generate Subtitles[/bold]\n")

        # ═══════════════════════════════════════════════════
        # SECTION 1: File Selection
        # ═══════════════════════════════════════════════════
        with Vertical(classes="section-box"):
            yield Static("📂 Step 1: Select Media File", classes="section-header")
            yield FileBrowserWidget(
                placeholder="Paste full path, e.g. /Users/you/video.mp4",
                id="gen-file-browser",
            )

        # ═══════════════════════════════════════════════════
        # SECTION 2: Source Language
        # ═══════════════════════════════════════════════════
        with Vertical(classes="section-box"):
            yield Static("🗣️ Step 2: Source Language", classes="section-header")
            yield Static("[dim]Bahasa yang diucapkan di video[/dim]")
            with FormRow():
                yield Label("Source Lang:")
                yield Select(
                    [
                        ("Auto Detect", "auto"),
                        ("Japanese (日本語)", "ja"),
                        ("English", "en"),
                        ("Chinese (中文)", "zh"),
                        ("Korean (한국어)", "ko"),
                        ("Indonesian", "id"),
                        ("Malay", "ms"),
                        ("Thai", "th"),
                        ("Vietnamese", "vi"),
                        ("French", "fr"),
                        ("German", "de"),
                        ("Spanish", "es"),
                        ("Russian", "ru"),
                        ("Arabic", "ar"),
                        ("Hindi", "hi"),
                    ],
                    value="auto",
                    id="gen-source-lang",
                    allow_blank=False,
                )

        # ═══════════════════════════════════════════════════
        # SECTION 3: Translation
        # ═══════════════════════════════════════════════════
        with Vertical(classes="section-box"):
            yield Static(
                "🌐 Step 3: Translation (Optional)",
                classes="section-header",
            )
            yield Static(
                "[dim]Centang untuk menerjemahkan subtitle ke bahasa lain[/dim]"
            )
            yield Static("")
            yield Checkbox(
                "Enable Translation",
                value=False,
                id="gen-translate-enable",
            )
            yield Static("")
            with FormRow():
                yield Label("Translate to:")
                yield Select(
                    [
                        ("Indonesian (Bahasa Indonesia)", "id"),
                        ("English", "en"),
                        ("Malay (Bahasa Melayu)", "ms"),
                        ("Japanese (日本語)", "ja"),
                        ("Chinese Simplified", "zh-CN"),
                        ("Chinese Traditional", "zh-TW"),
                        ("Korean (한국어)", "ko"),
                        ("Thai (ภาษาไทย)", "th"),
                        ("Vietnamese (Tiếng Việt)", "vi"),
                        ("French (Français)", "fr"),
                        ("German (Deutsch)", "de"),
                        ("Spanish (Español)", "es"),
                        ("Portuguese", "pt"),
                        ("Russian (Русский)", "ru"),
                        ("Arabic (العربية)", "ar"),
                        ("Hindi (हिन्दी)", "hi"),
                        ("Italian", "it"),
                        ("Dutch", "nl"),
                        ("Turkish", "tr"),
                        ("Javanese (Basa Jawa)", "jw"),
                        ("Sundanese (Basa Sunda)", "su"),
                    ],
                    value="id",
                    id="gen-target-lang",
                    allow_blank=False,
                )

        # ═══════════════════════════════════════════════════
        # SECTION 4: AI & Subtitle
        # ═══════════════════════════════════════════════════
        with Vertical(classes="section-box"):
            yield Static("⚙️ Step 4: AI & Subtitle", classes="section-header")

            with FormRow():
                yield Label("Format:")
                yield Select(
                    [
                        ("SRT (simple text)", "srt"),
                        ("ASS (styled)", "ass"),
                    ],
                    value=default_format,
                    id="gen-format",
                    allow_blank=False,
                )

            with FormRow():
                yield Label("Style:")
                yield Select(
                    [
                        ("Netflix", "netflix"),
                        ("Minimal", "minimal"),
                        ("Custom", "custom"),
                    ],
                    value=default_style,
                    id="gen-style",
                    allow_blank=False,
                )

            with FormRow():
                yield Label("AI Model:")
                yield Select(
                    [
                        ("Tiny (fastest)", "tiny"),
                        ("Base (balanced)", "base"),
                        ("Small (better context)", "small"),
                        ("Medium (high accuracy)", "medium"),
                        ("Large-v3 (best)", "large-v3"),
                    ],
                    value=default_model,
                    id="gen-model",
                    allow_blank=False,
                )

            with FormRow():
                yield Label("Beam Size:")
                yield Input(default_beam, id="gen-beam")

            with FormRow():
                yield Label("Sync Mode:")
                yield Select(
                    [
                        ("Off (fastest)", "off"),
                        ("Light (recommended)", "light"),
                        ("Full audio analysis", "full"),
                    ],
                    value="light",
                    id="gen-sync-mode",
                    allow_blank=False,
                )

            with FormRow():
                yield Label("Context Hint:")
                yield Input(
                    placeholder="Names / terms / scene context (optional)",
                    id="gen-prompt",
                )

        # ═══════════════════════════════════════════════════
        # SECTION 5: Video Output
        # ═══════════════════════════════════════════════════
        with Vertical(classes="section-box"):
            yield Static("🖥️ Step 5: Video Output", classes="section-header")

            with FormRow():
                yield Label("Output Mode:")
                yield Select(
                    [
                        ("Subtitle file only", "subtitle"),
                        ("Soft subtitle track", "soft"),
                        ("Hard subtitle render", "hard"),
                    ],
                    value="subtitle",
                    id="gen-video-mode",
                    allow_blank=False,
                )

            with FormRow():
                yield Label("Encoder:")
                yield Select(
                    [
                        ("libx264 (CRF quality)", "libx264"),
                        ("Auto", "auto"),
                        ("VideoToolbox (fast)", "videotoolbox"),
                    ],
                    value=default_encoder,
                    id="gen-encoder",
                    allow_blank=False,
                )

            with FormRow():
                yield Label("CRF:")
                yield Input(default_crf, id="gen-crf")

            with FormRow():
                yield Label("Preset:")
                yield Select(
                    [
                        ("ultrafast", "ultrafast"),
                        ("superfast", "superfast"),
                        ("veryfast", "veryfast"),
                        ("faster", "faster"),
                        ("fast", "fast"),
                        ("medium", "medium"),
                        ("slow", "slow"),
                        ("slower", "slower"),
                        ("veryslow", "veryslow"),
                    ],
                    value=default_preset,
                    id="gen-preset",
                    allow_blank=False,
                )

            with FormRow():
                yield Label("Resolution:")
                yield Select(
                    [
                        ("Source resolution", "source"),
                        ("1080p", "1080p"),
                        ("720p", "720p"),
                        ("480p", "480p"),
                        ("Custom WxH", "custom"),
                    ],
                    value="source",
                    id="gen-resolution",
                    allow_blank=False,
                )

            with FormRow():
                yield Label("Custom Size:")
                yield Input(
                    placeholder="e.g. 1280x720 (fit + pad)",
                    id="gen-custom-size",
                )

            yield Static(
                "[dim]Hard render can resize video while keeping aspect ratio via fit + pad.[/dim]",
                id="gen-render-note",
            )

            yield Checkbox(
                "Replace original video file (in-place)",
                value=False,
                id="gen-overwrite-video",
            )
            yield Checkbox(
                "Keep separate subtitle file (.srt/.ass)",
                value=True,
                id="gen-keep-subtitle",
            )

        # ═══════════════════════════════════════════════════
        # SECTION 6: Generate
        # ═══════════════════════════════════════════════════
        with Vertical(classes="section-box"):
            yield Static("▶️ Step 6: Generate!", classes="section-header")
            with Horizontal(classes="action-row"):
                yield Button(
                    "▶  Generate Subtitle",
                    id="btn-gen-start",
                    variant="success",
                )
                yield Button(
                    "⏹  Cancel",
                    id="btn-gen-cancel",
                    variant="error",
                    disabled=True,
                )

            yield TaskProgress(
                task_name="Subtitle Generation",
                id="gen-progress",
            )

        # ═══════════════════════════════════════════════════
        # LOG
        # ═══════════════════════════════════════════════════
        yield Static("\n[bold]Log[/bold]")
        yield LogPanel(id="gen-log")

    # ══════════════════════════════════════════════════════
    # EVENT HANDLERS
    # ══════════════════════════════════════════════════════

    def on_file_browser_widget_file_selected(
        self, event: FileBrowserWidget.FileSelected
    ) -> None:
        self._selected_file = event.path
        log = self.query_one("#gen-log", LogPanel)
        log.write(f"Selected: {event.path}", "info")

    def on_mount(self) -> None:
        self._refresh_option_states()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in {"gen-video-mode", "gen-encoder", "gen-resolution"}:
            self._refresh_option_states()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-gen-start":
            self._start_generation()
        elif event.button.id == "btn-gen-cancel":
            self._is_running = False

    @staticmethod
    def _parse_int(
        raw_value: str,
        label: str,
        minimum: int,
        maximum: Optional[int] = None,
    ) -> int:
        value = raw_value.strip()
        if not value:
            raise ValueError(f"{label} cannot be empty.")
        if not value.isdigit():
            raise ValueError(f"{label} must be a number.")

        parsed = int(value)
        if parsed < minimum:
            raise ValueError(f"{label} must be at least {minimum}.")
        if maximum is not None and parsed > maximum:
            raise ValueError(f"{label} must be at most {maximum}.")
        return parsed

    @classmethod
    def _resolve_render_size(
        cls,
        preset: str,
        custom_size: str,
    ) -> Optional[tuple[int, int]]:
        if preset in cls.RESOLUTION_PRESETS:
            return cls.RESOLUTION_PRESETS[preset]
        if preset != "custom":
            raise ValueError(f"Unknown resolution preset: {preset}")

        cleaned = custom_size.strip().lower().replace(" ", "")
        if "x" not in cleaned:
            raise ValueError("Custom size must use WxH format, for example 1280x720.")

        width_raw, height_raw = cleaned.split("x", 1)
        width = cls._parse_int(width_raw, "Custom width", minimum=2)
        height = cls._parse_int(height_raw, "Custom height", minimum=2)
        if width % 2 != 0 or height % 2 != 0:
            raise ValueError("Custom width and height must be even numbers.")
        return width, height

    def _refresh_option_states(self) -> None:
        video_mode = str(self.query_one("#gen-video-mode", Select).value)
        encoder = str(self.query_one("#gen-encoder", Select).value)
        resolution = str(self.query_one("#gen-resolution", Select).value)

        hard_mode = video_mode == "hard"
        video_mode_enabled = video_mode != "subtitle"
        crf_enabled = hard_mode and encoder != "videotoolbox"
        custom_size_enabled = hard_mode and resolution == "custom"

        self.query_one("#gen-encoder", Select).disabled = not hard_mode
        self.query_one("#gen-crf", Input).disabled = not crf_enabled
        self.query_one("#gen-preset", Select).disabled = not crf_enabled
        self.query_one("#gen-resolution", Select).disabled = not hard_mode
        self.query_one("#gen-custom-size", Input).disabled = not custom_size_enabled
        self.query_one("#gen-overwrite-video", Checkbox).disabled = not video_mode_enabled
        self.query_one("#gen-keep-subtitle", Checkbox).disabled = not video_mode_enabled

    # ══════════════════════════════════════════════════════
    # GENERATION PIPELINE
    # ══════════════════════════════════════════════════════

    def _start_generation(self) -> None:
        if self._is_running:
            self.notify("Already running!", severity="warning")
            return

        if self._selected_file is None:
            self.notify(
                "Select a file first! Enter path and click ✓ Select.",
                severity="warning",
            )
            return

        self._is_running = True
        self.query_one("#btn-gen-start", Button).disabled = True
        self.query_one("#btn-gen-cancel", Button).disabled = False
        self.query_one("#gen-progress", TaskProgress).reset()
        self.query_one("#gen-log", LogPanel).write(
            "Starting subtitle generation…", "info"
        )

        thread = threading.Thread(target=self._run_pipeline, daemon=True)
        thread.start()

    def _run_pipeline(self) -> None:
        """Runs in background thread."""
        progress = self.query_one("#gen-progress", TaskProgress)
        log = self.query_one("#gen-log", LogPanel)

        try:
            from app.models.schemas import StylePreset, SubtitleFormat
            from app.services.subtitle_service import SubtitleService

            # Read form values
            source_val = self.query_one("#gen-source-lang", Select).value
            source_lang = (
                None if str(source_val) == "auto" else str(source_val)
            )

            translate_on = self.query_one(
                "#gen-translate-enable", Checkbox
            ).value
            target_val = self.query_one("#gen-target-lang", Select).value
            translate_to = str(target_val) if translate_on else None

            fmt = SubtitleFormat(
                str(self.query_one("#gen-format", Select).value)
            )
            style = StylePreset(
                str(self.query_one("#gen-style", Select).value)
            )
            model = str(self.query_one("#gen-model", Select).value)
            beam_size = self._parse_int(
                self.query_one("#gen-beam", Input).value,
                "Beam size",
                minimum=1,
                maximum=10,
            )
            sync_mode = str(self.query_one("#gen-sync-mode", Select).value)
            context_hint = self.query_one("#gen-prompt", Input).value.strip() or None
            video_mode = str(self.query_one("#gen-video-mode", Select).value)
            hard_subtitle = video_mode == "hard"
            embed_video = video_mode == "soft"
            overwrite_video = self.query_one("#gen-overwrite-video", Checkbox).value
            keep_subtitle = self.query_one("#gen-keep-subtitle", Checkbox).value
            encoder = str(self.query_one("#gen-encoder", Select).value)
            crf_value = self._parse_int(
                self.query_one("#gen-crf", Input).value,
                "CRF",
                minimum=0,
                maximum=51,
            )
            preset = str(self.query_one("#gen-preset", Select).value)
            render_resolution = str(self.query_one("#gen-resolution", Select).value)
            render_size = self._resolve_render_size(
                render_resolution,
                self.query_one("#gen-custom-size", Input).value,
            ) if hard_subtitle else None
            svc = SubtitleService(model_size=model)

            if video_mode == "subtitle":
                overwrite_video = False
                keep_subtitle = True

            # Log config
            self.app.call_from_thread(
                log.write, f"File: {self._selected_file}", "info"
            )
            self.app.call_from_thread(
                log.write,
                f"Model: {model} | Beam: {beam_size} | Sync: {sync_mode} | "
                f"Format: {fmt.value} | Source: {source_lang or 'auto'}",
                "info",
            )
            if translate_to:
                self.app.call_from_thread(
                    log.write,
                    f"🌐 Translation: → {translate_to}",
                    "info",
                )
            if context_hint:
                self.app.call_from_thread(
                    log.write,
                    f"🧠 Context hint enabled ({len(context_hint)} chars)",
                    "info",
                )
            if video_mode != "subtitle":
                mode = "in-place" if overwrite_video else "new video file"
                if hard_subtitle:
                    strategy = svc.describe_hard_subtitle_strategy(
                        encoder=encoder,
                        crf=crf_value,
                        preset=preset,
                        target_size=render_size,
                    )
                    caption = f"🔥 Hard subtitle enabled ({mode}, {strategy})"
                else:
                    caption = f"🎞️ Soft subtitle enabled ({mode}, no re-encode)"
                self.app.call_from_thread(
                    log.write,
                    caption,
                    "info",
                )
                if render_size is not None:
                    self.app.call_from_thread(
                        log.write,
                        f"🖼️ Target resolution: {render_size[0]}x{render_size[1]} (fit + pad)",
                        "info",
                    )

            def _on_progress(pct: float, msg: str) -> None:
                if not self._is_running:
                    raise InterruptedError("Cancelled by user")
                self.app.call_from_thread(
                    setattr, progress, "progress", pct
                )
                self.app.call_from_thread(
                    setattr, progress, "status_text", msg
                )
                self.app.call_from_thread(log.write, msg, "info")

            selected_file = self._selected_file
            if selected_file is None:
                raise ValueError("No file selected")

            result_path = svc.generate_from_file(
                input_path=selected_file,
                language=source_lang,
                fmt=fmt,
                style_preset=style,
                apply_sync=(sync_mode != "off"),
                sync_mode=sync_mode,
                beam_size=beam_size,
                initial_prompt=context_hint,
                translate_to=translate_to,
                embed_subtitle=embed_video,
                hard_subtitle=hard_subtitle,
                overwrite_video=overwrite_video,
                keep_subtitle_file=keep_subtitle,
                hard_subtitle_encoder=encoder if hard_subtitle else None,
                hard_subtitle_crf=crf_value if hard_subtitle else None,
                hard_subtitle_preset=preset if hard_subtitle else None,
                render_width=render_size[0] if render_size else None,
                render_height=render_size[1] if render_size else None,
                progress_callback=_on_progress,
            )

            done_text = "Video updated" if video_mode != "subtitle" else "Saved"
            self.app.call_from_thread(
                progress.complete, f"{done_text} → {result_path.name}"
            )
            self.app.call_from_thread(
                log.write, f"✅ Output: {result_path}", "success"
            )
            self.app.call_from_thread(
                self.notify,
                f"Done: {result_path.name}",
                severity="information",
            )

        except InterruptedError:
            self.app.call_from_thread(progress.fail, "Cancelled")
            self.app.call_from_thread(
                log.write, "Cancelled by user.", "warning"
            )
        except Exception as exc:
            self.app.call_from_thread(
                progress.fail, str(exc)[:80]
            )
            self.app.call_from_thread(
                log.write, f"Error: {exc}", "error"
            )
            self.app.call_from_thread(
                self.notify, f"Failed: {exc}", severity="error"
            )
        finally:
            self._is_running = False
            self.app.call_from_thread(
                setattr,
                self.query_one("#btn-gen-start", Button),
                "disabled",
                False,
            )
            self.app.call_from_thread(
                setattr,
                self.query_one("#btn-gen-cancel", Button),
                "disabled",
                True,
            )
