#!/usr/bin/env python3
"""DoodStream extractor core logic and project adapter."""

from __future__ import annotations

import re
import os
import sys
import time
import string
import random
import logging
import hashlib
import json
import asyncio
from abc import ABC, abstractmethod
from copy import deepcopy
from enum import Enum, auto
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
    NamedTuple,
    Callable,
    Generator,
)
from dataclasses import dataclass, field, asdict
from urllib.parse import urlparse, urljoin, parse_qs, urlencode, quote
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache, wraps
from contextlib import contextmanager
from datetime import datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base import ExtractorBase, register_extractor, ExtractionError as BaseExtractionError
from models.media import MediaInfo, MediaType, StreamFormat

try:
    import aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# CONSTANTS & CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

class DoodDomain(Enum):
    """All known DoodStream domains."""
    DOODSTREAM_COM  = "doodstream.com"
    DOOD_WATCH      = "dood.watch"
    DOOD_TO         = "dood.to"
    DOOD_SO         = "dood.so"
    DOOD_PM         = "dood.pm"
    DOOD_WF         = "dood.wf"
    DOOD_RE         = "dood.re"
    DOOD_CX         = "dood.cx"
    DOOD_LA         = "dood.la"
    DOOD_WS         = "dood.ws"
    DOOD_SH         = "dood.sh"
    DOOD_YT         = "dood.yt"
    DOOD_LI         = "dood.li"
    DS2PLAY_COM     = "ds2play.com"
    DOODS_PRO       = "doods.pro"
    DOOOOD_COM      = "dooood.com"
    DS2VIDEO_COM    = "ds2video.com"
    DOSTREAM_COM    = "dostream.com"
    D0O0D_COM       = "d0o0d.com"
    DO0OD_COM       = "do0od.com"
    D000D_COM       = "d000d.com"
    D0000D_COM      = "d0000d.com"
    DOODAPI_COM     = "doodapi.com"


# Build set of all known domains for validation
VALID_DOMAINS: set[str] = {d.value for d in DoodDomain}

# Domain pattern for regex matching
DOMAIN_PATTERN = "|".join(
    re.escape(d).replace(r"\.", r"\.") for d in sorted(VALID_DOMAINS, key=len, reverse=True)
)

# Master URL pattern
URL_PATTERN = re.compile(
    rf"https?://(?:www\.)?(?:{DOMAIN_PATTERN})/(?:d|e)/([a-zA-Z0-9]+)",
    re.IGNORECASE,
)

# JavaScript extraction patterns
PASS_MD5_PATTERNS = [
    re.compile(r"""\$\.get\s*\(\s*['"](/pass_md5/[^'"]+)['"]""", re.IGNORECASE),
    re.compile(r"""pass_md5/([^'"&\s]+)""", re.IGNORECASE),
    re.compile(r"""(?:fetch|XMLHttpRequest)\s*\(\s*['"](/pass_md5/[^'"]+)['"]""", re.IGNORECASE),
    re.compile(r"""url\s*[:=]\s*['"](/pass_md5/[^'"]+)['"]""", re.IGNORECASE),
    re.compile(r"""['"](/pass_md5/[^'"]+)['"]""", re.IGNORECASE),
]

TOKEN_PATTERNS = [
    re.compile(r"""[?&]token=([a-zA-Z0-9]+)"""),
    re.compile(r"""token\s*[:=]\s*['"]([a-zA-Z0-9]+)['"]"""),
    re.compile(r"""makePlay\s*\(.*?['"]([a-zA-Z0-9]{10,})['"]"""),
]

EXPIRY_PATTERNS = [
    re.compile(r"""[?&]expiry=(\d+)"""),
    re.compile(r"""expiry\s*[:=]\s*['"]?(\d+)['"]?"""),
]

TITLE_PATTERNS = [
    re.compile(r"""<title>([^<]+)</title>""", re.IGNORECASE),
    re.compile(r"""class=["']title["'][^>]*>([^<]+)""", re.IGNORECASE),
    re.compile(r"""og:title["']\s+content=["']([^"']+)""", re.IGNORECASE),
    re.compile(r"""<h[1-6][^>]*class=["'][^"']*title[^"']*["'][^>]*>([^<]+)""", re.IGNORECASE),
]

THUMBNAIL_PATTERNS = [
    re.compile(r"""og:image["']\s+content=["']([^"']+)""", re.IGNORECASE),
    re.compile(r"""poster\s*[:=]\s*["']([^"']+)""", re.IGNORECASE),
    re.compile(r"""snapshotUrl\s*[:=]\s*["']([^"']+)""", re.IGNORECASE),
    re.compile(r"""image\s*[:=]\s*["']([^"']+\.(?:jpg|png|webp))""", re.IGNORECASE),
]

SUBTITLE_PATTERNS = [
    re.compile(
        r"""(?:track|source)[^>]*src=["']([^"']+\.(?:vtt|srt|ass|ssa))["'][^>]*"""
        r"""(?:label=["']([^"']+)["'])?[^>]*(?:srclang=["']([^"']+)["'])?""",
        re.IGNORECASE,
    ),
    re.compile(
        r"""(?:subtitle|caption)s?\s*[:=]\s*\[\s*\{[^}]*(?:url|src|file)\s*:\s*["']([^"']+)["']"""
        r"""[^}]*(?:label|lang)\s*:\s*["']([^"']+)["']""",
        re.IGNORECASE,
    ),
]


@dataclass(frozen=True)
class ExtractorConfig:
    """Immutable configuration for the DoodStream extractor."""
    max_retries: int = 3
    retry_delay: float = 1.5
    retry_backoff: float = 2.0
    timeout: int = 30
    connect_timeout: int = 10
    random_string_length: int = 10
    cache_ttl: int = 300              # seconds
    max_cache_size: int = 128
    parallel_workers: int = 4
    respect_rate_limit: bool = True
    rate_limit_delay: float = 0.5
    verify_ssl: bool = True
    follow_redirects: bool = True
    max_redirects: int = 10
    debug_dump_html: bool = False
    debug_dump_dir: str = "./debug_dumps"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    )
    accept_language: str = "en-US,en;q=0.9"


DEFAULT_CONFIG = ExtractorConfig()


# ═══════════════════════════════════════════════════════════════════════════════
# DATA MODELS
# ═══════════════════════════════════════════════════════════════════════════════

