tui/intelligence/advisor.py
# pyright: reportOptionalMemberAccess=false, reportPossiblyUnboundVariable=false, reportOptionalCall=false, reportAttributeAccessIssue=false, reportCallIssue=false
"""
Interactive TUI (Text User Interface) for Universal Media Downloader.
Provides menu-driven interface with Rich library for beautiful terminal UI.

Smart download flow:
1. Custom extractors (PubJav, SupJav, JWPlayer, HLS, Social)
2. Direct yt-dlp backend for social platforms and fallback for 1000+ sites
3. Generic HTML extractor
"""

import os
import sys
import asyncio
import shutil
import re
from pathlib import Path
from typing import Any, Optional, List
import logging

logger = logging.getLogger(__name__)

Console = None
Table = None
Panel = None
Prompt = None
Confirm = None
Rule = None
box = None
rprint = None

# Check Rich availability
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt, Confirm
    from rich.rule import Rule
    from rich import box
    from rich import print as rprint
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from config import Config, setup_logging
from utils.network import SessionManager
from core.downloader import Downloader
from core.history import DownloadHistory
from core.erome_download import (
    download_erome_jobs,
    erome_photo_parallel_workers,
    erome_video_uses_aria2,
    prepare_erome_download_jobs,
)
from models.media import MediaInfo, StreamType
from utils.helpers import sanitize_filename
from extractors.base import registry
import extractors  # Register built-in extractors


def check_rich():
    if not RICH_AVAILABLE:
        print("Interactive mode requires 'rich' library.")
        print("Install: pip install rich")
        print("Or use CLI mode: python main.py download \"URL\"")
        sys.exit(1)


console: Any = Console() if RICH_AVAILABLE else None


def _compact_error_message(message: str, limit: int = 120) -> str:
    text = (message or '').strip()
    if not text:
        return ''
    if 'Cloudflare' in text or '\n' in text:
        return text
    return text[:limit]


def _human_size(num_bytes: float) -> str:
    """Format bytes into human-readable size."""
    size = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024.0
        unit_idx += 1

    if unit_idx == 0:
        return f"{int(size)}{units[unit_idx]}"
    return f"{size:.1f}{units[unit_idx]}"


def _estimated_filesize_bytes(fmt, duration_seconds: Optional[int]) -> Optional[int]:
    """Estimate filesize from bitrate + duration when exact size is unavailable."""
    if fmt.filesize and fmt.filesize > 0:
        return int(fmt.filesize)

    if not duration_seconds or duration_seconds <= 0:
        return None

    if not fmt.bitrate or fmt.bitrate <= 0:
        return None

    # bitrate in kbps -> bytes
    return int((fmt.bitrate * 1000 / 8) * duration_seconds)


def _guess_bitrate_kbps(fmt) -> Optional[int]:
    """Try to derive bitrate when extractor doesn't set it explicitly."""
    if fmt.bitrate and fmt.bitrate > 0:
        return int(fmt.bitrate)

    candidates = [fmt.format_id or "", fmt.quality or "", fmt.label or ""]
    for text in candidates:
        match = re.search(r"(\d{3,5})\s*kbps", text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))

    # Common pattern from some extractors: hls-3611-0 (bitrate ~3611 kbps)
    match = re.search(r"(?:^|[-_])hls-(\d{3,5})(?:[-_]|$)", (fmt.format_id or "").lower())
    if match:
        return int(match.group(1))

    return None


def _probe_hls_duration_seconds(fmt, timeout_seconds: int = 12) -> Optional[int]:
    """Probe HLS playlist and read total duration from EXTINF entries."""
    if fmt.stream_type != StreamType.HLS:
        return None

    try:
        import requests
        from extractors.hls import HLSParser

        headers = dict(fmt.headers or {})
        headers.setdefault("User-Agent", "Mozilla/5.0")

        response = requests.get(fmt.url, headers=headers, timeout=timeout_seconds)
        response.raise_for_status()
        parser = HLSParser(fmt.url, response.text)

        if parser.is_master_playlist():
            variants = parser.parse_master_playlist()
            video_variants = [
                v for v in variants
                if v.get('is_video', True) and isinstance(v.get('url'), str)
            ]
            if video_variants:
                if fmt.height:
                    chosen = min(
                        video_variants,
                        key=lambda v: abs((v.get('height') or fmt.height) - fmt.height),
                    )
                else:
                    chosen = max(
                        video_variants,
                        key=lambda v: (v.get('height') or 0, v.get('bitrate') or 0),
                    )

                variant_url = chosen.get('url')
                if isinstance(variant_url, str) and variant_url:
                    response = requests.get(variant_url, headers=headers, timeout=timeout_seconds)
                    response.raise_for_status()
                    parser = HLSParser(variant_url, response.text)

        media_info = parser.parse_media_playlist()
        total_duration = media_info.get('total_duration')
        if total_duration and total_duration > 0:
            return int(total_duration)
    except Exception as e:
        logger.debug(f"HLS duration probe failed for {fmt.format_id}: {e}")

    return None


