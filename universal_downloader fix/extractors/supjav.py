from __future__ import annotations

import logging
import re
from typing import Any, Dict, Iterable, Optional, Tuple
from urllib.parse import urlparse

from .base import ExtractorBase, ExtractionError, register_extractor
from models.media import MediaInfo, MediaType, StreamFormat, StreamType

logger = logging.getLogger(__name__)


@register_extractor()
class SupJavExtractor(ExtractorBase):
    EXTRACTOR_NAME = "supjav"
    EXTRACTOR_DESCRIPTION = "SupJav compatibility adapter powered by supjav_extractor"

    URL_PATTERNS = [
        r'https?://(?:www\.)?supjav\.com/\d+\.html',
        r'https?://(?:www\.)?supjav\.com/.+\.html',
        r'https?://(?:www\.)?supjav\.(?:ru|homes|to|net|org)/[\w\-./?%=&+#]+',
        r'https?://(?:www\.)?turbovidhls\.com/(?:t|e|embed|v|d)/.+',
        r'https?://(?:cdn\d*\.)?turboviplay\.com/.+',
        r'https?://(?:www\.)?callistanise\.com/(?:v|e|embed|d)/.+',
        r'https?://(?:www\.)?[a-z0-9-]*vidhide[a-z0-9-]*\.(?:com|net|org)/(?:v|e|embed|d)/.+',
    ]

    SUPJAV_MIRROR_HOSTS = {
        "supjav.com",
        "supjav.ru",
        "supjav.homes",
        "supjav.to",
        "supjav.net",
        "supjav.org",
    }

    QUALITY_DIMENSIONS: Dict[str, Tuple[int, int]] = {
        "2160p": (3840, 2160),
        "1080p": (1920, 1080),
        "720p": (1280, 720),
        "480p": (854, 480),
        "360p": (640, 360),
    }

    def extract(self, url: str) -> MediaInfo:
        if self._is_supjav_page(url):
            try:
                result = self._extract_with_new_engine(url)
                if result.streams:
                    return self._build_media_info(url, result)
                logger.warning("SupJav adapter: new engine found no streams for %s", url)
            except Exception as exc:
                logger.warning("SupJav adapter: new engine failed for %s: %s", url, exc)

        return self._extract_with_legacy(url)

    def _extract_with_new_engine(self, url: str) -> Any:
        try:
            from . import supjav_extractor as engine
        except SystemExit as exc:
            raise ExtractionError(
                "supjav_extractor dependencies are missing; install requests and beautifulsoup4"
            ) from exc
        except Exception as exc:
            raise ExtractionError(f"failed to import supjav_extractor: {exc}") from exc

        extractor = engine.SupjavExtractor(config=self._build_engine_config(engine))
        self._apply_session_context(extractor)
        return extractor.extract(self._canonicalize_supjav_url(url))

    def _extract_with_legacy(self, url: str) -> MediaInfo:
        from .supjav_legacy import SupJavExtractor as LegacySupJavExtractor

        legacy_extractor = LegacySupJavExtractor(self.session, config=self.config)
        return legacy_extractor.extract(url)

    def _build_engine_config(self, engine: Any) -> Any:
        timeout_value = self._safe_int(self._config_value("download", "timeout"), 60)
        max_retry_value = self._safe_int(self._config_value("download", "max_retries"), 3)
        request_timeout = timeout_value if timeout_value is not None else 60
        max_retries = max_retry_value if max_retry_value is not None else 3
        debug_enabled = bool(self._config_value("extractor", "debug", default=False))
        headless = bool(self._config_value("extractor", "headless", default=True))

        return engine.ExtractorConfig(
            request_timeout=max(10, request_timeout),
            max_retries=max(1, max_retries),
            headless=headless,
            debug=debug_enabled,
            log_level="DEBUG" if debug_enabled else "ERROR",
        )

    def _apply_session_context(self, extractor: Any) -> None:
        http_client = getattr(extractor, "_http", None)
        target_session = getattr(http_client, "_session", None)
        raw_session = getattr(self.session, "_session", None)
        rotator = getattr(http_client, "_rotator", None)

        if target_session is None or raw_session is None:
            return

        try:
            target_session.cookies.update(raw_session.cookies)
        except Exception:
            pass

        try:
            target_session.proxies.update(getattr(raw_session, "proxies", {}) or {})
        except Exception:
            pass

        user_agent = (
            self._config_value("extractor", "user_agent")
            or getattr(self.session, "user_agent", None)
            or getattr(raw_session, "headers", {}).get("User-Agent")
        )
        if user_agent:
            try:
                target_session.headers["User-Agent"] = str(user_agent)
            except Exception:
                pass

            if rotator is not None and hasattr(rotator, "_current_ua"):
                try:
                    rotator._current_ua = str(user_agent)
                except Exception:
                    pass

    def _build_media_info(self, url: str, result: Any) -> MediaInfo:
        formats = self._convert_streams(result.streams)
        if not formats:
            raise ExtractionError(self._result_error(result))

        page_metadata = {}
        if isinstance(getattr(result, "metadata", None), dict):
            raw_page_metadata = result.metadata.get("page_metadata")
            if isinstance(raw_page_metadata, dict):
                page_metadata = raw_page_metadata

        title = page_metadata.get("title") or self._extract_title_from_url(url)
        description = page_metadata.get("description")
        thumbnail = page_metadata.get("thumbnail")
        duration = self._safe_int(page_metadata.get("duration"), None)
        media_id = str(page_metadata.get("jav_code") or self._generate_id(url))

        return MediaInfo(
            id=media_id,
            title=title,
            url=url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            description=description,
            thumbnail=thumbnail,
            duration=duration,
        )

    def _convert_streams(self, streams: Iterable[Any]) -> list[StreamFormat]:
        seen_urls = set()
        converted: list[StreamFormat] = []

        ordered_streams = sorted(
            [stream for stream in streams if getattr(stream, "url", None)],
            key=lambda stream: getattr(stream, "score", 0),
            reverse=True,
        )

        for index, stream in enumerate(ordered_streams, start=1):
            if stream.url in seen_urls:
                continue
            seen_urls.add(stream.url)
            converted.append(self._convert_stream(stream, index))

        return converted

    def _convert_stream(self, stream: Any, index: int) -> StreamFormat:
        quality = self._quality_label(stream)
        width, height = self._dimensions_for_stream(stream, quality)
        stream_type = self._stream_type_for_stream(stream)
        ext = self._extension_for_stream(stream)
        metadata = getattr(stream, "metadata", {}) or {}
        headers = dict(getattr(stream, "headers", {}) or {})
        filesize = self._safe_int(metadata.get("content_length"), None)
        bitrate = self._safe_int(metadata.get("bitrate"), None)
        format_id = self._format_id(stream, index, height, ext)

        return StreamFormat(
            format_id=format_id,
            url=stream.url,
            ext=ext,
            quality=quality,
            width=width,
            height=height,
            bitrate=bitrate,
            filesize=filesize,
            stream_type=stream_type,
            is_video=True,
            is_audio=True,
            headers=headers,
            cookies=self._cookies_for_url(stream.url),
            label=self._label_for_stream(stream, stream_type, quality),
        )

    def _quality_label(self, stream: Any) -> Optional[str]:
        raw_quality = getattr(getattr(stream, "quality", None), "value", None)
        if isinstance(raw_quality, str) and raw_quality and raw_quality.lower() != "unknown":
            return raw_quality

        match = re.search(r"(2160|1080|720|480|360)p?", getattr(stream, "url", ""), re.IGNORECASE)
        if match:
            return f"{match.group(1)}p"

        return None

    def _dimensions_for_stream(self, stream: Any, quality: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
        metadata = getattr(stream, "metadata", {}) or {}
        width = self._safe_int(metadata.get("width"), None)
        height = self._safe_int(metadata.get("height"), None)
        if width and height:
            return width, height

        if quality and quality in self.QUALITY_DIMENSIONS:
            return self.QUALITY_DIMENSIONS[quality]

        return None, None

    def _stream_type_for_stream(self, stream: Any) -> StreamType:
        raw_format = str(getattr(getattr(stream, "format", None), "value", "")).lower()
        url = getattr(stream, "url", "").lower()

        if raw_format == "m3u8" or ".m3u8" in url:
            return StreamType.HLS
        if raw_format == "flv":
            return StreamType.PROGRESSIVE
        if raw_format == "webm":
            return StreamType.PROGRESSIVE
        return StreamType.DIRECT

    def _extension_for_stream(self, stream: Any) -> str:
        raw_format = str(getattr(getattr(stream, "format", None), "value", "")).lower()
        if raw_format in {"mp4", "webm", "flv"}:
            return raw_format
        if raw_format == "m3u8":
            return "mp4"

        url = getattr(stream, "url", "").lower()
        for ext in ("mp4", "webm", "flv", "m3u8"):
            if f".{ext}" in url:
                return "mp4" if ext == "m3u8" else ext

        return "mp4"

    def _format_id(self, stream: Any, index: int, height: Optional[int], ext: str) -> str:
        server_name = re.sub(r"[^a-z0-9]+", "-", getattr(stream, "server_name", "supjav").lower()).strip("-")
        quality = height or 0
        return f"sj-ai-{server_name or 'supjav'}-{quality}-{index}-{ext}"

    def _label_for_stream(
        self,
        stream: Any,
        stream_type: StreamType,
        quality: Optional[str],
    ) -> str:
        server_name = getattr(stream, "server_name", "SupJav") or "SupJav"
        kind = {
            StreamType.HLS: "HLS",
            StreamType.DIRECT: "MP4",
            StreamType.PROGRESSIVE: "Progressive",
            StreamType.DASH: "DASH",
        }.get(stream_type, "Video")
        parts = [server_name, kind]
        if quality:
            parts.append(quality)
        return " ".join(parts)

    def _cookies_for_url(self, url: str) -> Dict[str, str]:
        raw_session = getattr(self.session, "_session", None)
        cookie_jar = getattr(raw_session, "cookies", None)
        if cookie_jar is None:
            return {}

        parsed = urlparse(url)
        hostname = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        is_secure = parsed.scheme == "https"
        matched: Dict[str, str] = {}

        for cookie in cookie_jar:
            try:
                domain = (cookie.domain or "").lstrip(".").lower()
                if domain and hostname != domain and not hostname.endswith(f".{domain}"):
                    continue

                cookie_path = cookie.path or "/"
                if cookie_path != "/" and not path.startswith(cookie_path):
                    continue

                if getattr(cookie, "secure", False) and not is_secure:
                    continue

                if cookie.name:
                    matched[cookie.name] = cookie.value
            except Exception:
                continue

        return matched

    def _config_value(self, *path: str, default: Any = None) -> Any:
        current: Any = self.config
        missing = object()

        for key in path:
            if isinstance(current, dict):
                current = current.get(key, missing)
            else:
                current = getattr(current, key, missing)

            if current is missing:
                return default

        return default if current is None else current

    @staticmethod
    def _safe_int(value: Any, default: Optional[int]) -> Optional[int]:
        if value is None:
            return default
        try:
            return int(value)
        except Exception:
            return default

    def _is_supjav_page(self, url: str) -> bool:
        hostname = self._normalized_hostname(url)
        return hostname in self.SUPJAV_MIRROR_HOSTS

    def _canonicalize_supjav_url(self, url: str) -> str:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        if self._normalized_hostname(url) not in self.SUPJAV_MIRROR_HOSTS:
            return url

        query = f"?{parsed.query}" if parsed.query else ""
        path = parsed.path or "/"
        return f"https://supjav.com{path}{query}"

    @staticmethod
    def _normalized_hostname(url: str) -> str:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        hostname = (parsed.hostname or "").lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        if hostname.startswith("m."):
            hostname = hostname[2:]
        return hostname

    @staticmethod
    def _result_error(result: Any) -> str:
        errors = getattr(result, "errors", None)
        if isinstance(errors, list) and errors:
            return str(errors[0])
        return "supjav_extractor returned no valid streams"