class StreamQuality(Enum):
    """Video quality levels."""
    AUTO    = auto()
    LOW     = auto()   # 360p
    MEDIUM  = auto()   # 480p
    HIGH    = auto()   # 720p
    FULL_HD = auto()   # 1080p
    ULTRA   = auto()   # 4K


@dataclass
class Subtitle:
    """Subtitle track information."""
    url: str
    language: str = "unknown"
    label: str = ""
    format: str = "vtt"

    def __post_init__(self):
        if not self.label:
            self.label = self.language
        ext = self.url.rsplit(".", 1)[-1].lower() if "." in self.url else "vtt"
        if ext in ("vtt", "srt", "ass", "ssa"):
            self.format = ext


@dataclass
class VideoStream:
    """Represents a single video stream/quality variant."""
    url: str
    quality: StreamQuality = StreamQuality.AUTO
    resolution: Optional[str] = None
    bitrate: Optional[int] = None
    file_size: Optional[int] = None
    content_type: Optional[str] = None
    headers: Dict[str, str] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return bool(self.url and self.url.startswith("http"))


@dataclass
class ExtractedMedia:
    """Complete extraction result containing all media information."""
    video_id: str
    title: str = "Unknown"
    thumbnail: Optional[str] = None
    duration: Optional[int] = None    # seconds
    streams: List[VideoStream] = field(default_factory=list)
    subtitles: List[Subtitle] = field(default_factory=list)
    source_url: str = ""
    domain: str = ""
    extracted_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    raw_metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def best_stream(self) -> Optional[VideoStream]:
        """Return the best available stream."""
        valid = [s for s in self.streams if s.is_valid]
        if not valid:
            return None
        # Prefer by quality enum ordinal (higher = better)
        return max(valid, key=lambda s: s.quality.value)

    @property
    def stream_url(self) -> Optional[str]:
        """Convenience: return best stream URL."""
        best = self.best_stream
        return best.url if best else None

    @property
    def is_valid(self) -> bool:
        return bool(self.streams and any(s.is_valid for s in self.streams))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        data = asdict(self)
        data["best_stream_url"] = self.stream_url
        for s in data["streams"]:
            s["quality"] = StreamQuality(s["quality"]).name
        return data

    def to_json(self, indent: int = 2) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


@dataclass
class CacheEntry:
    """Cache entry with TTL support."""
    data: ExtractedMedia
    created_at: float = field(default_factory=time.time)
    ttl: int = 300

    @property
    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEPTIONS
# ═══════════════════════════════════════════════════════════════════════════════

class DoodExtractorError(Exception):
    """Base exception for all extractor errors."""
    def __init__(self, message: str, video_id: str = "", url: str = ""):
        self.video_id = video_id
        self.url = url
        super().__init__(message)


class InvalidURLError(DoodExtractorError):
    """Raised when the URL is not a valid DoodStream URL."""
    pass


class VideoNotFoundError(DoodExtractorError):
    """Raised when the video does not exist or has been removed."""
    pass


class ExtractionError(DoodExtractorError):
    """Raised when extraction of the video URL fails."""
    pass


class NetworkError(DoodExtractorError):
    """Raised on network-related failures."""
    pass


class RateLimitError(DoodExtractorError):
    """Raised when rate-limited by the server."""
    pass


class CaptchaError(DoodExtractorError):
    """Raised when a CAPTCHA challenge is encountered."""
    pass


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def generate_random_string(length: int = 10, charset: Optional[str] = None) -> str:
    """Generate a cryptographically-aware random string."""
    if charset is None:
        charset = string.ascii_letters + string.digits
    return "".join(random.choices(charset, k=length))


def normalize_url(url: str) -> str:
    """Normalize a DoodStream URL to its embed form."""
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")

    if len(path_parts) >= 2:
        video_id = path_parts[-1]
        # Always use embed URL for consistency
        new_path = f"/e/{video_id}"
        return f"{parsed.scheme}://{parsed.netloc}{new_path}"

    return url


def extract_video_id(url: str) -> Optional[str]:
    """Extract the video ID from a DoodStream URL."""
    match = URL_PATTERN.search(url)
    if match:
        return match.group(1)

    # Fallback: try to get ID from path
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] in ("d", "e"):
        return parts[1].split("?")[0].split("#")[0]

    return None


def extract_domain(url: str) -> str:
    """Extract the domain from a URL."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def validate_url(url: str) -> bool:
    """Validate whether a URL is a supported DoodStream URL."""
    if not url:
        return False
    domain = extract_domain(url)
    return domain in VALID_DOMAINS and extract_video_id(url) is not None


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Sanitize a string for use as a filename."""
    # Remove invalid chars
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name)
    name = name.strip(". ")
    if len(name) > max_length:
        name = name[:max_length].rsplit(" ", 1)[0]
    return name or "untitled"


def human_readable_size(size_bytes: int) -> str:
    """Convert bytes to human readable string."""
    if size_bytes == 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    size = float(size_bytes)
    while size >= 1024.0 and i < len(units) - 1:
        size /= 1024.0
        i += 1
    return f"{size:.2f} {units[i]}"