def _resolve_duration_for_table(formats, duration_seconds: Optional[int]) -> Optional[int]:
    """Resolve duration from metadata; fallback to HLS probing."""
    if duration_seconds and duration_seconds > 0:
        return int(duration_seconds)

    for fmt in formats:
        if fmt.stream_type != StreamType.HLS:
            continue
        probed = _probe_hls_duration_seconds(fmt)
        if probed and probed > 0:
            return probed

    return None


def _format_size_cell(fmt, duration_seconds: Optional[int], bitrate_kbps: Optional[int] = None) -> str:
    """Return displayable size text for format table."""
    if fmt.filesize and fmt.filesize > 0:
        return _human_size(fmt.filesize)

    effective_bitrate = bitrate_kbps if bitrate_kbps and bitrate_kbps > 0 else fmt.bitrate
    if effective_bitrate:
        # temporary fallback for estimator helper (expects fmt.bitrate)
        class _Tmp:
            bitrate = effective_bitrate
            filesize = None

        estimate = _estimated_filesize_bytes(_Tmp, duration_seconds)
    else:
        estimate = None

    if estimate:
        return f"~{_human_size(estimate)}"

    # For HLS/DASH we often only know bitrate, not exact total file size.
    if effective_bitrate and effective_bitrate > 0:
        return f"~{effective_bitrate}kbps"

    return "unknown"


def _ordered_formats(formats) -> List:
    """Sort formats for user-facing selection."""
    stream_rank = {
        StreamType.DIRECT: 3,
        StreamType.PROGRESSIVE: 3,
        StreamType.HLS: 2,
        StreamType.DASH: 1,
    }
    return sorted(
        list(formats),
        key=lambda f: (f.quality_score, stream_rank.get(f.stream_type, 0)),
        reverse=True,
    )


def _pick_format(media_info: MediaInfo, quality: str, audio_only: bool = False):
    """Always show format table and allow interactive format selection."""
    if audio_only:
        audio_formats = media_info.get_audio_formats()
        candidates = audio_formats if audio_formats else media_info.formats
    else:
        candidates = media_info.formats

    ordered = _ordered_formats(candidates)
    if not ordered:
        return None

    _show_format_table(ordered, media_info.duration)
    selected = _prompt_format_selection(ordered)
    if selected:
        return selected

    if audio_only:
        return ordered[0]

    quality_text = (quality or "best").strip().lower()
    if quality_text == "best":
        return ordered[0]
    if quality_text == "worst":
        return ordered[-1]

    quality_match = media_info.get_format_by_quality(quality_text)
    if quality_match and quality_match in ordered:
        return quality_match

    return ordered[0]


def _show_format_table(formats, duration_seconds: Optional[int] = None) -> None:
    """Render available formats table for interactive selection."""
    resolved_duration = _resolve_duration_for_table(formats, duration_seconds)

    bitrate_overrides = {}
    for fmt in formats:
        guessed = _guess_bitrate_kbps(fmt)
        if guessed and guessed > 0:
            bitrate_overrides[fmt.format_id] = guessed

    table = Table(title=f"Available Formats ({len(formats)})", show_header=True, border_style="blue")
    table.add_column("No", width=4, justify="right", style="bold yellow")
    table.add_column("ID", width=18, style="cyan")
    table.add_column("Resolution", width=12)
    table.add_column("Quality", width=12)
    table.add_column("Type", width=8)
    table.add_column("Size", width=10)
    table.add_column("Note", overflow="fold")

    for idx, fmt in enumerate(formats, 1):
        bitrate_guess = bitrate_overrides.get(fmt.format_id)
        size = _format_size_cell(fmt, resolved_duration, bitrate_kbps=bitrate_guess)
        resolution = fmt.resolution
        if resolution == "unknown" and (not fmt.is_video and fmt.is_audio):
            resolution = "audio"

        table.add_row(
            str(idx),
            fmt.format_id,
            resolution,
            fmt.quality or "",
            fmt.stream_type.value,
            size,
            fmt.label or "",
        )

    console.print(table)


