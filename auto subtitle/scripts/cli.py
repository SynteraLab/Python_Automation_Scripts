#!/usr/bin/env python3
"""
CLI tool for Auto Subtitle AI — with translation support.

Usage:
    # Transkrip saja (bahasa asli)
    python scripts/cli.py generate video.mp4

    # Jepang → Indonesia
    python scripts/cli.py generate anime.mp4 --translate id

    # Jepang → Inggris
    python scripts/cli.py generate anime.mp4 --translate en

    # Dengan opsi lengkap
    python scripts/cli.py generate anime.mp4 --language ja --translate id --format ass --style netflix

    # Batch processing dengan terjemahan
    python scripts/cli.py batch ./anime_folder/ --translate id

    # Live microphone
    python scripts/cli.py live
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse

from tqdm import tqdm

from app.core.logging_config import setup_logging


def _resolve_sync_mode(args: argparse.Namespace):
    sync_mode = getattr(args, "sync_mode", None)
    if sync_mode:
        return sync_mode
    if getattr(args, "no_sync", False):
        return "off"
    return None


def _parse_render_size(value: str | None) -> tuple[int, int] | tuple[None, None]:
    if not value:
        return None, None
    cleaned = value.strip().lower().replace(" ", "")
    if "x" not in cleaned:
        raise ValueError("--render-size must use WxH format, for example 1280x720")
    width_text, height_text = cleaned.split("x", 1)
    if not width_text.isdigit() or not height_text.isdigit():
        raise ValueError("--render-size must use numeric WxH values")
    return int(width_text), int(height_text)


def cmd_generate(args: argparse.Namespace) -> None:
    """Generate subtitles for a single file."""
    from app.models.schemas import StylePreset, SubtitleFormat
    from app.services.subtitle_service import SubtitleService

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ File not found: {input_path}")
        sys.exit(1)

    fmt = SubtitleFormat(args.format)
    preset = StylePreset(args.style)
    svc = SubtitleService(model_size=args.model)

    # Info terjemahan
    if args.translate:
        print(f"🌐 Translation enabled: → {args.translate}")
    video_mode = "hard" if args.hard_subtitle else "soft" if args.embed_video else "none"
    if video_mode != "none":
        file_mode = "in-place" if args.in_place else "new video file"
        if video_mode == "hard":
            strategy = svc.describe_hard_subtitle_strategy()
            print(f"🔥 Hard subtitle: enabled ({file_mode}, {strategy})")
        else:
            print(f"🎞️  Soft subtitle: enabled ({file_mode}, no re-encode)")

    if args.in_place and video_mode == "none":
        print("❌ --in-place requires --embed-video or --hard-subtitle")
        sys.exit(2)
    if args.no_subtitle_file and video_mode == "none":
        print("❌ --no-subtitle-file requires --embed-video or --hard-subtitle")
        sys.exit(2)

    try:
        render_width, render_height = _parse_render_size(getattr(args, "render_size", None))
    except ValueError as exc:
        print(f"❌ {exc}")
        sys.exit(2)

    sync_mode = _resolve_sync_mode(args)

    pbar = tqdm(total=100, desc="Generating subtitles", unit="%", ncols=80)

    def _progress(pct: float, msg: str) -> None:
        pbar.update(pct - pbar.n)
        pbar.set_postfix_str(msg[:40])

    output = Path(args.output) if args.output else None

    try:
        result_path = svc.generate_from_file(
            input_path=input_path,
            output_path=output,
            language=args.language,
            fmt=fmt,
            style_preset=preset,
            apply_sync=not args.no_sync,
            sync_mode=sync_mode,
            beam_size=args.beam_size,
            initial_prompt=args.prompt,
            translate_to=args.translate,
            embed_subtitle=(video_mode == "soft"),
            hard_subtitle=(video_mode == "hard"),
            overwrite_video=args.in_place,
            keep_subtitle_file=not args.no_subtitle_file,
            hard_subtitle_encoder=args.hard_subtitle_encoder,
            hard_subtitle_crf=args.hard_subtitle_crf,
            hard_subtitle_preset=args.hard_subtitle_preset,
            render_width=render_width,
            render_height=render_height,
            progress_callback=_progress,
        )
    except Exception as exc:
        pbar.close()
        print(f"\n❌ Error: {exc}")
        sys.exit(1)

    pbar.close()
    if video_mode == "hard":
        print(f"\n✅ Video with hard subtitle saved → {result_path}")
    elif video_mode == "soft":
        print(f"\n✅ Video with soft subtitle saved → {result_path}")
    else:
        print(f"\n✅ Subtitle saved → {result_path}")


def cmd_batch(args: argparse.Namespace) -> None:
    """Process all media files in a folder."""
    from app.models.schemas import StylePreset, SubtitleFormat
    from app.services.subtitle_service import SubtitleService
    from app.utils.file_manager import FileManager

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"❌ Not a directory: {folder}")
        sys.exit(1)

    fm = FileManager()
    files = fm.scan_folder(folder)
    if not files:
        print("⚠️  No media files found.")
        return

    print(f"📁 Found {len(files)} files in {folder}")
    if args.translate:
        print(f"🌐 Translation: → {args.translate}")

    fmt = SubtitleFormat(args.format)
    preset = StylePreset(args.style)
    svc = SubtitleService(model_size=args.model)
    sync_mode = _resolve_sync_mode(args)

    succeeded, failed = 0, 0
    for i, fpath in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}] {fpath.name}")
        pbar = tqdm(total=100, desc="  Processing", unit="%", ncols=80)

        def _progress(pct: float, msg: str) -> None:
            pbar.update(pct - pbar.n)
            pbar.set_postfix_str(msg[:40])

        try:
            out_dir = Path(args.output_dir) if args.output_dir else fpath.parent
            suffix = f"_{args.translate}" if args.translate else ""
            out_path = out_dir / f"{fpath.stem}{suffix}.{fmt.value}"

            svc.generate_from_file(
                input_path=fpath,
                output_path=out_path,
                language=args.language,
                fmt=fmt,
                style_preset=preset,
                apply_sync=not args.no_sync,
                sync_mode=sync_mode,
                translate_to=args.translate,    # ← BARU!
                progress_callback=_progress,
            )
            succeeded += 1
            pbar.close()
            print(f"  ✅ {out_path}")
        except Exception as exc:
            failed += 1
            pbar.close()
            print(f"  ❌ Failed: {exc}")

    print(f"\n🏁 Done: {succeeded} succeeded, {failed} failed")


def cmd_live(args: argparse.Namespace) -> None:
    """Live microphone transcription."""
    try:
        import sounddevice as sd
    except ImportError:
        print("❌ Install sounddevice: pip install sounddevice")
        sys.exit(1)

    from app.services.realtime_service import RealtimeServiceFactory

    session = RealtimeServiceFactory.create_session(
        model_size=args.model,
        language=args.language,
        sample_rate=16000,
    )

    print("🎤 Live transcription started. Press Ctrl+C to stop.\n")

    def _audio_callback(indata, frames, time_info, status):
        if status:
            print(f"⚠️  {status}", file=sys.stderr)
        pcm = indata.tobytes()
        messages = session.feed_audio(pcm)
        for msg in messages:
            print(f"[{msg.start:.1f}s → {msg.end:.1f}s] {msg.text}")

    try:
        with sd.InputStream(
            samplerate=16000,
            channels=1,
            dtype="int16",
            blocksize=int(16000 * 0.5),
            callback=_audio_callback,
        ):
            print("(listening…)\n")
            while True:
                sd.sleep(100)
    except KeyboardInterrupt:
        for msg in session.flush():
            print(f"[{msg.start:.1f}s → {msg.end:.1f}s] {msg.text}")
        print("\n🛑 Stopped.")


def cmd_translate(args: argparse.Namespace) -> None:
    """Translate an existing subtitle file."""
    from app.engines.translator import TranslationEngine

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"❌ File not found: {input_path}")
        sys.exit(1)

    print(f"🌐 Translating: {args.source} → {args.target}")
    print(f"📄 Input: {input_path}")

    # Read subtitle file
    import pysubs2
    subs = pysubs2.load(str(input_path))

    translator = TranslationEngine(
        source_lang=args.source,
        target_lang=args.target,
    )

    total = len(subs.events)
    pbar = tqdm(total=total, desc="Translating", unit="line", ncols=80)

    for event in subs.events:
        if event.text.strip():
            # Remove ASS formatting tags for translation
            clean_text = event.plaintext if hasattr(event, 'plaintext') else event.text
            translated = translator.translate_text(clean_text)
            event.text = translated
        pbar.update(1)

    pbar.close()

    # Save output
    if args.output:
        output_path = Path(args.output)
    else:
        output_path = input_path.parent / f"{input_path.stem}_{args.target}{input_path.suffix}"

    subs.save(str(output_path))
    print(f"\n✅ Translated subtitle saved → {output_path}")


def main() -> None:
    setup_logging()

    parser = argparse.ArgumentParser(
        prog="subtitle-cli",
        description="Auto Subtitle AI — CLI with Translation",
    )
    parser.add_argument("--model", default=None, help="Whisper model size")

    sub = parser.add_subparsers(dest="command", required=True)

    # ── generate ─────────────────────────────────────────────
    p_gen = sub.add_parser("generate", help="Generate subtitles for one file")
    p_gen.add_argument("input", help="Input media file")
    p_gen.add_argument("-o", "--output", default=None, help="Output path")
    p_gen.add_argument("-l", "--language", default=None, help="Source language (auto-detect if empty)")
    p_gen.add_argument("-f", "--format", default="srt", choices=["srt", "ass"])
    p_gen.add_argument("-s", "--style", default="netflix", choices=["netflix", "minimal", "custom"])
    p_gen.add_argument("--no-sync", action="store_true", help="Skip sync correction")
    p_gen.add_argument("--sync-mode", default=None, choices=["off", "light", "full"], help="Sync correction mode")
    p_gen.add_argument("--beam-size", type=int, default=None, help="Override transcription beam size")
    p_gen.add_argument("--prompt", default=None, help="Initial prompt or context hint")
    p_gen.add_argument(
        "--embed-video",
        action="store_true",
        help="Embed soft subtitle track into video without re-encoding",
    )
    p_gen.add_argument(
        "--hard-subtitle",
        action="store_true",
        help="Burn hard subtitle permanently into video (re-encode video)",
    )
    p_gen.add_argument(
        "--in-place",
        action="store_true",
        help="Replace original video file (only valid with --embed-video)",
    )
    p_gen.add_argument(
        "--no-subtitle-file",
        action="store_true",
        help="Remove generated .srt/.ass after embedding subtitle into video",
    )
    p_gen.add_argument("--hard-subtitle-encoder", default=None, help="Override encoder for hard subtitle rendering")
    p_gen.add_argument("--hard-subtitle-crf", type=int, default=None, help="Override CRF for hard subtitle rendering")
    p_gen.add_argument("--hard-subtitle-preset", default=None, help="Override preset for hard subtitle rendering")
    p_gen.add_argument("--render-size", default=None, help="Target hard subtitle render size in WxH format")
    p_gen.add_argument(
        "-t", "--translate", default=None,
        help="Translate to language code (e.g., id=Indonesian, en=English)",
    )

    # ── batch ────────────────────────────────────────────────
    p_batch = sub.add_parser("batch", help="Batch process a folder")
    p_batch.add_argument("folder", help="Folder with media files")
    p_batch.add_argument("--output-dir", default=None)
    p_batch.add_argument("-l", "--language", default=None)
    p_batch.add_argument("-f", "--format", default="srt", choices=["srt", "ass"])
    p_batch.add_argument("-s", "--style", default="netflix", choices=["netflix", "minimal", "custom"])
    p_batch.add_argument("--no-sync", action="store_true")
    p_batch.add_argument("--sync-mode", default=None, choices=["off", "light", "full"], help="Sync correction mode")
    p_batch.add_argument(
        "-t", "--translate", default=None,
        help="Translate to language code",
    )

    # ── live ─────────────────────────────────────────────────
    p_live = sub.add_parser("live", help="Live microphone transcription")
    p_live.add_argument("-l", "--language", default=None)

    # ── translate (subtitle file saja) ───────────────────────
    p_trans = sub.add_parser("translate", help="Translate existing subtitle file")
    p_trans.add_argument("input", help="Input subtitle file (.srt or .ass)")
    p_trans.add_argument("-o", "--output", default=None, help="Output path")
    p_trans.add_argument("--source", default="ja", help="Source language code")
    p_trans.add_argument("--target", default="id", help="Target language code")

    args = parser.parse_args()

    commands = {
        "generate": cmd_generate,
        "batch": cmd_batch,
        "live": cmd_live,
        "translate": cmd_translate,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