def retry_decorator(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple = (Exception,),
):
    """Decorator for automatic retry with exponential backoff."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            f"Attempt {attempt}/{max_retries} failed: {e}. "
                            f"Retrying in {current_delay:.1f}s..."
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(f"All {max_retries} attempts failed.")
            raise last_exception
        return wrapper
    return decorator


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP SESSION FACTORY
# ═══════════════════════════════════════════════════════════════════════════════

class SessionFactory:
    """Factory for creating configured HTTP sessions."""

    @staticmethod
    def create(config: ExtractorConfig = DEFAULT_CONFIG) -> requests.Session:
        """Create a requests.Session with retry strategy and default headers."""
        session = requests.Session()

        # Configure retry strategy
        retry_strategy = Retry(
            total=config.max_retries,
            backoff_factor=config.retry_delay,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "HEAD", "OPTIONS"],
            raise_on_status=False,
        )

        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=20,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        # Default headers
        session.headers.update({
            "User-Agent": config.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                      "image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": config.accept_language,
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
            "sec-ch-ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
        })

        session.verify = config.verify_ssl
        session.max_redirects = config.max_redirects

        return session


# ═══════════════════════════════════════════════════════════════════════════════
# HTML PARSER
# ═══════════════════════════════════════════════════════════════════════════════

class HTMLParser:
    """Parse DoodStream HTML pages to extract required data."""

    def __init__(self, html: str, url: str):
        self.html = html
        self.url = url
        self._soup = None

    @property
    def soup(self):
        if self._soup is None and BS4_AVAILABLE:
            self._soup = BeautifulSoup(self.html, "html.parser")
        return self._soup

    def find_pass_md5_url(self) -> Optional[str]:
        """Extract the /pass_md5/ URL from the page source."""
        for pattern in PASS_MD5_PATTERNS:
            match = pattern.search(self.html)
            if match:
                path = match.group(1) if match.group(1).startswith("/") else f"/pass_md5/{match.group(1)}"
                logger.debug(f"Found pass_md5 path: {path}")
                return path

        # Advanced: search in all <script> blocks
        script_blocks = re.findall(
            r"<script[^>]*>(.*?)</script>", self.html, re.DOTALL | re.IGNORECASE
        )
        for block in script_blocks:
            for pattern in PASS_MD5_PATTERNS:
                match = pattern.search(block)
                if match:
                    path = match.group(1) if match.group(1).startswith("/") else f"/pass_md5/{match.group(1)}"
                    logger.debug(f"Found pass_md5 path in script block: {path}")
                    return path

        # Last resort: search for obfuscated pass_md5
        obfuscated = re.search(
            r"""(?:atob|decodeURIComponent)\s*\(\s*['"]([A-Za-z0-9+/=]+)['"]""",
            self.html,
        )
        if obfuscated:
            import base64
            try:
                decoded = base64.b64decode(obfuscated.group(1)).decode("utf-8")
                if "/pass_md5/" in decoded:
                    md5_match = re.search(r"(/pass_md5/[^\s'\"]+)", decoded)
                    if md5_match:
                        logger.debug(f"Found obfuscated pass_md5: {md5_match.group(1)}")
                        return md5_match.group(1)
            except Exception:
                pass

        return None

    def find_token(self) -> Optional[str]:
        """Extract the token parameter from page source."""
        for pattern in TOKEN_PATTERNS:
            match = pattern.search(self.html)
            if match:
                return match.group(1)
        return None

    def find_expiry(self) -> Optional[str]:
        """Extract expiry timestamp from page source."""
        for pattern in EXPIRY_PATTERNS:
            match = pattern.search(self.html)
            if match:
                return match.group(1)
        return None

    def find_title(self) -> str:
        """Extract video title from the page."""
        for pattern in TITLE_PATTERNS:
            match = pattern.search(self.html)
            if match:
                title = match.group(1).strip()
                # Clean up common suffixes
                for suffix in [
                    " - DoodStream", " - Doodstream", "| DoodStream",
                    " - dood.watch", " - dood.to", "Watch ",
                ]:
                    title = title.replace(suffix, "").strip()
                if title and title.lower() not in ("doodstream", "video not found"):
                    return title

        if self.soup:
            h1 = self.soup.find("h1")
            if h1:
                return h1.get_text(strip=True)

        return "Unknown"

    def find_thumbnail(self) -> Optional[str]:
        """Extract video thumbnail URL."""
        for pattern in THUMBNAIL_PATTERNS:
            match = pattern.search(self.html)
            if match:
                thumb = match.group(1)
                if not thumb.startswith("http"):
                    thumb = urljoin(self.url, thumb)
                return thumb
        return None

    def find_duration(self) -> Optional[int]:
        """Extract video duration in seconds."""
        patterns = [
            re.compile(r"""duration["']\s*content=["'](\d+)["']""", re.I),
            re.compile(r"""duration\s*[:=]\s*["']?(\d+)["']?""", re.I),
            re.compile(r"""(\d{1,2}):(\d{2}):(\d{2})"""),
            re.compile(r"""(\d{1,2}):(\d{2})"""),
        ]
        for p in patterns:
            m = p.search(self.html)
            if m:
                groups = m.groups()
                if len(groups) == 1:
                    return int(groups[0])
                elif len(groups) == 3:
                    return int(groups[0]) * 3600 + int(groups[1]) * 60 + int(groups[2])
                elif len(groups) == 2:
                    return int(groups[0]) * 60 + int(groups[1])
        return None

    def find_subtitles(self) -> List[Subtitle]:
        """Extract subtitle tracks from the page."""
        subtitles = []
        seen_urls: set = set()

        for pattern in SUBTITLE_PATTERNS:
            for match in pattern.finditer(self.html):
                url = match.group(1)
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                if not url.startswith("http"):
                    url = urljoin(self.url, url)

                lang = match.group(3) if len(match.groups()) >= 3 and match.group(3) else "unknown"
                label = match.group(2) if len(match.groups()) >= 2 and match.group(2) else lang

                subtitles.append(Subtitle(url=url, language=lang, label=label))

        # Also try BeautifulSoup for better parsing
        if self.soup:
            for track in self.soup.find_all("track", kind="captions"):
                src = track.get("src", "")
                if src and src not in seen_urls:
                    seen_urls.add(src)
                    if not src.startswith("http"):
                        src = urljoin(self.url, src)
                    subtitles.append(Subtitle(
                        url=src,
                        language=track.get("srclang", "unknown"),
                        label=track.get("label", track.get("srclang", "unknown")),
                    ))

        return subtitles

    def is_file_not_found(self) -> bool:
        """Check if the page indicates the file was not found / removed."""
        not_found_indicators = [
            "file not found",
            "video not found",
            "file has been removed",
            "video has been removed",
            "file is no longer available",
            "404 not found",
            "file was deleted",
            "the file you are looking for is not available",
            "this video doesn't exist",
        ]
        html_lower = self.html.lower()
        return any(indicator in html_lower for indicator in not_found_indicators)

    def detect_captcha(self) -> bool:
        """Check if the page contains a CAPTCHA challenge."""
        captcha_indicators = [
            "g-recaptcha",
            "h-captcha",
            "cf-turnstile",
            "captcha-container",
            "verify you are human",
            "challenge-platform",
        ]
        html_lower = self.html.lower()
        return any(indicator in html_lower for indicator in captcha_indicators)

    def extract_all_metadata(self) -> Dict[str, Any]:
        """Extract all available metadata from the page."""
        meta: Dict[str, Any] = {}

        # Open Graph tags
        og_tags = re.findall(
            r"""<meta\s+(?:property|name)=["']og:(\w+)["']\s+content=["']([^"']*)["']""",
            self.html, re.I,
        )
        for key, value in og_tags:
            meta[f"og_{key}"] = value

        # File size
        size_match = re.search(r"""(\d+(?:\.\d+)?)\s*(MB|GB|KB|TB)""", self.html, re.I)
        if size_match:
            size_val = float(size_match.group(1))
            unit = size_match.group(2).upper()
            multipliers = {"KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
            meta["file_size_bytes"] = int(size_val * multipliers.get(unit, 1))
            meta["file_size_human"] = f"{size_val} {unit}"

        # Views
        views_match = re.search(r"""(\d[\d,]*)\s*(?:views?|plays?)""", self.html, re.I)
        if views_match:
            meta["views"] = int(views_match.group(1).replace(",", ""))

        # Upload date
        date_match = re.search(
            r"""(?:uploaded?|date|published)\s*[:=]?\s*["']?(\d{4}[-/]\d{2}[-/]\d{2})""",
            self.html, re.I,
        )
        if date_match:
            meta["upload_date"] = date_match.group(1)

        return meta


# ═══════════════════════════════════════════════════════════════════════════════
# URL BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

class DoodURLBuilder:
    """Build the final playback URL from extracted components."""

    @staticmethod
    def build(
        pass_md5_response: str,
        token: Optional[str] = None,
        expiry: Optional[str] = None,
        random_str_length: int = 10,
    ) -> str:
        """
        Construct the final video URL.

        DoodStream URL format:
            {pass_md5_response}{random_string}?token={token}&expiry={expiry}
        """
        rand = generate_random_string(random_str_length)
        md5_of_rand = hashlib.md5(rand.encode()).hexdigest()

        base_url = pass_md5_response.strip()

        # Build query parameters
        params: Dict[str, str] = {}
        if token:
            params["token"] = token
        if expiry:
            params["expiry"] = expiry

        # Construct URL
        url = f"{base_url}{rand}"
        if params:
            url += "?" + urlencode(params)

        return url

    @staticmethod
    def build_v2(
        pass_md5_response: str,
        page_html: str,
        random_str_length: int = 10,
    ) -> str:
        """
        Alternative URL construction for newer DoodStream versions.
        Extracts token and expiry from the page HTML dynamically.
        """
        rand = generate_random_string(random_str_length)
        base_url = pass_md5_response.strip()

        # Try to find the construction pattern in JS
        # Some versions append: randomString + "?token=" + token + "&expiry=" + expiry
        token_match = re.search(r"""[?&]token=([a-zA-Z0-9]+)""", page_html)
        expiry_match = re.search(r"""[?&]expiry=(\d+)""", page_html)

        token = token_match.group(1) if token_match else ""
        expiry = expiry_match.group(1) if expiry_match else str(int(time.time() * 1000))

        if token and expiry:
            return f"{base_url}{rand}?token={token}&expiry={expiry}"

        return f"{base_url}{rand}"


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN EXTRACTOR - SYNCHRONOUS
# ═══════════════════════════════════════════════════════════════════════════════

class DoodStreamExtractor:
    """
    Professional DoodStream video URL extractor.

    Features:
        - Multi-domain support (20+ known DoodStream domains)
        - Automatic retry with exponential backoff
        - In-memory caching with TTL
        - Metadata extraction (title, thumbnail, duration, subtitles)
        - Robust error handling with typed exceptions
        - Debug mode with HTML dump support
        - URL validation and normalization
        - Session reuse with connection pooling
        - Rate limiting support
        - Stream URL verification

    Usage:
        >>> extractor = DoodStreamExtractor()
        >>> result = extractor.extract("https://doodstream.com/e/abc123")
        >>> print(result.stream_url)
        >>> print(result.title)
        >>> print(result.to_json())
    """

    def __init__(
        self,
        config: Optional[ExtractorConfig] = None,
        session: Optional[requests.Session] = None,
    ):
        self.config = config or DEFAULT_CONFIG
        self.session = session or SessionFactory.create(self.config)
        self._cache: Dict[str, CacheEntry] = {}
        self._last_request_time: float = 0.0
        self._request_count: int = 0

        logger.info("DoodStream Extractor initialized")
        logger.debug(f"Config: retries={self.config.max_retries}, timeout={self.config.timeout}")

    # ─── Cache Management ─────────────────────────────────────────────────

    def _get_cached(self, video_id: str) -> Optional[ExtractedMedia]:
        """Retrieve from cache if available and not expired."""
        entry = self._cache.get(video_id)
        if entry and not entry.is_expired:
            logger.debug(f"Cache HIT for {video_id}")
            return entry.data
        elif entry:
            logger.debug(f"Cache EXPIRED for {video_id}")
            del self._cache[video_id]
        return None

    def _set_cache(self, video_id: str, data: ExtractedMedia) -> None:
        """Store result in cache."""
        if len(self._cache) >= self.config.max_cache_size:
            # Evict oldest entry
            oldest_key = min(self._cache, key=lambda k: self._cache[k].created_at)
            del self._cache[oldest_key]
            logger.debug(f"Cache evicted: {oldest_key}")

        self._cache[video_id] = CacheEntry(data=data, ttl=self.config.cache_ttl)
        logger.debug(f"Cache SET for {video_id}")

    def clear_cache(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
        logger.info("Cache cleared")

    # ─── Rate Limiting ────────────────────────────────────────────────────

    def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        if not self.config.respect_rate_limit:
            return
        elapsed = time.time() - self._last_request_time
        if elapsed < self.config.rate_limit_delay:
            wait = self.config.rate_limit_delay - elapsed
            logger.debug(f"Rate limiting: waiting {wait:.2f}s")
            time.sleep(wait)
        self._last_request_time = time.time()

    # ─── HTTP Requests ────────────────────────────────────────────────────

    def _request(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        referer: Optional[str] = None,
        **kwargs,
    ) -> requests.Response:
        """
        Make an HTTP request with rate limiting, custom headers, and error handling.
        """
        self._rate_limit()
        self._request_count += 1

        req_headers = {}
        if referer:
            req_headers["Referer"] = referer
        if headers:
            req_headers.update(headers)

        timeout = kwargs.pop("timeout", (self.config.connect_timeout, self.config.timeout))

        try:
            logger.debug(f"HTTP {method} → {url}")
            response = self.session.request(
                method=method,
                url=url,
                headers=req_headers,
                timeout=timeout,
                allow_redirects=self.config.follow_redirects,
                **kwargs,
            )

            logger.debug(f"HTTP {response.status_code} ← {url} [{len(response.content)} bytes]")

            if response.status_code == 429:
                raise RateLimitError(
                    f"Rate limited by server (429). URL: {url}", url=url
                )

            return response

        except requests.exceptions.Timeout as e:
            raise NetworkError(f"Request timed out: {url}", url=url) from e
        except requests.exceptions.ConnectionError as e:
            raise NetworkError(f"Connection error: {url}", url=url) from e
        except requests.exceptions.TooManyRedirects as e:
            raise NetworkError(f"Too many redirects: {url}", url=url) from e
        except (RateLimitError, NetworkError):
            raise
        except requests.exceptions.RequestException as e:
            raise NetworkError(f"Request failed: {url} - {e}", url=url) from e

    # ─── Debug Support ────────────────────────────────────────────────────

    def _dump_html(self, html: str, video_id: str, stage: str) -> None:
        """Dump HTML to file for debugging."""
        if not self.config.debug_dump_html:
            return
        dump_dir = Path(self.config.debug_dump_dir)
        dump_dir.mkdir(parents=True, exist_ok=True)
        filepath = dump_dir / f"{video_id}_{stage}_{int(time.time())}.html"
        filepath.write_text(html, encoding="utf-8")
        logger.debug(f"HTML dumped → {filepath}")

    # ─── Core Extraction ──────────────────────────────────────────────────

    def _fetch_embed_page(self, url: str, video_id: str) -> str:
        """Fetch the embed page HTML."""
        response = self._request(url)

        if response.status_code == 404:
            raise VideoNotFoundError(
                f"Video not found (404): {video_id}", video_id=video_id, url=url
            )

        if response.status_code != 200:
            raise NetworkError(
                f"Unexpected status {response.status_code} for {url}",
                video_id=video_id, url=url,
            )

        html = response.text
        self._dump_html(html, video_id, "embed_page")
        return html

    def _fetch_pass_md5(self, base_url: str, pass_md5_path: str, referer: str) -> str:
        """Fetch the pass_md5 endpoint to get the partial video URL."""
        full_url = urljoin(base_url, pass_md5_path)

        response = self._request(
            full_url,
            referer=referer,
            headers={
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
            },
        )

        if response.status_code != 200:
            raise ExtractionError(
                f"pass_md5 request failed with status {response.status_code}",
                url=full_url,
            )

        result = response.text.strip()
        if not result:
            raise ExtractionError("Empty response from pass_md5 endpoint", url=full_url)

        logger.debug(f"pass_md5 response: {result[:80]}...")
        return result

    def _verify_stream_url(self, url: str, referer: str) -> Tuple[bool, Optional[int], Optional[str]]:
        """Verify a stream URL is accessible and get file info."""
        try:
            response = self._request(
                url,
                method="HEAD",
                referer=referer,
                headers={"Range": "bytes=0-0"},
            )

            is_valid = response.status_code in (200, 206, 302, 301)
            content_length = None
            content_type = response.headers.get("Content-Type")

            if "Content-Length" in response.headers:
                content_length = int(response.headers["Content-Length"])
            elif "Content-Range" in response.headers:
                range_header = response.headers["Content-Range"]
                size_match = re.search(r"/(\d+)", range_header)
                if size_match:
                    content_length = int(size_match.group(1))

            return is_valid, content_length, content_type

        except Exception as e:
            logger.warning(f"Stream URL verification failed: {e}")
            return False, None, None

    @retry_decorator(max_retries=3, delay=1.5, backoff=2.0, exceptions=(NetworkError, ExtractionError))
    def _extract_impl(self, url: str, video_id: str) -> ExtractedMedia:
        """Internal extraction implementation with retry support."""
        embed_url = normalize_url(url)
        parsed = urlparse(embed_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        domain = extract_domain(embed_url)

        logger.info(f"Extracting video: {video_id} from {domain}")

        # Step 1: Fetch embed page
        html = self._fetch_embed_page(embed_url, video_id)

        # Step 2: Parse HTML
        parser = HTMLParser(html, embed_url)

        # Check for errors
        if parser.is_file_not_found():
            raise VideoNotFoundError(
                f"Video {video_id} not found or removed",
                video_id=video_id, url=url,
            )

        if parser.detect_captcha():
            raise CaptchaError(
                f"CAPTCHA detected for video {video_id}",
                video_id=video_id, url=url,
            )

        # Step 3: Extract pass_md5 URL
        pass_md5_path = parser.find_pass_md5_url()
        if not pass_md5_path:
            self._dump_html(html, video_id, "no_pass_md5")
            raise ExtractionError(
                f"Could not find pass_md5 URL in page source for {video_id}",
                video_id=video_id, url=url,
            )

        # Step 4: Extract token and expiry
        token = parser.find_token()
        expiry = parser.find_expiry()

        logger.debug(f"Token: {token}, Expiry: {expiry}")

        # Step 5: Fetch pass_md5 response
        pass_md5_response = self._fetch_pass_md5(base_url, pass_md5_path, embed_url)

        # Step 6: Build final video URL
        video_url = DoodURLBuilder.build(
            pass_md5_response=pass_md5_response,
            token=token,
            expiry=expiry,
            random_str_length=self.config.random_string_length,
        )

        logger.info(f"Video URL constructed successfully for {video_id}")

        # Step 7: Build required headers for playback
        playback_headers = {
            "Referer": embed_url,
            "User-Agent": self.config.user_agent,
            "Accept": "*/*",
            "Accept-Language": self.config.accept_language,
            "Range": "bytes=0-",
        }

        # Step 8: Verify stream URL
        is_valid, file_size, content_type = self._verify_stream_url(video_url, embed_url)

        if is_valid:
            logger.info(
                f"Stream verified ✓ | Size: {human_readable_size(file_size) if file_size else 'unknown'}"
            )
        else:
            logger.warning("Stream URL verification returned invalid — URL may still work for direct download")

        # Step 9: Extract metadata
        title = parser.find_title()
        thumbnail = parser.find_thumbnail()
        duration = parser.find_duration()
        subtitles = parser.find_subtitles()
        raw_meta = parser.extract_all_metadata()

        # Step 10: Build result
        stream = VideoStream(
            url=video_url,
            quality=StreamQuality.AUTO,
            file_size=file_size,
            content_type=content_type,
            headers=playback_headers,
        )

        result = ExtractedMedia(
            video_id=video_id,
            title=title,
            thumbnail=thumbnail,
            duration=duration,
            streams=[stream],
            subtitles=subtitles,
            source_url=url,
            domain=domain,
            raw_metadata=raw_meta,
        )

        return result

    # ─── Public API ───────────────────────────────────────────────────────

    def extract(self, url: str, use_cache: bool = True) -> ExtractedMedia:
        """
        Extract video information from a DoodStream URL.

        Args:
            url:       The DoodStream video URL.
            use_cache: Whether to use cached results (default True).

        Returns:
            ExtractedMedia object containing video streams, metadata, etc.

        Raises:
            InvalidURLError:    If the URL is not a valid DoodStream URL.
            VideoNotFoundError: If the video does not exist.
            ExtractionError:    If the video URL cannot be extracted.
            NetworkError:       If a network error occurs.
            RateLimitError:     If the server rate-limits the request.
            CaptchaError:       If a CAPTCHA is encountered.
        """
        # Validate URL
        video_id = extract_video_id(url)
        if not video_id:
            raise InvalidURLError(f"Invalid DoodStream URL: {url}", url=url)

        domain = extract_domain(url)
        if domain not in VALID_DOMAINS:
            raise InvalidURLError(
                f"Unsupported domain: {domain}. "
                f"Supported: {', '.join(sorted(VALID_DOMAINS))}",
                url=url,
            )

        # Check cache
        if use_cache:
            cached = self._get_cached(video_id)
            if cached:
                logger.info(f"Returning cached result for {video_id}")
                return cached

        # Perform extraction
        result = self._extract_impl(url, video_id)

        # Cache result
        if use_cache and result.is_valid:
            self._set_cache(video_id, result)

        return result

    def extract_url(self, url: str) -> Optional[str]:
        """
        Convenience method: extract only the direct video URL.

        Returns:
            The direct video URL string, or None on failure.
        """
        try:
            result = self.extract(url)
            return result.stream_url
        except DoodExtractorError as e:
            logger.error(f"Extraction failed: {e}")
            return None

    def batch_extract(
        self,
        urls: List[str],
        parallel: bool = False,
        max_workers: Optional[int] = None,
    ) -> List[ExtractedMedia]:
        """
        Extract multiple videos.

        Args:
            urls:        List of DoodStream URLs.
            parallel:    Whether to use parallel extraction.
            max_workers: Number of parallel workers (default from config).

        Returns:
            List of ExtractedMedia results (may contain failed entries).
        """
        workers = max_workers or self.config.parallel_workers
        results: List[ExtractedMedia] = []

        logger.info(f"Batch extraction: {len(urls)} URLs, parallel={parallel}")

        if parallel and len(urls) > 1:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(self._safe_extract, url): url for url in urls}
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        result = future.result()
                        if result:
                            results.append(result)
                    except Exception as e:
                        logger.error(f"Batch extraction failed for {url}: {e}")
        else:
            for url in urls:
                result = self._safe_extract(url)
                if result:
                    results.append(result)

        logger.info(f"Batch extraction complete: {len(results)}/{len(urls)} successful")
        return results

    def _safe_extract(self, url: str) -> Optional[ExtractedMedia]:
        """Extract without raising exceptions."""
        try:
            return self.extract(url)
        except DoodExtractorError as e:
            logger.error(f"Failed to extract {url}: {e}")
            return None

    def get_info(self, url: str) -> Dict[str, Any]:
        """
        Get video information as a dictionary.

        Returns:
            Dictionary with video info or error details.
        """
        try:
            result = self.extract(url)
            return {
                "status": "success",
                "data": result.to_dict(),
            }
        except DoodExtractorError as e:
            return {
                "status": "error",
                "error_type": type(e).__name__,
                "message": str(e),
                "url": url,
            }

    @property
    def stats(self) -> Dict[str, Any]:
        """Return extractor statistics."""
        return {
            "total_requests": self._request_count,
            "cache_size": len(self._cache),
            "cache_entries": list(self._cache.keys()),
        }

    def close(self) -> None:
        """Close the HTTP session and clean up resources."""
        self.session.close()
        self.clear_cache()
        logger.info("Extractor closed")

    def __enter__(self) -> "DoodStreamExtractor":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def __repr__(self) -> str:
        return (
            f"<DoodStreamExtractor("
            f"domains={len(VALID_DOMAINS)}, "
            f"cache={len(self._cache)}, "
            f"requests={self._request_count})>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# ASYNC EXTRACTOR
# ═══════════════════════════════════════════════════════════════════════════════

class AsyncDoodStreamExtractor:
    """
    Asynchronous version of the DoodStream extractor.
    Requires `aiohttp` package.
    """

    def __init__(self, config: Optional[ExtractorConfig] = None):
        if not AIOHTTP_AVAILABLE:
            raise ImportError("aiohttp is required for async extraction. Install: pip install aiohttp")
        self.config = config or DEFAULT_CONFIG
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache: Dict[str, CacheEntry] = {}

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create an aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(
                total=self.config.timeout,
                connect=self.config.connect_timeout,
            )
            headers = {
                "User-Agent": self.config.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": self.config.accept_language,
            }
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                headers=headers,
            )
        return self._session

    async def _request(
        self,
        url: str,
        referer: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> str:
        """Make an async HTTP GET request."""
        session = await self._get_session()
        headers = {}
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)

        async with session.get(url, headers=headers, ssl=self.config.verify_ssl) as resp:
            if resp.status == 429:
                raise RateLimitError(f"Rate limited: {url}", url=url)
            if resp.status == 404:
                raise VideoNotFoundError(f"Not found: {url}", url=url)
            if resp.status != 200:
                raise NetworkError(f"HTTP {resp.status}: {url}", url=url)
            return await resp.text()

    async def extract(self, url: str) -> ExtractedMedia:
        """
        Asynchronously extract video info from a DoodStream URL.
        """
        video_id = extract_video_id(url)
        if not video_id:
            raise InvalidURLError(f"Invalid URL: {url}", url=url)

        # Check cache
        cached = self._cache.get(video_id)
        if cached and not cached.is_expired:
            return cached.data

        embed_url = normalize_url(url)
        parsed = urlparse(embed_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        domain = extract_domain(embed_url)

        logger.info(f"[Async] Extracting: {video_id}")

        # Fetch page
        html = await self._request(embed_url)
        parser = HTMLParser(html, embed_url)

        if parser.is_file_not_found():
            raise VideoNotFoundError(f"Video not found: {video_id}", video_id=video_id, url=url)

        if parser.detect_captcha():
            raise CaptchaError(f"CAPTCHA detected: {video_id}", video_id=video_id, url=url)

        pass_md5_path = parser.find_pass_md5_url()
        if not pass_md5_path:
            raise ExtractionError(f"No pass_md5 URL found: {video_id}", video_id=video_id, url=url)

        token = parser.find_token()
        expiry = parser.find_expiry()

        # Fetch pass_md5
        pass_md5_url = urljoin(base_url, pass_md5_path)
        pass_md5_response = await self._request(
            pass_md5_url,
            referer=embed_url,
            extra_headers={
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "*/*",
            },
        )
        pass_md5_response = pass_md5_response.strip()

        # Build URL
        video_url = DoodURLBuilder.build(
            pass_md5_response=pass_md5_response,
            token=token,
            expiry=expiry,
            random_str_length=self.config.random_string_length,
        )

        playback_headers = {
            "Referer": embed_url,
            "User-Agent": self.config.user_agent,
        }

        stream = VideoStream(
            url=video_url,
            quality=StreamQuality.AUTO,
            headers=playback_headers,
        )

        result = ExtractedMedia(
            video_id=video_id,
            title=parser.find_title(),
            thumbnail=parser.find_thumbnail(),
            duration=parser.find_duration(),
            streams=[stream],
            subtitles=parser.find_subtitles(),
            source_url=url,
            domain=domain,
            raw_metadata=parser.extract_all_metadata(),
        )

        # Cache
        self._cache[video_id] = CacheEntry(data=result, ttl=self.config.cache_ttl)

        logger.info(f"[Async] Extraction complete: {video_id}")
        return result

    async def batch_extract(self, urls: List[str]) -> List[ExtractedMedia]:
        """Extract multiple URLs concurrently."""
        tasks = [self.extract(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        extracted = []
        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                logger.error(f"[Async] Failed {url}: {result}")
            else:
                extracted.append(result)

        return extracted

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            logger.info("[Async] Session closed")

    async def __aenter__(self) -> "AsyncDoodStreamExtractor":
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()


def _config_value(source: Any, key: str, *, section: Optional[str] = None, default: Any = None) -> Any:
    """Resolve a config value from dict-like or object-like config inputs."""
    if source is None:
        return default

    if isinstance(source, dict):
        if key in source and source.get(key) is not None:
            return source.get(key)
        if section:
            return _config_value(source.get(section), key, default=default)
        return default

    if section and hasattr(source, section):
        return _config_value(getattr(source, section), key, default=default)

    if hasattr(source, key):
        value = getattr(source, key)
        return default if value is None else value

    return default


@register_extractor()
class DoodstreamExtractor(ExtractorBase):
    """Main-project adapter for the DoodStream extractor core."""

    EXTRACTOR_NAME = "doodstream"
    EXTRACTOR_DESCRIPTION = "DoodStream extractor (embed page + pass_md5 resolution)"
    URL_PATTERNS = [
        rf"https?://(?:www\.)?(?:{DOMAIN_PATTERN})/(?:d|e)/[A-Za-z0-9]+(?:[/?#].*)?$",
        r"https?://(?:www\.)?doodstm\.surge\.sh/v(?:/)?(?:\?[^#]*\bv=[A-Za-z0-9_-]+[^#]*)?$",
    ]
    WRAPPER_HOSTS = {"doodstm.surge.sh"}

    def __init__(self, session, config: Optional[Dict[str, Any]] = None):
        super().__init__(session, config=config)
        self._core = DoodStreamExtractor(
            config=self._build_core_config(),
            session=self._get_requests_session(),
        )

    def _get_requests_session(self) -> requests.Session:
        raw_session = getattr(self.session, "_session", None)
        if isinstance(raw_session, requests.Session):
            return raw_session
        if isinstance(self.session, requests.Session):
            return self.session
        raise BaseExtractionError("doodstream: unsupported session type")

    def _build_core_config(self) -> ExtractorConfig:
        raw_session = self._get_requests_session()
        timeout = int(
            _config_value(
                self.config,
                "timeout",
                section="download",
                default=getattr(self.session, "timeout", DEFAULT_CONFIG.timeout),
            )
            or DEFAULT_CONFIG.timeout
        )
        max_retries = int(
            getattr(self.session, "max_retries", None)
            or _config_value(self.config, "max_retries", section="download", default=DEFAULT_CONFIG.max_retries)
            or DEFAULT_CONFIG.max_retries
        )
        retry_delay = float(
            _config_value(self.config, "retry_delay", section="download", default=DEFAULT_CONFIG.retry_delay)
            or DEFAULT_CONFIG.retry_delay
        )
        connect_timeout = int(
            _config_value(
                self.config,
                "connect_timeout",
                default=min(timeout, DEFAULT_CONFIG.connect_timeout),
            )
            or min(timeout, DEFAULT_CONFIG.connect_timeout)
        )

        return ExtractorConfig(
            max_retries=max_retries,
            retry_delay=retry_delay,
            timeout=timeout,
            connect_timeout=max(1, connect_timeout),
            debug_dump_html=bool(
                _config_value(self.config, "save_debug_html", section="extractor", default=False)
                or _config_value(self.config, "debug_dump_html", default=False)
            ),
            user_agent=str(
                raw_session.headers.get("User-Agent")
                or getattr(self.session, "user_agent", DEFAULT_CONFIG.user_agent)
                or DEFAULT_CONFIG.user_agent
            ),
            accept_language=str(
                raw_session.headers.get("Accept-Language")
                or DEFAULT_CONFIG.accept_language
            ),
            verify_ssl=bool(getattr(raw_session, "verify", DEFAULT_CONFIG.verify_ssl)),
            max_redirects=int(getattr(raw_session, "max_redirects", DEFAULT_CONFIG.max_redirects)),
        )

    def extract(self, url: str) -> MediaInfo:
        logger.info("DoodStream extraction for: %s", url)
        if self._is_wrapper_url(url):
            return self._extract_wrapper_media(url)
        try:
            result = self._core.extract(url)
        except DoodExtractorError as exc:
            raise BaseExtractionError(str(exc)) from exc
        return self._to_media_info(result, url)

    def _is_wrapper_url(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host in self.WRAPPER_HOSTS

    def _extract_wrapper_media(self, url: str) -> MediaInfo:
        parsed = urlparse(url)
        video_id = (parse_qs(parsed.query).get("v") or [None])[0]
        if not video_id:
            raise BaseExtractionError("doodstream: wrapper URL missing video id")

        html = ""
        stream_url = self._build_wrapper_fallback_stream_url(video_id)
        headers = {
            "Referer": url,
            "Origin": f"{parsed.scheme}://{parsed.netloc}",
        }

        filesize = None
        content_type = None
        try:
            response = self._get_requests_session().head(
                stream_url,
                headers=headers,
                allow_redirects=True,
                timeout=self._core.config.timeout,
            )
            if response.ok:
                content_type = response.headers.get("Content-Type")
                content_length = response.headers.get("Content-Length")
                if content_length and content_length.isdigit():
                    filesize = int(content_length)
        except Exception:
            pass

        return MediaInfo(
            id=str(video_id),
            title=self._extract_wrapper_title(html, video_id),
            url=url,
            formats=[
                StreamFormat(
                    format_id="dood-wrapper-0",
                    url=stream_url,
                    ext="webm" if (content_type or "").lower().endswith("webm") else "mp4",
                    filesize=filesize,
                    stream_type=self._detect_stream_type(stream_url),
                    is_video=True,
                    is_audio=True,
                    headers=headers,
                    cookies=dict(self._get_requests_session().cookies.get_dict()),
                    label="Direct MP4",
                )
            ],
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
        )

    def _resolve_wrapper_stream_url(self, html: str, video_id: str) -> str:
        if not html:
            return self._build_wrapper_fallback_stream_url(video_id)

        direct_match = re.search(r"https?://cdn\d*\.videy\.co/[A-Za-z0-9_-]+\.mp4", html, re.IGNORECASE)
        if direct_match and "${" not in direct_match.group(0):
            return direct_match.group(0)

        template_match = re.search(
            r"(https?://cdn\d*\.videy\.co/)\$\{[^}]*videoId[^}]*\}\.mp4",
            html,
            re.IGNORECASE,
        )
        if template_match:
            return f"{template_match.group(1)}{quote(video_id, safe='')}.mp4"

        if "videy.co" in html.lower():
            return self._build_wrapper_fallback_stream_url(video_id)

        raise BaseExtractionError("doodstream: could not resolve wrapped stream URL")

    @staticmethod
    def _build_wrapper_fallback_stream_url(video_id: str) -> str:
        return f"https://cdn2.videy.co/{quote(video_id, safe='')}.mp4"

    @staticmethod
    def _extract_wrapper_title(html: str, video_id: str) -> str:
        title_match = re.search(r"<title>([^<]+)</title>", html, re.IGNORECASE)
        if title_match:
            title = re.sub(r"\s+", " ", title_match.group(1)).strip()
            normalized = title.lower()
            if title and normalized not in {"video streaming", "video streaming v1.00.8"}:
                return title
        return f"Videy {video_id}"

    def _to_media_info(self, result: ExtractedMedia, original_url: str) -> MediaInfo:
        raw_metadata = dict(result.raw_metadata or {})
        session_cookies = self._get_requests_session().cookies.get_dict()
        formats: List[StreamFormat] = []

        for index, stream in enumerate(result.streams):
            if not stream.is_valid:
                continue

            height = self._parse_quality(stream.resolution) if stream.resolution else None
            width = int(height * 16 / 9) if height else None
            filesize = stream.file_size
            if filesize is None and isinstance(raw_metadata.get("file_size_bytes"), int):
                filesize = raw_metadata.get("file_size_bytes")

            formats.append(
                StreamFormat(
                    format_id=f"dood-{index}",
                    url=stream.url,
                    ext=self._guess_extension(stream),
                    quality=stream.resolution,
                    width=width,
                    height=height,
                    bitrate=stream.bitrate,
                    filesize=filesize,
                    stream_type=self._detect_stream_type(stream.url),
                    is_video=True,
                    is_audio=True,
                    headers=dict(stream.headers or {}),
                    cookies=dict(session_cookies),
                    label=stream.resolution or "DoodStream direct",
                )
            )

        formats = self._deduplicate_formats(formats)
        formats.sort(key=lambda fmt: fmt.quality_score, reverse=True)

        if not formats:
            raise BaseExtractionError("doodstream: no playable formats found")

        subtitles: Dict[str, List[Dict[str, str]]] = {}
        for subtitle in result.subtitles:
            language = subtitle.language or "unknown"
            subtitles.setdefault(language, []).append({
                "url": subtitle.url,
                "ext": subtitle.format,
                "name": subtitle.label or language,
            })

        view_count = raw_metadata.get("views")
        if not isinstance(view_count, int):
            view_count = None

        return MediaInfo(
            id=result.video_id or self._generate_id(original_url),
            title=result.title or self._extract_title_from_url(original_url),
            url=result.source_url or original_url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            description=raw_metadata.get("og_description"),
            thumbnail=result.thumbnail or raw_metadata.get("og_image"),
            duration=result.duration,
            upload_date=raw_metadata.get("upload_date"),
            view_count=view_count,
            subtitles=subtitles,
        )

    @staticmethod
    def _guess_extension(stream: VideoStream) -> str:
        content_type = (stream.content_type or "").lower()
        stream_url = stream.url.lower()

        if "webm" in content_type or ".webm" in stream_url:
            return "webm"
        if "matroska" in content_type or ".mkv" in stream_url:
            return "mkv"
        if "flv" in content_type or ".flv" in stream_url:
            return "flv"
        return "mp4"