def _prompt_format_selection(formats):
    """Ask user to pick format by index or ID. Enter = default best."""
    if not sys.stdin.isatty():
        return None

    choice = Prompt.ask(
        "[bold cyan]Pilih format (No/ID), Enter = best quality[/bold cyan]",
        default=""
    ).strip()

    if not choice:
        return None

    choice_lower = choice.lower()
    for fmt in formats:
        if (fmt.format_id or "").lower() == choice_lower:
            return fmt

    numeric_match = re.fullmatch(r"(?:no\s*)?#?\s*(\d+)\s*[\.)]?\s*", choice_lower)
    if numeric_match:
        idx = int(numeric_match.group(1))
        if 1 <= idx <= len(formats):
            return formats[idx - 1]

    console.print(f"[yellow]⚠ Format '{choice}' tidak valid, pakai best quality[/yellow]")
    return None


def smart_download(url: str, config: Config, quality: str = "best",
                   audio_only: bool = False, cookies_browser: Optional[str] = None) -> bool:
    """
    Smart download: try custom extractors → yt-dlp → generic.
    Returns True if successful.
    """
    history = DownloadHistory()
    last_error_message: Optional[str] = None
    from extractors.ytdlp import YtdlpExtractor, YTDLP_AVAILABLE
    
    # Check if already downloaded
    if history.is_downloaded(url):
        console.print(f"[yellow]⚠ Already downloaded: {url}[/yellow]")
        if not Confirm.ask("Download again?", default=False):
            return False

    console.print(f"\n[bold cyan]🔗 {url}[/bold cyan]")

    # === Step 1: Try custom extractors ===
    session = SessionManager(
        user_agent=config.extractor.user_agent,
        proxy=config.proxy.to_dict(),
        cookies_file=config.cookies_file,
        cookies_from_browser=cookies_browser or config.cookies_from_browser,
    )

    try:
        extractor_class = registry.find_extractor(url)
        if extractor_class and extractor_class.EXTRACTOR_NAME != "generic":
            console.print(f"[dim]Using extractor: {extractor_class.EXTRACTOR_NAME}[/dim]")
            try:
                ext_config = dict(vars(config))
                if cookies_browser:
                    ext_config['cookies_from_browser'] = cookies_browser
                extractor = extractor_class(session, config=ext_config)
                media_info = extractor.extract(url)

                if media_info and media_info.formats:
                    console.print(f"[bold]{media_info.title}[/bold]")
                    console.print(f"[dim]Found {len(media_info.formats)} format(s)[/dim]")
                    fmt = _pick_format(media_info, quality=quality, audio_only=audio_only)

                    if fmt:
                        console.print(f"[dim]Format: {fmt.format_note}[/dim]")

                        if YtdlpExtractor.uses_direct_backend(media_info):
                            console.print("[dim]Mode: yt-dlp direct backend[/dim]")
                            output = YtdlpExtractor.download_media_info(
                                media_info,
                                ext_config,
                                selected_format=fmt,
                                quality=quality,
                                audio_only=audio_only,
                                display_name=media_info.title,
                            )
                            if not output:
                                raise RuntimeError("Direct yt-dlp download returned no file")
                        else:
                            downloader = Downloader(config, session=session)
                            try:
                                output = asyncio.run(downloader.download(media_info, fmt))
                            except Exception as e:
                                fallback_fmt = None
                                if fmt.stream_type == StreamType.HLS:
                                    direct_candidates = [
                                        f for f in media_info.formats
                                        if f.format_id != fmt.format_id and f.stream_type != StreamType.HLS
                                    ]
                                    if direct_candidates:
                                        direct_candidates.sort(key=lambda f: f.quality_score, reverse=True)
                                        fallback_fmt = direct_candidates[0]

                                if not fallback_fmt:
                                    raise

                                console.print(
                                    "[dim]Primary HLS failed, retrying with "
                                    f"{fallback_fmt.format_note}[/dim]"
                                )
                                output = asyncio.run(downloader.download(media_info, fallback_fmt))
                                fmt = fallback_fmt

                        console.print(f"\n[green]✓ Downloaded: {output}[/green]")

                        history.record(
                            url=url, title=media_info.title,
                            extractor=media_info.extractor,
                            quality=fmt.quality or '',
                            filepath=output, status="completed"
                        )
                        return True
            except Exception as e:
                last_error_message = str(e)
                console.print(f"[dim]Custom extractor failed: {_compact_error_message(str(e))}[/dim]")
                logger.debug(f"Custom extractor error: {e}")
    finally:
        session.close()

    # === Step 2: Try yt-dlp ===
    if YTDLP_AVAILABLE:
        console.print(f"[dim]Trying yt-dlp...[/dim]")
        yt_session = None
        try:
            yt_session = SessionManager(
                user_agent=config.extractor.user_agent,
                proxy=config.proxy.to_dict(),
                cookies_file=config.cookies_file,
                cookies_from_browser=cookies_browser or config.cookies_from_browser,
            )

            yt_config = dict(vars(config))
            if cookies_browser:
                yt_config['cookies_from_browser'] = cookies_browser
            yt_ext = YtdlpExtractor(yt_session, config=yt_config)
            yt_info = yt_ext.extract(url)

            format_selector = None
            if yt_info and yt_info.formats:
                console.print(f"[bold]{yt_info.title}[/bold]")
                console.print(f"[dim]Found {len(yt_info.formats)} format(s)[/dim]")
                yt_fmt = _pick_format(yt_info, quality=quality, audio_only=audio_only)

                if yt_fmt:
                    console.print(f"[dim]Format: {yt_fmt.format_note}[/dim]")
                    format_selector = YtdlpExtractor.build_format_selector(
                        yt_info,
                        yt_fmt,
                        audio_only=audio_only,
                    )

            filepath = YtdlpExtractor.download_with_ytdlp(
                url=url,
                output_dir=config.download.output_dir,
                quality=quality,
                audio_only=audio_only,
                format_selector=format_selector,
                cookies_browser=cookies_browser or config.cookies_from_browser,
                cookies_file=config.cookies_file,
                proxy=config.proxy.http or config.proxy.https or config.proxy.socks5,
                user_agent=config.extractor.user_agent,
                display_name=yt_info.title if yt_info else None,
            )
            if filepath:
                console.print(f"\n[green]✓ Downloaded (yt-dlp): {filepath}[/green]")
                history.record(
                    url=url, title=Path(filepath).stem if filepath else '',
                    extractor="yt-dlp", filepath=filepath or '',
                    status="completed"
                )
                return True
            else:
                console.print(f"[dim]yt-dlp download returned no file[/dim]")
        except Exception as e:
            last_error_message = str(e)
            console.print(f"[dim]yt-dlp failed: {_compact_error_message(str(e))}[/dim]")
        finally:
            if yt_session:
                yt_session.close()
    else:
        console.print(f"[dim]yt-dlp not installed, skipping[/dim]")

    # === Step 3: Try generic extractor ===
    console.print(f"[dim]Trying generic extractor...[/dim]")
    session = None
    try:
        session = SessionManager(
            user_agent=config.extractor.user_agent,
            proxy=config.proxy.to_dict(),
            cookies_file=config.cookies_file,
            cookies_from_browser=cookies_browser or config.cookies_from_browser,
        )
        from extractors.generic import GenericExtractor
        ext = GenericExtractor(session, config=vars(config))
        media_info = ext.extract(url)

        if media_info and media_info.formats:
            fmt = _pick_format(media_info, quality=quality, audio_only=audio_only)

            if fmt:
                downloader = Downloader(config, session=session)
                output = asyncio.run(downloader.download(media_info, fmt))
                console.print(f"\n[green]✓ Downloaded: {output}[/green]")
                history.record(
                    url=url, title=media_info.title,
                    extractor="generic", filepath=output, status="completed"
                )
                return True
    except Exception as e:
        last_error_message = str(e)
        logger.debug(f"Generic extractor error: {e}")
    finally:
        if session:
            session.close()

    # All failed
    console.print(f"\n[red]✗ Could not download: {url}[/red]")
    if last_error_message:
        console.print(f"[yellow]{_compact_error_message(last_error_message, limit=200)}[/yellow]")
    console.print(f"[dim]All extractors and yt-dlp failed[/dim]")
    history.record(url=url, status="failed", error="All methods failed")
    return False


