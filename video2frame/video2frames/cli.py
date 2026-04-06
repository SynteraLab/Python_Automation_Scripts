"""
CLI entry point for Video2Frames.

• **No arguments**  → launches the interactive TUI
• **With arguments** → runs in non-interactive (scripting) mode
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path
from typing import Optional

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
from rich.table import Table

from video2frames import __app_name__, __version__
from video2frames.config import Config, ConfigManager
from video2frames.engine import (
    ExtractionResult,
    FrameExtractor,
    VideoDetector,
)
from video2frames.preview import FramePreview
from video2frames.utils import (
    check_ffmpeg,
    check_ffprobe,
    find_ffmpeg,
    find_video_files,
    format_duration,
    format_size,
    get_ffmpeg_install_instructions,
    setup_logging,
)

console = Console()
_extractor: Optional[FrameExtractor] = None


def _sigint(sig, frame):
    if _extractor:
        _extractor.cancel()
    console.print("\n[bold yellow]⚠  Cancelled[/]")


signal.signal(signal.SIGINT, _sigint)


# ══════════════════════════════════════════════════════════════════════
#  Argument parser
# ══════════════════════════════════════════════════════════════════════

def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="video2frames",
        description=f"{__app_name__} v{__version__} — Video to Image Sequence",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m video2frames                        # TUI mode\n"
            "  python -m video2frames -i clip.mp4 -o ./out\n"
            "  python -m video2frames -i ./vids/ --batch\n"
            "  python -m video2frames --validate ./out/clip/\n"
            "  python -m video2frames --preview  ./out/clip/\n"
        ),
    )
    p.add_argument("--input", "-i", help="Video file or folder")
    p.add_argument("--output", "-o", default="./output")
    p.add_argument("--format", "-f", default="png",
                   choices=["png", "jpg", "bmp", "tiff"])
    p.add_argument("--threads", "-t", type=int, default=4)
    p.add_argument("--batch", "-b", action="store_true")
    p.add_argument("--parallel", "-p", type=int, default=2)
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--compression", type=int, default=3)
    p.add_argument("--validate", metavar="DIR")
    p.add_argument("--preview", metavar="DIR")
    p.add_argument("--config", metavar="FILE")
    p.add_argument("--preset")
    p.add_argument("--save-preset", metavar="NAME")
    p.add_argument("--no-validate", action="store_true")
    p.add_argument("--version", action="version",
                   version=f"%(prog)s {__version__}")
    return p


# ══════════════════════════════════════════════════════════════════════
#  Non-interactive mode
# ══════════════════════════════════════════════════════════════════════

def _run_cli(args: argparse.Namespace) -> None:
    cm = ConfigManager()

    # ── config ────────────────────────────────────────────────────────
    if args.config:
        cfg = cm.load_config(Path(args.config))
    elif args.preset:
        cfg = cm.load_preset(args.preset) or Config()
    else:
        cfg = cm.load_config()

    cfg.output_dir = args.output
    cfg.format = args.format
    cfg.threads = args.threads
    cfg.overwrite = args.overwrite
    cfg.compression_level = args.compression
    cfg.batch_parallel = args.parallel
    if args.no_validate:
        cfg.validate_after = False

    ff, fp = find_ffmpeg()
    cfg.ffmpeg_path, cfg.ffprobe_path = ff, fp

    if args.save_preset:
        cm.save_preset(args.save_preset, cfg)
        console.print(f"[green]Preset '{args.save_preset}' saved.[/]")

    # ── logging ───────────────────────────────────────────────────────
    log = setup_logging(Path(cfg.log_dir))
    console.print(f"[dim]Log → {log}[/]")

    # ── preflight ─────────────────────────────────────────────────────
    ok, info = check_ffmpeg(cfg.ffmpeg_path)
    if not ok:
        console.print(f"[red]✗  FFmpeg: {info}[/]")
        console.print(get_ffmpeg_install_instructions())
        sys.exit(1)
    console.print(f"[green]✓[/]  {info}")

    ok, info = check_ffprobe(cfg.ffprobe_path)
    if not ok:
        console.print(f"[red]✗  FFprobe: {info}[/]")
        sys.exit(1)
    console.print(f"[green]✓[/]  {info}")

    global _extractor
    extractor = FrameExtractor(
        cfg.ffmpeg_path, cfg.ffprobe_path,
        cfg.threads, cfg.compression_level,
    )
    _extractor = extractor
    detector = VideoDetector(cfg.ffprobe_path)

    # ── validate-only ─────────────────────────────────────────────────
    if args.validate:
        report = extractor.validate_extraction(
            Path(args.validate), output_format=cfg.format,
        )
        _print_validation(report)
        return

    # ── preview-only ──────────────────────────────────────────────────
    if args.preview:
        FramePreview().preview_frames(Path(args.preview), cfg.format)
        return

    # ── extraction ────────────────────────────────────────────────────
    if not args.input:
        console.print("[red]--input is required in non-interactive mode[/]")
        sys.exit(1)

    inp = Path(args.input)

    if args.batch or inp.is_dir():
        videos = find_video_files(inp)
        if not videos:
            console.print(f"[red]No videos in {inp}[/]")
            sys.exit(1)
        console.print(f"Found [bold]{len(videos)}[/] video(s).")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=30),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[blue]Batch", total=len(videos))

            def _done(r: ExtractionResult) -> None:
                s = "[green]✓[/]" if r.success else "[red]✗[/]"
                progress.console.print(
                    f"  {s}  {r.video_path.name}  →  "
                    f"{r.extracted_frames:,} frames"
                )
                progress.update(task, advance=1)

            results = extractor.extract_batch(
                videos, Path(cfg.output_dir),
                cfg.format, cfg.overwrite,
                cfg.batch_parallel,
                video_done_callback=_done,
            )

        ok = sum(1 for r in results if r.success)
        console.print(
            f"\n[bold]Done:[/]  [green]{ok} ok[/]  "
            f"[red]{len(results) - ok} failed[/]"
        )
    else:
        if not inp.is_file():
            console.print(f"[red]Not found: {inp}[/]")
            sys.exit(1)

        meta = detector.detect(inp)
        out = Path(cfg.output_dir) / inp.stem

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=40, complete_style="green"),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task(
                f"[cyan]{inp.name}", total=meta.total_frames
            )

            def _cb(cur: int, tot: int) -> None:
                progress.update(task, completed=cur)

            result = extractor.extract_single(
                inp, out, cfg.format, cfg.overwrite, _cb,
            )
            progress.update(task, completed=result.extracted_frames)

        if result.success:
            console.print(Panel(
                f"[green]✓  {result.extracted_frames:,} frames[/]  "
                f"in {format_duration(result.processing_time)}  →  {out}",
                border_style="green",
            ))
            if cfg.validate_after:
                report = extractor.validate_extraction(
                    out, video_path=inp, output_format=cfg.format,
                )
                _print_validation(report)
        else:
            console.print(f"[red]✗  {result.error}[/]")
            sys.exit(1)


def _print_validation(report: dict) -> None:
    tbl = Table(box=box.SIMPLE_HEAVY)
    tbl.add_column("Check", style="bold")
    tbl.add_column("Result")
    tbl.add_row("Files", f"{report.get('total_files', '?'):,}")
    if report.get("expected_frames") is not None:
        tbl.add_row(
            "Count match",
            "[green]✓[/]" if report.get("count_match")
            else "[red]✗[/]",
        )
    tbl.add_row(
        "Naming",
        "[green]✓[/]" if report.get("naming_continuous")
        else "[red]✗[/]",
    )
    tbl.add_row(
        "Overall",
        "[bold green]PASS[/]" if report.get("valid")
        else "[bold red]FAIL[/]",
    )
    console.print(tbl)


# ══════════════════════════════════════════════════════════════════════
#  Entry point
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    parser = _parser()
    args = parser.parse_args()

    has_action = any([args.input, args.validate, args.preview])

    if has_action:
        _run_cli(args)
    else:
        # ── launch TUI ────────────────────────────────────────────────
        from video2frames.tui import VideoTUI
        VideoTUI().run()


if __name__ == "__main__":
    main()