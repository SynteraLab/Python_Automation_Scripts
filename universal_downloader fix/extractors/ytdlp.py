"""
yt-dlp wrapper extractor.
Falls back to yt-dlp for sites not handled by custom extractors.
Requires: pip install yt-dlp
"""

import subprocess
import json
import shutil
import os
import sys
import importlib.util
import time
import re
from typing import Callable, Optional, Dict, List, Any, Tuple
from pathlib import Path
import logging

from .base import ExtractorBase, ExtractionError
from models.media import MediaInfo, StreamFormat, MediaType, StreamType
from utils.progress import ProgressBar

logger = logging.getLogger(__name__)
RICH_PROGRESS_AVAILABLE = importlib.util.find_spec("rich") is not None


MEDIA_FILE_EXTENSIONS = {
    ".mp4", ".mkv", ".webm", ".m4v", ".mov", ".avi", ".ts",
    ".mp3", ".m4a", ".aac", ".flac", ".wav", ".opus", ".ogg",
}


def _option_value(source: Any, key: str, section: Optional[str] = None, default: Any = None) -> Any:
    if source is None:
        return default

    if isinstance(source, dict):
        if key in source and source.get(key) is not None:
            return source.get(key)
        if section:
            return _option_value(source.get(section), key, default=default)
        return default

    if section and hasattr(source, section):
        return _option_value(getattr(source, section), key, default=default)

    if hasattr(source, key):
        value = getattr(source, key)
        return default if value is None else value

    return default


def _resolve_output_target(output_dir: str, output_path: Optional[str]) -> Tuple[str, str]:
    default_dir = os.path.expanduser(output_dir)

    if not output_path:
        os.makedirs(default_dir, exist_ok=True)
        return default_dir, os.path.join(default_dir, '%(title)s [%(id)s].%(ext)s')

    raw_path = os.path.expanduser(output_path)
    target = Path(raw_path)
    is_directory = (
        raw_path.endswith((os.sep, '/'))
        or (target.exists() and target.is_dir())
        or not target.suffix
    )

    if is_directory:
        os.makedirs(raw_path, exist_ok=True)
        return raw_path, os.path.join(raw_path, '%(title)s [%(id)s].%(ext)s')

    target.parent.mkdir(parents=True, exist_ok=True)
    return str(target.parent), str(target)