def show_history():
    """Show download history."""
    history = DownloadHistory()
    records = history.get_history(limit=20)
    stats = history.get_stats()

    console.print(f"\n[bold]Download Statistics:[/bold]")
    console.print(f"  Total: {stats['total']} | Success: {stats['successful']} | Failed: {stats['failed']} | Size: {stats['total_gb']}GB")

    if not records:
        console.print("[dim]No download history yet[/dim]")
        return

    table = Table(title="Recent Downloads", show_header=True, border_style="blue")
    table.add_column("#", width=4)
    table.add_column("Title", max_width=40)
    table.add_column("Extractor", width=12)
    table.add_column("Status", width=10)
    table.add_column("Date", width=16)

    for idx, rec in enumerate(records, 1):
        status = rec.get('status', '')
        status_fmt = {
            'completed': '[green]✓ Done[/green]',
            'failed': '[red]✗ Failed[/red]',
        }.get(status, status)

        title = (rec.get('title', '') or '')[:38]
        date = (rec.get('created_at', '') or '')[:16]
        table.add_row(str(idx), title, rec.get('extractor', ''), status_fmt, date)

    console.print(table)


def show_extractors():
    """Show available extractors."""
    from extractors.ytdlp import YTDLP_AVAILABLE

    table = Table(title="Available Extractors", show_header=True, border_style="cyan")
    table.add_column("Extractor", style="cyan", width=15)
    table.add_column("Description")
    table.add_column("Status", width=10)

    for ext in registry.get_all():
        table.add_row(
            ext.EXTRACTOR_NAME,
            ext.EXTRACTOR_DESCRIPTION,
            "[green]Active[/green]"
        )

    yt_status = "[green]Active[/green]" if YTDLP_AVAILABLE else "[red]Not installed[/red]"
    table.add_row("yt-dlp", "Fallback for 1000+ sites (YouTube, TikTok, etc.)", yt_status)

    console.print(table)


