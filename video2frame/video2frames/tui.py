"""
Full-featured Text User Interface for Video2Frames.

Design principle — **paste a path, get your frames**:

  1. Launch the app  (``python -m video2frames``)
  2. At the main menu, drag / paste a video file path
  3. Metadata is displayed, smart defaults are applied
  4. Press Enter a couple of times → extraction runs
  5. Results + follow-up actions are offered

Everything else (batch mode, file browser, settings, history, presets,
validation, preview) is reachable from the same menu without leaving
the TUI.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import signal
import subprocess as sp
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.rule import Rule
from rich.table import Table

from video2frames import __app_name__, __version__
from video2frames.browser import FileBrowser, clean_path, is_path_like, is_video_file
from video2frames.config import Config, ConfigManager
from video2frames.engine import (
    ExtractionResult,
    FrameExtractor,
    VideoDetector,
    VideoMetadata,
)
from video2frames.preview import FramePreview
from video2frames.utils import (
    VIDEO_EXTENSIONS,
    check_ffmpeg,
    check_ffprobe,
    estimate_png_size,
    find_ffmpeg,
    find_video_files,
    format_duration,
    format_size,
    get_disk_free_space,
    get_ffmpeg_install_instructions,
    setup_logging,
)

logger = logging.getLogger("video2frames.tui")

HISTORY_FILE = Path.home() / ".video2frames" / "history.json"


# ═══════════════════════════════════════════════════════════════════════
#  Main TUI class
# ═══════════════════════════════════════════════════════════════════════

class VideoTUI:
    """Top-level TUI application.  Create one instance and call ``run()``."""

    def __init__(self) -> None:
        self.console = Console()
        self.cm = ConfigManager()
        self.cfg = self.cm.load_config()

        self.extractor: Optional[FrameExtractor] = None
        self.detector: Optional[VideoDetector] = None
        self.browser = FileBrowser()
        self.previewer = FramePreview()
        self.history: List[Dict[str, Any]] = []
        self._log_file: Optional[Path] = None

        signal.signal(signal.SIGINT, self._on_sigint)

    # ── signal handling ───────────────────────────────────────────────
    def _on_sigint(self, _sig: int, _frame: Any) -> None:
        if self.extractor:
            self.extractor.cancel()
        self.console.print("\n[bold yellow]⚠  Interrupted — returning to menu …[/]")

    # ══════════════════════════════════════════════════════════════════
    #  Lifecycle
    # ══════════════════════════════════════════════════════════════════

    def run(self) -> None:
        """Entry point — banner → preflight → main loop."""
        self._clear()
        self._banner()
        if not self._preflight():
            sys.exit(1)
        self._load_history()
        self._loop()

    def _clear(self) -> None:
        os.system("cls" if os.name == "nt" else "clear")

    # ══════════════════════════════════════════════════════════════════
    #  Banner & preflight
    # ══════════════════════════════════════════════════════════════════

    def _banner(self) -> None:
        self.console.print(Panel(
            f"[bold cyan]🎬  {__app_name__}[/]  [dim]v{__version__}[/]\n"
            "[dim]Video → Image Sequence Converter[/]",
            border_style="cyan",
            expand=False,
            padding=(1, 4),
        ))

    def _preflight(self) -> bool:
        """Validate FFmpeg / FFprobe, init engine, start logging."""
        ffmpeg, ffprobe = find_ffmpeg()
        self.cfg.ffmpeg_path = ffmpeg
        self.cfg.ffprobe_path = ffprobe

        ok, info = check_ffmpeg(ffmpeg)
        if not ok:
            self.console.print(f"  [red]✗  FFmpeg — {info}[/]")
            self.console.print()
            self.console.print(get_ffmpeg_install_instructions())
            return False
        self.console.print(f"  [green]✓[/]  {info}")

        ok, info = check_ffprobe(ffprobe)
        if not ok:
            self.console.print(f"  [red]✗  FFprobe — {info}[/]")
            return False
        self.console.print(f"  [green]✓[/]  {info}")

        self.console.print(
            f"  [green]✓[/]  Platform: {platform.system()} {platform.machine()}"
        )

        free = get_disk_free_space(Path.cwd())
        self.console.print(f"  [green]✓[/]  Disk free: {format_size(free)}")

        self.detector = VideoDetector(self.cfg.ffprobe_path)
        self.extractor = FrameExtractor(
            self.cfg.ffmpeg_path,
            self.cfg.ffprobe_path,
            self.cfg.threads,
            self.cfg.compression_level,
        )

        self._log_file = setup_logging(Path(self.cfg.log_dir))
        self.console.print(f"  [green]✓[/]  Log: [dim]{self._log_file}[/]")
        self.console.print()
        return True

    # ══════════════════════════════════════════════════════════════════
    #  Main loop — the heart of the TUI
    # ══════════════════════════════════════════════════════════════════

    def _loop(self) -> None:
        while True:
            self._menu()
            try:
                raw = Prompt.ask("[bold cyan]▶[/] ").strip()
            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[cyan]Goodbye! 👋[/]")
                break

            if not raw:
                continue

            # ── smart path detection ──────────────────────────────────
            if is_path_like(raw):
                path = clean_path(raw)
                if is_video_file(path):
                    self.console.print(
                        f"  [green]✓  Video file detected[/] → "
                        f"[bold]{path.name}[/]"
                    )
                    self._quick_extract(path)
                    continue
                if path.is_dir():
                    vids = find_video_files(path)
                    if vids:
                        self.console.print(
                            f"  [green]✓  Folder detected[/] → "
                            f"[bold]{len(vids)}[/] video(s)"
                        )
                        self._batch_extract(path, vids)
                    else:
                        self.console.print(
                            f"  [yellow]No video files in {path}[/]"
                        )
                    continue
                if path.is_file():
                    self.console.print(
                        f"  [yellow]Unsupported extension: {path.suffix}[/]\n"
                        f"  [dim]Supported: "
                        f"{' '.join(sorted(VIDEO_EXTENSIONS)[:12])} …[/]"
                    )
                    continue
                self.console.print(f"  [red]✗  Path not found: {path}[/]")
                continue

            # ── menu routing ──────────────────────────────────────────
            if raw == "1":
                self._quick_extract_prompt()
            elif raw == "2":
                self._browse_and_extract()
            elif raw == "3":
                self._batch_prompt()
            elif raw == "4":
                self._inspect()
            elif raw == "5":
                self._preview_menu()
            elif raw == "6":
                self._validate_menu()
            elif raw == "7":
                self._history_menu()
            elif raw == "8":
                self._settings_menu()
            elif raw in ("0", "q", "quit", "exit"):
                self.console.print("[cyan]Goodbye! 👋[/]")
                break
            else:
                self.console.print(f"  [red]Unknown: {raw}[/]")

    def _menu(self) -> None:
        self.console.print()
        self.console.print(Rule("[bold cyan]Main Menu", style="cyan"))
        m = Table(box=None, show_header=False, padding=(0, 2), pad_edge=False)
        m.add_column(style="bold cyan", width=5, justify="right")
        m.add_column(min_width=45)
        m.add_row("1", "⚡  Quick Extract       [dim]paste video path[/]")
        m.add_row("2", "📂  Browse & Extract    [dim]file browser[/]")
        m.add_row("3", "📦  Batch Extract       [dim]paste folder path[/]")
        m.add_row("4", "🔍  Inspect Video       [dim]view metadata[/]")
        m.add_row("5", "👁   Preview Frames      [dim]OpenCV viewer[/]")
        m.add_row("6", "✅  Validate            [dim]verify extraction[/]")
        m.add_row("7", "📋  History             [dim]recent extractions[/]")
        m.add_row("8", "⚙   Settings & Presets")
        m.add_row("0", "🚪  Exit")
        self.console.print(m)
        self.console.print()
        self.console.print(
            "[dim]💡  Tip: paste / drag a video path here for instant extraction[/]"
        )
        self.console.print()

    # ══════════════════════════════════════════════════════════════════
    #  Path input helpers
    # ══════════════════════════════════════════════════════════════════

    def _ask_path(
        self,
        label: str = "Path",
        *,
        must_exist: bool = True,
        file_only: bool = False,
        dir_only: bool = False,
        default: str = "",
        allow_browse: bool = True,
    ) -> Optional[Path]:
        """
        Prompt for a path with validation, cleaning, and browser fallback.

        Returns *None* when the user cancels (empty / Ctrl-C).
        """
        hint_parts = []
        if allow_browse:
            hint_parts.append("b=browse")
        hint_parts.append("empty=cancel")
        hint = f"[dim]({', '.join(hint_parts)})[/]"

        try:
            raw = Prompt.ask(f"[bold]{label}[/] {hint}", default=default).strip()
        except (KeyboardInterrupt, EOFError):
            return None

        if not raw:
            return None

        # browser shortcut
        if raw.lower() == "b" and allow_browse:
            start_dir = Path(default).parent if default else None
            return self.browser.browse(
                start=start_dir,
                select_folder=dir_only,
                title="Select folder" if dir_only else "Select video",
            )

        path = clean_path(raw)

        if must_exist and not path.exists():
            self.console.print(f"  [red]✗  Not found: {path}[/]")
            # suggest similar names
            parent = path.parent
            if parent.is_dir():
                stem = path.name.lower()
                similar = [
                    f.name
                    for f in parent.iterdir()
                    if not f.name.startswith(".")
                    and stem[:4] in f.name.lower()
                ][:5]
                if similar:
                    self.console.print(
                        f"  [dim]  Did you mean: {', '.join(similar)}?[/]"
                    )
            return None

        if file_only and not path.is_file():
            self.console.print(f"  [red]✗  Not a file: {path}[/]")
            return None

        if dir_only and not path.is_dir():
            self.console.print(f"  [red]✗  Not a directory: {path}[/]")
            return None

        self.console.print(f"  [green]✓  {path}[/]")
        return path

    # ══════════════════════════════════════════════════════════════════
    #  Metadata display
    # ══════════════════════════════════════════════════════════════════

    def _show_meta(self, meta: VideoMetadata) -> None:
        tbl = Table(
            title=f"[bold white]🎬  {meta.filename}[/]",
            box=box.ROUNDED,
            show_lines=True,
            border_style="cyan",
            title_style="bold",
            min_width=50,
        )
        tbl.add_column("Property", style="bold cyan", width=18)
        tbl.add_column("Value", min_width=30)

        tbl.add_row("📁  File", str(meta.filepath))
        tbl.add_row("📐  Resolution", f"{meta.resolution}   ({meta.aspect_ratio})")
        tbl.add_row("🎞   FPS", str(meta.fps))
        tbl.add_row("⏱   Duration", format_duration(meta.duration))
        tbl.add_row("🔢  Total Frames", f"{meta.total_frames:,}")
        tbl.add_row("🎛   Codec", meta.codec)
        tbl.add_row("🎨  Pixel Format", meta.pixel_format)
        if meta.bitrate:
            tbl.add_row("📊  Bitrate", f"{meta.bitrate // 1000:,} kbps")
        tbl.add_row("💾  File Size", format_size(meta.file_size))

        est = estimate_png_size(meta.width, meta.height) * meta.total_frames
        tbl.add_row("📦  Est. Output", f"~{format_size(est)}")
        self.console.print(tbl)

    # ══════════════════════════════════════════════════════════════════
    #  1 — Quick Extract
    # ══════════════════════════════════════════════════════════════════

    def _quick_extract_prompt(self) -> None:
        self.console.print(Rule("[bold green]Quick Extract", style="green"))
        if self.history:
            self.console.print("[dim]Recent:[/]")
            for h in self.history[-3:]:
                self.console.print(f"  [dim]•  {h.get('input', '?')}[/]")
            self.console.print()

        path = self._ask_path("Video file", file_only=True)
        if path is None:
            return
        if not is_video_file(path):
            self.console.print(f"  [red]Not a supported video: {path.suffix}[/]")
            return
        self._quick_extract(path)

    def _quick_extract(self, video_path: Path) -> None:
        """Detect → show metadata → confirm → extract → post-actions."""
        self.console.print()
        self.console.print(Rule("[bold green]Quick Extract", style="green"))

        # ── metadata ──────────────────────────────────────────────────
        try:
            with self.console.status("[bold cyan]  Analysing video …[/]"):
                meta = self.detector.detect(video_path)  # type: ignore[union-attr]
        except Exception as exc:
            self.console.print(f"  [red]✗  Cannot read video: {exc}[/]")
            return

        self._show_meta(meta)

        # ── disk space ────────────────────────────────────────────────
        est = estimate_png_size(meta.width, meta.height) * meta.total_frames
        out_base = Path(self.cfg.output_dir)
        free = get_disk_free_space(
            out_base if out_base.exists() else Path.cwd()
        )
        if est > free * 0.85:
            self.console.print(
                f"\n  [bold red]⚠  Estimated output ~{format_size(est)} "
                f"but only {format_size(free)} free![/]"
            )
            if not Confirm.ask("  Continue anyway?", default=False):
                return

        # ── output dir ────────────────────────────────────────────────
        default_out = str(Path(self.cfg.output_dir) / video_path.stem)
        raw = Prompt.ask(
            "[bold]Output directory[/]", default=default_out
        ).strip()
        output_dir = clean_path(raw) if raw else Path(default_out)

        # ── check existing ────────────────────────────────────────────
        if output_dir.exists():
            existing = list(output_dir.glob(f"*.{self.cfg.format}"))
            if existing and not self.cfg.overwrite:
                self.console.print(
                    f"  [yellow]⚠  {len(existing)} existing "
                    f".{self.cfg.format} file(s) in {output_dir}[/]"
                )
                if not Confirm.ask("  Overwrite?", default=False):
                    return
                self.cfg.overwrite = True

        # ── format ────────────────────────────────────────────────────
        fmt = Prompt.ask(
            "[bold]Format[/]",
            choices=["png", "jpg", "bmp", "tiff"],
            default=self.cfg.format,
        )

        self.console.print()

        # ── run ───────────────────────────────────────────────────────
        result = self._extract_progress(video_path, output_dir, fmt, meta)
        if result and result.success:
            self._add_history(video_path, output_dir, result)
            self._post_actions(output_dir, video_path)

    # ══════════════════════════════════════════════════════════════════
    #  2 — Browse & Extract
    # ══════════════════════════════════════════════════════════════════

    def _browse_and_extract(self) -> None:
        self.console.print(Rule("[bold blue]Browse & Extract", style="blue"))
        start = None
        if self.history:
            last = self.history[-1].get("input")
            if last:
                start = Path(last).parent

        chosen = self.browser.browse(start=start, title="Select a video")
        if chosen is None:
            self.console.print("  [dim]Cancelled.[/]")
            return

        if is_video_file(chosen):
            self._quick_extract(chosen)
        elif chosen.is_dir():
            vids = find_video_files(chosen)
            if vids:
                self._batch_extract(chosen, vids)
            else:
                self.console.print(f"  [yellow]No videos in {chosen}[/]")
        else:
            self.console.print(f"  [red]Unsupported: {chosen}[/]")

    # ══════════════════════════════════════════════════════════════════
    #  3 — Batch Extract
    # ══════════════════════════════════════════════════════════════════

    def _batch_prompt(self) -> None:
        self.console.print(Rule("[bold magenta]Batch Extract", style="magenta"))
        path = self._ask_path("Folder with videos", dir_only=True)
        if path is None:
            return
        vids = find_video_files(path)
        if not vids:
            self.console.print(f"  [red]No video files found in {path}[/]")
            return
        self._batch_extract(path, vids)

    def _batch_extract(self, folder: Path, videos: List[Path]) -> None:
        self.console.print()
        tbl = Table(box=box.SIMPLE, show_edge=False)
        tbl.add_column("#", style="dim", width=4, justify="right")
        tbl.add_column("File", min_width=30, no_wrap=True)
        tbl.add_column("Size", justify="right", width=10)
        for i, v in enumerate(videos, 1):
            tbl.add_row(str(i), v.name, format_size(v.stat().st_size))
            if i >= 25:
                tbl.add_row("…", f"[dim]+{len(videos) - 25} more[/]", "")
                break
        self.console.print(tbl)

        if not Confirm.ask(
            f"\n  Process all [bold]{len(videos)}[/] video(s)?", default=True
        ):
            return

        out_raw = Prompt.ask(
            "[bold]Output root[/]", default=self.cfg.output_dir
        ).strip()
        output_base = clean_path(out_raw) if out_raw else Path(self.cfg.output_dir)

        parallel = IntPrompt.ask(
            "[bold]Parallel jobs[/]", default=self.cfg.batch_parallel
        )
        self.console.print()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=35, complete_style="magenta",
                      finished_style="bold magenta"),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=self.console,
        ) as progress:
            task = progress.add_task(
                "[bold magenta]Batch", total=len(videos)
            )

            def _done(r: ExtractionResult) -> None:
                mark = "[green]✓[/]" if r.success else "[red]✗[/]"
                progress.console.print(
                    f"  {mark}  {r.video_path.name}  →  "
                    f"{r.extracted_frames:,} frames  "
                    f"({format_duration(r.processing_time)})"
                )
                progress.update(task, advance=1)

            self.extractor.reset()  # type: ignore[union-attr]
            results = self.extractor.extract_batch(  # type: ignore[union-attr]
                videos, output_base,
                self.cfg.format, self.cfg.overwrite,
                parallel,
                video_done_callback=_done,
            )

        ok = sum(1 for r in results if r.success)
        total_fr = sum(r.extracted_frames for r in results)
        total_t = sum(r.processing_time for r in results)

        self.console.print()
        self.console.print(Panel(
            f"[bold]Batch Complete[/]\n\n"
            f"  ✅  Succeeded   : [green]{ok}[/]\n"
            f"  ❌  Failed      : [red]{len(results) - ok}[/]\n"
            f"  🔢  Total frames: {total_fr:,}\n"
            f"  ⏱   Total time  : {format_duration(total_t)}\n"
            f"  📁  Output      : {output_base}",
            border_style="magenta", padding=(1, 2),
        ))
        for r in results:
            if r.success:
                self._add_history(r.video_path, r.output_dir, r)

    # ══════════════════════════════════════════════════════════════════
    #  4 — Inspect metadata
    # ══════════════════════════════════════════════════════════════════

    def _inspect(self) -> None:
        self.console.print(Rule("[bold yellow]Inspect Video", style="yellow"))
        path = self._ask_path("Video file", file_only=True)
        if path is None:
            return
        try:
            with self.console.status("[bold cyan]  Analysing …[/]"):
                meta = self.detector.detect(path)  # type: ignore[union-attr]
            self._show_meta(meta)
        except Exception as exc:
            self.console.print(f"  [red]Error: {exc}[/]")

    # ══════════════════════════════════════════════════════════════════
    #  5 — Preview
    # ══════════════════════════════════════════════════════════════════

    def _preview_menu(self) -> None:
        self.console.print(Rule("[bold]Preview Frames"))
        path = self._ask_path("Frame directory", dir_only=True)
        if path is None:
            return

        fmt = Prompt.ask(
            "[bold]Format[/]",
            choices=["png", "jpg", "bmp", "tiff"],
            default=self.cfg.format,
        )

        mode = Prompt.ask(
            "[bold]Mode[/]",
            choices=["viewer", "montage"],
            default="viewer",
        )

        self.console.print(
            "[dim]Viewer controls: ← →  navigate  ·  Space  play/pause  "
            "·  +/-  speed  ·  Q  quit[/]"
        )
        try:
            if mode == "viewer":
                self.previewer.preview_frames(path, fmt)
            else:
                self.previewer.show_montage(path, fmt)
        except ImportError as exc:
            self.console.print(f"  [red]{exc}[/]")
        except Exception as exc:
            self.console.print(f"  [red]Preview error: {exc}[/]")

    # ══════════════════════════════════════════════════════════════════
    #  6 — Validate
    # ══════════════════════════════════════════════════════════════════

    def _validate_menu(self) -> None:
        self.console.print(Rule("[bold]Validate Extraction"))
        frame_dir = self._ask_path("Frame directory", dir_only=True)
        if frame_dir is None:
            return

        self.console.print(
            "  [dim]Optionally provide the original video for count check.[/]"
        )
        video_path = self._ask_path(
            "Original video",
            file_only=True,
            default="",
            allow_browse=True,
        )
        self._run_validate(frame_dir, video_path)

    def _run_validate(
        self, frame_dir: Path, video_path: Optional[Path] = None
    ) -> None:
        try:
            with self.console.status("[bold cyan]  Validating …[/]"):
                report = self.extractor.validate_extraction(  # type: ignore
                    frame_dir,
                    video_path=video_path,
                    output_format=self.cfg.format,
                )
        except Exception as exc:
            self.console.print(f"  [red]Validation error: {exc}[/]")
            return

        if "error" in report:
            self.console.print(f"  [red]{report['error']}[/]")
            return

        tbl = Table(
            title="[bold]Validation Report[/]",
            box=box.ROUNDED, border_style="cyan",
        )
        tbl.add_column("Check", style="bold", width=22)
        tbl.add_column("Result", min_width=28)

        tbl.add_row("Files on disk", f"{report['total_files']:,}")
        if report["expected_frames"] is not None:
            tbl.add_row("Expected frames", f"{report['expected_frames']:,}")
            ok = report.get("count_match")
            tbl.add_row(
                "Count match",
                "[green]✓  Match[/]" if ok else "[red]✗  MISMATCH[/]",
            )
        tbl.add_row(
            "Naming continuous",
            "[green]✓  OK[/]" if report["naming_continuous"]
            else f"[red]✗  {len(report['missing_names'])} gap(s)[/]",
        )
        tbl.add_row(
            "Corrupted samples",
            "[green]✓  None[/]" if not report["corrupted_samples"]
            else f"[red]✗  {len(report['corrupted_samples'])} found[/]",
        )
        if "frame_resolution" in report:
            tbl.add_row("Frame resolution", report["frame_resolution"])
        if "resolution_match" in report:
            tbl.add_row(
                "Resolution match",
                "[green]✓  Match[/]" if report["resolution_match"]
                else "[red]✗  MISMATCH[/]",
            )
        overall = report.get("valid", False)
        tbl.add_row(
            "[bold]Overall[/]",
            "[bold green]✓  PASS[/]" if overall
            else "[bold red]✗  FAIL[/]",
        )
        self.console.print(tbl)

    # ══════════════════════════════════════════════════════════════════
    #  7 — History
    # ══════════════════════════════════════════════════════════════════

    def _load_history(self) -> None:
        try:
            if HISTORY_FILE.exists():
                data = json.loads(HISTORY_FILE.read_text())
                self.history = data if isinstance(data, list) else []
        except Exception:
            self.history = []

    def _save_history(self) -> None:
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(
                json.dumps(self.history[-50:], indent=2, default=str)
            )
        except Exception:
            pass

    def _add_history(
        self, src: Path, out: Path, result: ExtractionResult
    ) -> None:
        self.history.append({
            "input": str(src),
            "output": str(out),
            "frames": result.extracted_frames,
            "time": round(result.processing_time, 2),
            "success": result.success,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._save_history()

    def _history_menu(self) -> None:
        self.console.print(Rule("[bold]Extraction History"))
        if not self.history:
            self.console.print("  [dim]No history yet — extract some frames![/]")
            return

        tbl = Table(box=box.ROUNDED, border_style="cyan", show_lines=True)
        tbl.add_column("#", style="dim", width=4, justify="right")
        tbl.add_column("Input", min_width=22, no_wrap=True)
        tbl.add_column("Frames", justify="right", width=10)
        tbl.add_column("Time", width=9)
        tbl.add_column("When", width=18)
        tbl.add_column("OK", width=3, justify="center")

        items = list(reversed(self.history[-20:]))
        for i, h in enumerate(items, 1):
            tbl.add_row(
                str(i),
                Path(h.get("input", "?")).name,
                f"{h.get('frames', 0):,}",
                format_duration(h.get("time", 0)),
                h.get("timestamp", "?"),
                "[green]✓[/]" if h.get("success") else "[red]✗[/]",
            )
        self.console.print(tbl)

        self.console.print()
        raw = Prompt.ask(
            "[dim]Enter # to re-extract, or empty to go back[/]",
            default="",
        ).strip()
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(items):
                src = Path(items[idx]["input"])
                if src.is_file():
                    self._quick_extract(src)
                else:
                    self.console.print(f"  [red]File not found: {src}[/]")

    # ══════════════════════════════════════════════════════════════════
    #  8 — Settings & Presets
    # ══════════════════════════════════════════════════════════════════

    def _settings_menu(self) -> None:
        while True:
            self.console.print()
            self.console.print(
                Rule("[bold yellow]Settings & Presets", style="yellow")
            )

            tbl = Table(
                title="[bold]Current Settings[/]",
                box=box.ROUNDED, border_style="yellow",
            )
            tbl.add_column("Setting", style="bold", width=22)
            tbl.add_column("Value", min_width=30)
            tbl.add_row("Output directory", self.cfg.output_dir)
            tbl.add_row("Default format", self.cfg.format)
            tbl.add_row("FFmpeg threads", str(self.cfg.threads))
            tbl.add_row("PNG compression", f"{self.cfg.compression_level}  (0-9)")
            tbl.add_row("Batch parallel", str(self.cfg.batch_parallel))
            tbl.add_row("Overwrite", str(self.cfg.overwrite))
            tbl.add_row("Auto-validate", str(self.cfg.validate_after))
            self.console.print(tbl)

            m = Table(box=None, show_header=False, padding=(0, 2))
            m.add_column(style="bold yellow", width=4, justify="right")
            m.add_column()
            m.add_row("1", "Edit settings")
            m.add_row("2", "Save current as preset")
            m.add_row("3", "Load a preset")
            m.add_row("4", "List presets")
            m.add_row("5", "Delete a preset")
            m.add_row("0", "Back")
            self.console.print(m)

            try:
                c = Prompt.ask("[bold yellow]▶[/]", default="0").strip()
            except (KeyboardInterrupt, EOFError):
                break

            if c == "0":
                break
            elif c == "1":
                self._edit_settings()
            elif c == "2":
                name = Prompt.ask("  Preset name").strip()
                if name:
                    self.cm.save_preset(name, self.cfg)
                    self.console.print(f"  [green]✓  Preset '{name}' saved[/]")
            elif c == "3":
                presets = self.cm.list_presets()
                if not presets:
                    self.console.print("  [dim]No presets.[/]")
                    continue
                self.console.print(
                    "  Available: "
                    + "  ".join(f"[cyan]{p}[/]" for p in presets)
                )
                name = Prompt.ask("  Load").strip()
                loaded = self.cm.load_preset(name)
                if loaded:
                    self.cfg = loaded
                    self.cfg.ffmpeg_path, self.cfg.ffprobe_path = find_ffmpeg()
                    self._rebuild_engine()
                    self.console.print(f"  [green]✓  '{name}' loaded[/]")
                else:
                    self.console.print(f"  [red]Not found: '{name}'[/]")
            elif c == "4":
                presets = self.cm.list_presets()
                for p in (presets or ["(none)"]):
                    self.console.print(f"  •  [cyan]{p}[/]")
            elif c == "5":
                name = Prompt.ask("  Delete preset").strip()
                if self.cm.delete_preset(name):
                    self.console.print(f"  [green]✓  Deleted '{name}'[/]")
                else:
                    self.console.print(f"  [red]Not found: '{name}'[/]")

    def _edit_settings(self) -> None:
        self.console.print()
        self.cfg.output_dir = Prompt.ask(
            "  Output dir", default=self.cfg.output_dir
        )
        self.cfg.format = Prompt.ask(
            "  Default format",
            choices=["png", "jpg", "bmp", "tiff"],
            default=self.cfg.format,
        )
        self.cfg.threads = IntPrompt.ask(
            "  FFmpeg threads", default=self.cfg.threads
        )
        self.cfg.compression_level = IntPrompt.ask(
            "  PNG compression (0=fast, 9=small)",
            default=self.cfg.compression_level,
        )
        self.cfg.batch_parallel = IntPrompt.ask(
            "  Batch parallel jobs", default=self.cfg.batch_parallel
        )
        self.cfg.overwrite = Confirm.ask(
            "  Overwrite by default?", default=self.cfg.overwrite
        )
        self.cfg.validate_after = Confirm.ask(
            "  Auto-validate?", default=self.cfg.validate_after
        )
        self.cm.save_config(self.cfg)
        self._rebuild_engine()
        self.console.print("  [green]✓  Settings saved[/]")

    def _rebuild_engine(self) -> None:
        self.extractor = FrameExtractor(
            self.cfg.ffmpeg_path,
            self.cfg.ffprobe_path,
            self.cfg.threads,
            self.cfg.compression_level,
        )
        self.detector = VideoDetector(self.cfg.ffprobe_path)

    # ══════════════════════════════════════════════════════════════════
    #  Extraction with progress bar
    # ══════════════════════════════════════════════════════════════════

    def _extract_progress(
        self,
        video_path: Path,
        output_dir: Path,
        fmt: str,
        meta: Optional[VideoMetadata] = None,
    ) -> Optional[ExtractionResult]:
        if meta is None:
            try:
                meta = self.detector.detect(video_path)  # type: ignore
            except Exception as exc:
                self.console.print(f"  [red]{exc}[/]")
                return None

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(
                bar_width=40,
                complete_style="green",
                finished_style="bold green",
            ),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=self.console,
            transient=False,
        ) as progress:
            task = progress.add_task(
                f"[cyan]Extracting {video_path.name}",
                total=meta.total_frames,
            )

            def _cb(current: int, _total: int) -> None:
                progress.update(task, completed=current)

            self.extractor.reset()  # type: ignore[union-attr]
            result = self.extractor.extract_single(  # type: ignore[union-attr]
                video_path,
                output_dir,
                output_format=fmt,
                overwrite=self.cfg.overwrite,
                progress_callback=_cb,
            )
            progress.update(task, completed=result.extracted_frames)

        self._print_result(result)

        # auto-validate
        if result.success and self.cfg.validate_after:
            self._run_validate(output_dir, video_path)

        return result

    def _print_result(self, r: ExtractionResult) -> None:
        self.console.print()
        if r.success:
            match = "[green]✓  Match[/]" if r.frames_match else "[red]✗  MISMATCH[/]"
            self.console.print(Panel(
                "[bold green]✓  EXTRACTION COMPLETE[/]\n\n"
                f"  📊  Frames extracted : [bold]{r.extracted_frames:,}[/]\n"
                f"  🎯  Expected         : {r.expected_frames:,}\n"
                f"  📋  Count check      : {match}\n"
                f"  ⏱   Time             : {format_duration(r.processing_time)}\n"
                f"  ⚡  Speed            : {r.speed_fps:.1f} frames/sec\n"
                f"  📁  Output           : {r.output_dir}",
                border_style="green", padding=(1, 2),
            ))
        else:
            self.console.print(Panel(
                f"[bold red]✗  EXTRACTION FAILED[/]\n\n  {r.error}",
                border_style="red", padding=(1, 2),
            ))

    # ══════════════════════════════════════════════════════════════════
    #  Post-extraction actions
    # ══════════════════════════════════════════════════════════════════

    def _post_actions(
        self, output_dir: Path, video_path: Optional[Path] = None
    ) -> None:
        self.console.print()
        self.console.print("[bold]What next?[/]")
        m = Table(box=None, show_header=False, padding=(0, 2))
        m.add_column(style="bold cyan", width=4, justify="right")
        m.add_column()
        m.add_row("1", "✅  Validate extraction")
        m.add_row("2", "👁   Preview frames (viewer)")
        m.add_row("3", "🖼   Show frame montage")
        m.add_row("4", "📂  Open output folder")
        m.add_row("0", "↩   Back to main menu")
        self.console.print(m)

        try:
            c = Prompt.ask("[bold cyan]▶[/]", default="0").strip()
        except (KeyboardInterrupt, EOFError):
            return

        if c == "1":
            self._run_validate(output_dir, video_path)
        elif c == "2":
            try:
                self.previewer.preview_frames(output_dir, self.cfg.format)
            except ImportError as exc:
                self.console.print(f"  [red]{exc}[/]")
        elif c == "3":
            try:
                self.previewer.show_montage(output_dir, self.cfg.format)
            except ImportError as exc:
                self.console.print(f"  [red]{exc}[/]")
        elif c == "4":
            self._open_folder(output_dir)

    @staticmethod
    def _open_folder(path: Path) -> None:
        system = platform.system()
        try:
            if system == "Darwin":
                sp.run(["open", str(path)], check=False)
            elif system == "Linux":
                sp.run(["xdg-open", str(path)], check=False)
            elif system == "Windows":
                sp.run(["explorer", str(path)], check=False)
        except Exception:
            pass