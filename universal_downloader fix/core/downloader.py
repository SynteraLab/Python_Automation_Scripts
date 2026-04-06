"""
Core download engine with async support and progress tracking.
"""

import os
import re
import asyncio
import aiohttp
import aiofiles
from pathlib import Path
from typing import Optional, List, Dict, Callable, Any
import tempfile
import logging
import shutil
import time
from collections import deque
from urllib.parse import urlparse

from models.media import MediaInfo, StreamFormat, DownloadTask, StreamType
from utils.network import AsyncSessionManager, RateLimiter, SessionManager
from utils.progress import ProgressBar, MultiProgressDisplay
from utils.helpers import sanitize_filename
from config import Config

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """Download-related errors."""
    pass


class Downloader:
    """Core download engine supporting direct, HLS, and DASH downloads.
    
    Uses aria2c for fast multi-connection downloads when available.
    Falls back to aiohttp for direct downloads, FFmpeg for HLS/DASH.
    """

    def __init__(self, config: Config, session: Optional[SessionManager] = None):
        self.config = config
        self.session = session
        self.output_dir = Path(config.download.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.chunk_size = config.download.chunk_size
        self.max_concurrent = config.download.max_concurrent
        self.max_retries = config.download.max_retries
        self.use_aria2 = config.download.use_aria2
        self.aria2_connections = config.download.aria2_connections

        self._rate_limiter = RateLimiter(config.download.rate_limit)

        # Initialize aria2
        self._aria2 = None
        if self.use_aria2:
            from core.aria2 import Aria2Downloader
            self._aria2 = Aria2Downloader(
                aria2c_path=config.download.aria2_path,
                connections=self.aria2_connections,
                max_retries=self.max_retries,
                proxy=config.proxy.http or config.proxy.socks5,
                user_agent=config.extractor.user_agent,
            )
            if self._aria2.is_available:
                actual_conn = min(self.aria2_connections, 16)
                print(f"  ⚡ aria2c detected — using {actual_conn}x multi-connection download")
            else:
                print(f"  ⚠ aria2c not found, using standard downloader")
                print(f"    Install for faster downloads: brew install aria2")
                self._aria2 = None

    def _network_timeout(self) -> int:
        """Resolve network timeout from config with safe lower bound."""
        configured = getattr(self.config.download, 'timeout', 60)
        try:
            timeout = int(configured)
        except Exception:
            timeout = 60
        return max(30, timeout)

    @staticmethod
    def _progress_label(title: str, suffix: Optional[str] = None) -> str:
        """Build a compact progress label."""
        cleaned = re.sub(r'\s+', ' ', (title or 'download')).strip()
        if suffix:
            cleaned = f"{cleaned} {suffix}"
        return cleaned[:22] or 'download'

    @staticmethod
    def _estimate_total_bytes(
        format_: StreamFormat,
        duration_seconds: Optional[float] = None,
    ) -> Optional[int]:
        """Estimate total bytes from filesize or bitrate metadata."""
        if format_.filesize and format_.filesize > 0:
            return int(format_.filesize)

        if duration_seconds and duration_seconds > 0 and format_.bitrate and format_.bitrate > 0:
            return int((format_.bitrate * 1000 / 8) * duration_seconds)

        return None

    @staticmethod
    def _format_display_name(format_: StreamFormat) -> str:
        """Build a readable name for retry messages."""
        return (
            format_.format_note
            or format_.label
            or format_.format_id
            or format_.stream_type.value
        )

    @staticmethod
    def _cleanup_partial_output(output_path: Path) -> None:
        """Remove partial files before retrying another source."""
        cleanup_targets = [
            output_path,
            output_path.with_suffix('.audio.tmp'),
            output_path.with_suffix('.merged.mp4'),
        ]
        for target in cleanup_targets:
            try:
                target.unlink(missing_ok=True)
            except Exception:
                continue

    @staticmethod
    def _emit_status(
        status_callback: Optional[Callable[[str], None]],
        message: str,
    ) -> None:
        """Send a best-effort status update to the caller."""
        if not status_callback:
            return
        try:
            status_callback(message)
        except Exception:
            pass

    @staticmethod
    def _emit_percent_status(
        status_callback: Optional[Callable[[str], None]],
        state: Dict[str, Any],
        prefix: str,
        current: float,
        total: float,
        detail: str,
    ) -> None:
        """Emit throttled percentage updates for long-running stages."""
        if not status_callback or total <= 0:
            return

        now = time.monotonic()
        last_emit_at = float(state.get("last_emit_at", 0.0))
        percent = int(max(0.0, min(100.0, (current / total) * 100.0)))
        if current < total and last_emit_at and (now - last_emit_at) < 1.0:
            return

        state["last_emit_at"] = now
        Downloader._emit_status(status_callback, f"{prefix}: {percent}% ({detail})")

    def _fallback_candidates(
        self,
        media_info: MediaInfo,
        selected_format: StreamFormat,
    ) -> List[StreamFormat]:
        """Return ordered alternative formats for extractor-specific retries."""
        if selected_format.stream_type != StreamType.HLS:
            return []

        if media_info.extractor not in {'pubjav', 'supjav', 'vstream'}:
            return []

        seen_urls = {selected_format.url}
        seen_ids = {selected_format.format_id}
        candidates: List[StreamFormat] = []

        if media_info.extractor == 'pubjav':
            allowed_streams = {StreamType.HLS}
        else:
            allowed_streams = {StreamType.HLS, StreamType.DIRECT, StreamType.PROGRESSIVE}

        for candidate in media_info.formats:
            if candidate.stream_type not in allowed_streams:
                continue
            if candidate.url in seen_urls or candidate.format_id in seen_ids:
                continue
            seen_urls.add(candidate.url)
            seen_ids.add(candidate.format_id)
            candidates.append(candidate)

        if media_info.extractor in {'supjav', 'vstream'}:
            selected_height = selected_format.height or 0
            candidates.sort(
                key=lambda candidate: (
                    0 if candidate.stream_type == StreamType.HLS else 1,
                    0 if (candidate.height or 0) == selected_height and selected_height else 1,
                    -candidate.quality_score,
                )
            )

        return candidates

    @staticmethod
    def _parse_cookie_header(cookie_header: Optional[str]) -> Dict[str, str]:
        """Parse a Cookie header string into a dictionary."""
        parsed: Dict[str, str] = {}
        if not cookie_header:
            return parsed

        for chunk in cookie_header.split(';'):
            if '=' not in chunk:
                continue
            name, value = chunk.split('=', 1)
            name = name.strip()
            value = value.strip()
            if name:
                parsed[name] = value
        return parsed

    def _session_cookies_for_url(self, url: Optional[str]) -> Dict[str, str]:
        """Collect matching cookies from the extractor session for the given URL."""
        if not url or self.session is None:
            return {}

        raw_session = getattr(self.session, '_session', None)
        cookie_jar = getattr(raw_session, 'cookies', None)
        if cookie_jar is None:
            return {}

        parsed_url = urlparse(url)
        hostname = (parsed_url.hostname or '').lower()
        if not hostname:
            return {}

        path = parsed_url.path or '/'
        is_secure = parsed_url.scheme == 'https'
        matched: Dict[str, str] = {}

        for cookie in cookie_jar:
            try:
                domain = (cookie.domain or '').lstrip('.').lower()
                if domain and hostname != domain and not hostname.endswith(f'.{domain}'):
                    continue

                cookie_path = cookie.path or '/'
                if cookie_path != '/' and not path.startswith(cookie_path):
                    continue

                if getattr(cookie, 'secure', False) and not is_secure:
                    continue

                if cookie.name:
                    matched[cookie.name] = cookie.value
            except Exception:
                continue

        return matched

    def _build_request_headers(
        self,
        url: str,
        headers: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Merge browser-like defaults, extractor headers, and cookies."""
        merged: Dict[str, str] = {
            'User-Agent': self.config.extractor.user_agent,
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
        }

        if headers:
            for key, value in headers.items():
                if value is None:
                    continue
                merged[key] = str(value)

        referer = merged.get('Referer')
        if referer and 'Origin' not in merged:
            parsed = urlparse(referer)
            if parsed.scheme and parsed.netloc:
                merged['Origin'] = f'{parsed.scheme}://{parsed.netloc}'

        cookie_values = self._session_cookies_for_url(url)
        if cookies:
            for name, value in cookies.items():
                if name and value is not None:
                    cookie_values[str(name)] = str(value)

        explicit_cookie_header = merged.get('Cookie')
        if explicit_cookie_header:
            cookie_values.update(self._parse_cookie_header(explicit_cookie_header))

        if cookie_values:
            merged['Cookie'] = '; '.join(
                f'{name}={value}' for name, value in cookie_values.items()
            )

        return merged

    def _request_proxies(self) -> Optional[Dict[str, str]]:
        """Resolve proxy mapping for requests-based fetches."""
        if self.session is not None:
            raw_session = getattr(self.session, '_session', None)
            raw_proxies = getattr(raw_session, 'proxies', None) if raw_session else None
            if raw_proxies:
                return dict(raw_proxies)

        proxies = self.config.proxy.to_dict()
        return proxies or None

    def _fetch_text_with_retries(
        self,
        url: str,
        headers: Dict[str, str],
        purpose: str,
        cookies: Optional[Dict[str, str]] = None,
    ) -> str:
        """Fetch text content with retry + progressive timeout."""
        import requests as sync_requests

        purpose_lower = purpose.lower()
        is_playlist_request = 'playlist' in purpose_lower
        configured_retries = max(2, int(self.max_retries))
        retries = min(configured_retries, 3) if is_playlist_request else configured_retries
        base_timeout = self._network_timeout()
        if is_playlist_request:
            base_timeout = min(base_timeout, 20)
            timeout_step = 10
            max_timeout = 40
        else:
            timeout_step = 15
            max_timeout = 180

        last_error: Optional[Exception] = None
        request_proxies = self._request_proxies()

        for attempt in range(1, retries + 1):
            timeout = min(base_timeout + (attempt - 1) * timeout_step, max_timeout)
            print(f"  {purpose}: attempt {attempt}/{retries} (timeout {timeout}s)...")
            try:
                request_headers = self._build_request_headers(url, headers, cookies)
                resp = sync_requests.get(
                    url,
                    headers=request_headers,
                    timeout=timeout,
                    allow_redirects=True,
                    proxies=request_proxies,
                )
                resp.raise_for_status()
                print(f"  {purpose}: OK")
                return resp.text
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    wait_seconds = min(1.5 * attempt, 4.0 if is_playlist_request else 6.0)
                    error_text = str(exc).strip().replace('\n', ' ')
                    if len(error_text) > 180:
                        error_text = error_text[:177] + '...'
                    print(
                        f"  {purpose} attempt {attempt}/{retries} failed: "
                        f"{error_text}; retrying in {wait_seconds:.1f}s"
                    )
                    logger.debug(
                        "%s fetch failed (attempt %s/%s): %s",
                        purpose,
                        attempt,
                        retries,
                        exc,
                    )
                    time.sleep(wait_seconds)

        raise DownloadError(f"{purpose} request failed: {last_error}")

    def _is_png_wrapped_hls_segment(
        self,
        segment_url: str,
        headers: Dict[str, str],
        cookies: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Best-effort detection for PNG-wrapped HLS segments."""
        lower_url = segment_url.lower()
        obvious_tokens = (
            '.image',
            '.pict',
            'tiktokcdn.com',
            'tplv-',
            'ttam-origin',
            '/docs/drv',
            'googleusercontent.com/d/',
        )
        if any(token in lower_url for token in obvious_tokens):
            return True

        # Probe first bytes of first segment (without full download).
        import requests as sync_requests
        request_headers = self._build_request_headers(segment_url, headers, cookies)

        try:
            with sync_requests.get(
                segment_url,
                headers=request_headers,
                timeout=self._network_timeout(),
                stream=True,
            ) as resp:
                resp.raise_for_status()

                content_type = (resp.headers.get('Content-Type') or '').lower()
                sampled = bytearray()
                for chunk in resp.iter_content(chunk_size=1024):
                    if not chunk:
                        continue
                    sampled.extend(chunk)
                    if len(sampled) >= 8192:
                        break

                if not sampled:
                    return False

                data = bytes(sampled)
                png_sig = b'\x89PNG\r\n\x1a\n'
                if not data.startswith(png_sig):
                    return False

                # Strong signal if server also reports image payload.
                if 'image/png' in content_type:
                    return True

                # Fallback signal: TS sync bytes appear after PNG footer.
                iend_pos = data.find(b'IEND')
                if iend_pos > 0:
                    png_end = iend_pos + 8
                    for idx in range(png_end, min(len(data) - 188, png_end + 2500)):
                        if data[idx] == 0x47 and data[idx + 188] == 0x47:
                            return True
        except Exception as e:
            logger.debug(f"PNG-wrap probe failed for segment {segment_url}: {e}")

        return False

    async def download(
        self,
        media_info: MediaInfo,
        format_: StreamFormat,
        audio_format: Optional[StreamFormat] = None,
        output_path: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
        show_progress: bool = True,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Download media with the specified format."""
        resolved_output_path = self._resolve_output_path(media_info, format_, output_path)

        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)

        candidates = [format_, *self._fallback_candidates(media_info, format_)]
        attempt_errors: List[str] = []

        for index, selected_format in enumerate(candidates, start=1):
            if index > 1:
                self._cleanup_partial_output(resolved_output_path)
                self._emit_status(
                    status_callback,
                    f"Retrying source {index - 1}/{len(candidates) - 1}: "
                    f"{self._format_display_name(selected_format)}",
                )
                print(
                    f"  PubJav fallback {index - 1}/{len(candidates) - 1}: "
                    f"trying {self._format_display_name(selected_format)}..."
                )

            selected_audio_format = audio_format
            if selected_format.is_audio:
                selected_audio_format = None

            try:
                return await self._download_selected_format(
                    media_info,
                    selected_format,
                    audio_format=selected_audio_format,
                    output_path=resolved_output_path,
                    progress_callback=progress_callback,
                    show_progress=show_progress,
                    status_callback=status_callback,
                )
            except Exception as e:
                attempt_errors.append(
                    f"{self._format_display_name(selected_format)} -> {str(e)[:180]}"
                )
                logger.warning(
                    "Download attempt %s/%s failed for %s: %s",
                    index,
                    len(candidates),
                    self._format_display_name(selected_format),
                    e,
                )
                if index == len(candidates):
                    if len(candidates) == 1:
                        raise
                    error_summary = '; '.join(attempt_errors[-3:])
                    raise DownloadError(
                        f"All PubJav HLS sources failed: {error_summary}"
                    ) from e

        raise DownloadError("Download failed: no candidate formats succeeded")

    async def _download_selected_format(
        self,
        media_info: MediaInfo,
        format_: StreamFormat,
        audio_format: Optional[StreamFormat] = None,
        output_path: Optional[Path] = None,
        progress_callback: Optional[Callable] = None,
        show_progress: bool = True,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> str:
        """Download a single selected format without trying alternatives."""
        resolved_output_path = output_path or self._generate_output_path(media_info, format_)
        resolved_output_path.parent.mkdir(parents=True, exist_ok=True)

        self._emit_status(status_callback, f"Saving to: {resolved_output_path}")
        logger.info(f"Downloading: {media_info.title}")
        logger.info(f"Format: {format_.format_note}")
        logger.info(f"Output: {resolved_output_path}")

        progress_label = self._progress_label(media_info.title or resolved_output_path.stem)
        duration_hint = float(media_info.duration) if media_info.duration else None
        total_bytes_hint = self._estimate_total_bytes(format_, duration_hint)

        try:
            if format_.stream_type == StreamType.HLS:
                await self._download_hls(
                    format_,
                    resolved_output_path,
                    progress_callback,
                    description=progress_label,
                    total_duration=duration_hint,
                    status_callback=status_callback,
                )
            elif format_.stream_type == StreamType.DASH:
                await self._download_dash(
                    format_,
                    resolved_output_path,
                    progress_callback,
                    description=progress_label,
                    total_duration=duration_hint,
                    status_callback=status_callback,
                )
            else:
                await self._download_direct(
                    format_,
                    resolved_output_path,
                    progress_callback,
                    description=progress_label,
                    total_bytes=total_bytes_hint,
                    stage='download',
                    detail=format_.format_note or format_.stream_type.value,
                    show_progress=show_progress,
                    status_callback=status_callback,
                )

            # Download and merge audio if separate
            if audio_format:
                audio_path = resolved_output_path.with_suffix('.audio.tmp')
                audio_total_hint = self._estimate_total_bytes(audio_format, duration_hint)
                await self._download_direct(
                    audio_format,
                    audio_path,
                    progress_callback,
                    description=self._progress_label(media_info.title or resolved_output_path.stem, 'audio'),
                    total_bytes=audio_total_hint,
                    stage='audio',
                    detail=audio_format.format_note or 'separate audio',
                    show_progress=show_progress,
                    status_callback=status_callback,
                )

                from core.merger import FFmpegMerger
                merger = FFmpegMerger(self.config.ffmpeg_path)
                merged_path = resolved_output_path.with_suffix('.merged.mp4')
                merge_progress = ProgressBar(
                    total=duration_hint,
                    description=progress_label,
                    unit='s',
                    stage='merge',
                )
                await merger.merge_video_audio(
                    str(resolved_output_path),
                    str(audio_path),
                    str(merged_path),
                    progress=merge_progress,
                    total_duration=duration_hint,
                    status_callback=status_callback,
                )

                audio_path.unlink(missing_ok=True)
                resolved_output_path.unlink(missing_ok=True)
                merged_path.rename(resolved_output_path)

            logger.info(f"Download complete: {resolved_output_path}")
            self._emit_status(status_callback, f"Saved file: {resolved_output_path}")
            return str(resolved_output_path)

        except Exception as e:
            logger.error(f"Download failed: {e}")
            self._emit_status(status_callback, f"Transfer failed: {e}")
            raise DownloadError(f"Download failed: {e}")

    async def _download_direct(
        self,
        format_: StreamFormat,
        output_path: Path,
        progress_callback: Optional[Callable] = None,
        description: Optional[str] = None,
        total_bytes: Optional[int] = None,
        stage: str = 'download',
        detail: Optional[str] = None,
        show_progress: bool = True,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Download direct file. Uses aria2c if available, otherwise aiohttp."""
        headers = self._build_request_headers(format_.url, format_.headers, format_.cookies)
        label = description or self._progress_label(output_path.stem)
        progress_detail = detail or format_.format_note or format_.stream_type.value
        progress = None
        if show_progress:
            progress = ProgressBar(
                total=total_bytes,
                description=label,
                stage=stage,
            )
            progress.set_stage(stage, progress_detail)

        # Try aria2c first (much faster with multi-connection)
        if self._aria2 and self._aria2.is_available:
            self._emit_status(status_callback, f"Direct download via aria2c ({self.aria2_connections} connections)")
            try:
                await self._aria2.download(
                    url=format_.url,
                    output_path=str(output_path),
                    headers=headers,
                    progress=progress,
                    progress_callback=progress_callback,
                    quiet=not show_progress,
                )
                return
            except Exception as e:
                logger.warning(f"aria2c failed ({e}), falling back to standard download")
                self._emit_status(status_callback, f"aria2c failed, falling back to standard downloader: {e}")
                if show_progress:
                    progress = ProgressBar(
                        total=total_bytes,
                        description=label,
                        stage=stage,
                    )
                    progress.set_stage(stage, f"fallback | {progress_detail}")
                else:
                    progress = None

        # Fallback: standard aiohttp download
        self._emit_status(status_callback, "Direct download via standard HTTP stream")
        try:
            async with AsyncSessionManager(
                user_agent=self.config.extractor.user_agent,
                proxy=self.config.proxy.http or self.config.proxy.socks5
            ) as session:
                async with await session.get(format_.url, headers=headers) as response:
                    if response.status not in (200, 206):
                        raise DownloadError(f"HTTP {response.status} for {format_.url}")

                    response_total = int(response.headers.get('Content-Length', 0) or 0)
                    if response_total > 0:
                        if progress is not None:
                            progress.set(total=response_total, stage=stage, detail=progress_detail)

                    async with aiofiles.open(output_path, 'wb') as f:
                        downloaded = 0
                        async for chunk in response.content.iter_chunked(self.chunk_size):
                            await f.write(chunk)
                            downloaded += len(chunk)
                            if progress is not None:
                                progress.update(
                                    len(chunk),
                                    byte_amount=len(chunk),
                                    total=response_total or total_bytes,
                                    stage=stage,
                                    detail=progress_detail,
                                )
                            if progress_callback:
                                progress_callback(downloaded, response_total or int(total_bytes or 0))
                            await self._rate_limiter.limit(len(chunk))

                    if progress is not None:
                        progress.finish(detail=progress_detail)
        except Exception as e:
            if progress is not None:
                progress.error(str(e)[:48])
            raise

    async def _download_hls(
        self,
        format_: StreamFormat,
        output_path: Path,
        progress_callback: Optional[Callable] = None,
        description: Optional[str] = None,
        total_duration: Optional[float] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Download HLS stream. Detects PNG-wrapped segments automatically."""
        from core.merger import FFmpegMerger
        from extractors.hls import HLSParser

        merger = FFmpegMerger(self.config.ffmpeg_path)
        headers = dict(format_.headers) if format_.headers else {}
        label = description or self._progress_label(output_path.stem)

        # Fetch the playlist
        self._emit_status(status_callback, "HLS: fetching playlist")
        print(f"  Fetching HLS playlist...")
        try:
            playlist_content = self._fetch_text_with_retries(
                format_.url,
                headers,
                purpose="HLS playlist",
                cookies=format_.cookies,
            )
        except Exception as e:
            if merger.is_available:
                reason = "timed out" if 'timed out' in str(e).lower() else 'failed'
                self._emit_status(status_callback, f"HLS: playlist fetch {reason}, trying FFmpeg direct mode")
                print(f"  Playlist fetch {reason}, trying FFmpeg direct mode...")
                try:
                    await self._download_hls_ffmpeg(
                        format_,
                        output_path,
                        merger,
                        description=label,
                        total_duration=total_duration,
                        status_callback=status_callback,
                    )
                    return
                except Exception as ffmpeg_error:
                    raise DownloadError(
                        f"Failed to fetch HLS playlist: {e}; "
                        f"FFmpeg direct mode also failed: {ffmpeg_error}"
                    ) from ffmpeg_error
            raise DownloadError(f"Failed to fetch HLS playlist: {e}")

        # Check if master playlist → get media playlist
        parser = HLSParser(format_.url, playlist_content)
        if parser.is_master_playlist():
            variants = parser.parse_master_playlist()
            if not variants:
                raise DownloadError("No variants in master playlist")

            video_variants = [v for v in variants if v.get('is_video', True)]
            variant_pool = video_variants or variants
            best = max(
                variant_pool,
                key=lambda v: (v.get('height', 0), v.get('bitrate', 0))
            )
            playlist_content = self._fetch_text_with_retries(
                best['url'],
                headers,
                purpose="HLS media playlist",
                cookies=format_.cookies,
            )
            parser = HLSParser(best['url'], playlist_content)

        media_info = parser.parse_media_playlist()
        segments = media_info['segments']
        is_encrypted = bool(media_info.get('encryption'))
        duration_hint = media_info.get('total_duration') or total_duration

        if not segments:
            raise DownloadError("No segments found in playlist")

        # Check if segments are PNG-wrapped (turbovidhls/tiktokcdn/google drive tricks)
        first_url = segments[0]['url']
        is_png_wrapped = self._is_png_wrapped_hls_segment(first_url, headers, format_.cookies)

        if is_png_wrapped:
            self._emit_status(status_callback, f"HLS: detected PNG-wrapped segments ({len(segments)} segments)")
            print(f"  Detected PNG-wrapped segments ({len(segments)} segments)")
            await self._download_hls_png_wrapped(
                segments,
                headers,
                format_.cookies,
                output_path,
                merger,
                description=label,
                total_duration=duration_hint,
                status_callback=status_callback,
            )
        elif not is_encrypted and self.max_concurrent > 1:
            # Fast path: unencrypted HLS can be downloaded in parallel safely.
            # This is often faster than FFmpeg's sequential segment fetch.
            try:
                self._emit_status(status_callback, f"HLS: parallel segment mode ({self.max_concurrent} workers)")
                print(f"  Fast HLS mode: parallel segments ({self.max_concurrent} workers)...")
                await self._download_hls_manual(
                    segments,
                    headers,
                    format_.cookies,
                    output_path,
                    merger,
                    description=label,
                    total_duration=duration_hint,
                    status_callback=status_callback,
                )
                return
            except Exception as e:
                logger.debug(f"Fast HLS mode failed: {e}, falling back to FFmpeg")

            if merger.is_available:
                try:
                    self._emit_status(status_callback, "HLS: parallel mode failed, switching to FFmpeg")
                    print(f"  Downloading HLS stream via FFmpeg...")
                    await self._download_hls_ffmpeg(
                        format_,
                        output_path,
                        merger,
                        description=label,
                        total_duration=duration_hint,
                        status_callback=status_callback,
                    )
                    return
                except Exception as e:
                    logger.debug(f"FFmpeg failed after fast mode fallback: {e}")
                    raise
            raise DownloadError("FFmpeg is required when fast HLS mode fails")
        elif merger.is_available:
            # Normal HLS → try FFmpeg first
            try:
                self._emit_status(status_callback, "HLS: downloading with FFmpeg")
                print(f"  Downloading HLS stream via FFmpeg...")
                await self._download_hls_ffmpeg(
                    format_,
                    output_path,
                    merger,
                    description=label,
                    total_duration=duration_hint,
                    status_callback=status_callback,
                )
                return
            except Exception as e:
                logger.debug(f"FFmpeg failed: {e}, trying manual download")
                await self._download_hls_manual(
                    segments,
                    headers,
                    format_.cookies,
                    output_path,
                    merger,
                    description=label,
                    total_duration=duration_hint,
                    status_callback=status_callback,
                )
        else:
            await self._download_hls_manual(
                segments,
                headers,
                format_.cookies,
                output_path,
                merger,
                description=label,
                total_duration=duration_hint,
                status_callback=status_callback,
            )

    async def _download_hls_ffmpeg(
        self,
        format_: StreamFormat,
        output_path: Path,
        merger,
        description: Optional[str] = None,
        total_duration: Optional[float] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Download normal HLS with FFmpeg."""
        headers = self._build_request_headers(format_.url, format_.headers, format_.cookies)
        recent_stderr = deque(maxlen=8)
        self._emit_status(status_callback, "HLS: FFmpeg direct mode running")
        progress = ProgressBar(
            total=total_duration,
            description=description or self._progress_label(output_path.stem),
            unit='s',
            stage='hls',
        )
        progress.set_stage('hls', 'ffmpeg direct')
        cmd = [
            merger.ffmpeg_path, '-y',
            '-http_persistent', '1',
            '-http_multiple', '1',
            '-rw_timeout', str(self._network_timeout() * 1000000),
            '-reconnect', '1',
            '-reconnect_streamed', '1',
            '-reconnect_at_eof', '1',
            '-reconnect_delay_max', '5',
        ]
        if headers:
            header_str = ''.join(f'{k}: {v}\r\n' for k, v in headers.items())
            cmd.extend(['-headers', header_str])
        cmd.extend([
            '-i', format_.url,
            '-c', 'copy',
            '-bsf:a', 'aac_adtstoasc',
            '-movflags', '+faststart',
            str(output_path)
        ])
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        status_state = {"last_emit_at": 0.0}
        stderr_pipe = process.stderr
        assert stderr_pipe is not None
        while True:
            line = await stderr_pipe.readline()
            if not line:
                break
            decoded = line.decode('utf-8', errors='replace').strip()
            if decoded:
                recent_stderr.append(decoded)
            if 'time=' in decoded:
                time_match = re.search(r'time=(\d+:\d+:\d+(?:\.\d+)?)', decoded)
                size_match = re.search(r'size=\s*([^\s]+)', decoded)
                speed_match = re.search(r'speed=\s*([^\s]+)', decoded)
                bitrate_match = re.search(r'bitrate=\s*([^\s]+)', decoded)

                detail_parts = ['ffmpeg direct']
                if speed_match:
                    detail_parts.append(f"ffmpeg {speed_match.group(1)}")
                if bitrate_match:
                    detail_parts.append(f"bitrate {bitrate_match.group(1)}")

                progress.set(
                    value=ProgressBar.parse_duration_text(time_match.group(1)) if time_match else None,
                    total=total_duration,
                    transferred_bytes=ProgressBar.parse_size_text(size_match.group(1)) if size_match else None,
                    stage='hls',
                    detail=' | '.join(detail_parts),
                )

                current_time = ProgressBar.parse_duration_text(time_match.group(1)) if time_match else None
                now = time.monotonic()
                last_emit_at = float(status_state.get("last_emit_at", 0.0))
                if current_time is not None and (not last_emit_at or (now - last_emit_at) >= 1.0):
                    status_state["last_emit_at"] = now
                    detail_parts = []
                    if total_duration and total_duration > 0:
                        percent = int(max(0.0, min(100.0, (current_time / total_duration) * 100.0)))
                        detail_parts.append(f"{percent}%")
                        detail_parts.append(
                            f"{ProgressBar._format_time(current_time)} / {ProgressBar._format_time(total_duration)}"
                        )
                    else:
                        detail_parts.append(f"{ProgressBar._format_time(current_time)} processed")
                    size_value = ProgressBar.parse_size_text(size_match.group(1)) if size_match else None
                    if size_value is not None:
                        detail_parts.append(f"size {ProgressBar._format_size(size_value)}")
                    if speed_match and speed_match.group(1) not in {'N/A', '0x'}:
                        detail_parts.append(f"speed {speed_match.group(1)}")
                    if bitrate_match and bitrate_match.group(1) != 'N/A':
                        detail_parts.append(f"bitrate {bitrate_match.group(1)}")
                    self._emit_status(status_callback, f"HLS progress: {' • '.join(detail_parts)}")
        await process.wait()
        if process.returncode != 0:
            detail = ""
            if recent_stderr:
                detail = f": {recent_stderr[-1][:220]}"
            progress.interrupt('ffmpeg hls failed')
            raise DownloadError(f"FFmpeg HLS download failed{detail}")
        progress.finish(detail='ffmpeg direct')
        self._emit_status(status_callback, "HLS: FFmpeg direct mode finished")

    async def _download_hls_png_wrapped(
        self,
        segments: List[Dict],
        headers: Dict,
        cookies: Optional[Dict[str, str]],
        output_path: Path,
        merger,
        description: Optional[str] = None,
        total_duration: Optional[float] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Download PNG-wrapped HLS segments, strip PNG headers, concat."""
        temp_dir = Path(tempfile.mkdtemp())
        import requests as sync_requests

        total = len(segments)
        progress = ProgressBar(
            total=total,
            description=description or self._progress_label(output_path.stem),
            unit='seg',
            stage='segment',
        )
        progress.set_stage('segment', 'png unwrap')
        status_state = {
            "last_emit_at": 0.0,
            "started_at": time.monotonic(),
            "downloaded_bytes": 0,
        }
        completed_segments = {"count": 0}

        # Download and strip each segment
        stripped_files = []
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def download_and_strip(idx: int, segment: Dict) -> Optional[str]:
            async with semaphore:
                seg_path = temp_dir / f"segment_{idx:05d}.ts"

                for attempt in range(self.max_retries):
                    try:
                        # Download segment
                        loop = asyncio.get_event_loop()
                        resp = await loop.run_in_executor(
                            None,
                            lambda: sync_requests.get(
                                segment['url'],
                                headers=self._build_request_headers(segment['url'], headers, cookies),
                                timeout=self._network_timeout(),
                            )
                        )
                        resp.raise_for_status()

                        data = resp.content

                        # Strip PNG header: find IEND marker + 4 bytes CRC
                        iend_pos = data.find(b'IEND')
                        if iend_pos > 0:
                            # Skip IEND (4) + CRC (4) = 8 bytes after IEND
                            png_end = iend_pos + 8
                            # Find TS sync byte (0x47) after PNG
                            ts_start = None
                            for i in range(png_end, min(png_end + 500, len(data) - 188)):
                                if data[i] == 0x47:
                                    # Verify TS sync: next sync byte at +188
                                    if i + 188 < len(data) and data[i + 188] == 0x47:
                                        ts_start = i
                                        break
                            if ts_start is not None:
                                data = data[ts_start:]
                            else:
                                # Fallback: just skip PNG header
                                data = data[png_end:]

                        # Write stripped data
                        async with aiofiles.open(seg_path, 'wb') as f:
                            await f.write(data)

                        progress.update(1, byte_amount=len(data), detail='png unwrap')
                        completed_segments["count"] += 1
                        status_state["downloaded_bytes"] = int(status_state.get("downloaded_bytes", 0)) + len(data)
                        elapsed = max(time.monotonic() - float(status_state.get("started_at", time.monotonic())), 0.001)
                        bytes_done = int(status_state.get("downloaded_bytes", 0))
                        speed_bytes = bytes_done / elapsed if bytes_done > 0 else 0.0
                        segment_rate = completed_segments["count"] / elapsed if elapsed > 0 else 0.0
                        eta_seconds = None
                        if segment_rate > 0:
                            eta_seconds = int(max(0.0, (total - completed_segments["count"]) / segment_rate))
                        self._emit_percent_status(
                            status_callback,
                            status_state,
                            "HLS progress",
                            completed_segments["count"],
                            total,
                            (
                                f"{completed_segments['count']}/{total} segments"
                                f" • {ProgressBar._format_size(bytes_done)}"
                                f" • {ProgressBar._format_size(speed_bytes)}/s"
                                + (
                                    f" • ETA {ProgressBar._format_time(eta_seconds)}"
                                    if eta_seconds is not None else ""
                                )
                            ),
                        )
                        return str(seg_path)

                    except Exception as e:
                        if attempt == self.max_retries - 1:
                            logger.error(f"Segment {idx} failed: {e}")
                            return None
                        await asyncio.sleep(1 * (attempt + 1))

        # Download all segments concurrently
        tasks = [download_and_strip(idx, seg) for idx, seg in enumerate(segments)]
        results = await asyncio.gather(*tasks)

        stripped_files = [f for f in results if f is not None]

        if not stripped_files:
            progress.error("all segments failed")
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloadError("All segment downloads failed")

        success_rate = len(stripped_files) / total * 100
        self._emit_status(status_callback, f"HLS: segments ready {len(stripped_files)}/{total} ({success_rate:.0f}%)")
        print(f"  {len(stripped_files)}/{total} segments downloaded ({success_rate:.0f}%)")

        # For PNG-wrapped streams, missing segments often produce broken A/V sync
        # or unreadable files. Fail fast so caller can retry.
        if len(stripped_files) != total:
            missing = total - len(stripped_files)
            progress.error(f"{missing} missing")
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise DownloadError(
                f"Incomplete HLS segments: {missing} segment(s) missing. "
                "Aborting to avoid corrupted output (retry the download)."
            )

        progress.finish()

        # Concatenate with FFmpeg
        if merger.is_available:
            self._emit_status(status_callback, "HLS: merging segments with FFmpeg")
            print(f"  Merging segments with FFmpeg...")
            concat_file = temp_dir / "concat.txt"
            async with aiofiles.open(concat_file, 'w') as f:
                for seg_file in stripped_files:
                    await f.write(f"file '{seg_file}'\n")
            merge_progress = ProgressBar(
                total=total_duration,
                description=description or self._progress_label(output_path.stem),
                unit='s',
                stage='merge',
            )
            await merger.concat_segments(
                str(concat_file),
                str(output_path),
                progress=merge_progress,
                total_duration=total_duration,
                status_callback=status_callback,
            )
        else:
            # Fallback: binary concat (less reliable but works)
            self._emit_status(status_callback, "HLS: concatenating segments")
            print(f"  Concatenating segments...")
            with open(output_path, 'wb') as out:
                for seg_file in stripped_files:
                    with open(seg_file, 'rb') as seg:
                        out.write(seg.read())

        shutil.rmtree(temp_dir, ignore_errors=True)

    async def _download_hls_manual(
        self,
        segments: List[Dict],
        headers: Dict,
        cookies: Optional[Dict[str, str]],
        output_path: Path,
        merger,
        description: Optional[str] = None,
        total_duration: Optional[float] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Download normal HLS segments manually and concat."""
        temp_dir = Path(tempfile.mkdtemp())
        try:
            total = len(segments)
            self._emit_status(status_callback, f"HLS: downloading {total} segment(s) with {self.max_concurrent} workers")
            progress = ProgressBar(
                total=total,
                description=description or self._progress_label(output_path.stem),
                unit='seg',
                stage='segment',
            )
            progress.set_stage('segment', f'{self.max_concurrent} workers')
            status_state = {
                "last_emit_at": 0.0,
                "started_at": time.monotonic(),
                "downloaded_bytes": 0,
            }
            completed_segments = {"count": 0}
            semaphore = asyncio.Semaphore(self.max_concurrent)

            async with AsyncSessionManager(
                user_agent=self.config.extractor.user_agent,
                proxy=self.config.proxy.http or self.config.proxy.socks5,
                timeout=self._network_timeout(),
            ) as session:
                async def download_segment(idx: int, segment: Dict) -> str:
                    async with semaphore:
                        seg_path = temp_dir / f"segment_{idx:05d}.ts"
                        content = b''
                        for attempt in range(self.max_retries):
                            try:
                                seg_headers = self._build_request_headers(
                                    segment['url'],
                                    headers,
                                    cookies,
                                )
                                async with await session.get(segment['url'], headers=seg_headers) as resp:
                                    if resp.status not in (200, 206):
                                        raise DownloadError(f"Segment HTTP {resp.status}: {segment['url']}")
                                    content = await resp.read()
                                    async with aiofiles.open(seg_path, 'wb') as f:
                                        await f.write(content)
                                    break
                            except Exception:
                                if attempt == self.max_retries - 1:
                                    progress.error('segment download failed')
                                    raise
                                await asyncio.sleep(1 * (attempt + 1))
                        progress.update(
                            1,
                            byte_amount=len(content),
                            detail=f'{self.max_concurrent} workers',
                        )
                        completed_segments["count"] += 1
                        status_state["downloaded_bytes"] = int(status_state.get("downloaded_bytes", 0)) + len(content)
                        elapsed = max(time.monotonic() - float(status_state.get("started_at", time.monotonic())), 0.001)
                        bytes_done = int(status_state.get("downloaded_bytes", 0))
                        speed_bytes = bytes_done / elapsed if bytes_done > 0 else 0.0
                        segment_rate = completed_segments["count"] / elapsed if elapsed > 0 else 0.0
                        eta_seconds = None
                        if segment_rate > 0:
                            eta_seconds = int(max(0.0, (total - completed_segments["count"]) / segment_rate))
                        self._emit_percent_status(
                            status_callback,
                            status_state,
                            "HLS progress",
                            completed_segments["count"],
                            total,
                            (
                                f"{completed_segments['count']}/{total} segments"
                                f" • {ProgressBar._format_size(bytes_done)}"
                                f" • {ProgressBar._format_size(speed_bytes)}/s"
                                + (
                                    f" • ETA {ProgressBar._format_time(eta_seconds)}"
                                    if eta_seconds is not None else ""
                                )
                            ),
                        )
                        return str(seg_path)

                tasks = [download_segment(idx, seg) for idx, seg in enumerate(segments)]
                segment_files = await asyncio.gather(*tasks)
                progress.finish()

            if merger.is_available:
                self._emit_status(status_callback, "HLS: merging segments with FFmpeg")
                concat_file = temp_dir / "concat.txt"
                async with aiofiles.open(concat_file, 'w') as f:
                    for seg_file in segment_files:
                        await f.write(f"file '{seg_file}'\n")
                merge_progress = ProgressBar(
                    total=total_duration,
                    description=description or self._progress_label(output_path.stem),
                    unit='s',
                    stage='merge',
                )
                await merger.concat_segments(
                    str(concat_file),
                    str(output_path),
                    progress=merge_progress,
                    total_duration=total_duration,
                    status_callback=status_callback,
                )
            else:
                self._emit_status(status_callback, "HLS: concatenating segments")
                with open(output_path, 'wb') as out:
                    for seg_file in segment_files:
                        with open(seg_file, 'rb') as seg:
                            out.write(seg.read())
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _download_dash(
        self,
        format_: StreamFormat,
        output_path: Path,
        progress_callback: Optional[Callable] = None,
        description: Optional[str] = None,
        total_duration: Optional[float] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """Download DASH stream (uses FFmpeg)."""
        from core.merger import FFmpegMerger
        merger = FFmpegMerger(self.config.ffmpeg_path)
        if not merger.is_available:
            raise DownloadError("FFmpeg is required for DASH downloads. Install with: brew install ffmpeg")
        self._emit_status(status_callback, "DASH: downloading with FFmpeg")
        print(f"  Downloading DASH stream via FFmpeg...")
        headers = self._build_request_headers(format_.url, format_.headers, format_.cookies)
        progress = ProgressBar(
            total=total_duration,
            description=description or self._progress_label(output_path.stem),
            unit='s',
            stage='dash',
        )
        await merger.download_stream(
            format_.url,
            str(output_path),
            headers=headers,
            progress=progress,
            total_duration=total_duration,
            stage='dash',
            detail='ffmpeg copy',
            status_callback=status_callback,
        )

    def _generate_output_path(self, media_info: MediaInfo, format_: StreamFormat) -> Path:
        """Generate output path from template."""
        template = self.config.download.output_template
        variables = {
            'title': sanitize_filename(media_info.title),
            'id': media_info.id,
            'extractor': media_info.extractor,
            'resolution': format_.resolution,
            'width': str(format_.width or 'unknown'),
            'height': str(format_.height or 'unknown'),
            'ext': format_.ext,
            'quality': format_.quality or 'unknown',
            'fps': str(format_.fps or ''),
        }
        filename = template
        for key, value in variables.items():
            filename = filename.replace(f'%({key})s', value)
        filename = filename.replace('%(', '').replace(')s', '')
        return self.output_dir / filename

    def _resolve_output_path(
        self,
        media_info: MediaInfo,
        format_: StreamFormat,
        output_path: Optional[str] = None,
    ) -> Path:
        """Resolve a custom output target as either file path or directory."""
        if not output_path:
            return self._generate_output_path(media_info, format_)

        raw_path = os.path.expanduser(output_path)
        target = Path(raw_path)
        is_directory = (
            raw_path.endswith((os.sep, '/'))
            or (target.exists() and target.is_dir())
            or not target.suffix
        )

        if not is_directory:
            return target

        generated_name = self._generate_output_path(media_info, format_).name
        return target / generated_name


class BatchDownloader:
    """Handles batch downloads from multiple URLs."""

    def __init__(self, downloader: Downloader, extractor_registry):
        self.downloader = downloader
        self.registry = extractor_registry

    async def download_batch(
        self, urls: List[str], quality: str = "best",
        progress_callback: Optional[Callable] = None
    ) -> Dict[str, Any]:
        """Download multiple URLs."""
        results = {'successful': [], 'failed': [], 'skipped': []}

        for idx, url in enumerate(urls):
            print(f"\n[{idx + 1}/{len(urls)}] {url}")
            session = None
            extractor = None
            try:
                extractor_class = self.registry.find_extractor(url)
                if not extractor_class:
                    logger.warning(f"No extractor found for: {url}")
                    results['skipped'].append({'url': url, 'reason': 'No extractor'})
                    continue

                from utils.network import SessionManager
                session = SessionManager(
                    user_agent=self.downloader.config.extractor.user_agent,
                    proxy=self.downloader.config.proxy.to_dict(),
                    cookies_file=self.downloader.config.cookies_file,
                    cookies_from_browser=self.downloader.config.cookies_from_browser,
                )

                extractor = extractor_class(session, config=vars(self.downloader.config))
                media_info = extractor.extract(url)

                if quality == "best":
                    format_ = media_info.best_format
                elif quality == "worst":
                    format_ = media_info.worst_format
                else:
                    format_ = media_info.get_format_by_quality(quality) or media_info.best_format

                if not format_:
                    results['skipped'].append({'url': url, 'reason': 'No format'})
                    continue

                from extractors.ytdlp import YtdlpExtractor

                if YtdlpExtractor.uses_direct_backend(media_info):
                    output_path = YtdlpExtractor.download_media_info(
                        media_info,
                        self.downloader.config,
                        selected_format=format_,
                        quality=quality,
                        display_name=media_info.title,
                    )
                    if not output_path:
                        raise DownloadError("Direct yt-dlp download returned no file")
                    results['successful'].append({
                        'url': url, 'title': media_info.title, 'output': output_path
                    })
                    continue

                download_engine = Downloader(self.downloader.config, session=session)
                output_path = await download_engine.download(
                    media_info, format_, progress_callback=progress_callback
                )
                results['successful'].append({
                    'url': url, 'title': media_info.title, 'output': output_path
                })
            except Exception as e:
                logger.error(f"Failed: {url}: {e}")
                results['failed'].append({'url': url, 'error': str(e)})
            finally:
                try:
                    if extractor is not None:
                        extractor.close()
                except Exception:
                    pass
                try:
                    if session is not None:
                        session.close()
                except Exception:
                    pass

        return results
