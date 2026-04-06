#!/usr/bin/env python3
"""
Video to MP4 Converter
- Stream copy first (zero quality loss when compatible)
- Re-encode fallback (libx264 + AAC)
- Supports file and folder inputs (optional recursive scan)

Usage:
    python3 video_converter.py                                # Interactive mode
    python3 video_converter.py input.mkv                      # Convert one file
    python3 video_converter.py ./videos --recursive -o out    # Convert folder
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


SUPPORTED_EXTENSIONS = {
    ".mkv",
    ".webm",
    ".flv",
    ".avi",
    ".mov",
    ".wmv",
    ".mpg",
    ".mpeg",
    ".m4v",
    ".3gp",
    ".ts",
    ".vob",
    ".ogv",
    ".rm",
    ".rmvb",
    ".asf",
    ".m2ts",
    ".mts",
    ".divx",
    ".f4v",
    ".mp4",
}

DEFAULT_CRF = 18
DEFAULT_PRESET = "medium"
DEFAULT_AUDIO_BITRATE = "192k"

FFMPEG_PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
    "placebo",
)

MACOS_VIDEO_OK = {"h264", "hevc", "h265", "mpeg4", "mjpeg", "prores"}
MACOS_AUDIO_OK = {"aac", "mp3", "alac", "ac3", "pcm_s16le", "pcm_s24le"}


@dataclass(frozen=True)
class ConversionOptions:
    crf: int = DEFAULT_CRF
    preset: str = DEFAULT_PRESET
    audio_bitrate: str = DEFAULT_AUDIO_BITRATE
    allow_stream_copy: bool = True
    overwrite: bool = False
    verbose: bool = True


@dataclass(frozen=True)
class ConversionResult:
    source: Path
    output: Path | None
    mode: str
    success: bool
    elapsed: float
    input_size: int
    output_size: int
    detail: str = ""


def fmt_size(byte_count: int) -> str:
    value = float(byte_count)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{value:.2f} TB"


def fmt_time(seconds: float) -> str:
    minutes, rem_seconds = divmod(int(seconds), 60)
    hours, rem_minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{rem_minutes:02d}:{rem_seconds:02d}"
    return f"{rem_minutes}:{rem_seconds:02d}"


def short_tail(text: str, limit: int = 8) -> str:
    rows = [line for line in text.splitlines() if line.strip()]
    if not rows:
        return "(no ffmpeg output)"
    return "\n".join(rows[-limit:])


def normalize_input(raw: str) -> Path:
    cleaned = raw.strip().strip("'").strip('"').strip()
    cleaned = cleaned.replace("\\ ", " ")
    return Path(cleaned).expanduser().resolve()


def find_ffmpeg() -> str | None:
    executable = shutil.which("ffmpeg")
    if executable:
        return executable

    common_paths = [
        "/usr/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/opt/homebrew/bin/ffmpeg",
    ]
    for candidate in common_paths:
        if Path(candidate).is_file():
            return candidate
    return None


def find_ffprobe(ffmpeg_path: str) -> str | None:
    sibling = Path(ffmpeg_path).with_name("ffprobe")
    if sibling.is_file():
        return str(sibling)

    executable = shutil.which("ffprobe")
    if executable:
        return executable
    return None


def get_version(ffmpeg: str) -> str:
    try:
        result = subprocess.run(
            [ffmpeg, "-version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return "unknown"
        return (result.stdout.splitlines() or ["unknown"])[0].strip()
    except Exception:
        return "unknown"


def probe_codecs(ffmpeg: str, filepath: Path) -> dict[str, str | bool]:
    ffprobe = find_ffprobe(ffmpeg)
    if not ffprobe:
        return {"video": "unknown", "audio": "unknown", "copy_safe": False}

    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                str(filepath),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {"video": "unknown", "audio": "unknown", "copy_safe": False}

        data = json.loads(result.stdout or "{}")
        video_codec = ""
        audio_codec = ""

        for stream in data.get("streams", []):
            codec_name = stream.get("codec_name", "").lower()
            stream_type = stream.get("codec_type", "").lower()
            if stream_type == "video" and not video_codec:
                video_codec = codec_name
            elif stream_type == "audio" and not audio_codec:
                audio_codec = codec_name

        video_ok = video_codec in MACOS_VIDEO_OK or not video_codec
        audio_ok = audio_codec in MACOS_AUDIO_OK or not audio_codec

        return {
            "video": video_codec or "none",
            "audio": audio_codec or "none",
            "copy_safe": video_ok and audio_ok,
        }
    except Exception:
        return {"video": "unknown", "audio": "unknown", "copy_safe": False}


def make_output_path(input_path: Path, output_dir: Path | None, overwrite: bool) -> Path:
    target_dir = output_dir or input_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    output_path = target_dir / f"{input_path.stem}.mp4"
    if output_path.resolve() == input_path.resolve():
        output_path = target_dir / f"{input_path.stem}_converted.mp4"

    if overwrite or not output_path.exists():
        return output_path

    for index in range(1, 1000):
        candidate = target_dir / f"{input_path.stem}_{index}.mp4"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"Cannot create output filename for {input_path.name}")


def run_ffmpeg(ffmpeg: str, args: list[str], verbose: bool = False) -> tuple[int, str]:
    command = [ffmpeg] + args
    process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        bufsize=1,
    )

    logs: deque[str] = deque(maxlen=3000)
    if process.stderr is not None:
        for raw_line in process.stderr:
            line = raw_line.rstrip()
            if not line:
                continue

            logs.append(line)

            if verbose:
                lower_line = line.lower()
                if any(token in lower_line for token in ("time=", "speed=", "error", "fail")):
                    print(f"      {line[:160]}")

    process.wait()
    return process.returncode, "\n".join(logs)


def collect_input_files(inputs: Sequence[str], recursive: bool = False) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    warnings: list[str] = []
    seen: set[Path] = set()

    def add_file(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in seen and resolved.suffix.lower() in SUPPORTED_EXTENSIONS:
            seen.add(resolved)
            files.append(resolved)

    for raw in inputs:
        path = normalize_input(raw)

        if path.is_file():
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                add_file(path)
            else:
                warnings.append(f"Skipped unsupported file: {path}")
            continue

        if path.is_dir():
            try:
                entries = sorted(path.rglob("*") if recursive else path.iterdir())
            except OSError as error:
                warnings.append(f"Cannot read folder {path}: {error}")
                continue

            before_count = len(files)
            for entry in entries:
                if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTENSIONS:
                    add_file(entry)

            if len(files) == before_count:
                warnings.append(f"No supported videos found in folder: {path}")
            continue

        warnings.append(f"Path not found: {raw}")

    return files, warnings


def convert_file(
    input_path: Path,
    options: ConversionOptions,
    output_dir: Path | None = None,
    ffmpeg: str | None = None,
) -> ConversionResult:
    ffmpeg_bin = ffmpeg or find_ffmpeg()

    if not ffmpeg_bin:
        print("  ERROR: FFmpeg not found")
        return ConversionResult(
            source=input_path,
            output=None,
            mode="failed",
            success=False,
            elapsed=0.0,
            input_size=0,
            output_size=0,
            detail="FFmpeg not found",
        )

    source = input_path.resolve()
    if not source.is_file():
        print(f"  ERROR: File not found: {source}")
        return ConversionResult(
            source=source,
            output=None,
            mode="failed",
            success=False,
            elapsed=0.0,
            input_size=0,
            output_size=0,
            detail="Input file not found",
        )

    input_size = source.stat().st_size
    if input_size == 0:
        print("  ERROR: File size is 0 bytes")
        return ConversionResult(
            source=source,
            output=None,
            mode="failed",
            success=False,
            elapsed=0.0,
            input_size=0,
            output_size=0,
            detail="Input file is empty",
        )

    try:
        output_path = make_output_path(source, output_dir, options.overwrite)
    except Exception as error:
        print(f"  ERROR: {error}")
        return ConversionResult(
            source=source,
            output=None,
            mode="failed",
            success=False,
            elapsed=0.0,
            input_size=input_size,
            output_size=0,
            detail=str(error),
        )

    output_preexisting = output_path.exists()

    print("   Analyzing codecs...")
    codec_info = probe_codecs(ffmpeg_bin, source)
    print(
        f"   Video: {codec_info['video']}  |  "
        f"Audio: {codec_info['audio']}  |  "
        f"macOS-safe: {codec_info['copy_safe']}"
    )

    if options.allow_stream_copy and bool(codec_info["copy_safe"]):
        print("   Stream copying (no quality loss)...")
        start_copy = time.time()
        return_code, ffmpeg_log = run_ffmpeg(
            ffmpeg_bin,
            [
                "-y",
                "-i",
                str(source),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            verbose=options.verbose,
        )
        elapsed_copy = time.time() - start_copy

        if return_code == 0 and output_path.is_file() and output_path.stat().st_size > 0:
            output_size = output_path.stat().st_size
            ratio = (output_size / input_size * 100) if input_size else 0
            print("   COPY MODE SUCCESS (no quality loss)")
            print(
                f"   {fmt_size(input_size)} -> {fmt_size(output_size)} "
                f"({ratio:.1f}%) in {fmt_time(elapsed_copy)}"
            )
            print(f"   Output: {output_path}")
            return ConversionResult(
                source=source,
                output=output_path,
                mode="copy",
                success=True,
                elapsed=elapsed_copy,
                input_size=input_size,
                output_size=output_size,
                detail="",
            )

        if output_path.exists() and not output_preexisting:
            output_path.unlink(missing_ok=True)

        print("   Stream copy failed, falling back to re-encode...")
        if not options.verbose:
            print("   Copy failure detail:")
            print(f"      {short_tail(ffmpeg_log, 4).replace(chr(10), chr(10) + '      ')}")
    elif options.allow_stream_copy:
        print(
            f"   Codec {codec_info['video']}/{codec_info['audio']} is not macOS-compatible"
        )
        print("   Skipping stream copy, going straight to re-encode...")
    else:
        print("   --no-copy enabled, going straight to re-encode...")

    print(
        f"   Re-encoding (libx264 preset={options.preset} "
        f"CRF={options.crf} + AAC {options.audio_bitrate})..."
    )
    start_encode = time.time()
    return_code, ffmpeg_log = run_ffmpeg(
        ffmpeg_bin,
        [
            "-y",
            "-i",
            str(source),
            "-c:v",
            "libx264",
            "-preset",
            options.preset,
            "-crf",
            str(options.crf),
            "-c:a",
            "aac",
            "-b:a",
            options.audio_bitrate,
            "-movflags",
            "+faststart",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ],
        verbose=options.verbose,
    )
    elapsed_encode = time.time() - start_encode

    if return_code == 0 and output_path.is_file() and output_path.stat().st_size > 0:
        output_size = output_path.stat().st_size
        ratio = (output_size / input_size * 100) if input_size else 0
        print(f"   RE-ENCODE SUCCESS (high quality CRF {options.crf})")
        print(
            f"   {fmt_size(input_size)} -> {fmt_size(output_size)} "
            f"({ratio:.1f}%) in {fmt_time(elapsed_encode)}"
        )
        print(f"   Output: {output_path}")
        return ConversionResult(
            source=source,
            output=output_path,
            mode="reencode",
            success=True,
            elapsed=elapsed_encode,
            input_size=input_size,
            output_size=output_size,
            detail="",
        )

    if output_path.exists() and not output_preexisting:
        output_path.unlink(missing_ok=True)

    detail = short_tail(ffmpeg_log, 10)
    print("   FAILED. Last ffmpeg lines:")
    for line in detail.splitlines():
        print(f"      {line}")

    return ConversionResult(
        source=source,
        output=None,
        mode="failed",
        success=False,
        elapsed=elapsed_encode,
        input_size=input_size,
        output_size=0,
        detail=detail,
    )


def run_batch(
    inputs: Sequence[str],
    options: ConversionOptions,
    output_dir: str | None = None,
    recursive: bool = False,
) -> list[ConversionResult]:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        print("ERROR: FFmpeg not found")
        print("Install with: brew install ffmpeg")
        sys.exit(1)

    files, warnings = collect_input_files(inputs, recursive=recursive)

    for warning in warnings:
        print(f"Warning: {warning}")

    if not files:
        print("No supported video files to convert.")
        return []

    out_dir = Path(output_dir).expanduser().resolve() if output_dir else None

    print("Video -> MP4 Converter")
    print(f"FFmpeg: {get_version(ffmpeg)}")
    print(
        "Mode: "
        + (
            "probe codecs -> stream copy -> re-encode fallback"
            if options.allow_stream_copy
            else "re-encode only (--no-copy)"
        )
    )
    print(
        f"Encode settings: preset={options.preset}, CRF={options.crf}, "
        f"audio={options.audio_bitrate}"
    )
    if out_dir:
        print(f"Output folder: {out_dir}")
    if recursive:
        print("Folder scan: recursive")
    print(f"Input files: {len(files)}")
    print("=" * 64)

    results: list[ConversionResult] = []
    started_at = time.time()

    try:
        for index, file_path in enumerate(files, start=1):
            print(f"\n[{index}/{len(files)}] {file_path.name}")
            result = convert_file(file_path, options, out_dir, ffmpeg)
            results.append(result)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    ok_count = sum(result.success for result in results)
    fail_count = sum(not result.success for result in results)
    copy_count = sum(result.success and result.mode == "copy" for result in results)
    reencode_count = sum(result.success and result.mode == "reencode" for result in results)

    total_input = sum(result.input_size for result in results)
    total_output = sum(result.output_size for result in results if result.success)
    total_elapsed = time.time() - started_at

    print("\n" + "=" * 64)
    print(
        f"Done: {ok_count} success ({copy_count} copy, {reencode_count} re-encode), "
        f"{fail_count} failed"
    )
    if total_input > 0 and ok_count > 0:
        ratio = total_output / total_input * 100
        print(
            f"Total size: {fmt_size(total_input)} -> {fmt_size(total_output)} "
            f"({ratio:.1f}%)"
        )
    print(f"Total time: {fmt_time(total_elapsed)}")
    print("=" * 64)

    return results


def run_interactive(options: ConversionOptions, recursive_default: bool = False) -> None:
    ffmpeg = find_ffmpeg()

    print("=" * 56)
    print("  Video -> MP4 Converter (Interactive)")
    print("=" * 56)

    if ffmpeg:
        print(f"  FFmpeg: {get_version(ffmpeg)}")
    else:
        print("  ERROR: FFmpeg not found")
        print("  Install with: brew install ffmpeg")
        return

    print(
        "  Strategy: "
        + (
            "probe -> stream copy -> re-encode fallback"
            if options.allow_stream_copy
            else "re-encode only"
        )
    )
    print(
        f"  Encode settings: preset={options.preset}, "
        f"CRF={options.crf}, audio={options.audio_bitrate}"
    )
    print()
    print("  HOW TO USE:")
    print("  - Paste file/folder path to add videos")
    print("  - Type 'convert' to start conversion")
    print("  - Type 'help' for commands")
    print()

    file_list: list[Path] = []
    output_dir: Path | None = None
    recursive_scan = recursive_default

    while True:
        try:
            raw = input(f"  [{len(file_list)} file(s)] > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Bye!")
            break

        if not raw:
            continue

        cmd = raw.lower().strip()

        if cmd in {"q", "quit", "exit"}:
            print("  Bye!")
            break

        if cmd == "help":
            print("  Commands:")
            print("    convert            Start conversion")
            print("    list               Show added files")
            print("    clear              Clear file list")
            print("    output <path>      Set output folder")
            print("    recursive on|off   Toggle recursive folder scan")
            print("    settings           Show current settings")
            print("    quit               Exit")
            print("  Or paste a file/folder path to add it.")
            continue

        if cmd == "settings":
            print(
                f"  preset={options.preset}, crf={options.crf}, "
                f"audio={options.audio_bitrate}, stream_copy={options.allow_stream_copy}"
            )
            print(f"  recursive scan: {recursive_scan}")
            print(f"  output folder: {output_dir or '(same as source)'}")
            continue

        if cmd == "clear":
            file_list.clear()
            print("  File list cleared.")
            continue

        if cmd == "list":
            if not file_list:
                print("  (no files added yet)")
                continue

            for index, file_path in enumerate(file_list, start=1):
                size = fmt_size(file_path.stat().st_size) if file_path.exists() else "?"
                print(f"  {index}. {file_path.name} ({size})")
            continue

        if cmd.startswith("output "):
            output_value = raw.split(None, 1)[1].strip()
            output_dir = normalize_input(output_value)
            print(f"  Output folder: {output_dir}")
            continue

        if cmd in {"recursive on", "rec on"}:
            recursive_scan = True
            print("  Recursive folder scan: ON")
            continue

        if cmd in {"recursive off", "rec off"}:
            recursive_scan = False
            print("  Recursive folder scan: OFF")
            continue

        if cmd in {"convert", "run", "start", "go"}:
            if not file_list:
                print("  No files added. Paste a file or folder path first.")
                continue

            print()
            run_batch(
                [str(file_path) for file_path in file_list],
                options=options,
                output_dir=str(output_dir) if output_dir else None,
                recursive=False,
            )
            print()
            file_list.clear()
            continue

        added_files, warnings = collect_input_files([raw], recursive=recursive_scan)
        for warning in warnings:
            print(f"  {warning}")

        added_count = 0
        for file_path in added_files:
            if file_path in file_list:
                print(f"  Already added: {file_path.name}")
                continue

            file_list.append(file_path)
            added_count += 1
            print(f"  Added: {file_path.name} ({fmt_size(file_path.stat().st_size)})")

        if added_count:
            print("  Type 'convert' when ready.")


def validate_audio_bitrate(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized.endswith("k"):
        raise argparse.ArgumentTypeError(
            "audio bitrate must be in kbps format (e.g. 128k, 192k)"
        )
    if not normalized[:-1].isdigit():
        raise argparse.ArgumentTypeError(
            "audio bitrate must contain only digits before 'k'"
        )
    return normalized


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert videos to MP4 (copy first, re-encode fallback).",
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="Video files and/or folders",
    )
    parser.add_argument("-o", "--output", help="Output directory")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Scan folder inputs recursively",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=DEFAULT_CRF,
        help="x264 quality 0-51 (lower is better, default: 18)",
    )
    parser.add_argument(
        "--preset",
        choices=FFMPEG_PRESETS,
        default=DEFAULT_PRESET,
        help="x264 preset (speed vs compression)",
    )
    parser.add_argument(
        "--audio-bitrate",
        type=validate_audio_bitrate,
        default=DEFAULT_AUDIO_BITRATE,
        help="AAC bitrate, e.g. 128k or 192k",
    )
    parser.add_argument(
        "--no-copy",
        action="store_true",
        help="Skip stream copy and always re-encode",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite output file if it already exists",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Reduce ffmpeg progress output",
    )

    args = parser.parse_args()
    if args.crf < 0 or args.crf > 51:
        parser.error("--crf must be between 0 and 51")

    return args


def main() -> None:
    args = parse_arguments()
    options = ConversionOptions(
        crf=args.crf,
        preset=args.preset,
        audio_bitrate=args.audio_bitrate,
        allow_stream_copy=not args.no_copy,
        overwrite=args.overwrite,
        verbose=not args.quiet,
    )

    if args.inputs:
        run_batch(
            args.inputs,
            options=options,
            output_dir=args.output,
            recursive=args.recursive,
        )
    else:
        run_interactive(options, recursive_default=args.recursive)


if __name__ == "__main__":
    main()
