# pyright: reportOptionalMemberAccess=false, reportPossiblyUnboundVariable=false, reportOptionalCall=false, reportAttributeAccessIssue=false, reportCallIssue=false
"""
Smart download orchestration controller.

Two interfaces:
- smart_download(): Interactive version with Textual UI feedback
- smart_download_headless(): Non-interactive version for workflow nodes

CRITICAL: The fallback chain is IDENTICAL to original tui.py:
  1. Custom extractors (PubJav, SupJav, JWPlayer, HLS, Social)
  2. Direct yt-dlp backend for social platforms
  3. yt-dlp standalone
  4. Generic HTML extractor

Session lifecycle: try...finally: session.close()
Async boundary: asyncio.run() for downloads inside thread workers
History recording: on BOTH success AND failure paths
"""

import asyncio
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..engine.events import event_bus
from ..engine.errors import ErrorBoundary, SafeExecutor
from ..logging_ import log_manager

from config import Config
from utils.progress import ProgressBar
from utils.network import SessionManager
from core.downloader import Downloader
from core.history import DownloadHistory
from models.media import MediaInfo, StreamType
from extractors.base import registry

logger = logging.getLogger(__name__)


_PROGRESS_MESSAGE_RE = re.compile(r"^(?:Progress:|HLS progress:|yt-dlp progress:|[A-Z-]+ progress:)")
_FINAL_MESSAGE_RE = re.compile(r"^(?:✓ Downloaded|✗ Download failed|Reason:|Saved file:|Transfer failed:)")


def _log_kind_for_message(message: str) -> str:
    if _PROGRESS_MESSAGE_RE.match(message or ""):
        return "progress"
    if _FINAL_MESSAGE_RE.match(message or ""):
        return "final"
    return "event"


# ── Format Helpers (preserved from original tui.py) ────────

def _compact_error_message(message: str, limit: int = 120) -> str:
    text = (message or '').strip()
    if not text:
        return ''
    if 'Cloudflare' in text or '\n' in text:
        return text
    return text[:limit]


def _friendly_download_error(message: str) -> str:
    text = _compact_error_message(message, limit=220)
    lowered = text.lower()
    if "http 403" in lowered:
        return (
            "HTTP 403: server rejected the media URL. "
            "This usually means the stream needs fresh cookies/referer headers or the preview URL blocks direct download."
        )
    return text


def _is_restricted_format(fmt: Any) -> bool:
    label = f"{getattr(fmt, 'label', '')} {getattr(fmt, 'format_note', '')}".lower()
    url = str(getattr(fmt, 'url', '') or '').lower()
    return (
        'preview' in label
        or 'trailer' in label
        or 'litevideo/freepv' in url
        or ('dmm.co.jp' in url and 'freepv' in url)
    )


def _pick_downloadable_format(formats: List[Any], selected_format: Optional[Any]) -> Optional[Any]:
    if selected_format is not None:
        return None if _is_restricted_format(selected_format) else selected_format

    for candidate in formats:
        if not _is_restricted_format(candidate):
            return candidate
    return None


def _human_size(num_bytes: float) -> str:
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
    if fmt.filesize and fmt.filesize > 0:
        return int(fmt.filesize)
    if not duration_seconds or duration_seconds <= 0:
        return None
    if not fmt.bitrate or fmt.bitrate <= 0:
        return None
    return int((fmt.bitrate * 1000 / 8) * duration_seconds)


