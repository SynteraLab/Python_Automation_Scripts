"""Live microphone transcription — fixed version."""

from __future__ import annotations

import threading
from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Input, Label, Select, Static

from app.tui.widgets.log_panel import LogPanel


class LivePanel(VerticalScroll):
    """Real-time microphone transcription."""

    DEFAULT_CSS = """
    LivePanel {
        width: 1fr;
        height: 1fr;
        padding: 1 2;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._is_recording = False
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def compose(self) -> ComposeResult:
        yield Static("🎤  [bold]Live Transcription[/bold]")

        # ── Status ───────────────────────────────────────────
        yield Static("\n⚫  [dim]Not Recording[/dim]", id="live-status")

        # ── Config ───────────────────────────────────────────
        yield Static("\n[bold]Configuration[/bold]")
        with Horizontal():
            yield Label("  Language: ")
            yield Input(placeholder="auto-detect", id="live-lang")

        with Horizontal():
            yield Label("  Model:    ")
            yield Select(
                [("Tiny (fastest)", "tiny"), ("Base", "base"), ("Small", "small")],
                value="tiny", id="live-model", allow_blank=False,
            )

        # ── Controls ─────────────────────────────────────────
        with Horizontal():
            yield Button("🔴  Start Recording", id="btn-live-start", variant="success")
            yield Button("⏹  Stop", id="btn-live-stop", variant="error", disabled=True)
            yield Button("🗑  Clear", id="btn-live-clear", variant="default")

        # ── Output ───────────────────────────────────────────
        yield Static("\n[bold]Live Subtitles[/bold]")
        yield Static(
            "[dim]Subtitles will appear here when you speak…[/dim]",
            id="live-transcript",
        )

        yield Static("\n[bold]System Log[/bold]")
        yield LogPanel(id="live-log")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-live-start":
            self._start_recording()
        elif event.button.id == "btn-live-stop":
            self._stop_recording()
        elif event.button.id == "btn-live-clear":
            self.query_one("#live-transcript", Static).update(
                "[dim]Subtitles will appear here when you speak…[/dim]"
            )

    def _start_recording(self) -> None:
        if self._is_recording:
            return

        # Check if sounddevice is available
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            self.notify(
                "Install sounddevice first: pip install sounddevice",
                severity="error",
            )
            return

        self._is_recording = True
        self._stop_event.clear()

        self.query_one("#live-status", Static).update("🔴  [bold red]Recording…[/bold red]")
        self.query_one("#btn-live-start", Button).disabled = True
        self.query_one("#btn-live-stop", Button).disabled = False
        self.query_one("#live-log", LogPanel).write("Starting mic capture…", "info")

        self._thread = threading.Thread(target=self._recording_loop, daemon=True)
        self._thread.start()

    def _stop_recording(self) -> None:
        self._stop_event.set()
        self._is_recording = False

        self.query_one("#live-status", Static).update("⚫  [dim]Not Recording[/dim]")
        self.query_one("#btn-live-start", Button).disabled = False
        self.query_one("#btn-live-stop", Button).disabled = True
        self.query_one("#live-log", LogPanel).write("Recording stopped.", "warning")

    def _recording_loop(self) -> None:
        """Background thread: capture mic → transcribe → display."""
        import sounddevice as sd

        log = self.query_one("#live-log", LogPanel)
        transcript = self.query_one("#live-transcript", Static)

        lang = self.query_one("#live-lang", Input).value.strip() or None
        model = str(self.query_one("#live-model", Select).value)

        self.app.call_from_thread(log.write, f"Model: {model}, Language: {lang or 'auto'}", "info")

        try:
            from app.services.realtime_service import RealtimeServiceFactory
            session = RealtimeServiceFactory.create_session(
                model_size=model, language=lang, sample_rate=16000,
            )
        except Exception as exc:
            self.app.call_from_thread(log.write, f"Engine error: {exc}", "error")
            self.app.call_from_thread(self._stop_recording)
            return

        self.app.call_from_thread(log.write, "Microphone active — speak now!", "success")

        lines: list[str] = []

        def _audio_cb(indata, frames, time_info, status):
            if status:
                self.app.call_from_thread(log.write, f"Audio: {status}", "warning")
            pcm = indata.tobytes()
            messages = session.feed_audio(pcm)
            for msg in messages:
                line = f"[green][{msg.start:.1f}s → {msg.end:.1f}s][/green]  {msg.text}"
                lines.append(line)
                display = "\n".join(lines[-30:])  # show last 30 lines
                self.app.call_from_thread(transcript.update, display)
                self.app.call_from_thread(
                    log.write, f"[{msg.start:.1f}–{msg.end:.1f}] {msg.text}", "info"
                )

        try:
            with sd.InputStream(
                samplerate=16000, channels=1, dtype="int16",
                blocksize=int(16000 * 0.5), callback=_audio_cb,
            ):
                while not self._stop_event.is_set():
                    self._stop_event.wait(timeout=0.1)

            # Flush remaining
            for msg in session.flush():
                line = f"[green][{msg.start:.1f}s → {msg.end:.1f}s][/green]  {msg.text}"
                lines.append(line)

            self.app.call_from_thread(transcript.update, "\n".join(lines[-30:]))

        except Exception as exc:
            self.app.call_from_thread(log.write, f"Error: {exc}", "error")
        finally:
            self._is_recording = False
            self.app.call_from_thread(self._stop_recording)