def _resolve_proxy_value(source: Any) -> Optional[str]:
    return (
        _option_value(source, 'http', section='proxy')
        or _option_value(source, 'https', section='proxy')
        or _option_value(source, 'socks5', section='proxy')
    )


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1B\[[0-?]*[ -/]*[@-~]", "", text)


def _normalize_candidate_path(candidate: str, output_dir: str) -> Optional[str]:
    raw = _strip_ansi(candidate).strip().strip('"').strip("'")
    if not raw:
        return None

    if raw.startswith("file://"):
        raw = raw[7:]

    if raw.startswith("~/"):
        raw = os.path.expanduser(raw)

    if not os.path.isabs(raw):
        normalized_raw = os.path.normpath(raw)
        normalized_output_dir = os.path.normpath(output_dir) if output_dir else ''
        output_base = os.path.basename(normalized_output_dir) if normalized_output_dir else ''

        if output_base and (
            normalized_raw == output_base
            or normalized_raw.startswith(output_base + os.sep)
        ):
            raw = normalized_raw
        else:
            raw = os.path.join(output_dir, raw)

    return os.path.normpath(raw)


def _extract_path_from_line(line: str, output_dir: str) -> Optional[str]:
    clean = _strip_ansi(line).strip()
    if not clean:
        return None

    patterns = [
        r"Destination:\s*(.+)$",
        r"Merging formats into\s+\"(.+)\"",
        r"\[download\]\s+(.+?)\s+has already been downloaded",
        r"\[ExtractAudio\]\s*Destination:\s*(.+)$",
    ]

    for pattern in patterns:
        match = re.search(pattern, clean)
        if not match:
            continue
        normalized = _normalize_candidate_path(match.group(1), output_dir)
        if normalized:
            return normalized

    # `--print after_move:filepath` may output a bare path line.
    normalized = _normalize_candidate_path(clean, output_dir)
    if normalized and Path(normalized).suffix.lower() in MEDIA_FILE_EXTENSIONS:
        return normalized

    return None


def _parse_ytdlp_progress_line(line: str) -> Optional[Dict[str, str]]:
    """Parse yt-dlp progress line into a structured dict."""
    clean = _strip_ansi(line).strip()
    if not clean.startswith("[download]"):
        return None

    payload = clean[len("[download]"):].strip()
    if not payload:
        return None

    # Non-progress status lines.
    ignored_tokens = ("Destination:", "has already been downloaded", "Merging formats")
    if any(token in payload for token in ignored_tokens):
        return None

    info: Dict[str, str] = {}

    percent_match = re.search(r"(?P<percent>\d{1,3}(?:\.\d+)?)%", payload)
    if percent_match:
        info["percent"] = f"{percent_match.group('percent')}%"

    size_match = re.search(r"of\s+~?\s*(?P<size>\S+)", payload)
    if size_match:
        info["size"] = size_match.group("size")

    speed_match = re.search(r"at\s+(?P<speed>\S+)", payload)
    if speed_match:
        info["speed"] = speed_match.group("speed")

    eta_match = re.search(r"ETA\s+(?P<eta>\S+)", payload)
    if eta_match:
        info["eta"] = eta_match.group("eta")

    frag_match = re.search(r"\(frag\s+(?P<frag>\d+/\d+)\)", payload)
    if frag_match:
        info["frag"] = frag_match.group("frag")

    return info or None


def _render_ytdlp_progress(info: Dict[str, str]) -> str:
    """Build readable progress line for terminal output."""
    percent_text = info.get("percent", "")
    percent_value = 0.0
    if percent_text:
        try:
            percent_value = max(0.0, min(100.0, float(percent_text.rstrip("%"))))
        except ValueError:
            percent_value = 0.0

    bar_width = 24
    filled = int(round((percent_value / 100.0) * bar_width)) if percent_text else 0
    filled = max(0, min(bar_width, filled))
    bar = ("▰" * filled) + ("▱" * (bar_width - filled))

    parts: List[str] = [f"[{bar}]"]
    if percent_text:
        parts.append(percent_text.rjust(6))
    if info.get("size"):
        parts.append(f"size {info['size']}")
    if info.get("speed"):
        parts.append(f"speed {info['speed']}")
    if info.get("eta"):
        parts.append(f"ETA {info['eta']}")
    if info.get("frag"):
        parts.append(f"frag {info['frag']}")

    return "[yt-dlp] " + " | ".join(parts)


def _parse_percent_value(percent_text: str) -> float:
    """Convert percent text (e.g. '24.5%') to clamped float."""
    try:
        value = float(percent_text.rstrip('%'))
    except ValueError:
        return 0.0
    return max(0.0, min(100.0, value))


def _find_recent_output_file(output_dir: str, started_at: float) -> Optional[str]:
    try:
        root = Path(output_dir)
        if not root.exists():
            return None

        candidates: List[Path] = []
        for file_path in root.glob("**/*"):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in MEDIA_FILE_EXTENSIONS:
                continue
            if file_path.stat().st_mtime < (started_at - 3):
                continue
            candidates.append(file_path)

        if not candidates:
            return None

        newest = max(candidates, key=lambda p: p.stat().st_mtime)
        return str(newest)
    except Exception:
        return None


def _is_working_ytdlp_command(cmd: List[str]) -> bool:
    """Check whether a yt-dlp command is executable and healthy."""
    try:
        result = subprocess.run(
            cmd + ["--version"],
            capture_output=True,
            text=True,
            timeout=8,
        )
        return result.returncode == 0
    except Exception:
        return False


def _resolve_ytdlp_cmd() -> Optional[List[str]]:
    """Resolve a usable yt-dlp command.

    Priority:
    1) standalone `yt-dlp` executable in PATH / common install paths
    2) `python -m yt_dlp` (module install)
    """
    candidates: List[str] = []

    in_path = shutil.which("yt-dlp")
    if in_path:
        candidates.append(in_path)

    for p in ("/opt/homebrew/bin/yt-dlp", "/usr/local/bin/yt-dlp"):
        if p not in candidates and Path(p).exists():
            candidates.append(p)

    for p in candidates:
        cmd = [p]
        if _is_working_ytdlp_command(cmd):
            return cmd

    if importlib.util.find_spec("yt_dlp") is not None:
        module_cmd = [sys.executable, "-m", "yt_dlp"]
        if _is_working_ytdlp_command(module_cmd):
            return module_cmd

    return None


YTDLP_AVAILABLE = _resolve_ytdlp_cmd() is not None


def _safe_int(value: Any) -> Optional[int]:
    if value in (None, "", "none"):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _merge_http_headers(*header_sets: Optional[Dict[str, Any]]) -> Dict[str, str]:
    merged: Dict[str, str] = {}
    for header_set in header_sets:
        if not isinstance(header_set, dict):
            continue
        for key, value in header_set.items():
            if key is None or value is None:
                continue
            key_text = str(key).strip()
            value_text = str(value).strip()
            if key_text and value_text:
                merged[key_text] = value_text
    return merged


def _guess_stream_type(fmt: Dict[str, Any], url: str) -> StreamType:
    protocol = str(fmt.get('protocol') or '').lower()
    url_lower = url.lower()

    if protocol in {'m3u8', 'm3u8_native'} or '.m3u8' in url_lower:
        return StreamType.HLS
    if protocol in {'dash', 'http_dash_segments', 'http_dash_segments_generator'} or '.mpd' in url_lower:
        return StreamType.DASH

    has_video = str(fmt.get('vcodec') or 'none') != 'none'
    has_audio = str(fmt.get('acodec') or 'none') != 'none'
    if has_video and has_audio:
        return StreamType.PROGRESSIVE
    return StreamType.DIRECT


def _build_format_label(fmt: Dict[str, Any], has_video: bool, has_audio: bool) -> Optional[str]:
    candidates = [
        fmt.get('format_note'),
        fmt.get('format'),
        fmt.get('resolution'),
    ]
    for candidate in candidates:
        if candidate:
            text = str(candidate).strip()
            if text:
                return text

    if has_audio and not has_video:
        abr = _safe_int(fmt.get('abr'))
        ext = str(fmt.get('ext') or 'audio')
        return f"{abr}kbps {ext}" if abr else f"audio {ext}"

    if has_video and not has_audio:
        height = _safe_int(fmt.get('height'))
        return f"{height}p video only" if height else "video only"

    return None


class YtdlpExtractor(ExtractorBase):
    """
    Extractor that wraps yt-dlp for 1000+ sites.
    Used as fallback when custom extractors fail.
    NOT registered in registry — called explicitly by download engine.
    """

    EXTRACTOR_NAME = "yt-dlp"
    EXTRACTOR_DESCRIPTION = "yt-dlp fallback (1000+ sites)"
    URL_PATTERNS = []
    REQUIRES_BROWSER = False

    @classmethod
    def is_available(cls) -> bool:
        return _resolve_ytdlp_cmd() is not None

    @staticmethod
    def uses_direct_backend(media_info: Optional[MediaInfo] = None, extractor: Optional[str] = None) -> bool:
        extractor_name = extractor or (media_info.extractor if media_info else '') or ''
        return extractor_name.startswith('social/') or extractor_name.startswith('yt-dlp/')

    @staticmethod
    def build_format_selector(
        media_info: MediaInfo,
        selected_format: Optional[StreamFormat],
        audio_only: bool = False,
        no_merge: bool = False,
    ) -> Optional[str]:
        if not selected_format:
            return None

        selector = selected_format.format_id
        if audio_only:
            return selector

        if selected_format.is_video and not selected_format.is_audio and not no_merge:
            audio_formats = media_info.get_audio_formats()
            if audio_formats:
                best_audio = max(audio_formats, key=lambda fmt: fmt.quality_score)
                audio_id = best_audio.format_id
                return f"{selected_format.format_id}+{audio_id}/{selected_format.format_id}+bestaudio/best"
            return f"{selected_format.format_id}+bestaudio/best"

        return selector

    @classmethod
    def download_media_info(
        cls,
        media_info: MediaInfo,
        config: Any,
        selected_format: Optional[StreamFormat] = None,
        output_path: Optional[str] = None,
        quality: str = "best",
        audio_only: bool = False,
        no_merge: bool = False,
        display_name: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        output_dir = _option_value(config, 'output_dir', section='download', default=str(Path.home() / 'Downloads'))
        cookies_browser = _option_value(config, 'cookies_from_browser')
        cookies_file = _option_value(config, 'cookies_file')
        proxy = _resolve_proxy_value(config)
        user_agent = _option_value(config, 'user_agent', section='extractor')

        return cls.download_with_ytdlp(
            url=media_info.url,
            output_dir=output_dir,
            output_path=output_path,
            quality=quality,
            audio_only=audio_only,
            format_selector=cls.build_format_selector(
                media_info,
                selected_format,
                audio_only=audio_only,
                no_merge=no_merge,
            ),
            cookies_browser=cookies_browser,
            cookies_file=cookies_file,
            proxy=proxy,
            user_agent=user_agent,
            display_name=display_name or media_info.title,
            progress_callback=progress_callback,
            status_callback=status_callback,
        )

    def extract(self, url: str) -> MediaInfo:
        """Extract info using yt-dlp --dump-json."""
        cmd_base = _resolve_ytdlp_cmd()
        if not cmd_base:
            raise ExtractionError("yt-dlp not installed. Install: pip install yt-dlp")

        logger.info(f"yt-dlp extraction for: {url}")

        cmd = cmd_base + [
            "--dump-json", "--no-download",
            "--no-warnings", "--no-playlist",
            url
        ]

        # Add cookies from browser if configured
        browser = _option_value(self.config, 'cookies_from_browser')
        if browser:
            cmd.extend(['--cookies-from-browser', browser])

        cookies_file = _option_value(self.config, 'cookies_file')
        if cookies_file:
            cmd.extend(['--cookies', cookies_file])

        proxy = _resolve_proxy_value(self.config)
        if proxy:
            cmd.extend(['--proxy', proxy])

        user_agent = _option_value(self.config, 'user_agent', section='extractor')
        if user_agent:
            cmd.extend(['--user-agent', user_agent])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
        except subprocess.TimeoutExpired:
            raise ExtractionError("yt-dlp timed out")

        if result.returncode != 0:
            error = result.stderr.strip()
            if 'Unsupported URL' in error:
                raise ExtractionError(f"yt-dlp: site not supported")
            raise ExtractionError(f"yt-dlp failed: {error[:200]}")

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            raise ExtractionError("yt-dlp returned invalid JSON")

        return self._parse_info(data, url)

    def _parse_info(self, data: Dict, url: str) -> MediaInfo:
        """Convert yt-dlp JSON to our MediaInfo format."""
        formats = []
        root_headers = _merge_http_headers(data.get('http_headers'))
        webpage_url = data.get('webpage_url') or data.get('original_url') or url

        for idx, fmt in enumerate(data.get('formats', [])):
            vcodec = fmt.get('vcodec', 'none')
            acodec = fmt.get('acodec', 'none')
            has_video = vcodec != 'none'
            has_audio = acodec != 'none'
            stream_url = fmt.get('url') or fmt.get('manifest_url')

            # Skip formats with no video or audio
            if not has_video and not has_audio:
                continue
            if not stream_url:
                continue

            height = _safe_int(fmt.get('height'))
            width = _safe_int(fmt.get('width'))
            quality = f"{height}p" if height else None
            stream_type = _guess_stream_type(fmt, stream_url)
            headers = _merge_http_headers(root_headers, fmt.get('http_headers'))
            if webpage_url and 'Referer' not in headers:
                headers['Referer'] = str(webpage_url)

            formats.append(StreamFormat(
                format_id=fmt.get('format_id', f'ytdlp-{idx}'),
                url=stream_url,
                ext=fmt.get('ext') or data.get('ext') or ('m4a' if has_audio and not has_video else 'mp4'),
                quality=quality,
                width=width,
                height=height,
                fps=_safe_int(fmt.get('fps')),
                vcodec=vcodec if has_video else None,
                acodec=acodec if has_audio else None,
                bitrate=_safe_int(fmt.get('tbr')),
                filesize=_safe_int(fmt.get('filesize')) or _safe_int(fmt.get('filesize_approx')),
                stream_type=stream_type,
                is_video=has_video,
                is_audio=has_audio,
                headers=headers,
                label=_build_format_label(fmt, has_video, has_audio),
            ))

        # Get duration
        duration = _safe_int(data.get('duration'))
        media_type = MediaType.AUDIO if formats and not any(fmt.is_video for fmt in formats) else MediaType.VIDEO

        subtitles = {}
        for language, entries in (data.get('subtitles') or {}).items():
            valid_entries = []
            for entry in entries or []:
                if not isinstance(entry, dict) or not entry.get('url'):
                    continue
                valid_entries.append({
                    'url': entry.get('url'),
                    'ext': entry.get('ext'),
                    'name': entry.get('name'),
                })
            if valid_entries:
                subtitles[str(language)] = valid_entries

        return MediaInfo(
            id=data.get('id', self._generate_id(url)),
            title=data.get('title', 'Unknown'),
            url=url,
            formats=formats,
            media_type=media_type,
            extractor=f"yt-dlp/{data.get('extractor', 'unknown')}",
            description=data.get('description'),
            thumbnail=data.get('thumbnail'),
            duration=duration,
            upload_date=data.get('upload_date'),
            uploader=data.get('uploader') or data.get('channel'),
            view_count=data.get('view_count'),
            subtitles=subtitles,
            chapters=data.get('chapters') or [],
            playlist_index=_safe_int(data.get('playlist_index')),
            playlist_count=_safe_int(data.get('n_entries')),
        )

    @staticmethod
    def download_with_ytdlp(
        url: str,
        output_dir: str = str(Path.home() / "Downloads"),
        output_path: Optional[str] = None,
        quality: str = "best",
        audio_only: bool = False,
        format_selector: Optional[str] = None,
        cookies_browser: Optional[str] = None,
        cookies_file: Optional[str] = None,
        proxy: Optional[str] = None,
        user_agent: Optional[str] = None,
        extra_args: Optional[List[str]] = None,
        display_name: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Optional[str]:
        """
        Download directly using yt-dlp subprocess.
        Returns output filepath or None.
        """
        cmd_base = _resolve_ytdlp_cmd()
        if not cmd_base:
            return None
        started_at = time.time()

        quality_map = {
            'best': 'bestvideo+bestaudio/best',
            'worst': 'worstvideo+worstaudio/worst',
            '1080p': 'bestvideo[height<=1080]+bestaudio/best',
            '720p': 'bestvideo[height<=720]+bestaudio/best',
            '480p': 'bestvideo[height<=480]+bestaudio/best',
            '360p': 'bestvideo[height<=360]+bestaudio/best',
            'audio-best': 'bestaudio/best',
        }

        format_spec = format_selector or quality_map.get(quality, quality)
        resolved_output_dir, output_tpl = _resolve_output_target(output_dir, output_path)

        cmd = cmd_base + [
            '-f', format_spec,
            '--merge-output-format', 'mp4',
            '-o', output_tpl,
            '--print', 'after_move:filepath',
            '--no-playlist',
            '--newline',
            '--progress',
            '--retries', '5',
        ]

        if audio_only:
            cmd = cmd_base + [
                '-f', format_selector or 'bestaudio/best',
                '--extract-audio',
                '--audio-format', 'mp3',
                '--audio-quality', '0',
                '-o', output_tpl,
                '--print', 'after_move:filepath',
                '--no-playlist',
                '--newline',
                '--progress',
            ]

        if cookies_browser:
            cmd.extend(['--cookies-from-browser', cookies_browser])
        if cookies_file:
            cmd.extend(['--cookies', cookies_file])
        if proxy:
            cmd.extend(['--proxy', proxy])
        if user_agent:
            cmd.extend(['--user-agent', user_agent])
        if extra_args:
            cmd.extend(extra_args)

        cmd.append(url)

        logger.info(f"yt-dlp download: {url}")

        progress: Optional[ProgressBar] = None
        try:
            progress = ProgressBar(
                total=100,
                description=(display_name or 'yt-dlp')[:22],
                unit='%',
                stage='ytdlp',
            )
            progress.set_stage('ytdlp', 'starting')

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            stdout = process.stdout
            if stdout is None:
                return None

            filepath = None
            output_lines: List[str] = []
            last_total_bytes: Optional[float] = None
            last_downloaded_bytes: Optional[float] = None
            saw_progress = False
            status_state = {"last_emit_at": 0.0}

            for line in stdout:
                line = line.strip()
                if line:
                    output_lines.append(line)
                    detected = _extract_path_from_line(line, resolved_output_dir)
                    if detected:
                        filepath = detected
                        if status_callback:
                            try:
                                status_callback(f"yt-dlp output: {detected}")
                            except Exception:
                                pass

                    progress_info = _parse_ytdlp_progress_line(line)
                    if progress_info:
                        saw_progress = True
                        percent_value = _parse_percent_value(progress_info.get("percent", "0%"))
                        total_bytes = ProgressBar.parse_size_text(progress_info.get("size"))
                        if total_bytes is not None:
                            last_total_bytes = total_bytes
                            last_downloaded_bytes = total_bytes * (percent_value / 100.0)

                        detail_parts = []
                        if progress_info.get("frag"):
                            detail_parts.append(f"frag {progress_info['frag']}")

                        progress.set(
                            value=percent_value,
                            total=100.0,
                            transferred_bytes=last_downloaded_bytes,
                            stage='ytdlp',
                            detail=' | '.join(detail_parts) or 'downloading',
                            speed=ProgressBar.parse_size_text(progress_info.get("speed")),
                            eta=ProgressBar.parse_duration_text(progress_info.get("eta")),
                        )

                        if progress_callback and last_downloaded_bytes is not None:
                            try:
                                progress_callback(int(last_downloaded_bytes), int(last_total_bytes or 0))
                            except Exception:
                                pass

                        percent_int = int(round(percent_value))
                        now = time.monotonic()
                        last_emit_at = float(status_state.get("last_emit_at", 0.0))
                        if status_callback and (
                            not last_emit_at or (now - last_emit_at) >= 1.0 or percent_int >= 100
                        ):
                            status_state["last_emit_at"] = now
                            detail_parts = []
                            detail_parts.append(f"{percent_int}%")
                            if last_downloaded_bytes is not None and last_total_bytes is not None:
                                detail_parts.append(
                                    f"{ProgressBar._format_size(last_downloaded_bytes)} / "
                                    f"{ProgressBar._format_size(last_total_bytes)}"
                                )
                            if progress_info.get("speed"):
                                detail_parts.append(f"speed {progress_info['speed']}")
                            if progress_info.get("eta"):
                                detail_parts.append(f"ETA {progress_info['eta']}")
                            if progress_info.get("frag"):
                                detail_parts.append(f"frag {progress_info['frag']}")
                            try:
                                status_callback(f"yt-dlp progress: {' • '.join(detail_parts)}")
                            except Exception:
                                pass
                        continue

                    if 'Merging formats' in line:
                        progress.set(
                            value=100.0 if saw_progress else None,
                            total=100.0,
                            transferred_bytes=last_total_bytes or last_downloaded_bytes,
                            stage='merge',
                            detail='video + audio',
                        )
                        if status_callback:
                            try:
                                status_callback("yt-dlp: merging video and audio")
                            except Exception:
                                pass
                        continue

                    if '[ExtractAudio]' in line:
                        progress.set_stage('audio', 'extracting audio')
                        if status_callback:
                            try:
                                status_callback("yt-dlp: extracting audio")
                            except Exception:
                                pass
                        continue

                    if 'Destination:' in line and not saw_progress:
                        progress.set_stage('prepare', 'writing output')

            process.wait()

            if process.returncode == 0:
                progress.set(
                    value=100.0,
                    total=100.0,
                    transferred_bytes=last_total_bytes or last_downloaded_bytes,
                    stage='done',
                    detail='yt-dlp complete',
                )
                progress.finish('yt-dlp complete')
                if filepath and os.path.exists(filepath):
                    return filepath

                recent_file = _find_recent_output_file(resolved_output_dir, started_at)
                if recent_file:
                    return recent_file

                logger.warning("yt-dlp finished but output file path was not detected")
                if output_lines:
                    logger.debug("Last yt-dlp lines: %s", output_lines[-5:])
                raise ExtractionError("yt-dlp finished but no output file was detected")

            tail = output_lines[-6:] if output_lines else []
            tail_text = " | ".join(tail)
            progress.error(f"exit {process.returncode}")
            raise ExtractionError(
                f"yt-dlp exited with code {process.returncode}"
                + (f" ({tail_text[:240]})" if tail_text else "")
            )

        except Exception as e:
            logger.error(f"yt-dlp download failed: {e}")
            if progress is not None:
                progress.error(str(e)[:48])
            raise