def show_supported_sites():
    """Show sites supported by yt-dlp."""
    from extractors.ytdlp import YTDLP_AVAILABLE

    console.print("\n[bold cyan]Custom Extractors (built-in):[/bold cyan]")
    console.print("  • Social Media (YouTube, TikTok, Instagram, Facebook, X, Reddit, SoundCloud, Twitch, Vimeo, etc.)")
    console.print("  • PubJav.com (multi-server, VidHide, HLS)")
    console.print("  • SupJav.com + TurboVidHLS (Cloudflare, HLS)")
    console.print("  • Any JWPlayer site (auto-detected)")
    console.print("  • HLS streams (.m3u8 direct)")
    console.print("  • Generic HTML (video tags, meta tags)")

    if YTDLP_AVAILABLE:
        console.print("\n[bold cyan]yt-dlp Supported Sites (1000+):[/bold cyan]")
        sites = [
            "YouTube, Vimeo, Dailymotion, Twitch",
            "Instagram, Facebook, Twitter/X, TikTok",
            "Reddit, Pinterest, LinkedIn, Threads",
            "SoundCloud, Bandcamp, Mixcloud",
            "Bilibili, Niconico, VK, Weibo",
            "Pornhub, Xvideos, XHamster",
            "And 1000+ more...",
        ]
        for s in sites:
            console.print(f"  • {s}")
    else:
        console.print("\n[yellow]Install yt-dlp for 1000+ more sites: pip install yt-dlp[/yellow]")


def _parse_selection_ranges(selection: str, max_value: int) -> List[int]:
    """Parse selection string like '1,3-5' or 'all' into sorted indices (1-based)."""
    normalized = selection.strip().lower()
    if normalized in {'all', '*'}:
        return list(range(1, max_value + 1))

    picked = set()
    for part in selection.split(','):
        token = part.strip()
        if not token:
            continue

        if '-' in token:
            start_str, end_str = token.split('-', 1)
            if not start_str.strip().isdigit() or not end_str.strip().isdigit():
                raise ValueError(f"Invalid range token: {token}")

            start = int(start_str.strip())
            end = int(end_str.strip())
            if start > end:
                start, end = end, start

            if start < 1 or end > max_value:
                raise ValueError(f"Range out of bounds: {token}")

            for idx in range(start, end + 1):
                picked.add(idx)
            continue

        if not token.isdigit():
            raise ValueError(f"Invalid token: {token}")

        idx = int(token)
        if idx < 1 or idx > max_value:
            raise ValueError(f"Index out of bounds: {idx}")
        picked.add(idx)

    if not picked:
        raise ValueError("No valid selection")

    return sorted(picked)


def _print_erome_items_table(items) -> None:
    """Render EroMe album item table."""
    table = Table(title=f"EroMe Album Items ({len(items)})", show_header=True, border_style="blue")
    table.add_column("No", width=4, justify="right", style="bold yellow")
    table.add_column("Type", width=8)
    table.add_column("Title", overflow="fold")
    table.add_column("Quality", width=10)
    table.add_column("Ext", width=6)

    for display_idx, item in enumerate(items, 1):
        fmt = item.format
        media_type = item.media_type.upper()
        quality = fmt.quality or "-"
        ext = fmt.ext or "bin"
        type_color = "cyan" if item.media_type == 'video' else "magenta"
        table.add_row(str(display_idx), f"[{type_color}]{media_type}[/{type_color}]", item.title, quality, ext)

    console.print(table)


