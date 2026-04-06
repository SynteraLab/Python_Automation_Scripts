"""
Command Line Interface for the universal downloader.
Enhanced with colors, better error handling, and JWPlayer support.
"""

import argparse
import asyncio
import sys
import re
from pathlib import Path
from typing import Optional, List
import logging

from config import Config, setup_logging
from utils.network import SessionManager
from core.downloader import Downloader, BatchDownloader
from core.erome_download import (
    download_erome_jobs,
    erome_photo_parallel_workers,
    erome_video_uses_aria2,
    prepare_erome_download_jobs,
)
from utils.helpers import sanitize_filename
from models.media import StreamType
from extractors.base import registry
import extractors  # Register built-in extractors

logger = logging.getLogger(__name__)


class Colors:
    RESET = '\033[0m'
    BOLD = '\033[1m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    DIM = '\033[2m'
    MAGENTA = '\033[95m'


BANNER = f"""{Colors.CYAN}
╔═══════════════════════════════════════════════╗
║  {Colors.BOLD}Universal Media Downloader{Colors.RESET}{Colors.CYAN}                  ║
║  Supports: Direct, HLS, DASH, JWPlayer        ║
║  ⚡ aria2c multi-connection acceleration       ║
╚═══════════════════════════════════════════════╝{Colors.RESET}
"""


def _print_formats_table(formats) -> None:
    print(f"\n{Colors.BOLD}Available formats ({len(formats)}):{Colors.RESET}")
    print("-" * 96)
    header = (
        f"{'No':<4} {'ID':<18} {'Resolution':<12} {'Quality':<12} "
        f"{'FPS':<6} {'Type':<8} {'Size':<10} {'Note':<18}"
    )
    print(f"{Colors.DIM}{header}{Colors.RESET}")
    print("-" * 96)

    for idx, fmt in enumerate(formats, 1):
        size = f"{fmt.filesize / 1024 / 1024:.1f}MB" if fmt.filesize else "?"
        stream = fmt.stream_type.value[:6]
        quality = fmt.quality or ""
        note = (fmt.label or "")[:18]
        print(
            f"{idx:<4} "
            f"{fmt.format_id:<18} "
            f"{fmt.resolution:<12} "
            f"{quality:<12} "
            f"{fmt.fps or '?':<6} "
            f"{stream:<8} "
            f"{size:<10} "
            f"{note:<18}"
        )


def _prompt_format_selection(formats):
    if not sys.stdin.isatty():
        return None

    print(
        f"\n{Colors.CYAN}Pilih format (No atau ID), Enter untuk default best quality:{Colors.RESET}"
    )
    choice = input(
        f"{Colors.BOLD}Format pilihan [Enter=best]: {Colors.RESET}"
    ).strip()

    if not choice:
        return None

    choice_lower = choice.lower()
    for fmt in formats:
        if (fmt.format_id or '').lower() == choice_lower:
            return fmt

    numeric_match = re.fullmatch(r"(?:no\s*)?#?\s*(\d+)\s*[\.)]?\s*", choice_lower)
    if numeric_match:
        idx = int(numeric_match.group(1))
        if 1 <= idx <= len(formats):
            return formats[idx - 1]

    print(f"{Colors.YELLOW}⚠ Format '{choice}' tidak valid, pakai best quality{Colors.RESET}")
    return None


def _parse_selection_ranges(selection: str, max_value: int) -> List[int]:
    """Parse selection string like '1,3-5' into sorted indices (1-based)."""
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
            if not start_str.isdigit() or not end_str.isdigit():
                raise ValueError(f"Invalid range token: {token}")
            start = int(start_str)
            end = int(end_str)
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

    return sorted(picked)


def _print_erome_items_table(items) -> None:
    print(f"\n{Colors.BOLD}Album media ({len(items)}):{Colors.RESET}")
    print("-" * 110)
    header = (
        f"{'No':<4} {'Type':<8} {'Resolution':<12} {'Quality':<10} "
        f"{'Ext':<6} {'Size':<10} {'Title':<56}"
    )
    print(f"{Colors.DIM}{header}{Colors.RESET}")
    print("-" * 110)

    for idx, item in enumerate(items, 1):
        fmt = item.format
        size = f"{fmt.filesize / 1024 / 1024:.1f}MB" if fmt.filesize else "?"
        media_type = item.media_type
        print(
            f"{idx:<4} "
            f"{media_type:<8} "
            f"{fmt.resolution:<12} "
            f"{(fmt.quality or ''):<10} "
            f"{fmt.ext:<6} "
            f"{size:<10} "
            f"{item.title[:56]:<56}"
        )


def _pick_best_erome_item(items):
    """Pick default best item (prefer video, then higher resolution)."""
    if not items:
        return None

    return max(
        items,
        key=lambda item: (
            1 if item.media_type == 'video' else 0,
            item.format.height or 0,
            item.format.width or 0,
        )
    )


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser."""
    parser = argparse.ArgumentParser(
        prog='unidown',
        description='Universal Media Downloader - Download videos from multiple platforms',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f'''
{Colors.BOLD}Examples:{Colors.RESET}
  %(prog)s download "https://example.com/video"
  %(prog)s download -q 720p "https://example.com/video"
  %(prog)s download --connections 32 "https://example.com/video"
  %(prog)s download --no-aria2 "https://example.com/video"
  %(prog)s download --audio-only "https://example.com/video"
  %(prog)s batch urls.txt
  %(prog)s list-formats "https://example.com/video"
  %(prog)s info "https://example.com/video"

{Colors.BOLD}Download acceleration:{Colors.RESET}
  aria2c auto-detected if installed (brew install aria2)
  Default: 16 connections per server (maximum)
  Override: --connections 8 (use fewer connections)

{Colors.BOLD}Supported sites:{Colors.RESET}
  - Any site with <video> tags or direct .mp4/.webm links
  - Any site using JWPlayer (auto-detected)
  - HLS streams (.m3u8)
  - DASH streams (.mpd) (requires FFmpeg)
  - JS-rendered pages (with --use-browser flag)
        '''
    )

    parser.add_argument('-c', '--config', help='Path to config file')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose output')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--no-color', action='store_true', help='Disable colored output')

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # Download command
    dl = subparsers.add_parser('download', aliases=['dl'], help='Download a single video')
    dl.add_argument('url', help='URL to download')
    dl.add_argument('-o', '--output', help='Output filename or directory')
    dl.add_argument('-q', '--quality', default='best',
                    help='Quality (best, worst, 720p, 1080p, etc.)')
    dl.add_argument('-f', '--format', help='Specific format ID to download')
    dl.add_argument('--audio-only', action='store_true', help='Download audio only')
    dl.add_argument('--no-merge', action='store_true', help='Do not merge video and audio')
    dl.add_argument('--proxy', help='Proxy URL (http://... or socks5://...)')
    dl.add_argument('--cookies', help='Path to cookies file')
    dl.add_argument('--cookies-from-browser',
                    choices=['chrome', 'firefox', 'edge', 'opera', 'brave'],
                    help='Load cookies from browser')
    dl.add_argument('--user-agent', help='Custom User-Agent string')
    dl.add_argument('--use-browser', action='store_true',
                    help='Use browser for JavaScript-rendered pages')
    dl.add_argument('--force-jwplayer', action='store_true',
                    help='Force JWPlayer extractor')
    dl.add_argument('--force-generic', action='store_true',
                    help='Force generic extractor (skip JWPlayer auto-detect)')
    dl.add_argument('--aria2', action='store_true', default=None,
                    help='Force use aria2c for faster downloads')
    dl.add_argument('--no-aria2', action='store_true',
                    help='Disable aria2c, use standard downloader')
    dl.add_argument('--connections', type=int, default=None,
                    help='Number of connections for aria2c (max: 16, default: 16)')
    dl.add_argument('--hls-workers', type=int, default=None,
                    help='Parallel workers for HLS segment downloads (default: 8)')

    # EroMe album command (media selection + download all)
    er = subparsers.add_parser('erome', help='Download EroMe album media (photos parallel, videos optimized)')
    er.add_argument('url', help='EroMe album URL (https://www.erome.com/a/...)')
    er.add_argument('-o', '--output-dir', help='Output directory (album subfolder will be created)')
    er.add_argument(
        '--all', '--download-all',
        dest='all',
        action='store_true',
        help='Download all media items in album'
    )
    er.add_argument(
        '--select', '--pick',
        dest='select',
        help='Select item numbers/ranges (e.g. 1,3-5 or all)'
    )
    er.add_argument('--type', choices=['all', 'video', 'photo'], default='all',
                    help='Filter listed media type before selection')
    er.add_argument('--list-only', action='store_true', help='Only list media, do not download')
    er.add_argument('--proxy', help='Proxy URL (http://... or socks5://...)')
    er.add_argument('--cookies', help='Path to cookies file')
    er.add_argument('--cookies-from-browser',
                    choices=['chrome', 'firefox', 'edge', 'opera', 'brave'],
                    help='Load cookies from browser')
    er.add_argument('--user-agent', help='Custom User-Agent string')
    er.add_argument('--connections', type=int, default=None,
                    help='Number of aria2c connections for EroMe videos (max: 16, default: 16)')

    # Batch command
    batch = subparsers.add_parser('batch', help='Download multiple videos from file')
    batch.add_argument('file', help='File containing URLs (one per line)')
    batch.add_argument('-q', '--quality', default='best', help='Quality preference')
    batch.add_argument('-o', '--output-dir', help='Output directory')
    batch.add_argument('--parallel', type=int, default=1, help='Number of parallel downloads')

    # List formats command
    fmt = subparsers.add_parser('list-formats', aliases=['formats', 'lf'],
                                help='List available formats for a URL')
    fmt.add_argument('url', help='URL to check')
    fmt.add_argument('--json', action='store_true', help='Output as JSON')
    fmt.add_argument('--proxy', help='Proxy URL (http://... or socks5://...)')
    fmt.add_argument('--cookies', help='Path to cookies file')
    fmt.add_argument('--cookies-from-browser',
                    choices=['chrome', 'firefox', 'edge', 'opera', 'brave'],
                    help='Load cookies from browser')
    fmt.add_argument('--user-agent', help='Custom User-Agent string')
    fmt.add_argument('--use-browser', action='store_true',
                    help='Use browser for JavaScript-rendered pages')
    fmt.add_argument('--force-jwplayer', action='store_true',
                    help='Force JWPlayer extractor')
    fmt.add_argument('--force-generic', action='store_true',
                    help='Force generic extractor (skip JWPlayer auto-detect)')

    # Info command
    info = subparsers.add_parser('info', help='Show video information')
    info.add_argument('url', help='URL to check')
    info.add_argument('--json', action='store_true', help='Output as JSON')
    info.add_argument('--proxy', help='Proxy URL (http://... or socks5://...)')
    info.add_argument('--cookies', help='Path to cookies file')
    info.add_argument('--cookies-from-browser',
                     choices=['chrome', 'firefox', 'edge', 'opera', 'brave'],
                     help='Load cookies from browser')
    info.add_argument('--user-agent', help='Custom User-Agent string')
    info.add_argument('--use-browser', action='store_true',
                     help='Use browser for JavaScript-rendered pages')
    info.add_argument('--force-jwplayer', action='store_true',
                     help='Force JWPlayer extractor')
    info.add_argument('--force-generic', action='store_true',
                     help='Force generic extractor (skip JWPlayer auto-detect)')

    # List extractors command
    subparsers.add_parser('list-extractors', aliases=['extractors'],
                          help='List available extractors')

    # Diagnostic command
    diag = subparsers.add_parser('diagnose', help='Run advanced diagnostic scan for a target page')
    diag.add_argument('url', help='URL to diagnose')
    diag.add_argument('-o', '--output-dir', default='diagnostic_output',
                      help='Directory for diagnostic reports')
    diag.add_argument('--no-headless', action='store_true',
                      help='Show the browser during the diagnostic scan')
    diag.add_argument('--no-screenshot', action='store_true',
                      help='Skip diagnostic screenshots')
    diag.add_argument('--no-stealth', action='store_true',
                      help='Disable Playwright stealth mode')
    diag.add_argument('--timeout', type=int, default=300,
                      help='Maximum diagnostic time in seconds')
    diag.add_argument('--max-scroll', type=int, default=20,
                      help='Maximum auto-scroll count during capture')
    diag.add_argument('--max-js', type=int, default=50,
                      help='Maximum JavaScript files to analyze')
    diag.add_argument('--proxy', help='Proxy server for diagnostic browser/session')

    return parser


def apply_cli_config(args, config: Config) -> Config:
    """Apply CLI arguments to config."""
    if hasattr(args, 'proxy') and args.proxy:
        if args.proxy.startswith('socks'):
            config.proxy.socks5 = args.proxy
        else:
            config.proxy.http = args.proxy
            config.proxy.https = args.proxy

    if hasattr(args, 'cookies') and args.cookies:
        config.cookies_file = args.cookies
    if hasattr(args, 'cookies_from_browser') and args.cookies_from_browser:
        config.cookies_from_browser = args.cookies_from_browser
    if hasattr(args, 'user_agent') and args.user_agent:
        config.extractor.user_agent = args.user_agent
    if hasattr(args, 'use_browser') and args.use_browser:
        config.extractor.use_browser = True
    if hasattr(args, 'output_dir') and args.output_dir:
        config.download.output_dir = args.output_dir

    # Aria2 settings
    if hasattr(args, 'aria2') and args.aria2:
        config.download.use_aria2 = True
    if hasattr(args, 'no_aria2') and args.no_aria2:
        config.download.use_aria2 = False
    if hasattr(args, 'connections') and args.connections:
        config.download.aria2_connections = args.connections
    if hasattr(args, 'hls_workers') and args.hls_workers:
        config.download.max_concurrent = max(1, args.hls_workers)

    return config


def _get_extractor(args, url: str, config: Config, session: SessionManager):
    """Get the appropriate extractor based on args and URL."""
    # Force JWPlayer
    if hasattr(args, 'force_jwplayer') and args.force_jwplayer:
        from extractors.jwplayer import JWPlayerExtractor
        return JWPlayerExtractor(session, config=vars(config))

    # Force generic (skip JW auto-detect)
    if hasattr(args, 'force_generic') and args.force_generic:
        from extractors.generic import GenericExtractor
        return GenericExtractor(session, config=vars(config))

    # Use browser extractor
    if hasattr(args, 'use_browser') and args.use_browser:
        from extractors.advanced import AdvancedExtractor
        return AdvancedExtractor(session, config=vars(config))

    # Auto-detect
    extractor_class = registry.find_extractor(url)
    if not extractor_class:
        return None
    return extractor_class(session, config=vars(config))


async def cmd_download(args, config: Config) -> int:
    """Handle download command."""
    config = apply_cli_config(args, config)
    from extractors.ytdlp import YtdlpExtractor

    session = SessionManager(
        user_agent=config.extractor.user_agent,
        proxy=config.proxy.to_dict(),
        cookies_file=config.cookies_file,
        cookies_from_browser=config.cookies_from_browser
    )
    extractor = None

    try:
        extractor = _get_extractor(args, args.url, config, session)
        if not extractor:
            print(f"{Colors.RED}✗ No extractor found for URL: {args.url}{Colors.RESET}")
            return 1

        print(f"{Colors.DIM}Using extractor: {extractor.EXTRACTOR_NAME}{Colors.RESET}")

        try:
            media_info = extractor.extract(args.url)
        except Exception as e:
            print(f"{Colors.RED}✗ Extraction failed: {e}{Colors.RESET}")
            return 1

        if not media_info.formats:
            print(f"{Colors.RED}✗ No downloadable formats found{Colors.RESET}")
            return 1

        print(f"{Colors.BOLD}{media_info.title}{Colors.RESET}")
        print(f"{Colors.DIM}Found {len(media_info.formats)} format(s){Colors.RESET}")
        _print_formats_table(media_info.formats)

        interactive_format = None
        if not args.format and not args.audio_only and args.quality == 'best':
            interactive_format = _prompt_format_selection(media_info.formats)

        # Select format
        if interactive_format:
            format_ = interactive_format
        elif args.format:
            format_ = next(
                (f for f in media_info.formats if f.format_id == args.format), None
            )
            if not format_:
                print(f"{Colors.RED}✗ Format {args.format} not found{Colors.RESET}")
                return 1
        elif args.audio_only:
            audio_formats = media_info.get_audio_formats()
            if not audio_formats:
                print(f"{Colors.RED}✗ No audio-only formats available{Colors.RESET}")
                return 1
            format_ = audio_formats[0]
        elif args.quality == 'best':
            format_ = media_info.best_format
        elif args.quality == 'worst':
            format_ = media_info.worst_format
        else:
            format_ = media_info.get_format_by_quality(args.quality)
            if not format_:
                print(f"{Colors.YELLOW}⚠ Quality {args.quality} not found, using best{Colors.RESET}")
                format_ = media_info.best_format

        if not format_:
            print(f"{Colors.RED}✗ No suitable format selected{Colors.RESET}")
            return 1

        print(f"{Colors.DIM}Format: {format_.format_note}{Colors.RESET}")

        if YtdlpExtractor.uses_direct_backend(media_info):
            print(f"{Colors.DIM}Mode: yt-dlp direct backend{Colors.RESET}")
            try:
                output_path = YtdlpExtractor.download_media_info(
                    media_info,
                    config,
                    selected_format=format_,
                    output_path=args.output,
                    quality=args.quality,
                    audio_only=args.audio_only,
                    no_merge=args.no_merge,
                    display_name=media_info.title,
                )
            except Exception as e:
                print(f"{Colors.RED}✗ Direct yt-dlp download failed: {e}{Colors.RESET}")
                return 1

            if not output_path:
                print(f"{Colors.RED}✗ Direct yt-dlp download returned no file{Colors.RESET}")
                return 1

            print(f"\n{Colors.GREEN}✓ Downloaded: {output_path}{Colors.RESET}")
            return 0

        # Check if we need separate audio
        audio_format = None
        if not args.no_merge and format_.is_video and not format_.is_audio:
            audio_formats = media_info.get_audio_formats()
            if audio_formats:
                audio_format = audio_formats[0]

        # Download
        downloader = Downloader(config, session=session)
        try:
            output_path = await downloader.download(
                media_info, format_,
                audio_format=audio_format,
                output_path=args.output
            )
        except Exception as e:
            fallback_fmt = None
            if format_.stream_type == StreamType.HLS:
                non_hls_formats = [
                    f for f in media_info.formats
                    if f.format_id != format_.format_id and f.stream_type != StreamType.HLS
                ]
                if non_hls_formats:
                    non_hls_formats.sort(key=lambda f: f.quality_score, reverse=True)
                    fallback_fmt = non_hls_formats[0]

            if not fallback_fmt:
                raise

            print(
                f"{Colors.YELLOW}⚠ Primary HLS failed ({str(e)[:80]}), "
                f"retrying with {fallback_fmt.format_note}{Colors.RESET}"
            )

            output_path = await downloader.download(
                media_info,
                fallback_fmt,
                audio_format=None,
                output_path=args.output,
            )
            format_ = fallback_fmt

        print(f"\n{Colors.GREEN}✓ Downloaded: {output_path}{Colors.RESET}")
        return 0

    finally:
        if extractor is not None:
            try:
                extractor.close()
            except Exception:
                pass
        session.close()


async def cmd_erome(args, config: Config) -> int:
    """Handle EroMe album command (list/select/all, video+photo)."""
    config = apply_cli_config(args, config)

    session = SessionManager(
        user_agent=config.extractor.user_agent,
        proxy=config.proxy.to_dict(),
        cookies_file=config.cookies_file,
        cookies_from_browser=config.cookies_from_browser
    )
    extractor = None

    try:
        from extractors.erome import EromeExtractor

        extractor = EromeExtractor(session, config=vars(config))
        album = extractor.extract_album_items(args.url)

        items = album['items']
        if args.type != 'all':
            items = [item for item in items if item.media_type == args.type]

        if not items:
            print(f"{Colors.RED}✗ No media found for filter: {args.type}{Colors.RESET}")
            return 1

        print(f"{Colors.BOLD}{album['title']}{Colors.RESET}")
        if album.get('uploader'):
            print(f"{Colors.DIM}Uploader: {album['uploader']}{Colors.RESET}")
        print(f"{Colors.DIM}Extractor: erome | Total items: {len(items)}{Colors.RESET}")

        _print_erome_items_table(items)

        if args.list_only:
            return 0

        selected_items = []

        if args.all and args.select:
            print(f"{Colors.YELLOW}⚠ Both --all and --select provided, using --all{Colors.RESET}")

        if args.all:
            selected_items = list(items)
        elif args.select:
            try:
                indices = _parse_selection_ranges(args.select, len(items))
            except ValueError as e:
                print(f"{Colors.RED}✗ Invalid --select value: {e}{Colors.RESET}")
                return 1
            selected_items = [items[i - 1] for i in indices]
        elif sys.stdin.isatty():
            print(
                f"\n{Colors.CYAN}Pilih media (No/range: 1,3-5), ketik 'all', Enter=best:{Colors.RESET}"
            )
            choice = input(f"{Colors.BOLD}Pilihan media: {Colors.RESET}").strip().lower()

            if not choice:
                best_item = _pick_best_erome_item(items)
                selected_items = [best_item] if best_item else []
            elif choice in {'all', '*'}:
                selected_items = list(items)
            else:
                try:
                    indices = _parse_selection_ranges(choice, len(items))
                except ValueError as e:
                    print(f"{Colors.RED}✗ Invalid selection: {e}{Colors.RESET}")
                    return 1
                selected_items = [items[i - 1] for i in indices]
        else:
            best_item = _pick_best_erome_item(items)
            selected_items = [best_item] if best_item else []

        if not selected_items:
            print(f"{Colors.RED}✗ No media selected{Colors.RESET}")
            return 1

        base_dir = Path(args.output_dir) if getattr(args, 'output_dir', None) else Path(config.download.output_dir)
        album_dir = base_dir / sanitize_filename(album['title'])
        album_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{Colors.DIM}Downloading {len(selected_items)} item(s) to: {album_dir}{Colors.RESET}")

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
            video_mode = 'aria2c' if video_aria2 else 'standard'
            mode_parts.append(f"video={video_mode}")
        if mode_parts:
            print(f"{Colors.DIM}Mode otomatis: {', '.join(mode_parts)}{Colors.RESET}")
        if video_count and not video_aria2:
            print(f"{Colors.DIM}aria2c tidak terdeteksi, video pakai standard mode{Colors.RESET}")

        def _on_photo_batch_start(batch, workers: int) -> None:
            if len(batch) > 1 and workers > 1:
                print(
                    f"{Colors.DIM}  Photo batch: {len(batch)} item(s) "
                    f"dengan {workers} worker(s){Colors.RESET}"
                )

        def _on_item_start(job, mode: str) -> None:
            mode_label = 'aria2' if mode == 'aria2' else 'standard'
            print(
                f"\n{Colors.CYAN}[{job.order}/{job.total}]{Colors.RESET} "
                f"{job.item.media_type.upper()} [{mode_label}] - {job.item.title}"
            )

        def _on_item_success(result) -> None:
            print(
                f"{Colors.GREEN}✓ [{result.job.order}/{result.job.total}] "
                f"Downloaded: {result.output_path}{Colors.RESET}"
            )

        def _on_item_failure(result) -> None:
            print(
                f"{Colors.RED}✗ [{result.job.order}/{result.job.total}] "
                f"Failed: {result.error}{Colors.RESET}"
            )

        results = await download_erome_jobs(
            jobs,
            config,
            on_item_start=_on_item_start,
            on_item_success=_on_item_success,
            on_item_failure=_on_item_failure,
            on_photo_batch_start=_on_photo_batch_start,
        )
        success = sum(1 for result in results if result.ok)
        failed = len(results) - success

        print(
            f"\n{Colors.BOLD}EroMe Summary:{Colors.RESET} "
            f"{Colors.GREEN}{success} success{Colors.RESET}, "
            f"{Colors.RED}{failed} failed{Colors.RESET}"
        )

        return 0 if failed == 0 else 1

    finally:
        if extractor is not None:
            try:
                extractor.close()
            except Exception:
                pass
        session.close()


async def cmd_batch(args, config: Config) -> int:
    """Handle batch download command."""
    config = apply_cli_config(args, config)

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"{Colors.RED}✗ File not found: {args.file}{Colors.RESET}")
        return 1

    with open(file_path) as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith('#')]

    if not urls:
        print(f"{Colors.RED}✗ No URLs found in file{Colors.RESET}")
        return 1

    print(f"Found {Colors.BOLD}{len(urls)}{Colors.RESET} URLs to download\n")

    downloader = Downloader(config)
    batch = BatchDownloader(downloader, registry)
    results = await batch.download_batch(urls, quality=args.quality)

    # Print summary
    print(f"\n{'=' * 50}")
    print(f"{Colors.BOLD}DOWNLOAD SUMMARY{Colors.RESET}")
    print(f"{'=' * 50}")
    print(f"{Colors.GREEN}Successful: {len(results['successful'])}{Colors.RESET}")
    print(f"{Colors.RED}Failed:     {len(results['failed'])}{Colors.RESET}")
    print(f"{Colors.YELLOW}Skipped:    {len(results['skipped'])}{Colors.RESET}")

    if results['failed']:
        print(f"\n{Colors.RED}Failed downloads:{Colors.RESET}")
        for item in results['failed']:
            print(f"  - {item['url']}: {item['error']}")

    return 0 if not results['failed'] else 1


async def cmd_list_formats(args, config: Config) -> int:
    """Handle list-formats command."""
    config = apply_cli_config(args, config)
    session = SessionManager(
        user_agent=config.extractor.user_agent,
        proxy=config.proxy.to_dict(),
        cookies_file=config.cookies_file,
        cookies_from_browser=config.cookies_from_browser,
    )
    extractor = None

    try:
        extractor = _get_extractor(args, args.url, config, session)
        if not extractor:
            print(f"{Colors.RED}✗ No extractor found for URL{Colors.RESET}")
            return 1

        media_info = extractor.extract(args.url)

        if args.json:
            print(media_info.to_json())
        else:
            print(f"\n{Colors.BOLD}Title:{Colors.RESET} {media_info.title}")
            print(f"{Colors.DIM}ID: {media_info.id} | Extractor: {media_info.extractor}{Colors.RESET}")
            print(f"\n{Colors.BOLD}Available formats ({len(media_info.formats)}):{Colors.RESET}")
            print("-" * 90)
            header = (
                f"{'ID':<15} {'Resolution':<12} {'Quality':<10} "
                f"{'FPS':<6} {'VCodec':<10} {'ACodec':<10} {'Type':<8} {'Size':<10}"
            )
            print(f"{Colors.DIM}{header}{Colors.RESET}")
            print("-" * 90)

            for fmt in media_info.formats:
                size = f"{fmt.filesize / 1024 / 1024:.1f}MB" if fmt.filesize else "?"
                stream = fmt.stream_type.value[:6]
                label = fmt.label or fmt.quality or ""
                print(
                    f"{fmt.format_id:<15} "
                    f"{fmt.resolution:<12} "
                    f"{label:<10} "
                    f"{fmt.fps or '?':<6} "
                    f"{fmt.vcodec or '-':<10} "
                    f"{fmt.acodec or '-':<10} "
                    f"{stream:<8} "
                    f"{size:<10}"
                )

        return 0
    finally:
        if extractor is not None:
            try:
                extractor.close()
            except Exception:
                pass
        session.close()


async def cmd_info(args, config: Config) -> int:
    """Handle info command."""
    config = apply_cli_config(args, config)
    session = SessionManager(
        user_agent=config.extractor.user_agent,
        proxy=config.proxy.to_dict(),
        cookies_file=config.cookies_file,
        cookies_from_browser=config.cookies_from_browser,
    )
    extractor = None

    try:
        extractor = _get_extractor(args, args.url, config, session)
        if not extractor:
            print(f"{Colors.RED}✗ No extractor found for URL{Colors.RESET}")
            return 1

        media_info = extractor.extract(args.url)

        if args.json:
            print(media_info.to_json())
        else:
            print(f"\n{Colors.BOLD}Title:{Colors.RESET} {media_info.title}")
            print(f"{Colors.DIM}ID:{Colors.RESET} {media_info.id}")
            print(f"{Colors.DIM}URL:{Colors.RESET} {media_info.url}")
            print(f"{Colors.DIM}Extractor:{Colors.RESET} {media_info.extractor}")

            if media_info.description:
                print(f"\n{Colors.DIM}Description:{Colors.RESET} {media_info.description[:200]}")
            if media_info.duration:
                mins, secs = divmod(media_info.duration, 60)
                print(f"{Colors.DIM}Duration:{Colors.RESET} {mins}:{secs:02d}")
            if media_info.uploader:
                print(f"{Colors.DIM}Uploader:{Colors.RESET} {media_info.uploader}")
            if media_info.upload_date:
                print(f"{Colors.DIM}Upload Date:{Colors.RESET} {media_info.upload_date}")
            if media_info.thumbnail:
                print(f"{Colors.DIM}Thumbnail:{Colors.RESET} {media_info.thumbnail}")

            print(f"\n{Colors.BOLD}Formats available:{Colors.RESET} {len(media_info.formats)}")
            if media_info.best_format:
                print(f"{Colors.GREEN}Best quality:{Colors.RESET} {media_info.best_format.format_note}")

        return 0
    finally:
        if extractor is not None:
            try:
                extractor.close()
            except Exception:
                pass
        session.close()


def cmd_list_extractors(args, config: Config) -> int:
    """Handle list-extractors command."""
    metadata_list = registry.list_all()

    print(f"\n{Colors.BOLD}Available extractors ({len(metadata_list)}):{Colors.RESET}")
    print("-" * 88)

    for meta in metadata_list:
        ext_class = meta.cls
        browser = f" {Colors.YELLOW}[browser]{Colors.RESET}" if ext_class.REQUIRES_BROWSER else ""
        generic = f" {Colors.MAGENTA}[generic]{Colors.RESET}" if meta.is_generic else ""
        version = f" v{meta.version}" if meta.version else ""
        print(
            f"  {Colors.CYAN}{meta.name:<18}{Colors.RESET} "
            f"{ext_class.EXTRACTOR_DESCRIPTION}"
            f" {Colors.DIM}(source={meta.source.value}, priority={meta.priority}){Colors.RESET}"
            f"{version}{browser}{generic}"
        )
        if meta.replaces:
            print(f"    {Colors.DIM}replaces: {', '.join(meta.replaces)}{Colors.RESET}")
        if ext_class.URL_PATTERNS:
            for pattern in ext_class.URL_PATTERNS[:2]:
                print(f"    {Colors.DIM}└─ {pattern[:60]}{Colors.RESET}")

    return 0


async def cmd_diagnose(args, config: Config) -> int:
    """Run the integrated diagnostic subsystem."""
    from config import DiagnosticConfig
    from diagnostic_main import run_diagnostic

    diag_config = DiagnosticConfig()
    diag_config.report.output_dir = args.output_dir
    diag_config.browser.headless = not args.no_headless
    diag_config.browser.stealth_mode = not args.no_stealth
    diag_config.report.take_screenshots = not args.no_screenshot
    diag_config.scan.max_total_time = args.timeout
    diag_config.scan.max_scrolls = args.max_scroll
    diag_config.scan.max_js_files = args.max_js

    proxy_value = args.proxy or config.proxy.http or config.proxy.https or config.proxy.socks5
    if proxy_value:
        diag_config.proxy = proxy_value

    if config.extractor.user_agent:
        diag_config.browser.user_agents = [config.extractor.user_agent]

    try:
        result, _report = await run_diagnostic(args.url, diag_config)
    except RuntimeError as exc:
        print(f"{Colors.RED}✗ Diagnostic unavailable: {exc}{Colors.RESET}")
        return 1

    return 0 if not result.errors else 1


def main() -> int:
    """Main entry point."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        print(BANNER)
        parser.print_help()
        return 0

    # Load config
    config = Config.load(args.config)

    if args.debug:
        config.log_level = "DEBUG"
    elif args.verbose:
        config.log_level = "INFO"

    setup_logging(config)
    extractors.bootstrap_extractors(config, log_summary=False)

    # Route to command handler
    try:
        if args.command in ['download', 'dl']:
            return asyncio.run(cmd_download(args, config))
        elif args.command == 'erome':
            return asyncio.run(cmd_erome(args, config))
        elif args.command == 'batch':
            return asyncio.run(cmd_batch(args, config))
        elif args.command in ['list-formats', 'formats', 'lf']:
            return asyncio.run(cmd_list_formats(args, config))
        elif args.command == 'info':
            return asyncio.run(cmd_info(args, config))
        elif args.command in ['list-extractors', 'extractors']:
            return cmd_list_extractors(args, config)
        elif args.command == 'diagnose':
            return asyncio.run(cmd_diagnose(args, config))
        else:
            parser.print_help()
            return 1
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}⚠ Download cancelled by user{Colors.RESET}")
        return 130
    except Exception as e:
        print(f"{Colors.RED}✗ Error: {e}{Colors.RESET}")
        if args.debug:
            import traceback
            traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