def _guess_bitrate_kbps(fmt) -> Optional[int]:
    if fmt.bitrate and fmt.bitrate > 0:
        return int(fmt.bitrate)
    candidates = [fmt.format_id or "", fmt.quality or "", fmt.label or ""]
    for text in candidates:
        match = re.search(r"(\d{3,5})\s*kbps", text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    match = re.search(r"(?:^|[-_])hls-(\d{3,5})(?:[-_]|$)", (fmt.format_id or "").lower())
    if match:
        return int(match.group(1))
    return None


def _probe_hls_duration_seconds(fmt, timeout_seconds: int = 12) -> Optional[int]:
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
    if fmt.filesize and fmt.filesize > 0:
        return _human_size(fmt.filesize)
    effective_bitrate = bitrate_kbps if bitrate_kbps and bitrate_kbps > 0 else fmt.bitrate
    if effective_bitrate:
        class _Tmp:
            bitrate = effective_bitrate
            filesize = None
        estimate = _estimated_filesize_bytes(_Tmp, duration_seconds)
    else:
        estimate = None
    if estimate:
        return f"~{_human_size(estimate)}"
    if effective_bitrate and effective_bitrate > 0:
        return f"~{effective_bitrate}kbps"
    return "unknown"


def _ordered_formats(formats) -> List:
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


def _parse_selection_ranges(selection: str, max_value: int) -> List[int]:
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


# ── Format Data Builders (for Textual widgets) ─────────────

def build_format_table_data(formats, duration_seconds: Optional[int] = None) -> List[Dict[str, str]]:
    """
    Build format table data as a list of dicts.
    Used by Textual DataTable widget.
    """
    resolved_duration = _resolve_duration_for_table(formats, duration_seconds)

    bitrate_overrides = {}
    for fmt in formats:
        guessed = _guess_bitrate_kbps(fmt)
        if guessed and guessed > 0:
            bitrate_overrides[fmt.format_id] = guessed

    rows = []
    for idx, fmt in enumerate(formats, 1):
        bitrate_guess = bitrate_overrides.get(fmt.format_id)
        size = _format_size_cell(fmt, resolved_duration, bitrate_kbps=bitrate_guess)
        resolution = fmt.resolution
        if resolution == "unknown" and (not fmt.is_video and fmt.is_audio):
            resolution = "audio"

        rows.append({
            "no": str(idx),
            "id": fmt.format_id,
            "resolution": resolution,
            "quality": fmt.quality or "",
            "type": fmt.stream_type.value,
            "size": size,
            "note": fmt.label or "",
            "_format": fmt,  # Reference to actual format object
        })

    return rows


def build_erome_table_data(items) -> List[Dict[str, str]]:
    """Build EroMe item table data for Textual DataTable."""
    rows = []
    for idx, item in enumerate(items, 1):
        fmt = item.format
        rows.append({
            "no": str(idx),
            "type": item.media_type.upper(),
            "title": item.title,
            "quality": fmt.quality or "-",
            "ext": fmt.ext or "bin",
            "_item": item,
        })
    return rows


# ── Headless Download (for workflow nodes) ─────────────────

def smart_download_headless(
    url: str,
    config: Config,
    quality: str = "best",
    audio_only: bool = False,
    cookies_browser: Optional[str] = None,
    progress_callback: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Non-interactive smart download for workflow nodes.
    Returns dict with {success, filepath, error, extractor, title}.

    IDENTICAL fallback chain to smart_download().
    No UI interaction — picks best format automatically.
    """
    result = {
        "success": False,
        "filepath": "",
        "error": "",
        "extractor": "",
        "title": "",
    }

    history = DownloadHistory()
    last_error_message: Optional[str] = None
    from extractors.ytdlp import YtdlpExtractor, YTDLP_AVAILABLE

    def _notify(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)
        log_manager.debug(msg, source="downloader", kind=_log_kind_for_message(msg))

    # === Step 1: Try custom extractors ===
    session = SessionManager(
        user_agent=config.extractor.user_agent,
        proxy=config.proxy.to_dict(),
        cookies_file=config.cookies_file,
        cookies_from_browser=cookies_browser or config.cookies_from_browser,
    )
    extractor = None

    try:
        extractor_class = registry.find_extractor(url)
        if extractor_class and extractor_class.EXTRACTOR_NAME != "generic":
            _notify(f"Using extractor: {extractor_class.EXTRACTOR_NAME}")
            try:
                ext_config = dict(vars(config))
                if cookies_browser:
                    ext_config['cookies_from_browser'] = cookies_browser
                extractor = extractor_class(session, config=ext_config)
                media_info = extractor.extract(url)

                if media_info and media_info.formats:
                    ordered = _ordered_formats(media_info.formats)
                    if audio_only:
                        audio_fmts = media_info.get_audio_formats()
                        fmt = audio_fmts[0] if audio_fmts else ordered[0]
                    else:
                        fmt = ordered[0]  # Best quality

                    if fmt is None:
                        raise RuntimeError(
                            "Only preview/restricted media URLs were found for this page"
                        )

                    if fmt:
                        if YtdlpExtractor.uses_direct_backend(media_info):
                            output = YtdlpExtractor.download_media_info(
                                media_info, ext_config,
                                selected_format=fmt,
                                quality=quality,
                                audio_only=audio_only,
                                display_name=media_info.title,
                                status_callback=_notify,
                            )
                            if not output:
                                raise RuntimeError("Direct yt-dlp returned no file")
                        else:
                            downloader = Downloader(config, session=session)
                            try:
                                output = asyncio.run(
                                    downloader.download(media_info, fmt, status_callback=_notify)
                                )
                            except Exception:
                                fallback_fmt = None
                                if fmt.stream_type == StreamType.HLS:
                                    candidates = [
                                        f for f in media_info.formats
                                        if f.format_id != fmt.format_id and f.stream_type != StreamType.HLS
                                    ]
                                    if candidates:
                                        candidates.sort(key=lambda f: f.quality_score, reverse=True)
                                        fallback_fmt = candidates[0]
                                if not fallback_fmt:
                                    raise
                                _notify(f"HLS failed, fallback to {fallback_fmt.format_note}")
                                output = asyncio.run(
                                    downloader.download(media_info, fallback_fmt, status_callback=_notify)
                                )
                                fmt = fallback_fmt

                        history.record(
                            url=url, title=media_info.title,
                            extractor=media_info.extractor,
                            quality=fmt.quality or '',
                            filepath=output, status="completed",
                        )
                        result.update(
                            success=True, filepath=str(output),
                            extractor=media_info.extractor, title=media_info.title,
                        )

                        event_bus.emit(
                            "download.completed", source="downloader",
                            url=url, filepath=str(output), extractor=media_info.extractor,
                        )
                        return result
            except Exception as e:
                last_error_message = str(e)
                _notify(f"Custom extractor failed: {_compact_error_message(str(e))}")
                logger.debug(f"Custom extractor error: {e}")
    finally:
        if extractor is not None:
            try:
                extractor.close()
            except Exception:
                pass
        session.close()

    # === Step 2: Try yt-dlp ===
    if YTDLP_AVAILABLE:
        _notify("Trying yt-dlp...")
        yt_session = None
        yt_ext = None
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
                ordered = _ordered_formats(yt_info.formats)
                if audio_only:
                    audio_fmts = yt_info.get_audio_formats()
                    yt_fmt = audio_fmts[0] if audio_fmts else ordered[0]
                else:
                    yt_fmt = ordered[0]

                if yt_fmt:
                    format_selector = YtdlpExtractor.build_format_selector(
                        yt_info, yt_fmt, audio_only=audio_only,
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
                status_callback=_notify,
            )
            if filepath:
                history.record(
                    url=url, title=Path(filepath).stem if filepath else '',
                    extractor="yt-dlp", filepath=filepath or '', status="completed",
                )
                result.update(
                    success=True, filepath=str(filepath),
                    extractor="yt-dlp", title=Path(filepath).stem,
                )
                event_bus.emit(
                    "download.completed", source="downloader",
                    url=url, filepath=str(filepath), extractor="yt-dlp",
                )
                return result
        except Exception as e:
            last_error_message = str(e)
            _notify(f"yt-dlp failed: {_compact_error_message(str(e))}")
        finally:
            if yt_ext is not None:
                try:
                    yt_ext.close()
                except Exception:
                    pass
            if yt_session:
                yt_session.close()

    # === Step 3: Try generic extractor ===
    _notify("Trying generic extractor...")
    session = None
    ext = None
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
            ordered = _ordered_formats(media_info.formats)
            fmt = ordered[0] if ordered else None

            if fmt:
                downloader = Downloader(config, session=session)
                output = asyncio.run(
                    downloader.download(media_info, fmt, status_callback=_notify)
                )
                history.record(
                    url=url, title=media_info.title,
                    extractor="generic", filepath=output, status="completed",
                )
                result.update(
                    success=True, filepath=str(output),
                    extractor="generic", title=media_info.title,
                )
                event_bus.emit(
                    "download.completed", source="downloader",
                    url=url, filepath=str(output), extractor="generic",
                )
                return result
    except Exception as e:
        last_error_message = str(e)
        logger.debug(f"Generic extractor error: {e}")
    finally:
        if ext is not None:
            try:
                ext.close()
            except Exception:
                pass
        if session:
            session.close()

    # All failed
    error_msg = last_error_message or "All methods failed"
    history.record(url=url, status="failed", error="All methods failed")
    result["error"] = _friendly_download_error(error_msg)

    event_bus.emit(
        "download.failed", source="downloader",
        url=url, error=result["error"],
    )
    return result


# ── Interactive Download (for Textual screens) ─────────────

def smart_download_interactive(
    url: str,
    config: Config,
    quality: str = "best",
    audio_only: bool = False,
    cookies_browser: Optional[str] = None,
    selected_format: Optional[Any] = None,
    preview_only: bool = False,
    on_status: Optional[Callable[[str], None]] = None,
    on_formats: Optional[Callable[[List], None]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    """
    Interactive smart download for Textual UI.

    Like smart_download_headless but:
    - Calls on_status(msg) for status updates
    - Calls on_formats(format_list) when formats are available
    - Accepts pre-selected format from UI

    Returns same dict as headless version.
    """
    result = {
        "success": False,
        "filepath": "",
        "error": "",
        "extractor": "",
        "title": "",
        "formats": [],
    }

    history = DownloadHistory()
    last_error_message: Optional[str] = None
    from extractors.ytdlp import YtdlpExtractor, YTDLP_AVAILABLE

    def _status(msg: str) -> None:
        if on_status:
            on_status(msg)
        log_manager.info(msg, source="downloader", kind=_log_kind_for_message(msg))

    progress_state = {
        "started_at": 0.0,
        "last_emit_at": 0.0,
    }

    def _progress(downloaded: int, total: int) -> None:
        if on_progress:
            try:
                on_progress(int(downloaded), int(total or 0))
            except Exception:
                pass

        now = time.monotonic()
        if not progress_state["started_at"]:
            progress_state["started_at"] = now

        last_emit_at = float(progress_state["last_emit_at"])
        should_emit = (not last_emit_at) or ((now - last_emit_at) >= 1.0)
        if total and total > 0 and downloaded >= total:
            should_emit = True
        if not should_emit or downloaded <= 0:
            return

        progress_state["last_emit_at"] = now
        elapsed = max(now - float(progress_state["started_at"]), 0.001)
        speed_bytes = downloaded / elapsed

        if total and total > 0:
            percent = int((downloaded / total) * 100)
            eta_seconds = None
            if speed_bytes > 0:
                eta_seconds = int(max(0.0, (total - downloaded) / speed_bytes))
            detail = (
                f"{percent}%"
                f" • {_human_size(downloaded)} / {_human_size(total)}"
                f" • {_human_size(speed_bytes)}/s"
            )
            if eta_seconds is not None:
                detail += f" • ETA {ProgressBar._format_time(eta_seconds)}"
            _status(f"Progress: {detail}")
        elif downloaded > 0:
            _status(
                f"Progress: {_human_size(downloaded)} downloaded"
                f" • {_human_size(speed_bytes)}/s"
            )

    _status(f"🔗 {url}")

    # Check already downloaded
    if history.is_downloaded(url):
        _status(f"⚠ Already downloaded: {url}")
        # In interactive mode, let UI decide whether to proceed
        # Don't block — return with a flag
        result["already_downloaded"] = True

    event_bus.emit("download.started", source="downloader", url=url)

    # === Step 1: Custom extractors ===
    session = SessionManager(
        user_agent=config.extractor.user_agent,
        proxy=config.proxy.to_dict(),
        cookies_file=config.cookies_file,
        cookies_from_browser=cookies_browser or config.cookies_from_browser,
    )
    extractor = None

    try:
        extractor_class = registry.find_extractor(url)
        if extractor_class and extractor_class.EXTRACTOR_NAME != "generic":
            _status(f"Using extractor: {extractor_class.EXTRACTOR_NAME}")
            try:
                ext_config = dict(vars(config))
                if cookies_browser:
                    ext_config['cookies_from_browser'] = cookies_browser
                extractor = extractor_class(session, config=ext_config)

                _status(f"Extracting with {extractor_class.EXTRACTOR_NAME}...")
                media_info = extractor.extract(url)

                if media_info and media_info.formats:
                    _status(f"Found {len(media_info.formats)} format(s)")
                    result["title"] = media_info.title

                    if audio_only:
                        audio_fmts = media_info.get_audio_formats()
                        candidates = audio_fmts if audio_fmts else media_info.formats
                    else:
                        candidates = media_info.formats

                    ordered = _ordered_formats(candidates)
                    result["formats"] = ordered

                    # Notify UI about available formats
                    if on_formats:
                        on_formats(ordered)

                    # Use pre-selected format or best
                    if preview_only:
                        _status(f"Formats ready for {url}. Select one, then click Download Selected.")
                        return result

                    if preview_only and ordered and all(_is_restricted_format(f) for f in ordered):
                        _status("Only preview/restricted formats found; direct download will likely be blocked.")

                    fmt = _pick_downloadable_format(ordered, selected_format)

                    if fmt is None:
                        raise RuntimeError(
                            "Only preview/restricted media URLs were found for this page"
                        )

                    if fmt:
                        _status(f"Format: {fmt.format_note}")

                        if YtdlpExtractor.uses_direct_backend(media_info):
                            _status("Mode: yt-dlp direct backend")
                            output = YtdlpExtractor.download_media_info(
                                media_info, ext_config,
                                selected_format=fmt,
                                quality=quality,
                                audio_only=audio_only,
                                display_name=media_info.title,
                                progress_callback=_progress,
                                status_callback=_status,
                            )
                            if not output:
                                raise RuntimeError("Direct yt-dlp returned no file")
                        else:
                            downloader = Downloader(config, session=session)
                            try:
                                output = asyncio.run(
                                    downloader.download(
                                        media_info,
                                        fmt,
                                        progress_callback=_progress,
                                        status_callback=_status,
                                    )
                                )
                            except Exception:
                                fallback_fmt = None
                                if fmt.stream_type == StreamType.HLS:
                                    candidates = [
                                        f for f in media_info.formats
                                        if f.format_id != fmt.format_id and f.stream_type != StreamType.HLS
                                    ]
                                    if candidates:
                                        candidates.sort(key=lambda f: f.quality_score, reverse=True)
                                        fallback_fmt = candidates[0]
                                if not fallback_fmt:
                                    raise
                                _status(f"HLS failed, retrying with {fallback_fmt.format_note}")
                                output = asyncio.run(
                                    downloader.download(
                                        media_info,
                                        fallback_fmt,
                                        progress_callback=_progress,
                                        status_callback=_status,
                                    )
                                )
                                fmt = fallback_fmt

                        _status(f"✓ Downloaded: {media_info.title} -> {output}")
                        history.record(
                            url=url, title=media_info.title,
                            extractor=media_info.extractor,
                            quality=fmt.quality or '',
                            filepath=output, status="completed",
                        )
                        result.update(
                            success=True, filepath=str(output),
                            extractor=media_info.extractor,
                        )
                        event_bus.emit(
                            "download.completed", source="downloader",
                            url=url, filepath=str(output), extractor=media_info.extractor,
                        )
                        return result
            except Exception as e:
                last_error_message = str(e)
                _status(f"Custom extractor failed: {_compact_error_message(str(e))}")
                logger.debug(f"Custom extractor error: {e}")
    finally:
        if extractor is not None:
            try:
                extractor.close()
            except Exception:
                pass
        session.close()

    # === Step 2: yt-dlp ===
    if YTDLP_AVAILABLE:
        _status("Trying yt-dlp...")
        yt_session = None
        yt_ext = None
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

            _status("Extracting with yt-dlp...")
            yt_info = yt_ext.extract(url)

            format_selector = None
            if yt_info and yt_info.formats:
                _status(f"Found {len(yt_info.formats)} format(s)")
                result["title"] = yt_info.title

                ordered = _ordered_formats(yt_info.formats)
                result["formats"] = ordered

                if on_formats:
                    on_formats(ordered)

                if preview_only:
                    _status(f"Formats ready for {url}. Select one, then click Download Selected.")
                    return result

                if preview_only and ordered and all(_is_restricted_format(f) for f in ordered):
                    _status("Only preview/restricted formats found; direct download will likely be blocked.")

                yt_fmt = _pick_downloadable_format(ordered, selected_format)

                if yt_fmt is None and ordered:
                    raise RuntimeError(
                        "Only preview/restricted media URLs were found for this page"
                    )

                if yt_fmt:
                    _status(f"Format: {yt_fmt.format_note}")
                    format_selector = YtdlpExtractor.build_format_selector(
                        yt_info, yt_fmt, audio_only=audio_only,
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
                progress_callback=_progress,
                status_callback=_status,
            )
            if filepath:
                saved_name = yt_info.title if yt_info else Path(filepath).stem
                _status(f"✓ Downloaded (yt-dlp): {saved_name} -> {filepath}")
                history.record(
                    url=url, title=Path(filepath).stem if filepath else '',
                    extractor="yt-dlp", filepath=filepath or '', status="completed",
                )
                result.update(
                    success=True, filepath=str(filepath),
                    extractor="yt-dlp", title=Path(filepath).stem,
                )
                event_bus.emit(
                    "download.completed", source="downloader",
                    url=url, filepath=str(filepath), extractor="yt-dlp",
                )
                return result
            else:
                _status("yt-dlp returned no file")
        except Exception as e:
            last_error_message = str(e)
            _status(f"yt-dlp failed: {_compact_error_message(str(e))}")
        finally:
            if yt_ext is not None:
                try:
                    yt_ext.close()
                except Exception:
                    pass
            if yt_session:
                yt_session.close()
    else:
        _status("yt-dlp not installed, skipping")

    # === Step 3: Generic extractor ===
    _status("Trying generic extractor...")
    session = None
    ext = None
    try:
        session = SessionManager(
            user_agent=config.extractor.user_agent,
            proxy=config.proxy.to_dict(),
            cookies_file=config.cookies_file,
            cookies_from_browser=cookies_browser or config.cookies_from_browser,
        )
        from extractors.generic import GenericExtractor
        ext = GenericExtractor(session, config=vars(config))

        _status("Extracting with generic...")
        media_info = ext.extract(url)

        if media_info and media_info.formats:
            ordered = _ordered_formats(media_info.formats)
            result["formats"] = ordered

            if on_formats:
                on_formats(ordered)

            if preview_only:
                _status(f"Formats ready for {url}. Select one, then click Download Selected.")
                return result

            if preview_only and ordered and all(_is_restricted_format(f) for f in ordered):
                _status("Only preview/restricted formats found; direct download will likely be blocked.")

            fmt = _pick_downloadable_format(ordered, selected_format)

            if fmt is None and ordered:
                raise RuntimeError(
                    "Only preview/restricted media URLs were found for this page"
                )

            if fmt:
                downloader = Downloader(config, session=session)
                output = asyncio.run(
                    downloader.download(
                        media_info,
                        fmt,
                        progress_callback=_progress,
                        status_callback=_status,
                    )
                )
                _status(f"✓ Downloaded: {media_info.title} -> {output}")
                history.record(
                    url=url, title=media_info.title,
                    extractor="generic", filepath=output, status="completed",
                )
                result.update(
                    success=True, filepath=str(output),
                    extractor="generic", title=media_info.title,
                )
                event_bus.emit(
                    "download.completed", source="downloader",
                    url=url, filepath=str(output), extractor="generic",
                )
                return result
    except Exception as e:
        last_error_message = str(e)
        logger.debug(f"Generic extractor error: {e}")
    finally:
        if ext is not None:
            try:
                ext.close()
            except Exception:
                pass
        if session:
            session.close()

    # All failed
    error_msg = last_error_message or "All methods failed"
    _status(f"✗ Download failed for: {url}")
    _status(f"Reason: {_friendly_download_error(error_msg)}")
    history.record(url=url, status="failed", error="All methods failed")
    result["error"] = _friendly_download_error(error_msg)

    event_bus.emit("download.failed", source="downloader", url=url, error=result["error"])
    return result


# ── EroMe Download Helper ─────────────────────────────────

def erome_download_execute(
    album: Dict[str, Any],
    selected_items: List,
    album_dir: Path,
    config: Config,
    on_status: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Execute EroMe download for selected items.
    Called from Textual screen worker thread.

    Returns {success_count, failed_count, results}
    """
    from core.erome_download import (
        download_erome_jobs,
        erome_photo_parallel_workers,
        erome_video_uses_aria2,
        prepare_erome_download_jobs,
    )

    def _status(msg: str) -> None:
        if on_status:
            on_status(msg)
        log_manager.info(msg, source="erome")

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
        _status(f"Mode: {', '.join(mode_parts)}")

    history = DownloadHistory()

    def _on_photo_batch_start(batch, workers: int) -> None:
        if len(batch) > 1 and workers > 1:
            _status(f"Photo batch: {len(batch)} item(s), {workers} worker(s)")

    def _on_item_start(job, mode: str) -> None:
        mode_label = "aria2" if mode == 'aria2' else "standard"
        _status(f"[{job.order}/{job.total}] {job.item.media_type.upper()} [{mode_label}] - {job.item.title}")

    def _on_item_success(r) -> None:
        _status(f"✓ [{r.job.order}/{r.job.total}] Downloaded: {r.output_path}")

    def _on_item_failure(r) -> None:
        _status(f"✗ [{r.job.order}/{r.job.total}] Failed: {r.error}")

    results = asyncio.run(
        download_erome_jobs(
            jobs, config,
            on_item_start=_on_item_start,
            on_item_success=_on_item_success,
            on_item_failure=_on_item_failure,
            on_photo_batch_start=_on_photo_batch_start,
        )
    )

    success = 0
    failed = 0
    for r in results:
        fmt = r.job.item.format
        if r.ok and r.output_path:
            success += 1
            history.record(
                url=fmt.url, title=r.job.item.title,
                extractor='erome', quality=fmt.quality or '',
                filepath=r.output_path, status='completed',
            )
        else:
            failed += 1
            history.record(
                url=fmt.url, title=r.job.item.title,
                extractor='erome', quality=fmt.quality or '',
                filepath=str(r.job.output_path),
                status='failed', error=r.error or 'Unknown error',
            )

    return {"success_count": success, "failed_count": failed, "results": results}