def _erome_download_menu(config: Config) -> None:
    """Interactive EroMe flow (list, select download, download all)."""
    from extractors.erome import EromeExtractor

    url = Prompt.ask("\n[cyan]🔗 EroMe album URL[/cyan]").strip()
    if not url:
        console.print("[yellow]URL kosong, dibatalkan[/yellow]")
        return

    media_filter = Prompt.ask(
        "Filter media",
        choices=["all", "video", "photo"],
        default="all",
    )

    session = SessionManager(
        user_agent=config.extractor.user_agent,
        proxy=config.proxy.to_dict(),
        cookies_file=config.cookies_file,
        cookies_from_browser=config.cookies_from_browser,
    )

    try:
        extractor = EromeExtractor(session, config=vars(config))
        album = extractor.extract_album_items(url)
    finally:
        session.close()

    items = album['items']
    if media_filter != 'all':
        items = [item for item in items if item.media_type == media_filter]

    if not items:
        console.print(f"[red]✗ No media found for filter: {media_filter}[/red]")
        return

    console.print(f"\n[bold]{album['title']}[/bold]")
    if album.get('uploader'):
        console.print(f"[dim]Uploader: {album['uploader']}[/dim]")
    console.print(f"[dim]Extractor: erome | Total items: {len(items)}[/dim]")

    _print_erome_items_table(items)

    mode = Prompt.ask(
        "Mode download",
        choices=["select", "all", "cancel"],
        default="select",
    )
    if mode == "cancel":
        return

    if mode == "all":
        selected_items = list(items)
    else:
        selection = Prompt.ask(
            "Pilih item (contoh: 1,3-5 atau all)",
            default="",
        ).strip().lower()

        if not selection:
            console.print("[yellow]Tidak ada pilihan, dibatalkan[/yellow]")
            return

        try:
            indices = _parse_selection_ranges(selection, len(items))
        except ValueError as e:
            console.print(f"[red]✗ Invalid selection: {e}[/red]")
            return
        selected_items = [items[i - 1] for i in indices]

    base_dir_input = Prompt.ask(
        "Output directory",
        default=config.download.output_dir,
    ).strip()
    base_dir = Path(base_dir_input or config.download.output_dir)
    album_dir = base_dir / sanitize_filename(album['title'])
    album_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[dim]Downloading {len(selected_items)} item(s) to: {album_dir}[/dim]")
    jobs = prepare_erome_download_jobs(album, selected_items, album_dir)
    photo_count = sum(1 for job in jobs if job.item.media_type == 'photo')
    video_count = sum(1 for job in jobs if job.item.media_type == 'video')
    video_aria2 = erome_video_uses_aria2()
    mode_parts = []
    if photo_count:
        photo_workers = erome_photo_parallel_workers(config, photo_count)
        if photo_count > 1 and photo_workers > 1:
            mode_parts.append(f"photo=parallel x{photo_workers}")
        else:
            mode_parts.append("photo=standard")
    if video_count:
        video_mode = "aria2c" if video_aria2 else "standard"
        mode_parts.append(f"video={video_mode}")
    if mode_parts:
        console.print(f"[dim]Mode otomatis: {', '.join(mode_parts)}[/dim]")
    if video_count and not video_aria2:
        console.print("[dim]aria2c tidak terdeteksi, video pakai standard mode[/dim]")

    history = DownloadHistory()
    def _on_photo_batch_start(batch, workers: int) -> None:
        if len(batch) > 1 and workers > 1:
            console.print(f"[dim]Photo batch: {len(batch)} item(s) dengan {workers} worker(s)[/dim]")

    def _on_item_start(job, mode: str) -> None:
        mode_label = "aria2" if mode == 'aria2' else "standard"
        console.print(
            f"\n[cyan][{job.order}/{job.total}][/cyan] "
            f"{job.item.media_type.upper()} [{mode_label}] - {job.item.title}"
        )

    def _on_item_success(result) -> None:
        console.print(
            f"[green]✓ [{result.job.order}/{result.job.total}] Downloaded: {result.output_path}[/green]"
        )

    def _on_item_failure(result) -> None:
        console.print(
            f"[red]✗ [{result.job.order}/{result.job.total}] Failed: {result.error}[/red]"
        )

    results = asyncio.run(
        download_erome_jobs(
            jobs,
            config,
            on_item_start=_on_item_start,
            on_item_success=_on_item_success,
            on_item_failure=_on_item_failure,
            on_photo_batch_start=_on_photo_batch_start,
        )
    )

    success = 0
    failed = 0
    for result in results:
        fmt = result.job.item.format
        if result.ok and result.output_path:
            success += 1
            history.record(
                url=fmt.url,
                title=result.job.item.title,
                extractor='erome',
                quality=fmt.quality or '',
                filepath=result.output_path,
                status='completed',
            )
        else:
            failed += 1
            history.record(
                url=fmt.url,
                title=result.job.item.title,
                extractor='erome',
                quality=fmt.quality or '',
                filepath=str(result.job.output_path),
                status='failed',
                error=result.error or 'Unknown error',
            )

    console.print(
        f"\n[bold]EroMe Summary:[/bold] [green]{success} success[/green], [red]{failed} failed[/red]"
    )


def _render_main_menu_table() -> None:
    """Render a cleaner and more aesthetic main menu layout."""
    header = Panel.fit(
        "[bold white]Choose an operation[/bold white]\n"
        "[dim]Fast workflow for download, extract, and manage tasks[/dim]",
        border_style="bright_cyan",
        box=box.ROUNDED,
        padding=(0, 2),
        title="[bold cyan]Main Menu[/bold cyan]",
        title_align="center",
    )
    console.print(header)

    table = Table(
        show_header=True,
        header_style="bold white on dark_blue",
        border_style="bright_blue",
        box=box.ROUNDED,
        expand=True,
        pad_edge=True,
        row_styles=["none", "grey23"],
    )
    table.add_column("No", width=4, justify="right", style="bold yellow")
    table.add_column("Feature", min_width=28, no_wrap=True, overflow="ellipsis", style="bold white")
    table.add_column("Description", min_width=38, style="grey70", overflow="fold")

    groups = [
        [
            ("1", "🎯", "Smart Download", "Auto-detect best method and download"),
            ("2", "🎵", "Audio Only", "Extract audio (MP3) from any video"),
            ("3", "📦", "Batch Download", "Download multiple URLs at once"),
            ("4", "📄", "Batch from File", "Import URLs from text file"),
            ("5", "ℹ️", "Video Info", "View metadata and available formats"),
        ],
        [
            ("6", "📊", "History and Stats", "Review download history and totals"),
            ("7", "🌐", "Supported Sites", "List all supported websites"),
            ("8", "🔧", "Extractors", "Show available extractors"),
            ("9", "📸", "EroMe Download", "Auto: photos parallel, videos use aria2"),
        ],
        [
            ("10", "⚙️", "Settings", "Configure download options"),
            ("0", "🚪", "Exit", "Quit application"),
        ],
    ]

    for group_idx, rows in enumerate(groups):
        for key, icon, name, desc in rows:
            feature = f"[bright_cyan]{icon}[/bright_cyan]  {name}"
            table.add_row(key, feature, desc)
        if group_idx < len(groups) - 1:
            table.add_section()

    console.print(table)

    console.print(
        Panel(
            "[dim]Tip:[/dim] [cyan]Ketik nomor menu[/cyan] [dim](0-10) |[/dim] "
            "[cyan]Enter[/cyan] [dim]= 1 |[/dim] [cyan]Ctrl+C[/cyan] [dim]= batal[/dim]",
            border_style="grey35",
            box=box.SQUARE,
            padding=(0, 1),
        )
    )


def run_tui():
    """Run interactive TUI."""
    check_rich()

    config = Config.load()
    setup_logging(config)

    console.print(
        Panel.fit(
            "[bold bright_cyan]🎬 Universal Media Downloader[/bold bright_cyan]\n"
            "[dim]Custom extractors + yt-dlp fallback + aria2c acceleration[/dim]",
            border_style="cyan",
            box=box.DOUBLE,
            padding=(0, 2),
        )
    )

    # Check dependencies
    deps = []
    deps.append(("FFmpeg", shutil.which("ffmpeg") is not None))
    deps.append(("aria2c", shutil.which("aria2c") is not None))

    from extractors.ytdlp import YTDLP_AVAILABLE
    deps.append(("yt-dlp", YTDLP_AVAILABLE))

    dep_badges = []
    for name, available in deps:
        if available:
            dep_badges.append(f"[green]✓ {name}[/green]")
        else:
            dep_badges.append(f"[yellow]✗ {name}[/yellow]")
    console.print(
        Panel.fit(
            "   ".join(dep_badges),
            border_style="grey39",
            box=box.SQUARE,
            padding=(0, 1),
            title="[bold]Environment[/bold]",
            title_align="left",
        )
    )

    while True:
        console.print()
        _render_main_menu_table()

        choice = Prompt.ask("[bold cyan]Choose menu (0-10)[/bold cyan]", default="1")

        try:
            if choice == "0":
                console.print("[yellow]👋 Goodbye![/yellow]")
                break

            elif choice == "1":
                url = Prompt.ask("\n[cyan]🔗 Paste URL[/cyan]")
                if url.strip():
                    smart_download(url.strip(), config, quality="best")

            elif choice == "2":
                url = Prompt.ask("\n[cyan]🔗 Paste URL[/cyan]")
                if url.strip():
                    smart_download(url.strip(), config, audio_only=True)

            elif choice == "3":
                console.print("\n[cyan]Enter URLs (one per line, empty to finish):[/cyan]")
                urls = []
                while True:
                    line = input().strip()
                    if not line:
                        break
                    urls.append(line)
                if urls:
                    quality = Prompt.ask("Quality", default="best")
                    for idx, u in enumerate(urls, 1):
                        console.print(f"\n[bold]--- [{idx}/{len(urls)}] ---[/bold]")
                        smart_download(u, config, quality=quality)

            elif choice == "4":
                filepath = Prompt.ask("\n[cyan]Path to URL file[/cyan]")
                filepath = filepath.strip().strip("'\"")
                if os.path.exists(filepath):
                    with open(filepath) as f:
                        urls = [l.strip() for l in f if l.strip() and not l.startswith('#')]
                    quality = Prompt.ask("Quality", default="best")
                    for idx, u in enumerate(urls, 1):
                        console.print(f"\n[bold]--- [{idx}/{len(urls)}] ---[/bold]")
                        smart_download(u, config, quality=quality)
                else:
                    console.print(f"[red]File not found: {filepath}[/red]")

            elif choice == "5":
                url = Prompt.ask("\n[cyan]🔗 Paste URL[/cyan]")
                if url.strip():
                    _show_video_info(url.strip(), config)

            elif choice == "6":
                show_history()

            elif choice == "7":
                show_supported_sites()

            elif choice == "8":
                show_extractors()

            elif choice == "9":
                _erome_download_menu(config)

            elif choice == "10":
                _settings_menu(config)

            else:
                console.print("[yellow]Invalid option[/yellow]")

        except KeyboardInterrupt:
            console.print("\n[yellow]Cancelled[/yellow]")
        except Exception as e:
            console.print(f"\n[red]Error: {e}[/red]")


def _show_video_info(url: str, config: Config):
    """Show video info for a URL."""
    session = SessionManager(
        user_agent=config.extractor.user_agent,
        proxy=config.proxy.to_dict(),
        cookies_file=config.cookies_file,
        cookies_from_browser=config.cookies_from_browser,
    )
    extractor_class = registry.find_extractor(url)

    try:
        if extractor_class:
            try:
                ext = extractor_class(session, config=vars(config))
                info = ext.extract(url)
                console.print(f"\n[bold]Title:[/bold] {info.title}")
                console.print(f"[dim]Extractor: {info.extractor} | Formats: {len(info.formats)}[/dim]")
                if info.duration:
                    m, s = divmod(info.duration, 60)
                    console.print(f"[dim]Duration: {m}:{s:02d}[/dim]")
                if info.best_format:
                    console.print(f"[dim]Best: {info.best_format.format_note}[/dim]")
                return
            except Exception:
                pass

        # Try yt-dlp
        from extractors.ytdlp import YtdlpExtractor, YTDLP_AVAILABLE
        if YTDLP_AVAILABLE:
            try:
                ext = YtdlpExtractor(session, config=vars(config))
                info = ext.extract(url)
                console.print(f"\n[bold]Title:[/bold] {info.title}")
                console.print(f"[dim]Extractor: {info.extractor} | Formats: {len(info.formats)}[/dim]")
                if info.duration:
                    m, s = divmod(info.duration, 60)
                    console.print(f"[dim]Duration: {m}:{s:02d}[/dim]")
                return
            except Exception:
                pass

        console.print("[red]Could not get video info[/red]")
    finally:
        session.close()


def _settings_menu(config: Config):
    """Settings submenu."""
    while True:
        console.print(f"\n[bold]Current Settings:[/bold]")
        console.print(f"  Download dir: {config.download.output_dir}")
        console.print(f"  Max concurrent: {config.download.max_concurrent}")
        console.print(f"  aria2c: {'enabled' if config.download.use_aria2 else 'disabled'}")
        console.print(f"  Connections: {config.download.aria2_connections}")
        console.print(f"  Proxy: {config.proxy.http or config.proxy.socks5 or 'none'}")

        console.print(f"\n  1. Change download directory")
        console.print(f"  2. Toggle aria2c")
        console.print(f"  3. Set proxy")
        console.print(f"  0. Back")

        c = Prompt.ask("Choose", default="0")
        if c == "0":
            break
        elif c == "1":
            d = Prompt.ask("Download directory", default=config.download.output_dir)
            config.download.output_dir = d
            os.makedirs(d, exist_ok=True)
        elif c == "2":
            config.download.use_aria2 = not config.download.use_aria2
            console.print(f"aria2c: {'enabled' if config.download.use_aria2 else 'disabled'}")
        elif c == "3":
            p = Prompt.ask("Proxy URL (empty to clear)", default="")
            config.proxy.http = p or None
            config.proxy.https = p or None
