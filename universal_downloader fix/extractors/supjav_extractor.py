#!/usr/bin/env python3
"""
SupJav AI-Assisted Video Extractor
===================================
Next-generation adaptive video URL extractor for supjav.com
Uses heuristic scoring, pattern learning, and multi-strategy fallbacks.

Single-file architecture with modular internal classes.
"""

# ═══════════════════════════════════════════════════════════════════════════════
# PART 1 — CORE FOUNDATION
# ═══════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import random
import re
import string
import sys
import time
import urllib.parse
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Type,
    Union,
)

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry as URLLibRetry
except ImportError:
    print("[FATAL] 'requests' is required: pip install requests")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("[FATAL] 'beautifulsoup4' is required: pip install beautifulsoup4")
    sys.exit(1)

# Optional imports — availability tracked at runtime
PLAYWRIGHT_AVAILABLE: bool = False
try:
    from playwright.sync_api import sync_playwright, Page, Browser, BrowserContext
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    pass

LXML_AVAILABLE: bool = False
try:
    import lxml  # noqa: F401
    LXML_AVAILABLE = True
except ImportError:
    pass


# ─── Version ─────────────────────────────────────────────────────────────────

__version__ = "2.0.0"
__author__ = "AI-Assisted Extractor Engine"


# ─── Enums ────────────────────────────────────────────────────────────────────

class StreamFormat(Enum):
    """Supported stream formats, ordered by preference."""
    MP4 = "mp4"
    M3U8 = "m3u8"
    WEBM = "webm"
    FLV = "flv"
    UNKNOWN = "unknown"


class Quality(Enum):
    """Video quality tiers."""
    Q4K = "2160p"
    Q1080 = "1080p"
    Q720 = "720p"
    Q480 = "480p"
    Q360 = "360p"
    UNKNOWN = "unknown"

    @property
    def priority(self) -> int:
        """Higher number = better quality."""
        mapping = {
            "2160p": 50,
            "1080p": 40,
            "720p": 30,
            "480p": 20,
            "360p": 10,
            "unknown": 5,
        }
        return mapping.get(self.value, 0)


class ExtractionStrategy(Enum):
    """Extraction strategy tiers — escalation order."""
    DIRECT_PARSE = auto()
    IFRAME_FOLLOW = auto()
    SCRIPT_SCAN = auto()
    SERVER_API = auto()
    HEADLESS_BROWSER = auto()


class Confidence(Enum):
    """Confidence level for extracted results."""
    CERTAIN = 100
    HIGH = 80
    MEDIUM = 60
    LOW = 40
    GUESS = 20
    NONE = 0


# ─── Configuration ────────────────────────────────────────────────────────────

@dataclass
class ExtractorConfig:
    """
    Central configuration for the entire extractor pipeline.

    All tuneable parameters live here — no magic constants buried in code.
    """

    # ── Network ──────────────────────────────────────────────────────────
    request_timeout: int = 30
    max_retries: int = 3
    retry_backoff_factor: float = 1.5
    retry_status_forcelist: Tuple[int, ...] = (429, 500, 502, 503, 504)
    max_redirects: int = 10

    # ── Rate limiting ────────────────────────────────────────────────────
    min_request_interval: float = 0.5   # seconds between requests
    max_request_interval: float = 2.0   # random jitter ceiling

    # ── Parsing ──────────────────────────────────────────────────────────
    html_parser: str = "lxml" if LXML_AVAILABLE else "html.parser"

    # ── Quality preference ───────────────────────────────────────────────
    preferred_quality: Quality = Quality.Q1080
    format_priority: List[StreamFormat] = field(
        default_factory=lambda: [
            StreamFormat.MP4,
            StreamFormat.M3U8,
            StreamFormat.WEBM,
            StreamFormat.FLV,
        ]
    )

    # ── Headless browser ─────────────────────────────────────────────────
    headless: bool = True
    browser_timeout: int = 30_000  # ms
    intercept_timeout: int = 15_000  # ms

    # ── Pattern learner ──────────────────────────────────────────────────
    pattern_cache_file: Optional[str] = None  # None = in-memory only
    max_cached_patterns: int = 500

    # ── Debug ────────────────────────────────────────────────────────────
    debug: bool = False
    log_level: str = "INFO"

    # ── Domain ───────────────────────────────────────────────────────────
    base_domain: str = "supjav.com"
    base_url: str = "https://supjav.com"

    def __post_init__(self) -> None:
        if self.debug:
            self.log_level = "DEBUG"


# ─── Logger Setup ─────────────────────────────────────────────────────────────

class ExtractorLogger:
    """
    Custom logger with coloured console output and structured formatting.

    Provides a single, consistent logging interface across all modules.
    """

    # ANSI colour codes
    _COLORS = {
        "DEBUG":    "\033[36m",   # Cyan
        "INFO":     "\033[32m",   # Green
        "WARNING":  "\033[33m",   # Yellow
        "ERROR":    "\033[31m",   # Red
        "CRITICAL": "\033[35m",   # Magenta
        "RESET":    "\033[0m",
    }

    _ICONS = {
        "DEBUG":    "🔍",
        "INFO":     "✅",
        "WARNING":  "⚠️ ",
        "ERROR":    "❌",
        "CRITICAL": "💀",
    }

    def __init__(self, config: ExtractorConfig) -> None:
        self._config = config
        self._logger = logging.getLogger("SupjavExtractor")
        self._logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))
        self._logger.handlers.clear()

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(self._build_formatter())
        self._logger.addHandler(handler)

    def _build_formatter(self) -> logging.Formatter:
        """Build a clean log formatter."""
        fmt = "[%(asctime)s] %(levelname)-8s │ %(message)s"
        datefmt = "%H:%M:%S"
        return logging.Formatter(fmt, datefmt=datefmt)

    def _emit(self, level: str, msg: str, *args: Any) -> None:
        """Emit a log entry with optional colour."""
        icon = self._ICONS.get(level, "")
        colour = self._COLORS.get(level, "")
        reset = self._COLORS["RESET"]

        formatted = f"{colour}{icon} {msg}{reset}"
        getattr(self._logger, level.lower())(formatted, *args)

    # ── Public API ───────────────────────────────────────────────────────

    def debug(self, msg: str, *args: Any) -> None:
        self._emit("DEBUG", msg, *args)

    def info(self, msg: str, *args: Any) -> None:
        self._emit("INFO", msg, *args)

    def warning(self, msg: str, *args: Any) -> None:
        self._emit("WARNING", msg, *args)

    def error(self, msg: str, *args: Any) -> None:
        self._emit("ERROR", msg, *args)

    def critical(self, msg: str, *args: Any) -> None:
        self._emit("CRITICAL", msg, *args)

    def section(self, title: str) -> None:
        """Print a visible section divider."""
        line = "═" * 60
        self._logger.info(f"\n{line}\n  {title}\n{line}")


# ─── Data Structures ─────────────────────────────────────────────────────────

@dataclass
class VideoStream:
    """
    Represents a single discovered video stream.

    This is the fundamental unit of output from every extraction strategy.
    """
    url: str
    format: StreamFormat = StreamFormat.UNKNOWN
    quality: Quality = Quality.UNKNOWN
    confidence: int = 0          # 0-100
    server_name: str = "unknown"
    headers: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        """
        Composite score combining quality, format preference, and confidence.
        Higher is better.
        """
        format_scores = {
            StreamFormat.MP4: 100,
            StreamFormat.M3U8: 70,
            StreamFormat.WEBM: 50,
            StreamFormat.FLV: 30,
            StreamFormat.UNKNOWN: 10,
        }
        fmt_score = format_scores.get(self.format, 0)
        q_score = self.quality.priority
        return (self.confidence * 0.4) + (q_score * 0.35) + (fmt_score * 0.25)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "url": self.url,
            "format": self.format.value,
            "quality": self.quality.value,
            "confidence": self.confidence,
            "score": round(self.score, 2),
            "server": self.server_name,
            "headers": self.headers,
            "metadata": self.metadata,
        }


@dataclass
class ServerInfo:
    """
    Represents a detected video hosting server/mirror.
    """
    name: str
    url: str
    server_type: str = "unknown"
    confidence: int = 50
    priority: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.url)


@dataclass
class ExtractionResult:
    """
    Final output of the extraction pipeline.

    Contains all discovered streams, ordered by score, plus diagnostics.
    """
    success: bool
    streams: List[VideoStream] = field(default_factory=list)
    best_stream: Optional[VideoStream] = None
    servers_found: List[ServerInfo] = field(default_factory=list)
    strategies_tried: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    elapsed_time: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.streams and not self.best_stream:
            self.best_stream = max(self.streams, key=lambda s: s.score)

    def to_dict(self) -> Dict[str, Any]:
        """Full serialization."""
        return {
            "success": self.success,
            "best_stream": self.best_stream.to_dict() if self.best_stream else None,
            "streams_count": len(self.streams),
            "streams": [s.to_dict() for s in sorted(
                self.streams, key=lambda s: s.score, reverse=True
            )],
            "servers_found": [
                {"name": s.name, "url": s.url, "type": s.server_type}
                for s in self.servers_found
            ],
            "strategies_tried": self.strategies_tried,
            "errors": self.errors,
            "elapsed_time": round(self.elapsed_time, 2),
            "metadata": self.metadata,
        }


# ─── User-Agent Rotation ─────────────────────────────────────────────────────

class HeaderRotator:
    """
    Manages rotating User-Agent strings and request headers to avoid
    fingerprinting and rate-limit detection.

    Uses a curated pool of modern browser UA strings.
    """

    _USER_AGENTS: List[str] = [
        # Chrome on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        # Chrome on Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        # Firefox on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) "
        "Gecko/20100101 Firefox/126.0",
        # Firefox on Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) "
        "Gecko/20100101 Firefox/126.0",
        # Edge
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
        # Safari
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Safari/605.1.15",
        # Chrome on Linux
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    ]

    _ACCEPT_LANGUAGES: List[str] = [
        "en-US,en;q=0.9",
        "en-US,en;q=0.9,ja;q=0.8",
        "en-GB,en;q=0.9",
        "en-US,en;q=0.9,zh-CN;q=0.8",
    ]

    def __init__(self) -> None:
        self._current_ua: str = random.choice(self._USER_AGENTS)
        self._session_id: str = self._generate_session_id()

    @staticmethod
    def _generate_session_id() -> str:
        """Generate a random session fingerprint."""
        return "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

    def rotate(self) -> None:
        """Select a new random User-Agent."""
        self._current_ua = random.choice(self._USER_AGENTS)
        self._session_id = self._generate_session_id()

    def get_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        """
        Build a complete, realistic set of request headers.

        Args:
            referer: Optional Referer URL to include.

        Returns:
            Dictionary of HTTP headers.
        """
        headers: Dict[str, str] = {
            "User-Agent": self._current_ua,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,image/avif,image/webp,*/*;q=0.8"
            ),
            "Accept-Language": random.choice(self._ACCEPT_LANGUAGES),
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none" if not referer else "cross-site",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        }
        if referer:
            headers["Referer"] = referer
        return headers

    def get_ajax_headers(self, referer: Optional[str] = None) -> Dict[str, str]:
        """Headers mimicking an XHR/fetch request."""
        headers = self.get_headers(referer)
        headers.update({
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        })
        return headers

    @property
    def user_agent(self) -> str:
        return self._current_ua


# ─── HTTP Client ──────────────────────────────────────────────────────────────

class HttpClient:
    """
    Production-grade HTTP client with:
      - Session reuse & connection pooling
      - Automatic retries with exponential backoff
      - Header rotation
      - Rate limiting (polite crawling)
      - Request/response logging
    """

    def __init__(self, config: ExtractorConfig, logger: ExtractorLogger) -> None:
        self._config = config
        self._log = logger
        self._rotator = HeaderRotator()
        self._session = self._build_session()
        self._last_request_time: float = 0.0

    def _build_session(self) -> requests.Session:
        """Construct a requests Session with retry adapter."""
        session = requests.Session()

        retry_strategy = URLLibRetry(
            total=self._config.max_retries,
            backoff_factor=self._config.retry_backoff_factor,
            status_forcelist=list(self._config.retry_status_forcelist),
            allowed_methods=["GET", "POST", "HEAD"],
            raise_on_status=False,
        )

        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=10,
            pool_maxsize=10,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.max_redirects = self._config.max_redirects

        return session

    def _throttle(self) -> None:
        """Enforce minimum delay between requests (polite crawling)."""
        elapsed = time.time() - self._last_request_time
        min_interval = random.uniform(
            self._config.min_request_interval,
            self._config.max_request_interval,
        )
        if elapsed < min_interval:
            sleep_time = min_interval - elapsed
            self._log.debug(f"Throttling: sleeping {sleep_time:.2f}s")
            time.sleep(sleep_time)

    def get(
        self,
        url: str,
        referer: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        allow_redirects: bool = True,
        timeout: Optional[int] = None,
        raw: bool = False,
    ) -> Optional[requests.Response]:
        """
        Perform a GET request with full protection stack.

        Args:
            url: Target URL.
            referer: Referer header value.
            headers: Override headers (merged with rotated defaults).
            allow_redirects: Follow redirects.
            timeout: Override timeout.
            raw: If True, return response even on non-2xx.

        Returns:
            Response object or None on failure.
        """
        self._throttle()

        merged_headers = self._rotator.get_headers(referer)
        if headers:
            merged_headers.update(headers)

        effective_timeout = timeout or self._config.request_timeout

        self._log.debug(f"GET → {url}")

        try:
            response = self._session.get(
                url,
                headers=merged_headers,
                timeout=effective_timeout,
                allow_redirects=allow_redirects,
            )
            self._last_request_time = time.time()

            self._log.debug(
                f"GET ← {response.status_code} | "
                f"{len(response.content)} bytes | {url[:80]}"
            )

            if raw:
                return response

            if response.ok:
                return response

            self._log.warning(
                f"HTTP {response.status_code} for {url[:80]}"
            )
            return None

        except requests.exceptions.Timeout:
            self._log.error(f"Timeout after {effective_timeout}s: {url[:80]}")
            return None
        except requests.exceptions.ConnectionError as exc:
            self._log.error(f"Connection error: {exc} — {url[:80]}")
            return None
        except requests.exceptions.TooManyRedirects:
            self._log.error(f"Too many redirects: {url[:80]}")
            return None
        except requests.exceptions.RequestException as exc:
            self._log.error(f"Request failed: {exc} — {url[:80]}")
            return None

    def post(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        referer: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> Optional[requests.Response]:
        """
        Perform a POST request with full protection stack.
        """
        self._throttle()

        merged_headers = self._rotator.get_ajax_headers(referer)
        if headers:
            merged_headers.update(headers)

        effective_timeout = timeout or self._config.request_timeout

        self._log.debug(f"POST → {url}")

        try:
            response = self._session.post(
                url,
                data=data,
                json=json_data,
                headers=merged_headers,
                timeout=effective_timeout,
            )
            self._last_request_time = time.time()

            self._log.debug(
                f"POST ← {response.status_code} | "
                f"{len(response.content)} bytes | {url[:80]}"
            )

            if response.ok:
                return response

            self._log.warning(f"HTTP {response.status_code} for POST {url[:80]}")
            return None

        except requests.exceptions.RequestException as exc:
            self._log.error(f"POST failed: {exc} — {url[:80]}")
            return None

    def head(self, url: str, referer: Optional[str] = None) -> Optional[requests.Response]:
        """Lightweight HEAD request to check URL validity."""
        self._throttle()
        headers = self._rotator.get_headers(referer)
        try:
            response = self._session.head(
                url, headers=headers,
                timeout=self._config.request_timeout,
                allow_redirects=True,
            )
            self._last_request_time = time.time()
            return response if response.ok else None
        except requests.exceptions.RequestException:
            return None

    def rotate_identity(self) -> None:
        """Rotate UA and rebuild session (identity reset)."""
        self._rotator.rotate()
        self._log.debug(f"Identity rotated → {self._rotator.user_agent[:50]}...")

    @property
    def rotator(self) -> HeaderRotator:
        return self._rotator


# ─── Utility Functions ────────────────────────────────────────────────────────

def normalize_url(url: str, base: Optional[str] = None) -> str:
    """
    Normalize a URL: handle protocol-relative, relative paths, etc.

    Args:
        url: The raw URL string.
        base: Base URL for resolving relative paths.

    Returns:
        Fully qualified URL string.
    """
    url = url.strip()

    if url.startswith("//"):
        return "https:" + url

    if url.startswith("/") and base:
        parsed = urllib.parse.urlparse(base)
        return f"{parsed.scheme}://{parsed.netloc}{url}"

    if not url.startswith(("http://", "https://")):
        if base:
            return urllib.parse.urljoin(base, url)

    return url


def extract_domain(url: str) -> str:
    """Extract the domain (netloc) from a URL."""
    parsed = urllib.parse.urlparse(url)
    return parsed.netloc.lower()


def url_fingerprint(url: str) -> str:
    """
    Generate a short fingerprint of a URL for caching/dedup.
    Uses path + query (ignores fragments and scheme).
    """
    parsed = urllib.parse.urlparse(url)
    key = f"{parsed.netloc}{parsed.path}{parsed.query}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def detect_stream_format(url: str) -> StreamFormat:
    """
    Detect the stream format from a URL string.

    Args:
        url: The stream URL.

    Returns:
        Detected StreamFormat enum value.
    """
    url_lower = url.lower().split("?")[0]

    if url_lower.endswith(".mp4") or ".mp4" in url_lower:
        return StreamFormat.MP4
    if url_lower.endswith(".m3u8") or ".m3u8" in url_lower:
        return StreamFormat.M3U8
    if url_lower.endswith(".webm"):
        return StreamFormat.WEBM
    if url_lower.endswith(".flv"):
        return StreamFormat.FLV

    # Check path segments
    if "/mp4/" in url_lower or "format=mp4" in url_lower:
        return StreamFormat.MP4
    if "/hls/" in url_lower or "format=hls" in url_lower:
        return StreamFormat.M3U8

    return StreamFormat.UNKNOWN


def detect_quality_from_url(url: str) -> Quality:
    """
    Heuristically detect video quality from URL patterns.

    Args:
        url: The stream URL.

    Returns:
        Best-guess Quality enum.
    """
    url_lower = url.lower()

    patterns: List[Tuple[str, Quality]] = [
        ("2160", Quality.Q4K),
        ("4k", Quality.Q4K),
        ("1080", Quality.Q1080),
        ("fullhd", Quality.Q1080),
        ("720", Quality.Q720),
        ("hd", Quality.Q720),
        ("480", Quality.Q480),
        ("sd", Quality.Q480),
        ("360", Quality.Q360),
    ]

    for pattern, quality in patterns:
        if pattern in url_lower:
            return quality

    return Quality.UNKNOWN


def safe_json_loads(text: str) -> Optional[Dict[str, Any]]:
    """
    Attempt to parse JSON from a string, returning None on failure.
    Handles common malformations (trailing commas, single quotes).
    """
    # Direct attempt
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try fixing single quotes
    try:
        fixed = text.replace("'", '"')
        return json.loads(fixed)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try removing trailing commas before } or ]
    try:
        fixed = re.sub(r",\s*([}\]])", r"\1", text)
        return json.loads(fixed)
    except (json.JSONDecodeError, TypeError):
        pass

    return None


def decode_base64_safe(encoded: str) -> Optional[str]:
    """
    Decode a base64 string with padding correction.

    Args:
        encoded: Base64-encoded string.

    Returns:
        Decoded string or None on failure.
    """
    try:
        # Fix padding
        missing_padding = len(encoded) % 4
        if missing_padding:
            encoded += "=" * (4 - missing_padding)
        decoded = base64.b64decode(encoded).decode("utf-8", errors="ignore")
        return decoded
    except Exception:
        return None


def generate_request_id() -> str:
    """Generate a unique request identifier for tracing."""
    timestamp = int(time.time() * 1000)
    rand_part = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    return f"req_{timestamp}_{rand_part}"


def retry_operation(
    func: Callable[..., Any],
    max_attempts: int = 3,
    backoff: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    logger: Optional[ExtractorLogger] = None,
) -> Any:
    """
    Generic retry wrapper with exponential backoff.

    Args:
        func: Callable to retry.
        max_attempts: Maximum number of attempts.
        backoff: Base backoff time in seconds.
        exceptions: Tuple of exception types to catch.
        logger: Optional logger for debug output.

    Returns:
        Result of func() on success.

    Raises:
        Last exception if all attempts fail.
    """
    last_exception: Optional[Exception] = None

    for attempt in range(1, max_attempts + 1):
        try:
            return func()
        except exceptions as exc:
            last_exception = exc
            if logger:
                logger.debug(
                    f"Retry {attempt}/{max_attempts} failed: {exc}"
                )
            if attempt < max_attempts:
                sleep_time = backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                time.sleep(sleep_time)

    raise last_exception  # type: ignore[misc]


# ─── Global Initialization Helper ────────────────────────────────────────────

def create_foundation(
    debug: bool = False,
    **config_overrides: Any,
) -> Tuple[ExtractorConfig, ExtractorLogger, HttpClient]:
    """
    One-call bootstrap: creates config, logger, and HTTP client.

    Args:
        debug: Enable debug mode.
        **config_overrides: Any ExtractorConfig field overrides.

    Returns:
        Tuple of (config, logger, http_client).
    """
    config_overrides["debug"] = debug
    config = ExtractorConfig(**config_overrides)
    logger = ExtractorLogger(config)
    client = HttpClient(config, logger)

    logger.info(f"SupJav Extractor v{__version__} initialized")
    logger.debug(f"Parser: {config.html_parser}")
    logger.debug(f"Playwright available: {PLAYWRIGHT_AVAILABLE}")
    logger.debug(f"User-Agent: {client.rotator.user_agent[:60]}...")

    return config, logger, client


# ═══════════════════════════════════════════════════════════════════════════════
# END OF PART 1
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# PART 2 — HEURISTIC ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
#
# Intelligent scoring system that evaluates, ranks, and selects the best
# video streams using multi-factor heuristic analysis.
# ═══════════════════════════════════════════════════════════════════════════════


class URLSignalAnalyzer:
    """
    Analyzes URL strings for signals that indicate video content,
    quality, format, and legitimacy.

    This is the lowest-level heuristic — operates purely on URL text.
    """

    # ── Positive signals: patterns that suggest a real video URL ──────────
    _VIDEO_POSITIVE_PATTERNS: List[Tuple[re.Pattern, int]] = [
        (re.compile(r"\.mp4(\?|$|#)", re.I), 30),
        (re.compile(r"\.m3u8(\?|$|#)", re.I), 25),
        (re.compile(r"\.webm(\?|$|#)", re.I), 20),
        (re.compile(r"\.flv(\?|$|#)", re.I), 15),
        (re.compile(r"/video[s]?/", re.I), 10),
        (re.compile(r"/stream[s]?/", re.I), 10),
        (re.compile(r"/media/", re.I), 8),
        (re.compile(r"/hls/", re.I), 12),
        (re.compile(r"/dash/", re.I), 10),
        (re.compile(r"/master\.m3u8", re.I), 20),
        (re.compile(r"/index\.m3u8", re.I), 18),
        (re.compile(r"/playlist\.m3u8", re.I), 18),
        (re.compile(r"token=", re.I), 5),
        (re.compile(r"expire[s]?=", re.I), 5),
        (re.compile(r"hash=", re.I), 3),
        (re.compile(r"/get(file|video|stream)", re.I), 12),
        (re.compile(r"(cdn|edge|node)\d*\.", re.I), 8),
        (re.compile(r"/download/", re.I), 10),
        (re.compile(r"cloudfront\.net", re.I), 6),
        (re.compile(r"googlevideo\.com", re.I), 10),
    ]

    # ── Negative signals: patterns suggesting non-video resources ────────
    _VIDEO_NEGATIVE_PATTERNS: List[Tuple[re.Pattern, int]] = [
        (re.compile(r"\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|ttf)(\?|$)", re.I), -30),
        (re.compile(r"/ads?/", re.I), -25),
        (re.compile(r"/banner[s]?/", re.I), -20),
        (re.compile(r"/track(ing|er)/", re.I), -25),
        (re.compile(r"analytics", re.I), -20),
        (re.compile(r"facebook\.com|google-analytics|doubleclick", re.I), -30),
        (re.compile(r"/pixel[s]?/", re.I), -15),
        (re.compile(r"\.php\?.*ad", re.I), -15),
        (re.compile(r"popunder|popup|clickunder", re.I), -25),
        (re.compile(r"/thumb(nail)?[s]?/", re.I), -20),
        (re.compile(r"/preview/", re.I), -10),
        (re.compile(r"/poster[s]?/", re.I), -15),
        (re.compile(r"recaptcha|captcha", re.I), -20),
    ]

    # ── Quality signal patterns ──────────────────────────────────────────
    _QUALITY_PATTERNS: List[Tuple[re.Pattern, Quality, int]] = [
        (re.compile(r"(^|[/_\-.])(2160|4k|uhd)([/_\-.]|$)", re.I), Quality.Q4K, 15),
        (re.compile(r"(^|[/_\-.])(1080|fullhd|fhd)([/_\-.]|$)", re.I), Quality.Q1080, 12),
        (re.compile(r"(^|[/_\-.])(720|hd)([/_\-.]|$)", re.I), Quality.Q720, 10),
        (re.compile(r"(^|[/_\-.])(480|sd)([/_\-.]|$)", re.I), Quality.Q480, 6),
        (re.compile(r"(^|[/_\-.])(360|low)([/_\-.]|$)", re.I), Quality.Q360, 4),
        (re.compile(r"(^|[/_\-.])(240|mobile)([/_\-.]|$)", re.I), Quality.Q360, 2),
        # Resolution in URL like /1920x1080/ or ?res=1080
        (re.compile(r"1920\s*x\s*1080", re.I), Quality.Q1080, 14),
        (re.compile(r"1280\s*x\s*720", re.I), Quality.Q720, 11),
        (re.compile(r"res(olution)?=1080", re.I), Quality.Q1080, 13),
        (re.compile(r"res(olution)?=720", re.I), Quality.Q720, 10),
        (re.compile(r"quality=high", re.I), Quality.Q1080, 8),
        (re.compile(r"quality=medium", re.I), Quality.Q720, 6),
        (re.compile(r"quality=low", re.I), Quality.Q480, 4),
    ]

    @classmethod
    def compute_video_likelihood(cls, url: str) -> int:
        """
        Score how likely a URL is to be a real video stream.

        Args:
            url: The URL to analyze.

        Returns:
            Integer score. Higher = more likely to be a video.
            Typically ranges from -50 to +80.
        """
        score = 0

        for pattern, points in cls._VIDEO_POSITIVE_PATTERNS:
            if pattern.search(url):
                score += points

        for pattern, points in cls._VIDEO_NEGATIVE_PATTERNS:
            if pattern.search(url):
                score += points  # points are already negative

        return score

    @classmethod
    def detect_quality(cls, url: str) -> Tuple[Quality, int]:
        """
        Detect video quality from URL patterns.

        Args:
            url: The URL to analyze.

        Returns:
            Tuple of (Quality enum, confidence_bonus).
        """
        best_quality = Quality.UNKNOWN
        best_confidence = 0

        for pattern, quality, confidence in cls._QUALITY_PATTERNS:
            if pattern.search(url):
                if quality.priority > best_quality.priority:
                    best_quality = quality
                    best_confidence = confidence
                elif quality.priority == best_quality.priority:
                    best_confidence = max(best_confidence, confidence)

        return best_quality, best_confidence

    @classmethod
    def is_likely_video(cls, url: str, threshold: int = 15) -> bool:
        """Quick boolean check if URL is likely a video."""
        return cls.compute_video_likelihood(url) >= threshold


class ServerReputationScorer:
    """
    Assigns reputation scores to known video hosting servers.

    Servers that historically provide reliable, high-quality streams
    receive higher scores. Unknown servers get a neutral baseline.
    """

    # ── Known server reputation database ─────────────────────────────────
    # Format: domain_fragment → (reputation_score, typical_quality, notes)
    _REPUTATION_DB: Dict[str, Tuple[int, Quality, str]] = {
        # High-reputation servers
        "streamtape": (75, Quality.Q720, "Reliable, moderate quality"),
        "doodstream": (70, Quality.Q720, "Token-based, decent quality"),
        "dood.": (70, Quality.Q720, "DoodStream variant"),
        "mixdrop": (72, Quality.Q720, "Stable, good availability"),
        "upstream": (68, Quality.Q720, "Moderate reliability"),
        "fembed": (65, Quality.Q720, "Multi-quality support"),
        "femax": (65, Quality.Q720, "Fembed variant"),
        "feurl": (65, Quality.Q720, "Fembed variant"),
        "filemoon": (73, Quality.Q1080, "Good quality, modern"),
        "streamwish": (70, Quality.Q1080, "HLS-based, good quality"),
        "vidhide": (68, Quality.Q720, "Newer server, moderate"),
        "voe.sx": (66, Quality.Q720, "Decent availability"),
        "vidoza": (64, Quality.Q720, "Standard hosting"),
        "mp4upload": (72, Quality.Q720, "Direct MP4, reliable"),
        "streamsb": (60, Quality.Q720, "Complex extraction"),
        "sbembed": (60, Quality.Q720, "StreamSB variant"),
        "embedsito": (58, Quality.Q720, "StreamSB variant"),
        "watchsb": (60, Quality.Q720, "StreamSB variant"),
        "supervideo": (62, Quality.Q720, "Moderate quality"),
        "highstream": (64, Quality.Q720, "Decent quality"),

        # Lower reputation / problematic
        "vidlox": (45, Quality.Q480, "Often slow"),
        "videobin": (50, Quality.Q480, "Basic hosting"),
        "netu": (40, Quality.Q480, "Heavy obfuscation"),
        "hqq": (38, Quality.Q480, "Very heavy obfuscation"),

        # CDN / direct hosting (high reputation)
        "cloudfront": (80, Quality.Q1080, "AWS CDN, fast"),
        "akamai": (82, Quality.Q1080, "Enterprise CDN"),
        "fastly": (80, Quality.Q1080, "Enterprise CDN"),
        "bunnycdn": (78, Quality.Q1080, "Fast CDN"),
        "cdn77": (76, Quality.Q1080, "Good CDN"),
    }

    @classmethod
    def score_server(cls, server_name: str, url: str = "") -> Tuple[int, Quality]:
        """
        Score a server by name/URL against the reputation database.

        Args:
            server_name: Name or identifier of the server.
            url: Optional URL for additional domain matching.

        Returns:
            Tuple of (reputation_score 0-100, expected_quality).
        """
        combined = f"{server_name} {url}".lower()

        best_score = 50  # neutral baseline
        best_quality = Quality.UNKNOWN

        for fragment, (score, quality, _notes) in cls._REPUTATION_DB.items():
            if fragment in combined:
                if score > best_score:
                    best_score = score
                    best_quality = quality

        return best_score, best_quality

    @classmethod
    def get_server_notes(cls, server_name: str) -> str:
        """Get human-readable notes about a server."""
        name_lower = server_name.lower()
        for fragment, (_score, _quality, notes) in cls._REPUTATION_DB.items():
            if fragment in name_lower:
                return notes
        return "Unknown server"


class FormatPrioritizer:
    """
    Ranks stream formats by desirability.

    MP4 is universally preferred (direct play everywhere).
    M3U8/HLS is second (requires segment downloading or ffmpeg).
    """

    # Base scores for each format
    _FORMAT_SCORES: Dict[StreamFormat, int] = {
        StreamFormat.MP4: 100,
        StreamFormat.M3U8: 70,
        StreamFormat.WEBM: 50,
        StreamFormat.FLV: 30,
        StreamFormat.UNKNOWN: 15,
    }

    # Bonus points for format-specific positive signals
    _FORMAT_BONUSES: Dict[StreamFormat, List[Tuple[re.Pattern, int]]] = {
        StreamFormat.MP4: [
            (re.compile(r"\.mp4\?.*token=", re.I), 10),      # tokenized = real
            (re.compile(r"\.mp4\?.*expire", re.I), 8),        # expiring = real
            (re.compile(r"/download/.*\.mp4", re.I), 12),     # explicit download
        ],
        StreamFormat.M3U8: [
            (re.compile(r"master\.m3u8", re.I), 10),           # master playlist
            (re.compile(r"index\.m3u8", re.I), 5),             # direct index
            (re.compile(r"\.m3u8\?.*token=", re.I), 8),       # tokenized
        ],
    }

    @classmethod
    def score_format(cls, fmt: StreamFormat, url: str = "") -> int:
        """
        Score a format with optional URL-based bonus.

        Args:
            fmt: The StreamFormat to score.
            url: Optional URL for bonus pattern matching.

        Returns:
            Integer score (higher = more desirable).
        """
        base = cls._FORMAT_SCORES.get(fmt, 10)
        bonus = 0

        if url and fmt in cls._FORMAT_BONUSES:
            for pattern, points in cls._FORMAT_BONUSES[fmt]:
                if pattern.search(url):
                    bonus += points

        return base + bonus

    @classmethod
    def rank_formats(cls, formats: List[StreamFormat]) -> List[StreamFormat]:
        """Sort formats by desirability (best first)."""
        return sorted(formats, key=lambda f: cls._FORMAT_SCORES.get(f, 0), reverse=True)


class HeuristicScorer:
    """
    Master scoring engine that combines all heuristic signals into a
    single, normalized confidence score for any VideoStream candidate.

    Scoring dimensions:
        1. URL video likelihood (via URLSignalAnalyzer)
        2. Format preference (via FormatPrioritizer)
        3. Quality detection (via URLSignalAnalyzer)
        4. Server reputation (via ServerReputationScorer)
        5. URL structural analysis (length, depth, entropy)
        6. Contextual bonuses (referrer chain, extraction method)

    Final score is normalized to 0-100.
    """

    # Weight for each scoring dimension (must sum to 1.0)
    _WEIGHTS: Dict[str, float] = {
        "video_likelihood": 0.25,
        "format_score":     0.20,
        "quality_score":    0.20,
        "server_reputation": 0.15,
        "structural_score": 0.10,
        "context_bonus":    0.10,
    }

    def __init__(self, config: ExtractorConfig, logger: ExtractorLogger) -> None:
        self._config = config
        self._log = logger

    def score_stream(
        self,
        url: str,
        fmt: Optional[StreamFormat] = None,
        quality: Optional[Quality] = None,
        server_name: str = "unknown",
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Dict[str, Any]]:
        """
        Compute a comprehensive heuristic score for a stream candidate.

        Args:
            url: The stream URL.
            fmt: Known format (auto-detected if None).
            quality: Known quality (auto-detected if None).
            server_name: Name of the hosting server.
            context: Additional context (extraction_method, depth, etc).

        Returns:
            Tuple of (final_score 0-100, score_breakdown dict).
        """
        context = context or {}
        breakdown: Dict[str, Any] = {}

        # ── 1. Video likelihood ──────────────────────────────────────────
        raw_likelihood = URLSignalAnalyzer.compute_video_likelihood(url)
        # Normalize from typical range [-50, +80] to [0, 100]
        likelihood_normalized = max(0, min(100, (raw_likelihood + 50) * 100 // 130))
        breakdown["video_likelihood"] = {
            "raw": raw_likelihood,
            "normalized": likelihood_normalized,
        }

        # ── 2. Format score ──────────────────────────────────────────────
        detected_fmt = fmt or detect_stream_format(url)
        format_raw = FormatPrioritizer.score_format(detected_fmt, url)
        # Already 0-110 range, normalize to 0-100
        format_normalized = min(100, format_raw)
        breakdown["format_score"] = {
            "format": detected_fmt.value,
            "raw": format_raw,
            "normalized": format_normalized,
        }

        # ── 3. Quality score ─────────────────────────────────────────────
        if quality and quality != Quality.UNKNOWN:
            detected_quality = quality
            quality_confidence_bonus = 10
        else:
            detected_quality, quality_confidence_bonus = URLSignalAnalyzer.detect_quality(url)

        quality_normalized = detected_quality.priority * 2  # 0-100 scale
        quality_normalized = min(100, quality_normalized + quality_confidence_bonus)
        breakdown["quality_score"] = {
            "quality": detected_quality.value,
            "normalized": quality_normalized,
        }

        # ── 4. Server reputation ─────────────────────────────────────────
        rep_score, _expected_q = ServerReputationScorer.score_server(server_name, url)
        breakdown["server_reputation"] = {
            "server": server_name,
            "score": rep_score,
        }

        # ── 5. Structural analysis ───────────────────────────────────────
        structural = self._analyze_structure(url)
        breakdown["structural_score"] = structural

        # ── 6. Context bonus ─────────────────────────────────────────────
        context_score = self._compute_context_bonus(context)
        breakdown["context_bonus"] = {
            "score": context_score,
            "context": context,
        }

        # ── Weighted combination ─────────────────────────────────────────
        raw_scores = {
            "video_likelihood": likelihood_normalized,
            "format_score": format_normalized,
            "quality_score": quality_normalized,
            "server_reputation": rep_score,
            "structural_score": structural["normalized"],
            "context_bonus": context_score,
        }

        final_score = sum(
            raw_scores[dim] * weight
            for dim, weight in self._WEIGHTS.items()
        )

        # Clamp to 0-100
        final_score = max(0, min(100, int(round(final_score))))

        breakdown["raw_scores"] = raw_scores
        breakdown["weights"] = self._WEIGHTS
        breakdown["final_score"] = final_score

        self._log.debug(
            f"Heuristic score: {final_score}/100 | "
            f"fmt={detected_fmt.value} q={detected_quality.value} "
            f"srv={server_name} | {url[:70]}..."
        )

        return final_score, breakdown

    def score_video_stream(
        self,
        stream: VideoStream,
        context: Optional[Dict[str, Any]] = None,
    ) -> VideoStream:
        """
        Score an existing VideoStream and update its confidence.

        Args:
            stream: The VideoStream to score.
            context: Additional context.

        Returns:
            The same VideoStream with updated confidence and metadata.
        """
        score, breakdown = self.score_stream(
            url=stream.url,
            fmt=stream.format if stream.format != StreamFormat.UNKNOWN else None,
            quality=stream.quality if stream.quality != Quality.UNKNOWN else None,
            server_name=stream.server_name,
            context=context,
        )

        stream.confidence = score

        # Auto-fill format/quality if they were unknown
        if stream.format == StreamFormat.UNKNOWN:
            stream.format = detect_stream_format(stream.url)
        if stream.quality == Quality.UNKNOWN:
            detected_q, _ = URLSignalAnalyzer.detect_quality(stream.url)
            stream.quality = detected_q

        stream.metadata["score_breakdown"] = breakdown

        return stream

    def rank_streams(self, streams: List[VideoStream]) -> List[VideoStream]:
        """
        Rank a list of VideoStreams by their composite score.

        Applies scoring to any unscored streams, then sorts descending.

        Args:
            streams: List of VideoStream candidates.

        Returns:
            Sorted list (best first).
        """
        for stream in streams:
            if stream.confidence == 0:
                self.score_video_stream(stream)

        ranked = sorted(streams, key=lambda s: s.score, reverse=True)

        if ranked and self._log:
            self._log.debug(f"Ranked {len(ranked)} streams:")
            for i, s in enumerate(ranked[:5]):
                self._log.debug(
                    f"  #{i+1}: score={s.score:.1f} conf={s.confidence} "
                    f"fmt={s.format.value} q={s.quality.value} "
                    f"srv={s.server_name} | {s.url[:60]}..."
                )

        return ranked

    def select_best(
        self,
        streams: List[VideoStream],
        preferred_format: Optional[StreamFormat] = None,
        preferred_quality: Optional[Quality] = None,
    ) -> Optional[VideoStream]:
        """
        Select the single best stream with optional preferences.

        Args:
            streams: Candidate streams.
            preferred_format: Boost streams matching this format.
            preferred_quality: Boost streams matching this quality.

        Returns:
            The best VideoStream, or None if list is empty.
        """
        if not streams:
            return None

        ranked = self.rank_streams(streams)

        if preferred_format or preferred_quality:
            # Apply preference bonuses and re-sort
            for stream in ranked:
                bonus = 0
                if preferred_format and stream.format == preferred_format:
                    bonus += 15
                if preferred_quality and stream.quality == preferred_quality:
                    bonus += 10
                stream.confidence = min(100, stream.confidence + bonus)

            ranked = sorted(ranked, key=lambda s: s.score, reverse=True)

        best = ranked[0]
        self._log.info(
            f"Best stream selected: score={best.score:.1f} "
            f"fmt={best.format.value} q={best.quality.value} "
            f"srv={best.server_name}"
        )

        return best

    def _analyze_structure(self, url: str) -> Dict[str, Any]:
        """
        Analyze URL structural properties as a quality signal.

        Longer, deeper URLs with path segments like /video/hash/file.mp4
        tend to be real video resources. Very short or overly simple URLs
        are more likely tracking redirects.
        """
        parsed = urllib.parse.urlparse(url)
        path_depth = len([s for s in parsed.path.split("/") if s])
        query_params = len(urllib.parse.parse_qs(parsed.query))
        url_length = len(url)

        # Scoring heuristics
        score = 50  # neutral start

        # Path depth: 2-5 segments is ideal
        if 2 <= path_depth <= 5:
            score += 15
        elif path_depth == 1:
            score -= 5
        elif path_depth > 7:
            score -= 10

        # Query parameters: 1-4 is good (tokens, expiry, etc.)
        if 1 <= query_params <= 4:
            score += 10
        elif query_params > 8:
            score -= 10

        # URL length: 50-300 chars is normal for video URLs
        if 50 <= url_length <= 300:
            score += 10
        elif url_length > 500:
            score -= 5
        elif url_length < 30:
            score -= 15

        # HTTPS bonus
        if parsed.scheme == "https":
            score += 5

        # Has file extension in path
        if re.search(r"\.\w{2,4}$", parsed.path):
            score += 10

        normalized = max(0, min(100, score))

        return {
            "path_depth": path_depth,
            "query_params": query_params,
            "url_length": url_length,
            "scheme": parsed.scheme,
            "normalized": normalized,
        }

    def _compute_context_bonus(self, context: Dict[str, Any]) -> int:
        """
        Compute bonus score based on extraction context.

        Context keys recognized:
            - extraction_method: str (e.g., "iframe", "script", "network_intercept")
            - follow_depth: int (how many redirects/iframes deep)
            - was_obfuscated: bool (decoded from packed/base64)
            - pattern_match: bool (matched a known pattern)
            - verified_head: bool (HEAD request confirmed accessible)
        """
        score = 50  # neutral

        method = context.get("extraction_method", "")
        method_scores = {
            "network_intercept": 25,   # captured from actual playback
            "direct_parse": 15,        # found in HTML directly
            "script_scan": 12,         # extracted from JS
            "iframe": 10,              # followed iframe chain
            "api_call": 18,            # server API endpoint
            "pattern_match": 15,       # matched known pattern
            "headless": 20,            # from headless browser
        }
        score += method_scores.get(method, 0)

        # Depth penalty: deeper chains = less reliable
        depth = context.get("follow_depth", 0)
        if depth > 3:
            score -= (depth - 3) * 5

        # Obfuscation: decoded content is usually real
        if context.get("was_obfuscated", False):
            score += 8

        # Pattern match bonus
        if context.get("pattern_match", False):
            score += 10

        # HEAD verification bonus
        if context.get("verified_head", False):
            score += 15

        return max(0, min(100, score))


class StreamDeduplicator:
    """
    Removes duplicate or near-duplicate streams from a candidate list.

    Uses URL fingerprinting and domain normalization to detect duplicates
    even when URLs differ slightly (different tokens, CDN nodes, etc.).
    """

    def __init__(self, logger: ExtractorLogger) -> None:
        self._log = logger

    def deduplicate(self, streams: List[VideoStream]) -> List[VideoStream]:
        """
        Remove duplicate streams, keeping the highest-scored version.

        Args:
            streams: List of candidate VideoStreams.

        Returns:
            Deduplicated list.
        """
        if len(streams) <= 1:
            return streams

        seen_fingerprints: Dict[str, VideoStream] = {}
        seen_normalized: Dict[str, VideoStream] = {}

        for stream in streams:
            # Level 1: Exact URL fingerprint
            fp = url_fingerprint(stream.url)
            if fp in seen_fingerprints:
                existing = seen_fingerprints[fp]
                if stream.score > existing.score:
                    seen_fingerprints[fp] = stream
                continue

            # Level 2: Normalized path (strip tokens/query)
            norm_key = self._normalize_for_dedup(stream.url)
            if norm_key in seen_normalized:
                existing = seen_normalized[norm_key]
                if stream.score > existing.score:
                    seen_normalized[norm_key] = stream
                    seen_fingerprints[fp] = stream
                continue

            seen_fingerprints[fp] = stream
            seen_normalized[norm_key] = stream

        result = list(seen_fingerprints.values())

        removed = len(streams) - len(result)
        if removed > 0:
            self._log.debug(f"Deduplication: {len(streams)} → {len(result)} ({removed} dupes removed)")

        return result

    @staticmethod
    def _normalize_for_dedup(url: str) -> str:
        """
        Create a normalized key for near-duplicate detection.
        Strips query parameters that are likely session-specific.
        """
        parsed = urllib.parse.urlparse(url)

        # Keep only the path and stable query params
        query_params = urllib.parse.parse_qs(parsed.query)

        # Remove known ephemeral params
        ephemeral_keys = {"token", "expire", "expires", "hash", "sig",
                          "signature", "t", "st", "e", "ip", "nonce",
                          "ts", "timestamp", "cb", "rand", "random"}

        stable_params = {
            k: v for k, v in query_params.items()
            if k.lower() not in ephemeral_keys
        }

        stable_query = urllib.parse.urlencode(stable_params, doseq=True)
        return f"{parsed.netloc}{parsed.path}?{stable_query}" if stable_query else f"{parsed.netloc}{parsed.path}"


class StreamValidator:
    """
    Validates candidate stream URLs to confirm they are real,
    accessible video resources.
    """

    # Content-Type values that indicate video
    _VIDEO_CONTENT_TYPES: Set[str] = {
        "video/mp4",
        "video/webm",
        "video/x-flv",
        "video/ogg",
        "application/vnd.apple.mpegurl",     # m3u8
        "application/x-mpegurl",             # m3u8
        "application/octet-stream",          # generic binary (often video)
        "binary/octet-stream",
    }

    def __init__(self, http_client: HttpClient, logger: ExtractorLogger) -> None:
        self._http = http_client
        self._log = logger

    def validate_stream(
        self,
        stream: VideoStream,
        quick: bool = True,
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Validate a stream URL is accessible and returns video content.

        Args:
            stream: The VideoStream to validate.
            quick: If True, use HEAD request only. If False, also check
                   first bytes of content.

        Returns:
            Tuple of (is_valid, validation_details).
        """
        details: Dict[str, Any] = {
            "url": stream.url,
            "checked": False,
            "accessible": False,
            "content_type": None,
            "content_length": None,
            "is_video_content": False,
        }

        try:
            # Prepare headers — some servers need Referer
            referer = stream.headers.get("Referer") or stream.metadata.get("referer")

            response = self._http.head(stream.url, referer=referer)
            details["checked"] = True

            if not response:
                self._log.debug(f"Validation failed (HEAD): {stream.url[:60]}")
                return False, details

            details["accessible"] = True
            details["status_code"] = response.status_code

            content_type = response.headers.get("Content-Type", "").lower().split(";")[0].strip()
            details["content_type"] = content_type

            content_length = response.headers.get("Content-Length")
            if content_length:
                details["content_length"] = int(content_length)

            # Check content type
            is_video = content_type in self._VIDEO_CONTENT_TYPES
            details["is_video_content"] = is_video

            # Also accept if content-length suggests video (> 500KB)
            if content_length and int(content_length) > 500_000:
                is_video = True
                details["is_video_content"] = True

            # M3U8 can have text/plain content type
            if stream.format == StreamFormat.M3U8 and content_type in ("text/plain", "text/html"):
                is_video = True
                details["is_video_content"] = True

            if is_video:
                self._log.debug(
                    f"Validation passed: {content_type} "
                    f"({details.get('content_length', '?')} bytes) "
                    f"| {stream.url[:60]}"
                )
            else:
                self._log.debug(
                    f"Validation uncertain: {content_type} | {stream.url[:60]}"
                )

            return is_video, details

        except Exception as exc:
            self._log.debug(f"Validation error: {exc} | {stream.url[:60]}")
            details["error"] = str(exc)
            return False, details

    def validate_and_boost(
        self,
        streams: List[VideoStream],
        max_checks: int = 5,
    ) -> List[VideoStream]:
        """
        Validate top candidates and boost confidence of confirmed streams.

        Args:
            streams: Sorted list of candidates (best first).
            max_checks: Maximum number of HEAD requests to make.

        Returns:
            Updated list with validation results applied.
        """
        checked = 0

        for stream in streams:
            if checked >= max_checks:
                break

            is_valid, details = self.validate_stream(stream)
            checked += 1

            if is_valid:
                # Boost confidence
                boost = 15
                stream.confidence = min(100, stream.confidence + boost)
                stream.metadata["validated"] = True
                stream.metadata["validation"] = details
                self._log.debug(
                    f"Stream validated ✓ (+{boost} confidence): {stream.url[:60]}"
                )
            elif details.get("accessible"):
                # Accessible but not confirmed video — small penalty
                stream.confidence = max(0, stream.confidence - 5)
                stream.metadata["validated"] = False
                stream.metadata["validation"] = details

        return streams


class QualityMatcher:
    """
    Matches user-preferred quality against available streams.

    Implements a "closest match" strategy: if the preferred quality
    isn't available, selects the nearest available quality (preferring
    higher over lower).
    """

    @staticmethod
    def find_best_quality_match(
        streams: List[VideoStream],
        preferred: Quality = Quality.Q1080,
    ) -> Optional[VideoStream]:
        """
        Find the stream closest to the preferred quality.

        Strategy:
            1. Exact match → return highest-scored of those
            2. No exact → prefer next-higher quality
            3. No higher → fall back to next-lower quality
            4. All unknown → return highest-scored overall

        Args:
            streams: Available streams.
            preferred: Desired quality.

        Returns:
            Best matching VideoStream, or None.
        """
        if not streams:
            return None

        # Group by quality
        by_quality: Dict[Quality, List[VideoStream]] = {}
        for s in streams:
            by_quality.setdefault(s.quality, []).append(s)

        # Sort each group by score
        for q in by_quality:
            by_quality[q].sort(key=lambda s: s.score, reverse=True)

        # Exact match
        if preferred in by_quality:
            return by_quality[preferred][0]

        # Find closest quality
        all_qualities = sorted(by_quality.keys(), key=lambda q: q.priority, reverse=True)

        # First try higher qualities
        higher = [q for q in all_qualities if q.priority > preferred.priority]
        if higher:
            # Pick the lowest among higher qualities (closest to preferred)
            closest_higher = min(higher, key=lambda q: q.priority)
            return by_quality[closest_higher][0]

        # Then try lower qualities
        lower = [q for q in all_qualities if q.priority < preferred.priority]
        if lower:
            # Pick the highest among lower qualities (closest to preferred)
            closest_lower = max(lower, key=lambda q: q.priority)
            return by_quality[closest_lower][0]

        # Fallback: return the best-scored stream overall
        return max(streams, key=lambda s: s.score)


# ═══════════════════════════════════════════════════════════════════════════════
# END OF PART 2
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# PART 3 — ADAPTIVE PARSER
# ═══════════════════════════════════════════════════════════════════════════════
#
# Intelligent HTML/JS parsing engine that extracts video-related data from
# web pages using multiple strategies: DOM traversal, script analysis,
# regex pattern matching, and obfuscation decoding (base64, packed JS).
# ═══════════════════════════════════════════════════════════════════════════════


class JSUnpacker:
    """
    Decodes obfuscated JavaScript commonly used by video hosting sites.

    Supports:
        - Dean Edwards' P.A.C.K.E.R. (eval(function(p,a,c,k,e,d)...))
        - Base64 encoded strings/URLs
        - Hex-escaped strings (\\x41\\x42...)
        - Unicode-escaped strings (\\u0041\\u0042...)
        - JJEncode / AAEncode (detection only, basic decode)
        - Simple string concatenation deobfuscation
    """

    # ── P.A.C.K.E.R. patterns ───────────────────────────────────────────
    _PACKER_PATTERN = re.compile(
        r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,?\s*[dr]?\s*\)",
        re.I | re.S,
    )

    _PACKER_ARGS_PATTERN = re.compile(
        r"}\s*\(\s*'(.*?)'\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*'(.*?)'\.split\s*\(\s*'([|])'\s*\)",
        re.S,
    )

    # Alternative packer pattern with different quoting
    _PACKER_ARGS_ALT = re.compile(
        r'}\s*\(\s*"(.*?)"\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*"(.*?)"\.split\s*\(\s*"([|])"\s*\)',
        re.S,
    )

    # ── Base64 detection ─────────────────────────────────────────────────
    _BASE64_PATTERN = re.compile(
        r'(?:atob|base64[_\-]?decode|decode(?:URI(?:Component)?)?)\s*\(\s*["\']'
        r'([A-Za-z0-9+/=]{20,})["\']',
        re.I,
    )

    _STANDALONE_BASE64 = re.compile(
        r'["\']([A-Za-z0-9+/]{40,}={0,2})["\']',
    )

    # ── Hex/Unicode escape ───────────────────────────────────────────────
    _HEX_ESCAPE = re.compile(r'\\x([0-9a-fA-F]{2})')
    _UNICODE_ESCAPE = re.compile(r'\\u([0-9a-fA-F]{4})')

    def __init__(self, logger: ExtractorLogger) -> None:
        self._log = logger

    def unpack_all(self, js_code: str) -> str:
        """
        Apply all decoding strategies to a JS string, expanding any
        obfuscated content found.

        Args:
            js_code: Raw JavaScript code.

        Returns:
            Decoded/expanded JavaScript string.
        """
        result = js_code

        # 1. Decode hex/unicode escapes first (often wraps other layers)
        result = self._decode_hex_escapes(result)
        result = self._decode_unicode_escapes(result)

        # 2. Unpack P.A.C.K.E.R.
        packer_results = self._unpack_packer(result)
        if packer_results:
            for unpacked in packer_results:
                result = result + "\n/* UNPACKED */\n" + unpacked
            self._log.debug(f"P.A.C.K.E.R. decoded: {len(packer_results)} block(s)")

        # 3. Decode base64 strings found in the code
        b64_decoded = self._decode_base64_strings(result)
        if b64_decoded:
            for decoded in b64_decoded:
                result = result + "\n/* B64_DECODED */\n" + decoded
            self._log.debug(f"Base64 decoded: {len(b64_decoded)} string(s)")

        # 4. Simple string concatenation resolution
        result = self._resolve_string_concat(result)

        return result

    def _unpack_packer(self, js_code: str) -> List[str]:
        """
        Decode Dean Edwards' P.A.C.K.E.R. obfuscation.

        The packer format:
            eval(function(p,a,c,k,e,d){...}('payload',radix,count,'dict|words'.split('|')))
        """
        results: List[str] = []

        if not self._PACKER_PATTERN.search(js_code):
            return results

        # Try both single-quote and double-quote variants
        for pattern in (self._PACKER_ARGS_PATTERN, self._PACKER_ARGS_ALT):
            for match in pattern.finditer(js_code):
                try:
                    payload = match.group(1)
                    radix = int(match.group(2))
                    count = int(match.group(3))
                    keywords_str = match.group(4)
                    separator = match.group(5)

                    keywords = keywords_str.split(separator)

                    unpacked = self._packer_substitute(payload, radix, keywords)
                    if unpacked and len(unpacked) > 20:
                        results.append(unpacked)
                except Exception as exc:
                    self._log.debug(f"Packer decode error: {exc}")

        return results

    def _packer_substitute(
        self,
        payload: str,
        radix: int,
        keywords: List[str],
    ) -> str:
        """
        Perform the keyword substitution step of P.A.C.K.E.R. decoding.

        Replaces base-N encoded word indices in the payload with
        their corresponding dictionary entries.
        """
        def replacer(match: re.Match) -> str:
            word = match.group(0)
            try:
                index = self._base_n_to_int(word, radix)
                if 0 <= index < len(keywords) and keywords[index]:
                    return keywords[index]
            except (ValueError, IndexError):
                pass
            return word

        # Match word boundaries — alphanumeric tokens
        result = re.sub(r'\b\w+\b', replacer, payload)
        return result

    @staticmethod
    def _base_n_to_int(string: str, radix: int) -> int:
        """
        Convert a base-N string to integer.
        Handles bases up to 62 (0-9, a-z, A-Z).
        """
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        result = 0
        for char in string:
            idx = alphabet.index(char)
            if idx >= radix:
                raise ValueError(f"Invalid digit '{char}' for base {radix}")
            result = result * radix + idx
        return result

    def _decode_base64_strings(self, js_code: str) -> List[str]:
        """
        Find and decode base64 strings in JavaScript code.
        Returns list of decoded strings that look useful.
        """
        decoded_results: List[str] = []

        # Pattern 1: Explicit base64 decode calls
        for match in self._BASE64_PATTERN.finditer(js_code):
            encoded = match.group(1)
            decoded = decode_base64_safe(encoded)
            if decoded and self._is_useful_decoded(decoded):
                decoded_results.append(decoded)

        # Pattern 2: Standalone long base64 strings (heuristic)
        for match in self._STANDALONE_BASE64.finditer(js_code):
            encoded = match.group(1)
            # Only try if it looks like base64 and is long enough
            if len(encoded) > 60 and self._looks_like_base64(encoded):
                decoded = decode_base64_safe(encoded)
                if decoded and self._is_useful_decoded(decoded):
                    decoded_results.append(decoded)

        return decoded_results

    def _decode_hex_escapes(self, text: str) -> str:
        """Decode \\xNN hex escapes in a string."""
        def replacer(match: re.Match) -> str:
            return chr(int(match.group(1), 16))

        try:
            return self._HEX_ESCAPE.sub(replacer, text)
        except Exception:
            return text

    def _decode_unicode_escapes(self, text: str) -> str:
        """Decode \\uNNNN unicode escapes in a string."""
        def replacer(match: re.Match) -> str:
            return chr(int(match.group(1), 16))

        try:
            return self._UNICODE_ESCAPE.sub(replacer, text)
        except Exception:
            return text

    def _resolve_string_concat(self, js_code: str) -> str:
        """
        Resolve simple JavaScript string concatenation patterns.
        E.g.: "https:" + "//example" + ".com/video.mp4"
        """
        # Pattern: "str1" + "str2" + "str3"
        concat_pattern = re.compile(
            r'(["\'])([^"\']*)\1\s*\+\s*(["\'])([^"\']*)\3'
        )

        max_iterations = 50
        iteration = 0
        result = js_code

        while concat_pattern.search(result) and iteration < max_iterations:
            result = concat_pattern.sub(
                lambda m: f'{m.group(1)}{m.group(2)}{m.group(4)}{m.group(1)}',
                result,
            )
            iteration += 1

        return result

    @staticmethod
    def _looks_like_base64(s: str) -> bool:
        """Heuristic check if a string is likely base64."""
        if not re.match(r'^[A-Za-z0-9+/]+=*$', s):
            return False
        # Check for reasonable character distribution
        unique_chars = len(set(s.replace('=', '')))
        return unique_chars > 10

    @staticmethod
    def _is_useful_decoded(decoded: str) -> bool:
        """Check if a decoded base64 string contains useful content."""
        if not decoded or len(decoded) < 10:
            return False
        # Check for URL-like content
        if 'http' in decoded or '.mp4' in decoded or '.m3u8' in decoded:
            return True
        # Check for HTML-like content
        if '<' in decoded and '>' in decoded:
            return True
        # Check for JSON-like content
        if '{' in decoded and '}' in decoded:
            return True
        # Check if mostly printable ASCII
        printable_ratio = sum(1 for c in decoded if c.isprintable()) / len(decoded)
        return printable_ratio > 0.85


class IframeExtractor:
    """
    Extracts and resolves iframe sources from HTML pages.

    Video hosting sites typically embed players via nested iframes.
    This class handles:
        - Direct iframe src extraction
        - data-src / data-lazy-src attributes
        - Dynamically injected iframe URLs from scripts
        - Nested iframe chains (recursive resolution)
    """

    # ── Patterns for iframe detection ────────────────────────────────────
    _IFRAME_SRC_ATTRS: List[str] = [
        "src", "data-src", "data-lazy-src", "data-original",
        "data-url", "data-frame-src", "data-player-src",
    ]

    # Script-based iframe injection patterns
    _SCRIPT_IFRAME_PATTERNS: List[re.Pattern] = [
        # document.write('<iframe src="..."')
        re.compile(
            r'document\.write\s*\(\s*["\']<iframe[^>]*src\s*=\s*[\\"\']+'
            r'(https?://[^"\'\\]+)[\\"\']',
            re.I | re.S,
        ),
        # innerHTML = '<iframe src="..."'
        re.compile(
            r'innerHTML\s*=\s*["\']<iframe[^>]*src\s*=\s*[\\"\']+'
            r'(https?://[^"\'\\]+)[\\"\']',
            re.I | re.S,
        ),
        # createElement('iframe')...src = '...'
        re.compile(
            r'createElement\s*\(\s*["\']iframe["\']\s*\)[\s\S]{0,200}'
            r'\.src\s*=\s*["\']([^"\']+)["\']',
            re.I | re.S,
        ),
        # jQuery .attr('src', '...')
        re.compile(
            r'\.attr\s*\(\s*["\']src["\']\s*,\s*["\']([^"\']+)["\']',
            re.I,
        ),
        # Generic iframe src in string
        re.compile(
            r'["\']<iframe[^>]*src=[\\]*["\']([^"\'<>\\]+)[\\]*["\']',
            re.I | re.S,
        ),
    ]

    def __init__(self, config: ExtractorConfig, logger: ExtractorLogger) -> None:
        self._config = config
        self._log = logger

    def extract_iframes(
        self,
        html: str,
        base_url: str,
    ) -> List[Dict[str, Any]]:
        """
        Extract all iframe sources from an HTML page.

        Args:
            html: Raw HTML string.
            base_url: Base URL for resolving relative paths.

        Returns:
            List of iframe dicts: {"url": str, "source": str, "attrs": dict}
        """
        iframes: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        # Strategy 1: BeautifulSoup DOM parsing
        dom_iframes = self._extract_from_dom(html, base_url)
        for iframe in dom_iframes:
            if iframe["url"] not in seen_urls:
                seen_urls.add(iframe["url"])
                iframes.append(iframe)

        # Strategy 2: Script-injected iframes
        script_iframes = self._extract_from_scripts(html, base_url)
        for iframe in script_iframes:
            if iframe["url"] not in seen_urls:
                seen_urls.add(iframe["url"])
                iframes.append(iframe)

        # Strategy 3: Regex fallback on raw HTML
        regex_iframes = self._extract_via_regex(html, base_url)
        for iframe in regex_iframes:
            if iframe["url"] not in seen_urls:
                seen_urls.add(iframe["url"])
                iframes.append(iframe)

        self._log.debug(f"Extracted {len(iframes)} unique iframe(s) from page")
        for i, iframe in enumerate(iframes):
            self._log.debug(f"  iframe[{i}]: {iframe['source']} → {iframe['url'][:80]}")

        return iframes

    def _extract_from_dom(
        self,
        html: str,
        base_url: str,
    ) -> List[Dict[str, Any]]:
        """Extract iframes using BeautifulSoup DOM traversal."""
        results: List[Dict[str, Any]] = []

        try:
            soup = BeautifulSoup(html, self._config.html_parser)

            for iframe in soup.find_all("iframe"):
                url = None
                source_attr = None

                # Check all known source attributes
                for attr in self._IFRAME_SRC_ATTRS:
                    val = iframe.get(attr)
                    if val and val.strip() and val.strip() != "about:blank":
                        url = normalize_url(val.strip(), base_url)
                        source_attr = attr
                        break

                if url and url.startswith("http"):
                    # Collect other potentially useful attributes
                    attrs = {}
                    for key in ["id", "class", "name", "width", "height",
                                "allowfullscreen", "frameborder", "sandbox"]:
                        val = iframe.get(key)
                        if val:
                            attrs[key] = val if not isinstance(val, list) else " ".join(val)

                    results.append({
                        "url": url,
                        "source": f"dom:{source_attr}",
                        "attrs": attrs,
                    })

            # Also check <embed> and <object> tags
            for embed in soup.find_all(["embed", "object"]):
                src = embed.get("src") or embed.get("data")
                if src and src.strip().startswith("http"):
                    url = normalize_url(src.strip(), base_url)
                    results.append({
                        "url": url,
                        "source": f"dom:{embed.name}",
                        "attrs": {"type": embed.get("type", "")},
                    })

        except Exception as exc:
            self._log.debug(f"DOM iframe extraction error: {exc}")

        return results

    def _extract_from_scripts(
        self,
        html: str,
        base_url: str,
    ) -> List[Dict[str, Any]]:
        """Extract iframe URLs injected via JavaScript."""
        results: List[Dict[str, Any]] = []

        try:
            soup = BeautifulSoup(html, self._config.html_parser)
            scripts = soup.find_all("script")

            for script in scripts:
                script_text = script.string or ""
                if not script_text.strip():
                    continue

                for pattern in self._SCRIPT_IFRAME_PATTERNS:
                    for match in pattern.finditer(script_text):
                        url = match.group(1).strip()
                        url = normalize_url(url, base_url)
                        if url.startswith("http"):
                            results.append({
                                "url": url,
                                "source": "script:injection",
                                "attrs": {},
                            })
        except Exception as exc:
            self._log.debug(f"Script iframe extraction error: {exc}")

        return results

    def _extract_via_regex(
        self,
        html: str,
        base_url: str,
    ) -> List[Dict[str, Any]]:
        """
        Regex-based fallback for iframe extraction.
        Catches iframes that BS4 may miss due to malformed HTML.
        """
        results: List[Dict[str, Any]] = []

        # Pattern: <iframe ... src="URL" ...>
        iframe_regex = re.compile(
            r'<iframe[^>]*?\bsrc\s*=\s*["\']([^"\']+)["\'][^>]*?>',
            re.I | re.S,
        )

        for match in iframe_regex.finditer(html):
            url = match.group(1).strip()
            if url and url != "about:blank":
                url = normalize_url(url, base_url)
                if url.startswith("http"):
                    results.append({
                        "url": url,
                        "source": "regex:iframe_src",
                        "attrs": {},
                    })

        # Pattern: <iframe ... data-src="URL" ...>
        data_src_regex = re.compile(
            r'<iframe[^>]*?\bdata-(?:lazy-)?src\s*=\s*["\']([^"\']+)["\'][^>]*?>',
            re.I | re.S,
        )

        for match in data_src_regex.finditer(html):
            url = normalize_url(match.group(1).strip(), base_url)
            if url.startswith("http"):
                results.append({
                    "url": url,
                    "source": "regex:iframe_data_src",
                    "attrs": {},
                })

        return results

    def classify_iframe(self, iframe: Dict[str, Any]) -> Dict[str, Any]:
        """
        Classify an iframe by its purpose/type.

        Adds classification metadata:
            - is_player: bool (likely a video player embed)
            - is_ad: bool (likely an advertisement)
            - server_hint: str (detected server name if known)
        """
        url = iframe["url"].lower()
        domain = extract_domain(iframe["url"])

        # Ad detection
        ad_patterns = [
            "ads", "banner", "popunder", "popup", "track",
            "doubleclick", "adserver", "clickadu", "juicyads",
            "exoclick", "trafficjunky", "propellerads",
        ]
        is_ad = any(p in url or p in domain for p in ad_patterns)

        # Player detection
        player_patterns = [
            "embed", "player", "play", "watch", "video",
            "stream", "frame", "load", "view",
        ]
        is_player = any(p in url for p in player_patterns) and not is_ad

        # Server hint from domain
        server_hint = self._detect_server_from_url(url, domain)

        iframe["classification"] = {
            "is_player": is_player,
            "is_ad": is_ad,
            "server_hint": server_hint,
            "domain": domain,
        }

        return iframe

    @staticmethod
    def _detect_server_from_url(url: str, domain: str) -> str:
        """Try to identify the video server from URL/domain."""
        server_fragments = {
            "streamtape": "streamtape",
            "doodstream": "doodstream",
            "dood.": "doodstream",
            "mixdrop": "mixdrop",
            "filemoon": "filemoon",
            "streamwish": "streamwish",
            "vidhide": "vidhide",
            "mp4upload": "mp4upload",
            "streamsb": "streamsb",
            "sbembed": "streamsb",
            "embedsito": "streamsb",
            "watchsb": "streamsb",
            "fembed": "fembed",
            "femax": "fembed",
            "feurl": "fembed",
            "supervideo": "supervideo",
            "upstream": "upstream",
            "voe.sx": "voe",
            "vidoza": "vidoza",
            "highstream": "highstream",
        }

        combined = f"{url} {domain}"
        for fragment, name in server_fragments.items():
            if fragment in combined:
                return name

        return "unknown"


class ScriptAnalyzer:
    """
    Deep analysis of <script> tags to extract video-related data.

    Searches for:
        - Direct video URLs in JavaScript
        - JSON configuration objects (player configs)
        - Variable assignments containing URLs
        - Function calls with URL parameters
        - Encoded/obfuscated video sources
    """

    # ── Direct URL patterns ──────────────────────────────────────────────
    _VIDEO_URL_PATTERNS: List[Tuple[re.Pattern, str]] = [
        # Direct MP4/M3U8 URLs in strings
        (re.compile(
            r'["\'](\s*https?://[^"\'<>\s]+\.(?:mp4|m3u8|webm|flv)(?:\?[^"\'<>\s]*)?)\s*["\']',
            re.I,
        ), "direct_url"),

        # source/src/file/url assignments
        (re.compile(
            r'(?:source|src|file|url|video_url|stream_url|video|mp4|hls)\s*'
            r'[:=]\s*["\'](\s*https?://[^"\'<>\s]+)\s*["\']',
            re.I,
        ), "assignment"),

        # JSON-style "file": "url" or "sources": [{"src": "url"}]
        (re.compile(
            r'["\'](?:file|src|source|url|stream)["\']'
            r'\s*:\s*["\'](\s*https?://[^"\'<>\s]+)\s*["\']',
            re.I,
        ), "json_property"),

        # Player setup calls: setup({sources: [{file: "..."}]})
        (re.compile(
            r'setup\s*\(\s*\{[\s\S]*?file\s*:\s*["\']([^"\']+)["\']',
            re.I | re.S,
        ), "player_setup"),

        # new Player({source: "..."})
        (re.compile(
            r'(?:new\s+\w*[Pp]layer|player\.\w+)\s*\(\s*[{[]\s*[\s\S]*?'
            r'(?:source|src|file|url)\s*[":]\s*["\']([^"\']+)["\']',
            re.I | re.S,
        ), "player_constructor"),

        # Plyr / video.js source assignment
        (re.compile(
            r'\.source\s*\(\s*\{[\s\S]*?src\s*:\s*["\']([^"\']+)["\']',
            re.I | re.S,
        ), "source_method"),

        # window.atob or decode followed by URL
        (re.compile(
            r'(?:atob|decode)\s*\(\s*["\']([A-Za-z0-9+/=]{20,})["\']',
            re.I,
        ), "base64_decode"),
    ]

    # ── JSON configuration patterns ──────────────────────────────────────
    _JSON_CONFIG_PATTERNS: List[re.Pattern] = [
        # var config = {...}
        re.compile(
            r'(?:var|let|const)\s+(?:config|options|settings|playerConfig|player_config'
            r'|videoConfig|video_config)\s*=\s*(\{[\s\S]*?\})\s*;',
            re.I,
        ),
        # Generic large JSON object with video-related keys
        re.compile(
            r'(\{[^{}]*(?:"(?:file|source|src|url|stream|video)"[^{}]*){1,}[^{}]*\})',
            re.I,
        ),
    ]

    # ── Encoded data patterns ────────────────────────────────────────────
    _ENCODED_PATTERNS: List[Tuple[re.Pattern, str]] = [
        # Base64 in variable assignment
        (re.compile(
            r'(?:var|let|const)\s+\w+\s*=\s*["\']([A-Za-z0-9+/]{40,}={0,2})["\']',
        ), "base64_var"),

        # Hex array like [0x68,0x74,0x74,0x70...]
        (re.compile(
            r'\[((?:0x[0-9a-f]{2},?\s*){10,})\]',
            re.I,
        ), "hex_array"),

        # String.fromCharCode(104,116,116,112...)
        (re.compile(
            r'String\.fromCharCode\s*\(([\d,\s]+)\)',
            re.I,
        ), "charcode"),
    ]

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
        unpacker: JSUnpacker,
    ) -> None:
        self._config = config
        self._log = logger
        self._unpacker = unpacker

    def analyze_page(
        self,
        html: str,
        base_url: str,
    ) -> List[Dict[str, Any]]:
        """
        Analyze all scripts in a page for video-related content.

        Args:
            html: Raw HTML.
            base_url: Base URL for resolving relative paths.

        Returns:
            List of findings: {"url": str, "source": str, "confidence": int, ...}
        """
        findings: List[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        try:
            soup = BeautifulSoup(html, self._config.html_parser)
            scripts = soup.find_all("script")

            for i, script in enumerate(scripts):
                script_text = script.string or ""

                # Also check src attribute for external scripts
                script_src = script.get("src", "")

                if not script_text.strip() and not script_src:
                    continue

                if script_text.strip():
                    # Run unpacker on the script content
                    expanded = self._unpacker.unpack_all(script_text)

                    # Extract URLs from expanded script
                    script_findings = self._extract_from_script(
                        expanded, base_url, f"script[{i}]"
                    )

                    for finding in script_findings:
                        if finding["url"] not in seen_urls:
                            seen_urls.add(finding["url"])
                            findings.append(finding)

                    # Try JSON config extraction
                    json_findings = self._extract_json_configs(
                        expanded, base_url, f"script[{i}]"
                    )
                    for finding in json_findings:
                        if finding["url"] not in seen_urls:
                            seen_urls.add(finding["url"])
                            findings.append(finding)

                    # Try encoded data extraction
                    encoded_findings = self._extract_encoded_data(
                        expanded, base_url, f"script[{i}]"
                    )
                    for finding in encoded_findings:
                        if finding["url"] not in seen_urls:
                            seen_urls.add(finding["url"])
                            findings.append(finding)

        except Exception as exc:
            self._log.debug(f"Script analysis error: {exc}")

        # Also scan raw HTML for patterns (outside <script> tags)
        raw_findings = self._extract_from_raw_html(html, base_url)
        for finding in raw_findings:
            if finding["url"] not in seen_urls:
                seen_urls.add(finding["url"])
                findings.append(finding)

        self._log.debug(f"Script analysis found {len(findings)} video URL candidate(s)")
        return findings

    def _extract_from_script(
        self,
        script_text: str,
        base_url: str,
        source_label: str,
    ) -> List[Dict[str, Any]]:
        """Extract direct video URLs from a script block."""
        results: List[Dict[str, Any]] = []

        for pattern, pattern_name in self._VIDEO_URL_PATTERNS:
            for match in pattern.finditer(script_text):
                raw_url = match.group(1).strip()

                # Handle base64 decode pattern
                if pattern_name == "base64_decode":
                    decoded = decode_base64_safe(raw_url)
                    if decoded and ("http" in decoded or "/" in decoded):
                        raw_url = decoded
                    else:
                        continue

                url = normalize_url(raw_url, base_url)

                if not url.startswith("http"):
                    continue

                # Quick check: is this likely a video URL?
                likelihood = URLSignalAnalyzer.compute_video_likelihood(url)

                results.append({
                    "url": url,
                    "source": f"{source_label}:{pattern_name}",
                    "confidence": max(20, min(85, 40 + likelihood)),
                    "pattern": pattern_name,
                    "format": detect_stream_format(url).value,
                })

        return results

    def _extract_json_configs(
        self,
        script_text: str,
        base_url: str,
        source_label: str,
    ) -> List[Dict[str, Any]]:
        """Extract video URLs from JSON configuration objects in scripts."""
        results: List[Dict[str, Any]] = []

        for pattern in self._JSON_CONFIG_PATTERNS:
            for match in pattern.finditer(script_text):
                json_str = match.group(1)
                parsed = safe_json_loads(json_str)

                if parsed:
                    urls = self._extract_urls_from_json(parsed)
                    for url_info in urls:
                        url = normalize_url(url_info["url"], base_url)
                        if url.startswith("http"):
                            results.append({
                                "url": url,
                                "source": f"{source_label}:json_config",
                                "confidence": 70,
                                "pattern": "json_config",
                                "format": detect_stream_format(url).value,
                                "json_key": url_info.get("key", ""),
                            })

        return results

    def _extract_urls_from_json(
        self,
        obj: Any,
        path: str = "",
    ) -> List[Dict[str, Any]]:
        """
        Recursively extract URLs from a JSON object.

        Looks for values that are URLs, especially under keys like
        'file', 'src', 'source', 'url', 'stream', etc.
        """
        results: List[Dict[str, Any]] = []

        video_keys = {
            "file", "src", "source", "url", "stream",
            "video", "video_url", "stream_url", "mp4",
            "hls", "dash", "download", "link",
        }

        if isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key

                if isinstance(value, str) and value.startswith("http"):
                    if key.lower() in video_keys or URLSignalAnalyzer.is_likely_video(value, 10):
                        results.append({"url": value, "key": new_path})

                elif isinstance(value, (dict, list)):
                    results.extend(self._extract_urls_from_json(value, new_path))

        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                new_path = f"{path}[{i}]"
                if isinstance(item, str) and item.startswith("http"):
                    if URLSignalAnalyzer.is_likely_video(item, 10):
                        results.append({"url": item, "key": new_path})
                elif isinstance(item, (dict, list)):
                    results.extend(self._extract_urls_from_json(item, new_path))

        return results

    def _extract_encoded_data(
        self,
        script_text: str,
        base_url: str,
        source_label: str,
    ) -> List[Dict[str, Any]]:
        """Extract URLs from encoded/obfuscated data in scripts."""
        results: List[Dict[str, Any]] = []

        for pattern, pattern_name in self._ENCODED_PATTERNS:
            for match in pattern.finditer(script_text):
                raw = match.group(1)
                decoded_url = None

                if pattern_name == "base64_var":
                    decoded = decode_base64_safe(raw)
                    if decoded and ("http" in decoded or ".mp4" in decoded or ".m3u8" in decoded):
                        decoded_url = decoded

                elif pattern_name == "hex_array":
                    try:
                        hex_values = re.findall(r'0x([0-9a-fA-F]{2})', raw)
                        decoded = "".join(chr(int(h, 16)) for h in hex_values)
                        if "http" in decoded:
                            decoded_url = decoded
                    except Exception:
                        pass

                elif pattern_name == "charcode":
                    try:
                        codes = [int(c.strip()) for c in raw.split(",") if c.strip()]
                        decoded = "".join(chr(c) for c in codes)
                        if "http" in decoded:
                            decoded_url = decoded
                    except Exception:
                        pass

                if decoded_url:
                    url = normalize_url(decoded_url.strip(), base_url)
                    if url.startswith("http"):
                        results.append({
                            "url": url,
                            "source": f"{source_label}:{pattern_name}",
                            "confidence": 60,
                            "pattern": pattern_name,
                            "format": detect_stream_format(url).value,
                            "was_obfuscated": True,
                        })

        return results

    def _extract_from_raw_html(
        self,
        html: str,
        base_url: str,
    ) -> List[Dict[str, Any]]:
        """
        Last-resort regex scan of raw HTML for video URLs.
        Catches URLs in HTML attributes, comments, and inline styles.
        """
        results: List[Dict[str, Any]] = []

        # Video source elements: <source src="...">
        source_pattern = re.compile(
            r'<source[^>]*\bsrc\s*=\s*["\']([^"\']+)["\'][^>]*/?\s*>',
            re.I,
        )
        for match in source_pattern.finditer(html):
            url = normalize_url(match.group(1).strip(), base_url)
            if url.startswith("http") and URLSignalAnalyzer.is_likely_video(url, 5):
                results.append({
                    "url": url,
                    "source": "raw_html:source_tag",
                    "confidence": 75,
                    "pattern": "source_element",
                    "format": detect_stream_format(url).value,
                })

        # <video> tag sources
        video_src_pattern = re.compile(
            r'<video[^>]*\bsrc\s*=\s*["\']([^"\']+)["\']',
            re.I,
        )
        for match in video_src_pattern.finditer(html):
            url = normalize_url(match.group(1).strip(), base_url)
            if url.startswith("http"):
                results.append({
                    "url": url,
                    "source": "raw_html:video_src",
                    "confidence": 80,
                    "pattern": "video_element",
                    "format": detect_stream_format(url).value,
                })

        # og:video meta tag
        og_video_pattern = re.compile(
            r'<meta[^>]*property\s*=\s*["\']og:video["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
            re.I,
        )
        for match in og_video_pattern.finditer(html):
            url = normalize_url(match.group(1).strip(), base_url)
            if url.startswith("http"):
                results.append({
                    "url": url,
                    "source": "raw_html:og_video",
                    "confidence": 70,
                    "pattern": "og_meta",
                    "format": detect_stream_format(url).value,
                })

        return results


class MetadataExtractor:
    """
    Extracts page-level metadata from SupJav pages.

    Collects:
        - Title / video name
        - Thumbnail URL
        - Tags / categories
        - Release code (e.g., ABC-123)
        - Page language hints
    """

    # ── JAV code pattern ─────────────────────────────────────────────────
    _JAV_CODE_PATTERN = re.compile(
        r'\b([A-Z]{2,6})-?(\d{2,5})\b',
        re.I,
    )

    def __init__(self, config: ExtractorConfig, logger: ExtractorLogger) -> None:
        self._config = config
        self._log = logger

    def extract_metadata(self, html: str, url: str) -> Dict[str, Any]:
        """
        Extract all available metadata from a page.

        Args:
            html: Raw HTML.
            url: Page URL.

        Returns:
            Metadata dictionary.
        """
        metadata: Dict[str, Any] = {
            "url": url,
            "domain": extract_domain(url),
        }

        try:
            soup = BeautifulSoup(html, self._config.html_parser)

            # Title
            metadata["title"] = self._extract_title(soup)

            # Thumbnail
            metadata["thumbnail"] = self._extract_thumbnail(soup)

            # Tags
            metadata["tags"] = self._extract_tags(soup)

            # JAV code
            title = metadata.get("title", "")
            jav_code = self._extract_jav_code(title, url)
            if jav_code:
                metadata["jav_code"] = jav_code

            # Page description
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc:
                metadata["description"] = meta_desc.get("content", "")

        except Exception as exc:
            self._log.debug(f"Metadata extraction error: {exc}")

        self._log.debug(f"Metadata: title='{metadata.get('title', '?')[:50]}' "
                        f"code={metadata.get('jav_code', 'N/A')}")

        return metadata

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Extract the page/video title."""
        # Try og:title first
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()

        # Try <title> tag
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            title = title_tag.string.strip()
            # Remove site name suffixes
            for suffix in [" - SupJav", " | SupJav", " – SupJav"]:
                if title.endswith(suffix):
                    title = title[:-len(suffix)]
            return title

        # Try h1
        h1 = soup.find("h1")
        if h1:
            return h1.get_text(strip=True)

        return ""

    def _extract_thumbnail(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract thumbnail/poster image URL."""
        # og:image
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            return og_image["content"].strip()

        # poster attribute on video tags
        video_tag = soup.find("video")
        if video_tag and video_tag.get("poster"):
            return video_tag["poster"].strip()

        return None

    def _extract_tags(self, soup: BeautifulSoup) -> List[str]:
        """Extract tags/categories from the page."""
        tags: List[str] = []

        # Common tag containers
        tag_containers = soup.find_all(
            ["a", "span"],
            class_=re.compile(r"tag|category|genre|label", re.I),
        )
        for tag in tag_containers:
            text = tag.get_text(strip=True)
            if text and len(text) < 50:
                tags.append(text)

        # Meta keywords
        meta_kw = soup.find("meta", attrs={"name": "keywords"})
        if meta_kw and meta_kw.get("content"):
            kw_tags = [t.strip() for t in meta_kw["content"].split(",")]
            tags.extend(kw_tags)

        # Deduplicate preserving order
        seen: Set[str] = set()
        unique_tags: List[str] = []
        for t in tags:
            if t.lower() not in seen:
                seen.add(t.lower())
                unique_tags.append(t)

        return unique_tags[:20]  # cap at 20 tags

    def _extract_jav_code(self, title: str, url: str) -> Optional[str]:
        """Extract JAV release code from title or URL."""
        # Search in title first
        match = self._JAV_CODE_PATTERN.search(title)
        if match:
            prefix = match.group(1).upper()
            number = match.group(2)
            return f"{prefix}-{number}"

        # Search in URL
        match = self._JAV_CODE_PATTERN.search(url)
        if match:
            prefix = match.group(1).upper()
            number = match.group(2)
            return f"{prefix}-{number}"

        return None


class AdaptiveParser:
    """
    Master parser that coordinates all parsing sub-systems.

    Orchestrates:
        1. Metadata extraction
        2. Iframe discovery
        3. Script analysis
        4. Combined deduplication and scoring

    This is the single entry point for all HTML parsing needs.
    """

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
    ) -> None:
        self._config = config
        self._log = logger
        self._unpacker = JSUnpacker(logger)
        self._iframe_extractor = IframeExtractor(config, logger)
        self._script_analyzer = ScriptAnalyzer(config, logger, self._unpacker)
        self._metadata_extractor = MetadataExtractor(config, logger)

    def parse_page(
        self,
        html: str,
        url: str,
    ) -> Dict[str, Any]:
        """
        Full parse of an HTML page.

        Returns a comprehensive result dict containing all extracted data.

        Args:
            html: Raw HTML content.
            url: The page URL.

        Returns:
            {
                "metadata": {...},
                "iframes": [...],
                "video_urls": [...],
                "scripts_analyzed": int,
                "raw_html_length": int,
            }
        """
        self._log.section(f"Parsing: {url[:70]}")
        start_time = time.time()

        result: Dict[str, Any] = {
            "metadata": {},
            "iframes": [],
            "video_urls": [],
            "scripts_analyzed": 0,
            "raw_html_length": len(html),
        }

        # ── 1. Metadata ─────────────────────────────────────────────────
        result["metadata"] = self._metadata_extractor.extract_metadata(html, url)

        # ── 2. Iframes ──────────────────────────────────────────────────
        raw_iframes = self._iframe_extractor.extract_iframes(html, url)
        classified_iframes = []
        for iframe in raw_iframes:
            classified = self._iframe_extractor.classify_iframe(iframe)
            classified_iframes.append(classified)

        # Filter out ads, keep player iframes
        player_iframes = [
            f for f in classified_iframes
            if f.get("classification", {}).get("is_player", False)
            or not f.get("classification", {}).get("is_ad", False)
        ]
        result["iframes"] = player_iframes

        # ── 3. Script analysis ───────────────────────────────────────────
        video_urls = self._script_analyzer.analyze_page(html, url)
        result["video_urls"] = video_urls

        soup = BeautifulSoup(html, self._config.html_parser)
        result["scripts_analyzed"] = len(soup.find_all("script"))

        # ── Summary ──────────────────────────────────────────────────────
        elapsed = time.time() - start_time
        self._log.info(
            f"Parse complete in {elapsed:.2f}s: "
            f"{len(result['iframes'])} iframes, "
            f"{len(result['video_urls'])} video URLs, "
            f"{result['scripts_analyzed']} scripts"
        )

        return result

    def quick_scan(self, html: str, url: str) -> List[str]:
        """
        Quick scan returning just video URLs found.
        Faster than full parse — skips metadata and classification.

        Args:
            html: Raw HTML.
            url: Page URL.

        Returns:
            List of discovered video URL strings.
        """
        urls: List[str] = []

        # Quick iframe scan
        iframes = self._iframe_extractor.extract_iframes(html, url)
        for iframe in iframes:
            urls.append(iframe["url"])

        # Quick script scan
        findings = self._script_analyzer.analyze_page(html, url)
        for finding in findings:
            urls.append(finding["url"])

        return list(set(urls))

    @property
    def unpacker(self) -> JSUnpacker:
        """Access the JS unpacker for external use."""
        return self._unpacker

    @property
    def iframe_extractor(self) -> IframeExtractor:
        """Access the iframe extractor for external use."""
        return self._iframe_extractor

    @property
    def script_analyzer(self) -> ScriptAnalyzer:
        """Access the script analyzer for external use."""
        return self._script_analyzer

    @property
    def metadata_extractor(self) -> MetadataExtractor:
        """Access the metadata extractor for external use."""
        return self._metadata_extractor


# ═══════════════════════════════════════════════════════════════════════════════
# END OF PART 3
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# PART 4 — SERVER DETECTION SYSTEM
# ═══════════════════════════════════════════════════════════════════════════════
#
# Detects, identifies, and catalogs all video hosting servers present on a
# SupJav page. Normalizes server data and assigns confidence scores for
# downstream handler routing.
# ═══════════════════════════════════════════════════════════════════════════════


class ServerFingerprint:
    """
    Defines the fingerprint of a known video hosting server.

    Each fingerprint contains domain patterns, URL signatures,
    button/link text patterns, and known embed URL formats.
    """

    def __init__(
        self,
        name: str,
        canonical_name: str,
        domain_patterns: List[str],
        url_patterns: List[re.Pattern],
        button_text_patterns: List[re.Pattern],
        embed_url_format: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        typical_quality: Quality = Quality.Q720,
        reliability: int = 50,
        notes: str = "",
    ) -> None:
        self.name = name
        self.canonical_name = canonical_name
        self.domain_patterns = [p.lower() for p in domain_patterns]
        self.url_patterns = url_patterns
        self.button_text_patterns = button_text_patterns
        self.embed_url_format = embed_url_format
        self.aliases = [a.lower() for a in (aliases or [])]
        self.typical_quality = typical_quality
        self.reliability = reliability
        self.notes = notes

    def matches_domain(self, domain: str) -> bool:
        """Check if a domain matches this server's fingerprint."""
        domain_lower = domain.lower()
        for pattern in self.domain_patterns:
            if pattern in domain_lower:
                return True
        return False

    def matches_url(self, url: str) -> bool:
        """Check if a URL matches this server's fingerprint."""
        url_lower = url.lower()
        # Domain check
        for pattern in self.domain_patterns:
            if pattern in url_lower:
                return True
        # Regex check
        for pattern in self.url_patterns:
            if pattern.search(url):
                return True
        return False

    def matches_text(self, text: str) -> bool:
        """Check if button/link text matches this server."""
        text_lower = text.lower().strip()
        # Direct name match
        if self.canonical_name.lower() in text_lower:
            return True
        for alias in self.aliases:
            if alias in text_lower:
                return True
        # Regex patterns
        for pattern in self.button_text_patterns:
            if pattern.search(text):
                return True
        return False

    def compute_match_confidence(
        self,
        url: str = "",
        text: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Compute a match confidence score (0-100) for this server fingerprint.

        Higher confidence = stronger match evidence.
        """
        score = 0
        factors = 0

        if url:
            url_lower = url.lower()
            # Domain match is strongest signal
            for pattern in self.domain_patterns:
                if pattern in url_lower:
                    score += 90
                    factors += 1
                    break

            # URL pattern match
            for pattern in self.url_patterns:
                if pattern.search(url):
                    score += 70
                    factors += 1
                    break

        if text:
            if self.matches_text(text):
                score += 60
                factors += 1

        if context:
            # Contextual hints (e.g., page structure, JS variables)
            if context.get("js_reference"):
                score += 30
                factors += 1

        if factors == 0:
            return 0

        return min(100, score // max(1, factors) + (factors * 5))


class ServerFingerprintDatabase:
    """
    Central database of all known video server fingerprints.

    This is the knowledge base that the detection system queries
    to identify which servers are present on a page.
    """

    _fingerprints: List[ServerFingerprint] = []
    _initialized: bool = False

    @classmethod
    def initialize(cls) -> None:
        """Build the fingerprint database. Called once at startup."""
        if cls._initialized:
            return

        cls._fingerprints = [
            # ── StreamTape ───────────────────────────────────────────
            ServerFingerprint(
                name="streamtape",
                canonical_name="StreamTape",
                domain_patterns=["streamtape.com", "streamtape.to", "streamtape.net",
                                 "streamta.pe", "strtape.", "strcloud.", "streamadblockplus."],
                url_patterns=[
                    re.compile(r'streamtape\.\w+/e/', re.I),
                    re.compile(r'streamtape\.\w+/v/', re.I),
                    re.compile(r'strtape\.\w+/e/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'stream\s*tape', re.I),
                    re.compile(r'st\s*player', re.I),
                ],
                embed_url_format="https://streamtape.com/e/{video_id}",
                aliases=["stape", "strtape", "strcloud", "streamad"],
                typical_quality=Quality.Q720,
                reliability=75,
                notes="Token in URL generated via JS; needs script parsing",
            ),

            # ── DoodStream ──────────────────────────────────────────
            ServerFingerprint(
                name="doodstream",
                canonical_name="DoodStream",
                domain_patterns=["doodstream.com", "dood.to", "dood.so", "dood.pm",
                                 "dood.la", "dood.ws", "dood.watch", "dood.cx",
                                 "dood.sh", "dood.re", "dood.yt", "doodapi.",
                                 "ds2play.", "do0od.", "doods."],
                url_patterns=[
                    re.compile(r'dood[s]?\.\w+/[ed]/', re.I),
                    re.compile(r'doodstream\.\w+/[ed]/', re.I),
                    re.compile(r'ds2play\.\w+/[ed]/', re.I),
                    re.compile(r'do0od\.\w+/[ed]/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'dood\s*stream', re.I),
                    re.compile(r'\bdood\b', re.I),
                ],
                embed_url_format="https://dood.to/e/{video_id}",
                aliases=["dood", "ds2play", "do0od", "doods"],
                typical_quality=Quality.Q720,
                reliability=70,
                notes="Uses /pass_md5/ token API; time-sensitive URLs",
            ),

            # ── MixDrop ─────────────────────────────────────────────
            ServerFingerprint(
                name="mixdrop",
                canonical_name="MixDrop",
                domain_patterns=["mixdrop.co", "mixdrop.to", "mixdrop.sx",
                                 "mixdrop.bz", "mixdrop.ch", "mixdrop.ag",
                                 "mixdrop.gl", "mixdrp.", "mdbekjwqa.",
                                 "mdhowto."],
                url_patterns=[
                    re.compile(r'mixdrop\.\w+/e/', re.I),
                    re.compile(r'mixdrp\.\w+/e/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'mix\s*drop', re.I),
                ],
                embed_url_format="https://mixdrop.co/e/{video_id}",
                aliases=["mixdrp", "mdbekjwqa", "mdhowto"],
                typical_quality=Quality.Q720,
                reliability=72,
                notes="Uses eval/packed JS; needs unpacker",
            ),

            # ── FileMoon ────────────────────────────────────────────
            ServerFingerprint(
                name="filemoon",
                canonical_name="FileMoon",
                domain_patterns=["filemoon.sx", "filemoon.to", "filemoon.in",
                                 "kerapoxy.", "moonmov."],
                url_patterns=[
                    re.compile(r'filemoon\.\w+/e/', re.I),
                    re.compile(r'kerapoxy\.\w+/e/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'file\s*moon', re.I),
                    re.compile(r'fmoon', re.I),
                ],
                embed_url_format="https://filemoon.sx/e/{video_id}",
                aliases=["fmoon", "kerapoxy", "moonmov"],
                typical_quality=Quality.Q1080,
                reliability=73,
                notes="HLS output; uses packed JS",
            ),

            # ── StreamWish ──────────────────────────────────────────
            ServerFingerprint(
                name="streamwish",
                canonical_name="StreamWish",
                domain_patterns=["streamwish.to", "streamwish.com", "swdyu.",
                                 "wishembed.", "embedwish.", "swhoi.",
                                 "strwish.", "sfastwish.", "awish."],
                url_patterns=[
                    re.compile(r'streamwish\.\w+/e/', re.I),
                    re.compile(r'swdyu\.\w+/e/', re.I),
                    re.compile(r'wishembed\.\w+/e/', re.I),
                    re.compile(r'awish\.\w+/e/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'stream\s*wish', re.I),
                    re.compile(r'swish', re.I),
                ],
                embed_url_format="https://streamwish.to/e/{video_id}",
                aliases=["swish", "swdyu", "wishembed", "embedwish", "swhoi", "strwish",
                         "sfastwish", "awish"],
                typical_quality=Quality.Q1080,
                reliability=70,
                notes="HLS output; packed JS with eval",
            ),

            # ── VidHide ─────────────────────────────────────────────
            ServerFingerprint(
                name="vidhide",
                canonical_name="VidHide",
                domain_patterns=["vidhide.com", "vidhidepro.", "vidhidevip.",
                                 "vhide.", "luluvdo.", "vid2hide."],
                url_patterns=[
                    re.compile(r'vidhide\w*\.\w+/e/', re.I),
                    re.compile(r'luluvdo\.\w+/e/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'vid\s*hide', re.I),
                    re.compile(r'vhide', re.I),
                ],
                embed_url_format="https://vidhide.com/e/{video_id}",
                aliases=["vhide", "vidhidepro", "luluvdo", "vid2hide"],
                typical_quality=Quality.Q720,
                reliability=68,
                notes="HLS output; packed JS",
            ),

            # ── MP4Upload ───────────────────────────────────────────
            ServerFingerprint(
                name="mp4upload",
                canonical_name="MP4Upload",
                domain_patterns=["mp4upload.com"],
                url_patterns=[
                    re.compile(r'mp4upload\.com/embed-', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'mp4\s*upload', re.I),
                ],
                embed_url_format="https://mp4upload.com/embed-{video_id}.html",
                aliases=["mp4up"],
                typical_quality=Quality.Q720,
                reliability=72,
                notes="Direct MP4; relatively straightforward",
            ),

            # ── StreamSB / SBEmbed ──────────────────────────────────
            ServerFingerprint(
                name="streamsb",
                canonical_name="StreamSB",
                domain_patterns=["streamsb.net", "sbembed.com", "sbplay.org",
                                 "embedsito.com", "watchsb.com", "sbfull.com",
                                 "ssbstream.", "sbani.", "sbspeed.",
                                 "cloudemb.", "playersb.", "tubesb.",
                                 "lvturbo.", "sbface.", "sbrity."],
                url_patterns=[
                    re.compile(r'(?:stream|embed|watch|play)sb\.\w+/e/', re.I),
                    re.compile(r'embedsito\.\w+/e/', re.I),
                    re.compile(r'cloudemb\.\w+/e/', re.I),
                    re.compile(r'lvturbo\.\w+/e/', re.I),
                    re.compile(r'tubesb\.\w+/e/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'stream\s*sb', re.I),
                    re.compile(r'sb\s*embed', re.I),
                    re.compile(r'sb\s*play', re.I),
                ],
                embed_url_format="https://streamsb.net/e/{video_id}",
                aliases=["sbembed", "sbplay", "embedsito", "watchsb", "sbfull",
                         "ssbstream", "sbani", "sbspeed", "cloudemb", "playersb",
                         "tubesb", "lvturbo", "sbface", "sbrity"],
                typical_quality=Quality.Q720,
                reliability=60,
                notes="Complex API extraction; multiple domain rotations",
            ),

            # ── Fembed / Femax20 ────────────────────────────────────
            ServerFingerprint(
                name="fembed",
                canonical_name="Fembed",
                domain_patterns=["fembed.com", "femax20.com", "feurl.com",
                                 "fcdn.stream", "embedsito.", "fembad.",
                                 "dutrag.", "diasfem.", "suzihaza."],
                url_patterns=[
                    re.compile(r'fembed\.\w+/v/', re.I),
                    re.compile(r'femax\d*\.\w+/v/', re.I),
                    re.compile(r'feurl\.\w+/v/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'fem\s*bed', re.I),
                    re.compile(r'femax', re.I),
                    re.compile(r'feurl', re.I),
                ],
                embed_url_format="https://femax20.com/v/{video_id}",
                aliases=["femax", "femax20", "feurl", "fcdn", "fembad",
                         "dutrag", "diasfem", "suzihaza"],
                typical_quality=Quality.Q720,
                reliability=65,
                notes="POST to /api/source/{id} returns multi-quality links",
            ),

            # ── VOE ─────────────────────────────────────────────────
            ServerFingerprint(
                name="voe",
                canonical_name="VOE",
                domain_patterns=["voe.sx", "voeunblock.", "voeunbl0ck.",
                                 "voeunblk.", "voeunblck.", "voe-unblock.",
                                 "audaciousdefaulthouse.", "laaborede.",
                                 "precsjlede."],
                url_patterns=[
                    re.compile(r'voe(?:unbl\w*)?\.\w+/e/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'\bvoe\b', re.I),
                ],
                embed_url_format="https://voe.sx/e/{video_id}",
                aliases=["voeunblock", "voeunbl0ck"],
                typical_quality=Quality.Q720,
                reliability=66,
                notes="HLS output; uses redirect chains",
            ),

            # ── Vidoza ───────────────────────────────────────────────
            ServerFingerprint(
                name="vidoza",
                canonical_name="Vidoza",
                domain_patterns=["vidoza.net", "vidoza.co"],
                url_patterns=[
                    re.compile(r'vidoza\.\w+/embed-', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'vidoza', re.I),
                ],
                embed_url_format="https://vidoza.net/embed-{video_id}.html",
                aliases=[],
                typical_quality=Quality.Q720,
                reliability=64,
                notes="Direct MP4 source in script tag",
            ),

            # ── SuperVideo ──────────────────────────────────────────
            ServerFingerprint(
                name="supervideo",
                canonical_name="SuperVideo",
                domain_patterns=["supervideo.tv", "supervideo.cc"],
                url_patterns=[
                    re.compile(r'supervideo\.\w+/e/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'super\s*video', re.I),
                ],
                embed_url_format="https://supervideo.tv/e/{video_id}",
                aliases=["svideo"],
                typical_quality=Quality.Q720,
                reliability=62,
                notes="Packed JS; HLS output",
            ),

            # ── Upstream ────────────────────────────────────────────
            ServerFingerprint(
                name="upstream",
                canonical_name="Upstream",
                domain_patterns=["upstream.to", "upstream.pm"],
                url_patterns=[
                    re.compile(r'upstream\.\w+/embed-', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'upstream', re.I),
                ],
                embed_url_format="https://upstream.to/embed-{video_id}.html",
                aliases=["ustream"],
                typical_quality=Quality.Q720,
                reliability=68,
                notes="Packed JS; M3U8 output",
            ),

            # ── HighStream ──────────────────────────────────────────
            ServerFingerprint(
                name="highstream",
                canonical_name="HighStream",
                domain_patterns=["highstream.tv"],
                url_patterns=[
                    re.compile(r'highstream\.\w+/e/', re.I),
                ],
                button_text_patterns=[
                    re.compile(r'high\s*stream', re.I),
                ],
                embed_url_format="https://highstream.tv/e/{video_id}",
                aliases=["hstream"],
                typical_quality=Quality.Q720,
                reliability=64,
                notes="Packed JS; HLS output",
            ),
        ]

        cls._initialized = True

    @classmethod
    def get_all(cls) -> List[ServerFingerprint]:
        """Return all registered fingerprints."""
        cls.initialize()
        return cls._fingerprints

    @classmethod
    def find_by_name(cls, name: str) -> Optional[ServerFingerprint]:
        """Find a fingerprint by its canonical name or alias."""
        cls.initialize()
        name_lower = name.lower()
        for fp in cls._fingerprints:
            if fp.name == name_lower or fp.canonical_name.lower() == name_lower:
                return fp
            if name_lower in fp.aliases:
                return fp
        return None

    @classmethod
    def identify_url(cls, url: str) -> Optional[ServerFingerprint]:
        """Identify which server a URL belongs to."""
        cls.initialize()
        best_match: Optional[ServerFingerprint] = None
        best_score = 0

        for fp in cls._fingerprints:
            score = fp.compute_match_confidence(url=url)
            if score > best_score:
                best_score = score
                best_match = fp

        return best_match if best_score >= 40 else None

    @classmethod
    def identify_text(cls, text: str) -> Optional[ServerFingerprint]:
        """Identify which server a button/link text refers to."""
        cls.initialize()
        for fp in cls._fingerprints:
            if fp.matches_text(text):
                return fp
        return None


class SupjavPageAnalyzer:
    """
    Specialized analyzer for SupJav.com page structure.

    Understands the specific DOM patterns and JavaScript structures
    used by SupJav to present server selection buttons and player
    embedding.

    SupJav typically shows:
        - A player area with an embedded iframe
        - Server selection buttons/tabs (e.g., "Server 1", "DoodStream")
        - JavaScript that swaps iframe src on button click
        - Sometimes AJAX calls to load server-specific embed URLs
    """

    # ── SupJav-specific patterns ─────────────────────────────────────────

    # Server button patterns - SupJav uses various structures
    _SERVER_BUTTON_PATTERNS: List[re.Pattern] = [
        # <li data-vs="N">ServerName</li> or <a data-vs="N">
        re.compile(
            r'<(?:li|a|div|button|span)[^>]*?'
            r'(?:data-vs|data-server|data-index|data-id)\s*=\s*["\'](\w+)["\']'
            r'[^>]*?>\s*(.*?)\s*</(?:li|a|div|button|span)>',
            re.I | re.S,
        ),
        # onclick handler with server switch
        re.compile(
            r'<(?:li|a|div|button|span)[^>]*?'
            r'onclick\s*=\s*["\'][^"\']*?(?:change_server|switch_server|load_server|chooseServer)'
            r'\s*\(\s*["\']?(\w+)["\']?\s*\)[^"\']*?["\']'
            r'[^>]*?>\s*(.*?)\s*</(?:li|a|div|button|span)>',
            re.I | re.S,
        ),
        # Class-based detection
        re.compile(
            r'<(?:li|a|div|button|span)[^>]*?'
            r'class\s*=\s*["\'][^"\']*(?:server|mirror|source|host)[^"\']*["\']'
            r'[^>]*?>\s*(.*?)\s*</(?:li|a|div|button|span)>',
            re.I | re.S,
        ),
    ]

    # Player iframe container patterns
    _PLAYER_CONTAINER_PATTERNS: List[re.Pattern] = [
        re.compile(
            r'<div[^>]*?(?:id|class)\s*=\s*["\'][^"\']*'
            r'(?:player|video|embed|watch|stream|content-player)[^"\']*["\']',
            re.I,
        ),
    ]

    # JavaScript server map patterns (common in SupJav)
    _JS_SERVER_MAP_PATTERNS: List[re.Pattern] = [
        # var servers = {"1": "url1", "2": "url2"}
        re.compile(
            r'(?:var|let|const)\s+(?:servers?|sources?|mirrors?|hosts?|embeds?)\s*=\s*(\{[^;]+\})',
            re.I | re.S,
        ),
        # var sources = [{"name": "...", "url": "..."}]
        re.compile(
            r'(?:var|let|const)\s+(?:servers?|sources?|mirrors?|hosts?|embeds?)\s*=\s*(\[[^\]]+\])',
            re.I | re.S,
        ),
        # server_list["1"] = "url" patterns
        re.compile(
            r'(?:server_list|source_list|mirror_list)\s*\[\s*["\']?(\w+)["\']?\s*\]\s*=\s*["\']([^"\']+)["\']',
            re.I,
        ),
    ]

    # AJAX endpoint patterns for loading servers
    _AJAX_ENDPOINT_PATTERNS: List[re.Pattern] = [
        # $.ajax({url: "/ajax/server/..."})
        re.compile(
            r'(?:ajax|fetch|get|post)\s*\(\s*[{]?\s*(?:url\s*:\s*)?["\']([^"\']*?'
            r'(?:ajax|api|server|source|embed|mirror)[^"\']*)["\']',
            re.I | re.S,
        ),
        # Direct AJAX URL in code
        re.compile(
            r'["\'](\/?(?:ajax|api)\/(?:server|embed|source|mirror)\/?\??[^"\']*)["\']',
            re.I,
        ),
    ]

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
        parser: AdaptiveParser,
    ) -> None:
        self._config = config
        self._log = logger
        self._parser = parser

    def detect_servers(
        self,
        html: str,
        page_url: str,
    ) -> List[ServerInfo]:
        """
        Detect all video servers available on a SupJav page.

        Combines multiple detection strategies and deduplicates results.

        Args:
            html: Raw HTML of the SupJav page.
            page_url: URL of the page.

        Returns:
            List of detected ServerInfo objects, sorted by confidence.
        """
        self._log.section("Server Detection")
        all_servers: List[ServerInfo] = []
        seen_keys: Set[str] = set()

        # ── Strategy 1: Parse server buttons/tabs ────────────────────
        button_servers = self._detect_from_buttons(html, page_url)
        for srv in button_servers:
            key = f"{srv.name}:{srv.url}"
            if key not in seen_keys:
                seen_keys.add(key)
                all_servers.append(srv)

        # ── Strategy 2: Parse iframes for server identification ──────
        iframe_servers = self._detect_from_iframes(html, page_url)
        for srv in iframe_servers:
            key = f"{srv.name}:{srv.url}"
            if key not in seen_keys:
                seen_keys.add(key)
                all_servers.append(srv)

        # ── Strategy 3: Parse JavaScript for server maps ─────────────
        js_servers = self._detect_from_scripts(html, page_url)
        for srv in js_servers:
            key = f"{srv.name}:{srv.url}"
            if key not in seen_keys:
                seen_keys.add(key)
                all_servers.append(srv)

        # ── Strategy 4: Detect AJAX endpoints for server loading ─────
        ajax_servers = self._detect_ajax_endpoints(html, page_url)
        for srv in ajax_servers:
            key = f"{srv.name}:{srv.url}"
            if key not in seen_keys:
                seen_keys.add(key)
                all_servers.append(srv)

        # ── Normalize and score all detected servers ─────────────────
        normalized = self._normalize_servers(all_servers)

        # Sort by confidence then reliability
        normalized.sort(key=lambda s: (s.confidence, s.priority), reverse=True)

        self._log.info(f"Detected {len(normalized)} server(s):")
        for i, srv in enumerate(normalized):
            self._log.info(
                f"  [{i+1}] {srv.name:<20s} | conf={srv.confidence:>3d} "
                f"| type={srv.server_type:<12s} | {srv.url[:60]}"
            )

        return normalized

    def _detect_from_buttons(
        self,
        html: str,
        page_url: str,
    ) -> List[ServerInfo]:
        """Detect servers from UI buttons/tabs on the page."""
        servers: List[ServerInfo] = []

        try:
            soup = BeautifulSoup(html, self._config.html_parser)

            # Strategy A: Look for elements with data-vs, data-server, etc.
            server_elements = soup.find_all(
                attrs={"data-vs": True}
            )
            if not server_elements:
                server_elements = soup.find_all(
                    attrs={"data-server": True}
                )
            if not server_elements:
                server_elements = soup.find_all(
                    attrs={"data-index": True}
                )

            for elem in server_elements:
                text = elem.get_text(strip=True)
                data_vs = (
                    elem.get("data-vs")
                    or elem.get("data-server")
                    or elem.get("data-index")
                    or ""
                )
                href = elem.get("href", "")

                # Try to identify the server
                fingerprint = ServerFingerprintDatabase.identify_text(text)

                server_name = (
                    fingerprint.canonical_name if fingerprint
                    else self._clean_server_name(text)
                )
                server_type = fingerprint.name if fingerprint else "unknown"

                # Build server URL
                server_url = ""
                if href and href.startswith("http"):
                    server_url = href
                elif data_vs:
                    server_url = f"data-vs:{data_vs}"

                confidence = 70 if fingerprint else 50

                servers.append(ServerInfo(
                    name=server_name,
                    url=server_url,
                    server_type=server_type,
                    confidence=confidence,
                    priority=fingerprint.reliability if fingerprint else 50,
                    metadata={
                        "source": "button",
                        "data_vs": data_vs,
                        "text": text,
                        "href": href,
                    },
                ))

            # Strategy B: Regex-based button detection (fallback)
            if not servers:
                for pattern in self._SERVER_BUTTON_PATTERNS:
                    for match in pattern.finditer(html):
                        groups = match.groups()
                        if len(groups) >= 2:
                            identifier = groups[0]
                            text = re.sub(r'<[^>]+>', '', groups[1]).strip()
                        elif len(groups) == 1:
                            text = re.sub(r'<[^>]+>', '', groups[0]).strip()
                            identifier = text
                        else:
                            continue

                        if not text or len(text) > 50:
                            continue

                        fingerprint = ServerFingerprintDatabase.identify_text(text)
                        server_name = (
                            fingerprint.canonical_name if fingerprint
                            else self._clean_server_name(text)
                        )

                        servers.append(ServerInfo(
                            name=server_name,
                            url=f"button:{identifier}",
                            server_type=fingerprint.name if fingerprint else "unknown",
                            confidence=55 if fingerprint else 35,
                            priority=fingerprint.reliability if fingerprint else 40,
                            metadata={
                                "source": "button_regex",
                                "identifier": identifier,
                                "text": text,
                            },
                        ))

        except Exception as exc:
            self._log.debug(f"Button detection error: {exc}")

        self._log.debug(f"Button detection found {len(servers)} server(s)")
        return servers

    def _detect_from_iframes(
        self,
        html: str,
        page_url: str,
    ) -> List[ServerInfo]:
        """Detect servers by analyzing iframe embed URLs."""
        servers: List[ServerInfo] = []

        iframes = self._parser.iframe_extractor.extract_iframes(html, page_url)

        for iframe in iframes:
            url = iframe["url"]
            classified = self._parser.iframe_extractor.classify_iframe(iframe)
            classification = classified.get("classification", {})

            if classification.get("is_ad", False):
                continue

            # Identify server from iframe URL
            fingerprint = ServerFingerprintDatabase.identify_url(url)

            if fingerprint:
                server_name = fingerprint.canonical_name
                server_type = fingerprint.name
                confidence = 80
            else:
                server_hint = classification.get("server_hint", "unknown")
                server_name = server_hint if server_hint != "unknown" else extract_domain(url)
                server_type = server_hint
                confidence = 55

            servers.append(ServerInfo(
                name=server_name,
                url=url,
                server_type=server_type,
                confidence=confidence,
                priority=fingerprint.reliability if fingerprint else 45,
                metadata={
                    "source": "iframe",
                    "iframe_attrs": iframe.get("attrs", {}),
                    "classification": classification,
                },
            ))

        self._log.debug(f"Iframe detection found {len(servers)} server(s)")
        return servers

    def _detect_from_scripts(
        self,
        html: str,
        page_url: str,
    ) -> List[ServerInfo]:
        """Detect servers from JavaScript code (server maps, variables)."""
        servers: List[ServerInfo] = []

        try:
            soup = BeautifulSoup(html, self._config.html_parser)
            all_scripts = ""
            for script in soup.find_all("script"):
                if script.string:
                    all_scripts += script.string + "\n"

            # Unpack obfuscated JS
            expanded = self._parser.unpacker.unpack_all(all_scripts)

            # Pattern 1: Server map objects
            for pattern in self._JS_SERVER_MAP_PATTERNS[:2]:
                for match in pattern.finditer(expanded):
                    data_str = match.group(1)
                    parsed = safe_json_loads(data_str)

                    if isinstance(parsed, dict):
                        for key, value in parsed.items():
                            if isinstance(value, str) and (
                                value.startswith("http") or value.startswith("//")
                            ):
                                url = normalize_url(value, page_url)
                                fingerprint = ServerFingerprintDatabase.identify_url(url)

                                servers.append(ServerInfo(
                                    name=fingerprint.canonical_name if fingerprint else f"Server_{key}",
                                    url=url,
                                    server_type=fingerprint.name if fingerprint else "unknown",
                                    confidence=75 if fingerprint else 50,
                                    priority=fingerprint.reliability if fingerprint else 45,
                                    metadata={
                                        "source": "js_map",
                                        "map_key": key,
                                    },
                                ))

                    elif isinstance(parsed, list):
                        for i, item in enumerate(parsed):
                            if isinstance(item, dict):
                                url = item.get("url") or item.get("src") or item.get("file") or ""
                                name = item.get("name") or item.get("label") or item.get("server") or ""

                                if url:
                                    url = normalize_url(url, page_url)
                                    fingerprint = ServerFingerprintDatabase.identify_url(url)
                                    if not fingerprint and name:
                                        fingerprint = ServerFingerprintDatabase.identify_text(name)

                                    servers.append(ServerInfo(
                                        name=fingerprint.canonical_name if fingerprint else (name or f"Server_{i}"),
                                        url=url,
                                        server_type=fingerprint.name if fingerprint else "unknown",
                                        confidence=75 if fingerprint else 50,
                                        priority=fingerprint.reliability if fingerprint else 45,
                                        metadata={
                                            "source": "js_array",
                                            "index": i,
                                            "original_name": name,
                                        },
                                    ))

            # Pattern 2: Individual assignment patterns
            for match in self._JS_SERVER_MAP_PATTERNS[2].finditer(expanded):
                key = match.group(1)
                url = normalize_url(match.group(2), page_url)

                if url.startswith("http"):
                    fingerprint = ServerFingerprintDatabase.identify_url(url)
                    servers.append(ServerInfo(
                        name=fingerprint.canonical_name if fingerprint else f"Server_{key}",
                        url=url,
                        server_type=fingerprint.name if fingerprint else "unknown",
                        confidence=65 if fingerprint else 40,
                        priority=fingerprint.reliability if fingerprint else 40,
                        metadata={
                            "source": "js_assignment",
                            "assignment_key": key,
                        },
                    ))

        except Exception as exc:
            self._log.debug(f"Script server detection error: {exc}")

        self._log.debug(f"Script detection found {len(servers)} server(s)")
        return servers

    def _detect_ajax_endpoints(
        self,
        html: str,
        page_url: str,
    ) -> List[ServerInfo]:
        """Detect AJAX endpoints used to load server embed URLs."""
        servers: List[ServerInfo] = []

        try:
            soup = BeautifulSoup(html, self._config.html_parser)
            all_scripts = ""
            for script in soup.find_all("script"):
                if script.string:
                    all_scripts += script.string + "\n"

            for pattern in self._AJAX_ENDPOINT_PATTERNS:
                for match in pattern.finditer(all_scripts):
                    endpoint = match.group(1)
                    url = normalize_url(endpoint, page_url)

                    servers.append(ServerInfo(
                        name=f"AJAX_Endpoint",
                        url=url,
                        server_type="ajax_endpoint",
                        confidence=45,
                        priority=30,
                        metadata={
                            "source": "ajax_detection",
                            "endpoint": endpoint,
                            "requires_request": True,
                        },
                    ))

        except Exception as exc:
            self._log.debug(f"AJAX endpoint detection error: {exc}")

        self._log.debug(f"AJAX detection found {len(servers)} endpoint(s)")
        return servers

    def _normalize_servers(self, servers: List[ServerInfo]) -> List[ServerInfo]:
        """
        Normalize and consolidate server data.

        - Merges duplicate servers (same server from different sources)
        - Picks highest-confidence version
        - Enhances with fingerprint data
        """
        if not servers:
            return []

        # Group by server type (or name if type is unknown)
        groups: Dict[str, List[ServerInfo]] = {}
        for srv in servers:
            key = srv.server_type if srv.server_type != "unknown" else srv.name.lower()
            groups.setdefault(key, []).append(srv)

        normalized: List[ServerInfo] = []

        for key, group in groups.items():
            if len(group) == 1:
                normalized.append(group[0])
                continue

            # Merge: pick the one with the best URL and highest confidence
            # Prefer servers with actual HTTP URLs over placeholders
            http_servers = [s for s in group if s.url.startswith("http")]
            if http_servers:
                # Pick highest confidence among those with real URLs
                best = max(http_servers, key=lambda s: s.confidence)
            else:
                best = max(group, key=lambda s: s.confidence)

            # Merge metadata from all versions
            merged_metadata: Dict[str, Any] = {}
            for srv in group:
                merged_metadata.update(srv.metadata)
            merged_metadata["merge_count"] = len(group)
            merged_metadata["merge_sources"] = [s.metadata.get("source", "?") for s in group]

            best.metadata = merged_metadata
            # Boost confidence for servers confirmed by multiple detection methods
            if len(group) > 1:
                multi_source_bonus = min(15, len(group) * 5)
                best.confidence = min(100, best.confidence + multi_source_bonus)

            normalized.append(best)

        return normalized

    @staticmethod
    def _clean_server_name(raw_text: str) -> str:
        """
        Clean raw text into a usable server name.

        Strips HTML, extra whitespace, special chars, etc.
        """
        # Remove HTML tags
        cleaned = re.sub(r'<[^>]+>', '', raw_text)
        # Remove extra whitespace
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        # Remove non-alphanumeric (keep spaces and basic punctuation)
        cleaned = re.sub(r'[^\w\s\-.]', '', cleaned).strip()
        # Cap length
        if len(cleaned) > 30:
            cleaned = cleaned[:30].strip()
        return cleaned if cleaned else "Unknown Server"


class ServerDetectionEngine:
    """
    Top-level engine that coordinates server detection across an entire
    extraction session.

    Manages:
        - Initial detection from main page
        - Follow-up detection from intermediate pages
        - Server preference matching
        - Detection result caching
    """

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
        http_client: HttpClient,
        parser: AdaptiveParser,
    ) -> None:
        self._config = config
        self._log = logger
        self._http = http_client
        self._parser = parser
        self._page_analyzer = SupjavPageAnalyzer(config, logger, parser)
        self._detection_cache: Dict[str, List[ServerInfo]] = {}

        # Initialize fingerprint database
        ServerFingerprintDatabase.initialize()

    def detect_from_url(
        self,
        url: str,
        html: Optional[str] = None,
    ) -> List[ServerInfo]:
        """
        Detect all video servers available at a given URL.

        Args:
            url: The page URL to analyze.
            html: Pre-fetched HTML (optional; fetched if not provided).

        Returns:
            List of detected ServerInfo objects.
        """
        # Check cache
        cache_key = url_fingerprint(url)
        if cache_key in self._detection_cache:
            self._log.debug(f"Server detection cache hit for {url[:60]}")
            return self._detection_cache[cache_key]

        # Fetch HTML if not provided
        if html is None:
            response = self._http.get(url)
            if not response:
                self._log.error(f"Failed to fetch page for server detection: {url[:60]}")
                return []
            html = response.text

        # Run detection
        servers = self._page_analyzer.detect_servers(html, url)

        # Cache results
        self._detection_cache[cache_key] = servers

        return servers

    def detect_from_html(
        self,
        html: str,
        url: str,
    ) -> List[ServerInfo]:
        """
        Detect servers from pre-fetched HTML.

        Args:
            html: Raw HTML content.
            url: The page URL.

        Returns:
            List of detected ServerInfo objects.
        """
        return self._page_analyzer.detect_servers(html, url)

    def find_preferred_server(
        self,
        servers: List[ServerInfo],
        preferred_name: Optional[str] = None,
    ) -> Optional[ServerInfo]:
        """
        Find a specific server by name/type preference.

        Args:
            servers: Available servers.
            preferred_name: Desired server name (partial match supported).

        Returns:
            Matching ServerInfo or None.
        """
        if not preferred_name or not servers:
            return servers[0] if servers else None

        preferred_lower = preferred_name.lower()

        # Exact match
        for srv in servers:
            if srv.name.lower() == preferred_lower or srv.server_type == preferred_lower:
                return srv

        # Partial match
        for srv in servers:
            if preferred_lower in srv.name.lower() or preferred_lower in srv.server_type:
                return srv

        # Fingerprint match
        fp = ServerFingerprintDatabase.find_by_name(preferred_name)
        if fp:
            for srv in servers:
                if srv.server_type == fp.name:
                    return srv

        self._log.warning(f"Preferred server '{preferred_name}' not found among {len(servers)} servers")
        return None

    def get_server_fingerprint(self, server: ServerInfo) -> Optional[ServerFingerprint]:
        """Get the fingerprint for a detected server."""
        if server.server_type != "unknown":
            return ServerFingerprintDatabase.find_by_name(server.server_type)

        # Try identifying from URL
        if server.url.startswith("http"):
            return ServerFingerprintDatabase.identify_url(server.url)

        # Try identifying from name
        return ServerFingerprintDatabase.identify_text(server.name)

    def get_detection_stats(self) -> Dict[str, Any]:
        """Return statistics about detection results."""
        return {
            "cache_size": len(self._detection_cache),
            "known_fingerprints": len(ServerFingerprintDatabase.get_all()),
            "cached_pages": list(self._detection_cache.keys()),
        }

    def clear_cache(self) -> None:
        """Clear the detection cache."""
        self._detection_cache.clear()


# ═══════════════════════════════════════════════════════════════════════════════
# END OF PART 4
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# PART 5 — SERVER HANDLERS
# ═══════════════════════════════════════════════════════════════════════════════
#
# Modular handler architecture for extracting video URLs from individual
# hosting servers. Each handler encapsulates the specific extraction logic
# (page structure, API calls, obfuscation) for its target server.
# ═══════════════════════════════════════════════════════════════════════════════


class ServerHandler(ABC):
    """
    Abstract base class for all server-specific extraction handlers.

    Each handler knows how to:
        1. Determine if it can handle a given server/URL
        2. Extract the final video stream URL(s)
        3. Report confidence in its extraction

    Subclasses MUST implement:
        - can_handle()
        - _extract_impl()

    Subclasses MAY override:
        - get_name()
        - get_priority()
        - _build_referer()
    """

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
        http_client: HttpClient,
        parser: AdaptiveParser,
    ) -> None:
        self._config = config
        self._log = logger
        self._http = http_client
        self._parser = parser

    @abstractmethod
    def can_handle(self, server: ServerInfo) -> bool:
        """
        Determine if this handler can process the given server.

        Args:
            server: The detected ServerInfo.

        Returns:
            True if this handler should be used.
        """
        ...

    @abstractmethod
    def _extract_impl(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
    ) -> List[VideoStream]:
        """
        Internal extraction implementation.

        Args:
            server: The server to extract from.
            page_html: Pre-fetched HTML of the embed page (optional).

        Returns:
            List of extracted VideoStream objects.
        """
        ...

    def extract(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
    ) -> List[VideoStream]:
        """
        Public extraction entry point with error handling and logging.

        Args:
            server: The server to extract from.
            page_html: Pre-fetched embed page HTML.

        Returns:
            List of VideoStream objects (may be empty on failure).
        """
        handler_name = self.get_name()
        self._log.info(f"Handler [{handler_name}] extracting from: {server.name}")
        self._log.debug(f"  URL: {server.url[:80]}")

        start_time = time.time()

        try:
            streams = self._extract_impl(server, page_html)

            elapsed = time.time() - start_time

            if streams:
                self._log.info(
                    f"Handler [{handler_name}] found {len(streams)} stream(s) "
                    f"in {elapsed:.2f}s"
                )
                for i, s in enumerate(streams):
                    self._log.debug(
                        f"  stream[{i}]: fmt={s.format.value} q={s.quality.value} "
                        f"conf={s.confidence} | {s.url[:70]}"
                    )
            else:
                self._log.warning(
                    f"Handler [{handler_name}] found no streams ({elapsed:.2f}s)"
                )

            return streams

        except Exception as exc:
            elapsed = time.time() - start_time
            self._log.error(
                f"Handler [{handler_name}] crashed after {elapsed:.2f}s: {exc}"
            )
            return []

    def get_name(self) -> str:
        """Human-readable handler name."""
        return self.__class__.__name__

    def get_priority(self) -> int:
        """
        Handler priority (higher = tried first when multiple handlers match).
        Default is 50. Specific handlers should return higher values.
        """
        return 50

    def _fetch_embed_page(
        self,
        url: str,
        referer: Optional[str] = None,
    ) -> Optional[str]:
        """
        Fetch an embed page with appropriate headers.

        Args:
            url: Embed page URL.
            referer: Referer header.

        Returns:
            HTML content or None.
        """
        response = self._http.get(url, referer=referer)
        if response:
            return response.text
        return None

    def _build_referer(self, server: ServerInfo) -> str:
        """
        Build an appropriate Referer header for requests to this server.

        Default implementation uses the server URL's origin.
        """
        parsed = urllib.parse.urlparse(server.url)
        return f"{parsed.scheme}://{parsed.netloc}/"

    def _create_stream(
        self,
        url: str,
        server: ServerInfo,
        confidence: int = 50,
        fmt: Optional[StreamFormat] = None,
        quality: Optional[Quality] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> VideoStream:
        """
        Factory method to create a properly initialized VideoStream.

        Auto-detects format and quality if not provided.
        """
        detected_fmt = fmt or detect_stream_format(url)
        detected_quality = quality or detect_quality_from_url(url)

        headers: Dict[str, str] = {}
        referer = self._build_referer(server)
        if referer:
            headers["Referer"] = referer
        if extra_headers:
            headers.update(extra_headers)

        stream_metadata: Dict[str, Any] = {
            "handler": self.get_name(),
            "server_name": server.name,
            "server_type": server.server_type,
            "extraction_method": "handler",
        }
        if metadata:
            stream_metadata.update(metadata)

        return VideoStream(
            url=url,
            format=detected_fmt,
            quality=detected_quality,
            confidence=confidence,
            server_name=server.name,
            headers=headers,
            metadata=stream_metadata,
        )

    def _extract_packed_js_urls(
        self,
        html: str,
        base_url: str,
    ) -> List[str]:
        """
        Common utility: extract video URLs from packed/obfuscated JS in HTML.

        Many servers use eval(function(p,a,c,k,e,d){...}) packing.
        This method unpacks and extracts URLs.
        """
        urls: List[str] = []

        expanded = self._parser.unpacker.unpack_all(html)

        # Search for video URLs in unpacked content
        video_url_patterns = [
            re.compile(r'(?:file|src|source|url)\s*[:=]\s*"(https?://[^"]+\.(?:mp4|m3u8)[^"]*)"', re.I),
            re.compile(r'(?:file|src|source|url)\s*[:=]\s*\'(https?://[^\']+\.(?:mp4|m3u8)[^\']*)\'' , re.I),
            re.compile(r'"(https?://[^"]+\.(?:mp4|m3u8)\?[^"]*)"', re.I),
            re.compile(r"'(https?://[^']+\.(?:mp4|m3u8)\?[^']*)'", re.I),
            re.compile(r'(https?://[^\s"\'<>]+\.m3u8(?:\?[^\s"\'<>]*)?)', re.I),
            re.compile(r'(https?://[^\s"\'<>]+\.mp4(?:\?[^\s"\'<>]*)?)', re.I),
        ]

        for pattern in video_url_patterns:
            for match in pattern.finditer(expanded):
                url = match.group(1).strip()
                url = normalize_url(url, base_url)
                if url.startswith("http") and url not in urls:
                    urls.append(url)

        return urls

    def _extract_sources_json(self, html: str) -> List[Dict[str, Any]]:
        """
        Common utility: extract sources from JSON-like structures in HTML.

        Looks for patterns like:
            sources: [{file: "...", label: "720p"}]
            {sources: [{src: "..."}]}
        """
        results: List[Dict[str, Any]] = []

        sources_patterns = [
            # sources: [{file: "...", label: "..."}]
            re.compile(
                r'sources\s*[:=]\s*\[\s*(\{[\s\S]*?\})\s*\]',
                re.I,
            ),
            # {file: "...", label: "..."} within sources array
            re.compile(
                r'sources\s*[:=]\s*(\[[\s\S]*?\])',
                re.I,
            ),
        ]

        for pattern in sources_patterns:
            for match in pattern.finditer(html):
                raw = match.group(1)

                # Try parsing as JSON array
                if raw.startswith("["):
                    parsed = safe_json_loads(raw)
                    if isinstance(parsed, list):
                        for item in parsed:
                            if isinstance(item, dict):
                                results.append(item)
                        continue

                # Try wrapping single object in array
                if raw.startswith("{"):
                    # There might be multiple objects
                    obj_pattern = re.compile(r'\{[^{}]+\}')
                    for obj_match in obj_pattern.finditer(raw):
                        parsed = safe_json_loads(obj_match.group(0))
                        if isinstance(parsed, dict):
                            results.append(parsed)

        return results


class GenericHandler(ServerHandler):
    """
    Generic fallback handler for unknown or unsupported servers.

    Uses broad heuristics to extract video URLs from any embed page:
        1. Parse the page with the adaptive parser
        2. Follow any iframes found
        3. Scan scripts for video URLs
        4. Try common API endpoint patterns

    This is the handler of last resort — lower confidence but wide coverage.
    """

    def get_name(self) -> str:
        return "GenericHandler"

    def get_priority(self) -> int:
        return 10  # lowest priority — only used as fallback

    def can_handle(self, server: ServerInfo) -> bool:
        """Generic handler can attempt any server."""
        return True

    def _extract_impl(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
    ) -> List[VideoStream]:
        streams: List[VideoStream] = []
        embed_url = server.url

        # ── Phase 1: Fetch embed page ────────────────────────────────
        if not embed_url.startswith("http"):
            self._log.debug("GenericHandler: No valid HTTP URL, skipping fetch")
            return streams

        html = page_html
        if not html:
            html = self._fetch_embed_page(embed_url, referer=self._config.base_url)

        if not html:
            self._log.warning("GenericHandler: Failed to fetch embed page")
            return streams

        # ── Phase 2: Direct video URL scan ───────────────────────────
        packed_urls = self._extract_packed_js_urls(html, embed_url)
        for url in packed_urls:
            streams.append(self._create_stream(
                url=url,
                server=server,
                confidence=55,
                metadata={"extraction_method": "generic:packed_js"},
            ))

        # ── Phase 3: Source JSON extraction ──────────────────────────
        sources = self._extract_sources_json(html)
        for src in sources:
            url = src.get("file") or src.get("src") or src.get("url") or ""
            label = src.get("label") or src.get("quality") or ""

            if url and url.startswith("http"):
                quality = Quality.UNKNOWN
                if label:
                    label_str = str(label).lower()
                    if "1080" in label_str:
                        quality = Quality.Q1080
                    elif "720" in label_str:
                        quality = Quality.Q720
                    elif "480" in label_str:
                        quality = Quality.Q480
                    elif "360" in label_str:
                        quality = Quality.Q360

                streams.append(self._create_stream(
                    url=url,
                    server=server,
                    confidence=65,
                    quality=quality,
                    metadata={
                        "extraction_method": "generic:sources_json",
                        "label": label,
                    },
                ))

        # ── Phase 4: Full adaptive parse ─────────────────────────────
        parse_result = self._parser.parse_page(html, embed_url)

        for finding in parse_result.get("video_urls", []):
            url = finding.get("url", "")
            if url and url.startswith("http"):
                # Avoid duplicates
                if any(s.url == url for s in streams):
                    continue
                streams.append(self._create_stream(
                    url=url,
                    server=server,
                    confidence=finding.get("confidence", 45),
                    metadata={
                        "extraction_method": f"generic:{finding.get('pattern', 'parse')}",
                        "was_obfuscated": finding.get("was_obfuscated", False),
                    },
                ))

        # ── Phase 5: Follow iframes (one level deep) ────────────────
        iframes = parse_result.get("iframes", [])
        for iframe in iframes[:3]:  # limit to 3 iframes
            iframe_url = iframe.get("url", "")
            if not iframe_url.startswith("http"):
                continue

            classification = iframe.get("classification", {})
            if classification.get("is_ad", False):
                continue

            self._log.debug(f"GenericHandler: Following iframe → {iframe_url[:70]}")

            iframe_html = self._fetch_embed_page(iframe_url, referer=embed_url)
            if not iframe_html:
                continue

            # Scan iframe page for video URLs
            iframe_urls = self._extract_packed_js_urls(iframe_html, iframe_url)
            for url in iframe_urls:
                if any(s.url == url for s in streams):
                    continue
                streams.append(self._create_stream(
                    url=url,
                    server=server,
                    confidence=50,
                    metadata={
                        "extraction_method": "generic:iframe_follow",
                        "iframe_url": iframe_url,
                        "follow_depth": 1,
                    },
                ))

            # Also check iframe sources JSON
            iframe_sources = self._extract_sources_json(iframe_html)
            for src in iframe_sources:
                url = src.get("file") or src.get("src") or src.get("url") or ""
                if url and url.startswith("http"):
                    if any(s.url == url for s in streams):
                        continue
                    streams.append(self._create_stream(
                        url=url,
                        server=server,
                        confidence=55,
                        metadata={"extraction_method": "generic:iframe_sources"},
                    ))

        # ── Phase 6: Common embed API patterns ──────────────────────
        api_streams = self._try_common_apis(embed_url, html, server)
        for stream in api_streams:
            if not any(s.url == stream.url for s in streams):
                streams.append(stream)

        return streams

    def _try_common_apis(
        self,
        embed_url: str,
        html: str,
        server: ServerInfo,
    ) -> List[VideoStream]:
        """
        Try common video server API patterns.

        Many servers expose a POST/GET API to retrieve the actual stream URL.
        """
        streams: List[VideoStream] = []

        # Extract video ID from URL
        video_id = self._extract_video_id(embed_url)
        if not video_id:
            return streams

        parsed = urllib.parse.urlparse(embed_url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        # Common API endpoints to try
        api_patterns = [
            f"{base}/api/source/{video_id}",
            f"{base}/ajax/embed/{video_id}",
            f"{base}/dl/{video_id}",
        ]

        for api_url in api_patterns:
            self._log.debug(f"GenericHandler: Trying API → {api_url}")

            response = self._http.post(
                api_url,
                data={"r": self._config.base_url, "d": parsed.netloc},
                referer=embed_url,
            )

            if not response:
                continue

            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError):
                continue

            # Parse API response for video URLs
            api_urls = self._parse_api_response(data)
            for url_info in api_urls:
                url = url_info.get("url", "")
                if url and url.startswith("http"):
                    streams.append(self._create_stream(
                        url=url,
                        server=server,
                        confidence=70,
                        quality=url_info.get("quality", Quality.UNKNOWN),
                        metadata={
                            "extraction_method": "generic:api",
                            "api_url": api_url,
                            "label": url_info.get("label", ""),
                        },
                    ))

            if streams:
                break  # Found streams, stop trying other APIs

        return streams

    def _parse_api_response(self, data: Any) -> List[Dict[str, Any]]:
        """Parse a video server API response for stream URLs."""
        results: List[Dict[str, Any]] = []

        if isinstance(data, dict):
            # Check for "data" key containing sources
            sources = data.get("data") or data.get("sources") or data.get("result")

            if isinstance(sources, list):
                for item in sources:
                    if isinstance(item, dict):
                        url = item.get("file") or item.get("src") or item.get("url") or ""
                        label = str(item.get("label") or item.get("quality") or "")
                        quality = self._label_to_quality(label)
                        if url:
                            results.append({"url": url, "quality": quality, "label": label})

            elif isinstance(sources, str) and sources.startswith("http"):
                results.append({"url": sources, "quality": Quality.UNKNOWN, "label": ""})

            # Also check top-level URL keys
            for key in ("file", "src", "url", "source", "stream"):
                val = data.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    results.append({"url": val, "quality": Quality.UNKNOWN, "label": key})

        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    url = item.get("file") or item.get("src") or item.get("url") or ""
                    label = str(item.get("label") or item.get("quality") or "")
                    if url:
                        results.append({
                            "url": url,
                            "quality": self._label_to_quality(label),
                            "label": label,
                        })

        return results

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        """Extract a video ID from common embed URL formats."""
        patterns = [
            re.compile(r'/(?:e|v|embed|d|f|play|watch)/([a-zA-Z0-9]+)'),
            re.compile(r'/embed-([a-zA-Z0-9]+)'),
            re.compile(r'[?&](?:v|id|video)=([a-zA-Z0-9]+)'),
            re.compile(r'/([a-zA-Z0-9]{8,})(?:\.html?)?$'),
        ]
        for pattern in patterns:
            match = pattern.search(url)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _label_to_quality(label: str) -> Quality:
        """Convert a quality label string to Quality enum."""
        label_lower = label.lower().strip()
        if "1080" in label_lower:
            return Quality.Q1080
        if "720" in label_lower:
            return Quality.Q720
        if "480" in label_lower:
            return Quality.Q480
        if "360" in label_lower:
            return Quality.Q360
        if "4k" in label_lower or "2160" in label_lower:
            return Quality.Q4K
        return Quality.UNKNOWN


class DoodStreamHandler(ServerHandler):
    """
    Handler for DoodStream and its domain variants.

    DoodStream extraction flow:
        1. Fetch the embed page
        2. Find the /pass_md5/ URL in the page JavaScript
        3. Request the /pass_md5/ endpoint to get a token
        4. Construct the final video URL using the token + timestamp

    DoodStream uses a time-sensitive token system:
        - The /pass_md5/ response gives a base URL
        - A random string + timestamp must be appended
        - The Referer header must match the embed page
    """

    # Patterns for finding the /pass_md5/ URL
    _PASS_MD5_PATTERNS: List[re.Pattern] = [
        # $.get('/pass_md5/...', function(data) { ... })
        re.compile(
            r"\$\.get\s*\(\s*['\"](/pass_md5/[^'\"]+)['\"]",
            re.I,
        ),
        # fetch('/pass_md5/...')
        re.compile(
            r"fetch\s*\(\s*['\"](/pass_md5/[^'\"]+)['\"]",
            re.I,
        ),
        # Generic string match
        re.compile(
            r"['\"](/pass_md5/[a-zA-Z0-9/\-_]+)['\"]",
            re.I,
        ),
        # Concatenated path: '/pass_md5/' + id + '/' + token
        re.compile(
            r"/pass_md5/[\w\-]+/[\w\-]+",
            re.I,
        ),
    ]

    # Pattern for the token construction logic
    _TOKEN_FUNCTION_PATTERN = re.compile(
        r'function\s+makePlay\s*\(\)\s*\{[\s\S]*?\}',
        re.I,
    )

    # DoodStream domain pattern
    _DOOD_DOMAIN = re.compile(
        r'(?:dood(?:stream)?|ds2play|do0od|doods)\.\w+',
        re.I,
    )

    def get_name(self) -> str:
        return "DoodStreamHandler"

    def get_priority(self) -> int:
        return 70

    def can_handle(self, server: ServerInfo) -> bool:
        """Check if this server is a DoodStream variant."""
        if server.server_type == "doodstream":
            return True
        if self._DOOD_DOMAIN.search(server.url):
            return True
        fp = ServerFingerprintDatabase.find_by_name("doodstream")
        if fp and fp.matches_url(server.url):
            return True
        return False

    def _extract_impl(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
    ) -> List[VideoStream]:
        streams: List[VideoStream] = []
        embed_url = server.url

        # Ensure we have an embed URL
        if not embed_url.startswith("http"):
            self._log.warning("DoodStream: No valid embed URL")
            return streams

        # Normalize to /e/ embed format
        embed_url = self._normalize_embed_url(embed_url)
        self._log.debug(f"DoodStream embed URL: {embed_url}")

        # ── Step 1: Fetch embed page ─────────────────────────────────
        html = page_html
        if not html:
            html = self._fetch_embed_page(embed_url, referer=self._config.base_url)

        if not html:
            self._log.warning("DoodStream: Failed to fetch embed page")
            return streams

        # ── Step 2: Find /pass_md5/ URL ──────────────────────────────
        pass_md5_path = self._find_pass_md5(html)
        if not pass_md5_path:
            self._log.warning("DoodStream: Could not find /pass_md5/ URL")
            # Fallback: try packed JS extraction
            fallback_urls = self._extract_packed_js_urls(html, embed_url)
            for url in fallback_urls:
                streams.append(self._create_stream(
                    url=url,
                    server=server,
                    confidence=40,
                    metadata={"extraction_method": "doodstream:fallback_packed"},
                ))
            return streams

        # ── Step 3: Request /pass_md5/ token ─────────────────────────
        parsed = urllib.parse.urlparse(embed_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        pass_md5_url = f"{base}{pass_md5_path}"

        self._log.debug(f"DoodStream pass_md5 URL: {pass_md5_url}")

        response = self._http.get(
            pass_md5_url,
            referer=embed_url,
        )

        if not response:
            self._log.warning("DoodStream: /pass_md5/ request failed")
            return streams

        token_base = response.text.strip()
        self._log.debug(f"DoodStream token base: {token_base[:80]}...")

        if not token_base.startswith("http"):
            self._log.warning(f"DoodStream: Unexpected token response: {token_base[:50]}")
            return streams

        # ── Step 4: Construct final video URL ────────────────────────
        random_str = self._generate_random_string(10)
        timestamp = int(time.time() * 1000)

        # The final URL format: {token_base}{random_string}?token={md5}&expiry={ts}
        # Some variants just append the random string
        final_url = f"{token_base}{random_str}?token={random_str}&expiry={timestamp}"

        # Also try simpler format
        simple_url = f"{token_base}{random_str}"

        # Add both candidates
        streams.append(self._create_stream(
            url=final_url,
            server=server,
            confidence=70,
            fmt=StreamFormat.MP4,
            extra_headers={
                "Referer": embed_url,
            },
            metadata={
                "extraction_method": "doodstream:pass_md5",
                "token_base": token_base[:50],
                "time_sensitive": True,
            },
        ))

        if simple_url != final_url:
            streams.append(self._create_stream(
                url=simple_url,
                server=server,
                confidence=60,
                fmt=StreamFormat.MP4,
                extra_headers={
                    "Referer": embed_url,
                },
                metadata={
                    "extraction_method": "doodstream:pass_md5_simple",
                    "time_sensitive": True,
                },
            ))

        return streams

    def _find_pass_md5(self, html: str) -> Optional[str]:
        """Find the /pass_md5/ URL path in the page."""
        # First try unpacked content
        expanded = self._parser.unpacker.unpack_all(html)

        for pattern in self._PASS_MD5_PATTERNS:
            match = pattern.search(expanded)
            if match:
                path = match.group(1) if match.lastindex else match.group(0)
                # Clean up the path
                path = path.strip("'\" ")
                if path.startswith("/pass_md5/"):
                    self._log.debug(f"DoodStream: Found pass_md5 path: {path}")
                    return path

        # Direct search in raw HTML
        for pattern in self._PASS_MD5_PATTERNS:
            match = pattern.search(html)
            if match:
                path = match.group(1) if match.lastindex else match.group(0)
                path = path.strip("'\" ")
                if path.startswith("/pass_md5/"):
                    return path

        return None

    def _normalize_embed_url(self, url: str) -> str:
        """Normalize DoodStream URL to /e/ embed format."""
        # Replace /d/ with /e/
        url = re.sub(r'/d/', '/e/', url)
        # Ensure no trailing parameters interfere
        return url

    def _build_referer(self, server: ServerInfo) -> str:
        """DoodStream requires exact embed page as referer."""
        if server.url.startswith("http"):
            return server.url
        return super()._build_referer(server)

    @staticmethod
    def _generate_random_string(length: int = 10) -> str:
        """Generate a random alphanumeric string for token construction."""
        chars = string.ascii_letters + string.digits
        return "".join(random.choices(chars, k=length))


class StreamSBHandler(ServerHandler):
    """
    Handler for StreamSB (and variants: SBEmbed, WatchSB, EmbedSito, etc).

    StreamSB extraction flow:
        1. Fetch the embed page
        2. Extract the video ID from the URL
        3. Construct an API request using the hex-encoded ID
        4. Parse the API response for stream URLs

    StreamSB uses a hex-encoding scheme:
        - The video ID is converted to hex
        - Prepended with a magic string
        - Sent to a /sources/ or /e/ endpoint
    """

    # StreamSB domain pattern
    _SB_DOMAIN = re.compile(
        r'(?:stream|embed|watch|play|tube|cloud)sb\.\w+|'
        r'embedsito\.\w+|lvturbo\.\w+|sbface\.\w+|sbrity\.\w+',
        re.I,
    )

    # Magic prefixes used for API construction
    _MAGIC_PREFIXES: List[str] = [
        "673535303966363fe363636393635363",
        "63363635363736353",
        "6272696e676974746f6d65",
    ]

    # ID extraction patterns
    _ID_PATTERNS: List[re.Pattern] = [
        re.compile(r'/e/([a-zA-Z0-9]+)'),
        re.compile(r'/embed-([a-zA-Z0-9]+)'),
        re.compile(r'/d/([a-zA-Z0-9]+)'),
        re.compile(r'/play/([a-zA-Z0-9]+)'),
    ]

    # Source extraction patterns from API response pages
    _SOURCE_PATTERNS: List[re.Pattern] = [
        re.compile(r'sources\s*:\s*\[\s*\{\s*file\s*:\s*"([^"]+)"', re.I),
        re.compile(r'"stream_data"\s*:\s*\{\s*"file"\s*:\s*"([^"]+)"', re.I),
        re.compile(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', re.I),
        re.compile(r'(https?://[^\s"\'<>]+\.mp4[^\s"\'<>]*)', re.I),
    ]

    def get_name(self) -> str:
        return "StreamSBHandler"

    def get_priority(self) -> int:
        return 65

    def can_handle(self, server: ServerInfo) -> bool:
        """Check if this server is a StreamSB variant."""
        if server.server_type == "streamsb":
            return True
        if self._SB_DOMAIN.search(server.url):
            return True
        fp = ServerFingerprintDatabase.find_by_name("streamsb")
        if fp and fp.matches_url(server.url):
            return True
        return False

    def _extract_impl(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
    ) -> List[VideoStream]:
        streams: List[VideoStream] = []
        embed_url = server.url

        if not embed_url.startswith("http"):
            return streams

        # ── Step 1: Extract video ID ─────────────────────────────────
        video_id = self._extract_sb_id(embed_url)
        if not video_id:
            self._log.warning("StreamSB: Could not extract video ID")
            return streams

        self._log.debug(f"StreamSB video ID: {video_id}")

        # ── Step 2: Fetch embed page ─────────────────────────────────
        html = page_html
        if not html:
            html = self._fetch_embed_page(embed_url, referer=self._config.base_url)

        parsed_origin = urllib.parse.urlparse(embed_url)
        base = f"{parsed_origin.scheme}://{parsed_origin.netloc}"

        # ── Step 3: Try hex-encoded API requests ─────────────────────
        hex_id = video_id.encode("utf-8").hex()

        for prefix in self._MAGIC_PREFIXES:
            api_path = f"/sources{prefix}{hex_id}"
            api_url = f"{base}{api_path}"

            self._log.debug(f"StreamSB: Trying API → {api_url[:80]}")

            response = self._http.get(
                api_url,
                referer=embed_url,
                headers={
                    "watchsb": "sbstream",
                },
            )

            if not response:
                continue

            # Try parsing as JSON
            try:
                data = response.json()
                stream_data = data.get("stream_data", {})
                file_url = stream_data.get("file", "")

                if file_url and file_url.startswith("http"):
                    streams.append(self._create_stream(
                        url=file_url,
                        server=server,
                        confidence=75,
                        extra_headers={
                            "Referer": embed_url,
                            "watchsb": "sbstream",
                        },
                        metadata={
                            "extraction_method": "streamsb:hex_api",
                            "api_url": api_url,
                            "prefix_used": prefix[:20],
                        },
                    ))
                    break  # Found a stream, stop trying prefixes

                # Check for backup/alternate keys
                for key in ("file", "backup", "sfile"):
                    url_val = stream_data.get(key, "")
                    if url_val and url_val.startswith("http"):
                        streams.append(self._create_stream(
                            url=url_val,
                            server=server,
                            confidence=70,
                            extra_headers={
                                "Referer": embed_url,
                                "watchsb": "sbstream",
                            },
                            metadata={
                                "extraction_method": f"streamsb:hex_api:{key}",
                            },
                        ))

                if streams:
                    break

            except (json.JSONDecodeError, ValueError, AttributeError):
                # Response wasn't JSON — try parsing as HTML
                response_text = response.text
                for src_pattern in self._SOURCE_PATTERNS:
                    match = src_pattern.search(response_text)
                    if match:
                        url = match.group(1)
                        if url.startswith("http"):
                            streams.append(self._create_stream(
                                url=url,
                                server=server,
                                confidence=60,
                                extra_headers={"Referer": embed_url},
                                metadata={"extraction_method": "streamsb:api_html_scan"},
                            ))

                if streams:
                    break

        # ── Step 4: Fallback — parse embed page directly ─────────────
        if not streams and html:
            self._log.debug("StreamSB: API extraction failed, trying direct parse")

            packed_urls = self._extract_packed_js_urls(html, embed_url)
            for url in packed_urls:
                streams.append(self._create_stream(
                    url=url,
                    server=server,
                    confidence=45,
                    metadata={"extraction_method": "streamsb:fallback_packed"},
                ))

            # Also try source JSON patterns
            for pattern in self._SOURCE_PATTERNS:
                for match in pattern.finditer(html):
                    url = match.group(1)
                    if url.startswith("http") and not any(s.url == url for s in streams):
                        streams.append(self._create_stream(
                            url=url,
                            server=server,
                            confidence=50,
                            metadata={"extraction_method": "streamsb:direct_scan"},
                        ))

        return streams

    def _extract_sb_id(self, url: str) -> Optional[str]:
        """Extract the video ID from a StreamSB URL."""
        for pattern in self._ID_PATTERNS:
            match = pattern.search(url)
            if match:
                return match.group(1)
        return None


class FileMoonHandler(ServerHandler):
    """
    Handler for FileMoon and its domain variants.

    FileMoon extraction flow:
        1. Fetch the embed page
        2. Find and unpack the P.A.C.K.E.R. obfuscated JavaScript
        3. Extract the HLS (m3u8) URL from the unpacked code
        4. Return the stream URL with appropriate headers

    FileMoon typically serves HLS streams and wraps the player
    configuration inside eval(function(p,a,c,k,e,d){...}) blocks.
    """

    _FILEMOON_DOMAIN = re.compile(
        r'(?:filemoon|kerapoxy|moonmov)\.\w+',
        re.I,
    )

    # Patterns for finding m3u8 URLs in unpacked content
    _HLS_PATTERNS: List[re.Pattern] = [
        re.compile(r'file\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"', re.I),
        re.compile(r'src\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"', re.I),
        re.compile(r'source\s*:\s*"(https?://[^"]+\.m3u8[^"]*)"', re.I),
        re.compile(r'"(https?://[^"]+\.m3u8(?:\?[^"]*)?)"', re.I),
        re.compile(r"'(https?://[^']+\.m3u8(?:\?[^']*)?)'", re.I),
    ]

    def get_name(self) -> str:
        return "FileMoonHandler"

    def get_priority(self) -> int:
        return 72

    def can_handle(self, server: ServerInfo) -> bool:
        """Check if this server is a FileMoon variant."""
        if server.server_type == "filemoon":
            return True
        if self._FILEMOON_DOMAIN.search(server.url):
            return True
        fp = ServerFingerprintDatabase.find_by_name("filemoon")
        if fp and fp.matches_url(server.url):
            return True
        return False

    def _extract_impl(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
    ) -> List[VideoStream]:
        streams: List[VideoStream] = []
        embed_url = server.url

        if not embed_url.startswith("http"):
            return streams

        # ── Step 1: Fetch embed page ─────────────────────────────────
        html = page_html
        if not html:
            html = self._fetch_embed_page(embed_url, referer=self._config.base_url)

        if not html:
            self._log.warning("FileMoon: Failed to fetch embed page")
            return streams

        # ── Step 2: Unpack P.A.C.K.E.R. JS ──────────────────────────
        expanded = self._parser.unpacker.unpack_all(html)

        # ── Step 3: Extract HLS URLs ────────────────────────────────
        found_urls: List[str] = []

        for pattern in self._HLS_PATTERNS:
            for match in pattern.finditer(expanded):
                url = match.group(1).strip()
                if url.startswith("http") and url not in found_urls:
                    found_urls.append(url)

        # Also try generic packed JS extraction
        packed_urls = self._extract_packed_js_urls(html, embed_url)
        for url in packed_urls:
            if url not in found_urls:
                found_urls.append(url)

        # ── Step 4: Build streams ────────────────────────────────────
        for url in found_urls:
            fmt = detect_stream_format(url)
            confidence = 75 if fmt == StreamFormat.M3U8 else 60

            streams.append(self._create_stream(
                url=url,
                server=server,
                confidence=confidence,
                fmt=fmt,
                extra_headers={"Referer": embed_url},
                metadata={
                    "extraction_method": "filemoon:packed_js",
                    "was_obfuscated": True,
                },
            ))

        # ── Step 5: Fallback — eval content scan ─────────────────────
        if not streams:
            self._log.debug("FileMoon: Primary extraction failed, trying eval scan")

            eval_pattern = re.compile(r'eval\s*\((.*)\)', re.S)
            for match in eval_pattern.finditer(html):
                eval_content = match.group(1)
                for hls_pattern in self._HLS_PATTERNS:
                    url_match = hls_pattern.search(eval_content)
                    if url_match:
                        url = url_match.group(1).strip()
                        if url.startswith("http"):
                            streams.append(self._create_stream(
                                url=url,
                                server=server,
                                confidence=55,
                                extra_headers={"Referer": embed_url},
                                metadata={"extraction_method": "filemoon:eval_scan"},
                            ))

        return streams


class MixDropHandler(ServerHandler):
    """
    Handler for MixDrop and its domain variants.

    MixDrop extraction flow:
        1. Fetch the embed page
        2. Unpack P.A.C.K.E.R. obfuscated JavaScript
        3. Find MDCore.wurl or similar variables containing the video URL
        4. Resolve protocol-relative URLs (//...) to HTTPS

    MixDrop typically uses packed JS that sets:
        MDCore.wurl = "//cdn-xxx.mixdrop.xx/v/xxxxx.mp4?...";
    """

    _MIXDROP_DOMAIN = re.compile(
        r'(?:mixdrop|mixdrp|mdbekjwqa|mdhowto)\.\w+',
        re.I,
    )

    # Patterns for MixDrop video URL variables
    _MDCORE_PATTERNS: List[re.Pattern] = [
        re.compile(r'MDCore\.wurl\s*=\s*"([^"]+)"', re.I),
        re.compile(r'MDCore\.vsrc\s*=\s*"([^"]+)"', re.I),
        re.compile(r'MDCore\.vurl\s*=\s*"([^"]+)"', re.I),
        re.compile(r'\bwurl\s*=\s*"([^"]+)"', re.I),
        re.compile(r'\bvsrc\d?\s*=\s*"([^"]+\.mp4[^"]*)"', re.I),
        re.compile(r'source\s*:\s*"(//[^"]+)"', re.I),
        re.compile(r'"(//[a-z0-9\-]+\.(?:mixdrop|mixdrp)\.\w+/[^"]+\.mp4[^"]*)"', re.I),
    ]

    def get_name(self) -> str:
        return "MixDropHandler"

    def get_priority(self) -> int:
        return 70

    def can_handle(self, server: ServerInfo) -> bool:
        """Check if this server is a MixDrop variant."""
        if server.server_type == "mixdrop":
            return True
        if self._MIXDROP_DOMAIN.search(server.url):
            return True
        fp = ServerFingerprintDatabase.find_by_name("mixdrop")
        if fp and fp.matches_url(server.url):
            return True
        return False

    def _extract_impl(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
    ) -> List[VideoStream]:
        streams: List[VideoStream] = []
        embed_url = server.url

        if not embed_url.startswith("http"):
            return streams

        # ── Step 1: Fetch embed page ─────────────────────────────────
        html = page_html
        if not html:
            html = self._fetch_embed_page(embed_url, referer=self._config.base_url)

        if not html:
            self._log.warning("MixDrop: Failed to fetch embed page")
            return streams

        # ── Step 2: Unpack ───────────────────────────────────────────
        expanded = self._parser.unpacker.unpack_all(html)

        # ── Step 3: Find MDCore URLs ─────────────────────────────────
        found_urls: List[str] = []

        for pattern in self._MDCORE_PATTERNS:
            for match in pattern.finditer(expanded):
                url = match.group(1).strip()
                url = normalize_url(url, embed_url)
                if url.startswith("http") and url not in found_urls:
                    found_urls.append(url)

        # ── Step 4: Also try generic extraction ──────────────────────
        packed_urls = self._extract_packed_js_urls(html, embed_url)
        for url in packed_urls:
            if url not in found_urls:
                found_urls.append(url)

        # ── Step 5: Build streams ────────────────────────────────────
        for url in found_urls:
            fmt = detect_stream_format(url)
            # MixDrop typically serves MP4
            if fmt == StreamFormat.UNKNOWN and ".mp4" not in url.lower():
                fmt = StreamFormat.MP4

            confidence = 72 if "mixdrop" in url.lower() or "mixdrp" in url.lower() else 55

            streams.append(self._create_stream(
                url=url,
                server=server,
                confidence=confidence,
                fmt=fmt,
                extra_headers={"Referer": embed_url},
                metadata={
                    "extraction_method": "mixdrop:mdcore",
                    "was_obfuscated": True,
                },
            ))

        return streams


class StreamTapeHandler(ServerHandler):
    """
    Handler for StreamTape and its domain variants.

    StreamTape extraction flow:
        1. Fetch the embed page
        2. Find the obfuscated download URL in JavaScript
        3. Reconstruct the URL from split string fragments
        4. The final URL is typically at /get_video?...

    StreamTape obfuscates the video URL by splitting it across
    multiple JS string operations and innerHTML assignments.
    """

    _STREAMTAPE_DOMAIN = re.compile(
        r'(?:streamtape|strtape|strcloud|streamadblockplus|stape)\.\w+',
        re.I,
    )

    # Patterns for finding the video URL in StreamTape
    _URL_PATTERNS: List[re.Pattern] = [
        # document.getElementById('...').innerHTML = '<a href="..."'
        re.compile(
            r"innerHTML\s*=\s*[\"'].*?(?:https?:)?//[^\"']*?/get_video\?[^\"']*",
            re.I | re.S,
        ),
        # Reconstructed URL via string concatenation
        re.compile(
            r"'(//[^']*streamtape[^']*?/get_video\?[^']*)'",
            re.I,
        ),
        # Two-part construction: base + token
        re.compile(
            r"(?:var|let|const)\s+\w+\s*=\s*['\"]([^'\"]*?/get_video\?[^'\"]*)['\"]",
            re.I,
        ),
        # Direct get_video URL
        re.compile(
            r"((?:https?:)?//[^\s'\"<>]+/get_video\?[^\s'\"<>]+)",
            re.I,
        ),
    ]

    # Token extraction patterns
    _TOKEN_PATTERNS: List[re.Pattern] = [
        # document.getElementById('robotlink').innerHTML = ... + token
        re.compile(
            r"getElementById\s*\(\s*['\"](?:robotlink|norobotlink)['\"]"
            r"\s*\)[\s\S]*?innerHTML\s*=\s*([\s\S]*?)(?:;|\n)",
            re.I,
        ),
        # var token = '...' or token assignment
        re.compile(
            r"(?:var|let|const)\s+\w*token\w*\s*=\s*['\"]([^'\"]+)['\"]",
            re.I,
        ),
    ]

    def get_name(self) -> str:
        return "StreamTapeHandler"

    def get_priority(self) -> int:
        return 73

    def can_handle(self, server: ServerInfo) -> bool:
        if server.server_type == "streamtape":
            return True
        if self._STREAMTAPE_DOMAIN.search(server.url):
            return True
        fp = ServerFingerprintDatabase.find_by_name("streamtape")
        if fp and fp.matches_url(server.url):
            return True
        return False

    def _extract_impl(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
    ) -> List[VideoStream]:
        streams: List[VideoStream] = []
        embed_url = server.url

        if not embed_url.startswith("http"):
            return streams

        # ── Step 1: Fetch embed page ─────────────────────────────────
        html = page_html
        if not html:
            html = self._fetch_embed_page(embed_url, referer=self._config.base_url)

        if not html:
            self._log.warning("StreamTape: Failed to fetch embed page")
            return streams

        # ── Step 2: Extract video URL ────────────────────────────────
        video_url = self._extract_streamtape_url(html, embed_url)

        if video_url:
            streams.append(self._create_stream(
                url=video_url,
                server=server,
                confidence=75,
                fmt=StreamFormat.MP4,
                extra_headers={"Referer": embed_url},
                metadata={
                    "extraction_method": "streamtape:url_reconstruct",
                },
            ))
        else:
            self._log.warning("StreamTape: Primary extraction failed")

        # ── Step 3: Fallback — generic packed extraction ─────────────
        if not streams:
            packed_urls = self._extract_packed_js_urls(html, embed_url)
            for url in packed_urls:
                streams.append(self._create_stream(
                    url=url,
                    server=server,
                    confidence=45,
                    metadata={"extraction_method": "streamtape:fallback"},
                ))

        return streams

    def _extract_streamtape_url(self, html: str, embed_url: str) -> Optional[str]:
        """
        Extract the video URL from StreamTape's obfuscated JS.

        StreamTape typically constructs the URL in two parts:
            1. A base URL containing /get_video?id=...&expires=...
            2. A token appended via string concatenation
        """
        # Strategy 1: Find the inner HTML assignment with get_video URL
        # Look for the pattern: document.getElementById('robotlink').innerHTML
        inner_html_pattern = re.compile(
            r"getElementById\s*\(\s*['\"](?:robotlink|norobotlink)['\"]"
            r"\s*\)\.innerHTML\s*=\s*['\"]?\s*(?:<a[^>]*href=[\"']?)?"
            r"\s*(//[^'\"<>\s]+/get_video\?[^'\"<>\s]*)",
            re.I | re.S,
        )

        match = inner_html_pattern.search(html)
        if match:
            url = "https:" + match.group(1).strip()
            self._log.debug(f"StreamTape: Found URL via innerHTML: {url[:70]}")

            # Check for token append
            token = self._find_appended_token(html, match.end())
            if token:
                url += token
                self._log.debug(f"StreamTape: Appended token: {token[:30]}")

            return url

        # Strategy 2: Direct get_video URL search
        for pattern in self._URL_PATTERNS:
            match = pattern.search(html)
            if match:
                raw = match.group(1) if match.lastindex else match.group(0)
                raw = raw.strip("'\" ")

                # Clean up HTML entities
                raw = raw.replace("&amp;", "&")

                url = normalize_url(raw, embed_url)
                if "/get_video" in url:
                    self._log.debug(f"StreamTape: Found URL via pattern: {url[:70]}")
                    return url

        # Strategy 3: Reconstruct from string concatenation
        concat_url = self._reconstruct_from_concat(html, embed_url)
        if concat_url:
            return concat_url

        return None

    def _find_appended_token(self, html: str, search_start: int) -> Optional[str]:
        """Find a token that's appended to the URL via string concatenation."""
        # Look for '+ token' or '+ "string"' near the innerHTML assignment
        remaining = html[search_start:search_start + 500]

        # Pattern: + 'tokenvalue' or + variableName
        append_pattern = re.compile(
            r"\+\s*(?:['\"]([^'\"]*)['\"]|(\w+))",
        )

        for match in append_pattern.finditer(remaining):
            if match.group(1) is not None:
                return match.group(1)
            elif match.group(2):
                # It's a variable name — try to find its value
                var_name = match.group(2)
                var_pattern = re.compile(
                    rf"(?:var|let|const)\s+{re.escape(var_name)}\s*=\s*['\"]([^'\"]*)['\"]"
                )
                var_match = var_pattern.search(html)
                if var_match:
                    return var_match.group(1)

        return None

    def _reconstruct_from_concat(
        self,
        html: str,
        embed_url: str,
    ) -> Optional[str]:
        """
        Try to reconstruct the URL from multiple string fragments.

        Some StreamTape versions split the URL across several variables.
        """
        # Find all variables that look like URL parts
        url_var_pattern = re.compile(
            r"(?:var|let|const)\s+(\w+)\s*=\s*['\"]([^'\"]*(?:/get_video|streamtape|token|id=)[^'\"]*)['\"]",
            re.I,
        )

        variables: Dict[str, str] = {}
        for match in url_var_pattern.finditer(html):
            variables[match.group(1)] = match.group(2)

        # Try to find one that looks like a full URL
        for _var_name, value in variables.items():
            if "/get_video?" in value:
                url = normalize_url(value, embed_url)
                if url.startswith("http"):
                    return url

        return None


# ─── Handler Registry ────────────────────────────────────────────────────────

class HandlerRegistry:
    """
    Central registry for all server handlers.

    Manages handler instantiation, lookup, and priority-based selection.
    Ensures the GenericHandler is always available as a fallback.
    """

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
        http_client: HttpClient,
        parser: AdaptiveParser,
    ) -> None:
        self._config = config
        self._log = logger
        self._http = http_client
        self._parser = parser
        self._handlers: List[ServerHandler] = []
        self._generic_handler: Optional[GenericHandler] = None

        self._register_all()

    def _register_all(self) -> None:
        """Register all built-in handlers."""
        handler_classes: List[Type[ServerHandler]] = [
            StreamTapeHandler,
            DoodStreamHandler,
            FileMoonHandler,
            MixDropHandler,
            StreamSBHandler,
            # GenericHandler registered separately as fallback
        ]

        for cls in handler_classes:
            try:
                handler = cls(self._config, self._log, self._http, self._parser)
                self._handlers.append(handler)
                self._log.debug(
                    f"Registered handler: {handler.get_name()} "
                    f"(priority={handler.get_priority()})"
                )
            except Exception as exc:
                self._log.warning(f"Failed to register handler {cls.__name__}: {exc}")

        # Sort by priority (highest first)
        self._handlers.sort(key=lambda h: h.get_priority(), reverse=True)

        # Register generic handler separately
        self._generic_handler = GenericHandler(
            self._config, self._log, self._http, self._parser
        )
        self._log.debug(
            f"Registered {len(self._handlers)} specific handler(s) + GenericHandler fallback"
        )

    def find_handler(self, server: ServerInfo) -> ServerHandler:
        """
        Find the best handler for a given server.

        Checks specific handlers first (by priority), falls back to GenericHandler.

        Args:
            server: The server to find a handler for.

        Returns:
            The best matching ServerHandler.
        """
        for handler in self._handlers:
            if handler.can_handle(server):
                self._log.debug(
                    f"Handler match: {handler.get_name()} → {server.name}"
                )
                return handler

        self._log.debug(f"No specific handler for '{server.name}', using GenericHandler")
        return self._generic_handler  # type: ignore[return-value]

    def find_all_handlers(self, server: ServerInfo) -> List[ServerHandler]:
        """
        Find ALL handlers that can handle a server (for retry chains).

        Returns handlers sorted by priority, with GenericHandler last.

        Args:
            server: The server to match.

        Returns:
            List of matching handlers.
        """
        matches: List[ServerHandler] = []

        for handler in self._handlers:
            if handler.can_handle(server):
                matches.append(handler)

        # Always include generic handler as final fallback
        if self._generic_handler:
            matches.append(self._generic_handler)

        return matches

    def get_handler_by_name(self, name: str) -> Optional[ServerHandler]:
        """Look up a handler by class name."""
        name_lower = name.lower()
        for handler in self._handlers:
            if handler.get_name().lower() == name_lower:
                return handler
        if self._generic_handler and self._generic_handler.get_name().lower() == name_lower:
            return self._generic_handler
        return None

    def extract_from_server(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
        try_all: bool = False,
    ) -> List[VideoStream]:
        """
        Extract video streams from a server using appropriate handler(s).

        Args:
            server: The server to extract from.
            page_html: Pre-fetched HTML.
            try_all: If True, try all matching handlers and combine results.

        Returns:
            List of extracted VideoStream objects.
        """
        if try_all:
            return self._extract_with_all(server, page_html)
        else:
            return self._extract_with_best(server, page_html)

    def _extract_with_best(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
    ) -> List[VideoStream]:
        """Extract using the single best handler."""
        handler = self.find_handler(server)
        return handler.extract(server, page_html)

    def _extract_with_all(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
    ) -> List[VideoStream]:
        """Extract using all matching handlers and combine results."""
        all_streams: List[VideoStream] = []
        seen_urls: Set[str] = set()

        handlers = self.find_all_handlers(server)
        self._log.debug(
            f"Trying {len(handlers)} handler(s) for '{server.name}'"
        )

        for handler in handlers:
            streams = handler.extract(server, page_html)
            for stream in streams:
                if stream.url not in seen_urls:
                    seen_urls.add(stream.url)
                    all_streams.append(stream)

            # If we got good results from a specific handler, skip generic
            if streams and handler.get_priority() > 10:
                high_confidence = any(s.confidence >= 65 for s in streams)
                if high_confidence:
                    self._log.debug(
                        f"High-confidence result from {handler.get_name()}, "
                        f"skipping remaining handlers"
                    )
                    break

        return all_streams

    def get_stats(self) -> Dict[str, Any]:
        """Return registry statistics."""
        return {
            "total_handlers": len(self._handlers) + 1,  # +1 for generic
            "specific_handlers": len(self._handlers),
            "handlers": [
                {
                    "name": h.get_name(),
                    "priority": h.get_priority(),
                }
                for h in self._handlers
            ] + [
                {
                    "name": "GenericHandler",
                    "priority": 10,
                    "role": "fallback",
                }
            ],
        }


# ═══════════════════════════════════════════════════════════════════════════════
# END OF PART 5
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# PART 6 — PATTERN LEARNER
# ═══════════════════════════════════════════════════════════════════════════════
#
# In-memory adaptive pattern learning system. Tracks successful extraction
# patterns and prioritizes them for future extractions. Enables the extractor
# to "remember" what worked and try those approaches first.
# ═══════════════════════════════════════════════════════════════════════════════


class PatternType(Enum):
    """Categories of learnable extraction patterns."""
    URL_REGEX = "url_regex"
    JS_VARIABLE = "js_variable"
    API_ENDPOINT = "api_endpoint"
    IFRAME_CHAIN = "iframe_chain"
    PACKED_JS = "packed_js"
    JSON_CONFIG = "json_config"
    DOM_SELECTOR = "dom_selector"
    NETWORK_INTERCEPT = "network_intercept"
    SERVER_PREFERENCE = "server_preference"
    HEADER_REQUIREMENT = "header_requirement"


@dataclass
class LearnedPattern:
    """
    A single learned extraction pattern.

    Records what worked, where it worked, how often it's been
    successful, and when it was last used.
    """
    pattern_id: str
    pattern_type: PatternType
    server_type: str                          # which server this applies to ("*" = any)
    pattern_data: Dict[str, Any]              # the actual pattern definition
    success_count: int = 0
    failure_count: int = 0
    total_attempts: int = 0
    first_seen: float = field(default_factory=time.time)
    last_success: float = 0.0
    last_failure: float = 0.0
    last_used: float = 0.0
    avg_confidence: float = 0.0               # average confidence of successful extractions
    tags: List[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        """Success rate as a fraction (0.0 to 1.0)."""
        if self.total_attempts == 0:
            return 0.0
        return self.success_count / self.total_attempts

    @property
    def reliability_score(self) -> float:
        """
        Composite reliability score considering:
            - Success rate
            - Total usage (more data = more reliable score)
            - Recency (recently successful patterns score higher)
            - Average confidence of results
        """
        if self.total_attempts == 0:
            return 0.0

        # Base: success rate (0-1)
        base = self.success_rate

        # Usage volume bonus: more attempts = more statistical confidence
        # Logarithmic scaling to avoid over-weighting high-volume patterns
        import math
        volume_factor = min(1.0, math.log(self.total_attempts + 1) / math.log(50))

        # Recency bonus: patterns successful in last hour score higher
        now = time.time()
        if self.last_success > 0:
            hours_since_success = (now - self.last_success) / 3600
            recency_factor = max(0.0, 1.0 - (hours_since_success / 24))  # decays over 24h
        else:
            recency_factor = 0.0

        # Confidence factor
        confidence_factor = self.avg_confidence / 100.0 if self.avg_confidence > 0 else 0.5

        # Weighted combination
        score = (
            base * 0.40 +
            volume_factor * 0.15 +
            recency_factor * 0.25 +
            confidence_factor * 0.20
        )

        return min(1.0, score)

    @property
    def is_stale(self) -> bool:
        """Check if pattern hasn't been successfully used in over 24 hours."""
        if self.last_success == 0:
            return True
        return (time.time() - self.last_success) > 86400  # 24 hours

    @property
    def is_unreliable(self) -> bool:
        """Check if pattern has a poor track record."""
        if self.total_attempts < 3:
            return False  # not enough data
        return self.success_rate < 0.25

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for storage/debugging."""
        return {
            "pattern_id": self.pattern_id,
            "pattern_type": self.pattern_type.value,
            "server_type": self.server_type,
            "pattern_data": self.pattern_data,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "total_attempts": self.total_attempts,
            "success_rate": round(self.success_rate, 3),
            "reliability_score": round(self.reliability_score, 3),
            "avg_confidence": round(self.avg_confidence, 1),
            "is_stale": self.is_stale,
            "is_unreliable": self.is_unreliable,
            "first_seen": self.first_seen,
            "last_success": self.last_success,
            "last_used": self.last_used,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LearnedPattern":
        """Deserialize from stored data."""
        return cls(
            pattern_id=data["pattern_id"],
            pattern_type=PatternType(data["pattern_type"]),
            server_type=data.get("server_type", "*"),
            pattern_data=data.get("pattern_data", {}),
            success_count=data.get("success_count", 0),
            failure_count=data.get("failure_count", 0),
            total_attempts=data.get("total_attempts", 0),
            first_seen=data.get("first_seen", time.time()),
            last_success=data.get("last_success", 0.0),
            last_failure=data.get("last_failure", 0.0),
            last_used=data.get("last_used", 0.0),
            avg_confidence=data.get("avg_confidence", 0.0),
            tags=data.get("tags", []),
        )


class PatternStore:
    """
    In-memory storage engine for learned patterns.

    Provides indexed access by pattern type, server type, and ID.
    Handles capacity management, eviction of stale/unreliable patterns,
    and optional persistence to disk.
    """

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
    ) -> None:
        self._config = config
        self._log = logger
        self._patterns: Dict[str, LearnedPattern] = {}            # id → pattern
        self._by_type: Dict[PatternType, List[str]] = {}          # type → [ids]
        self._by_server: Dict[str, List[str]] = {}                # server → [ids]
        self._max_patterns = config.max_cached_patterns
        self._dirty: bool = False                                  # has unsaved changes

        # Load persisted patterns if configured
        if config.pattern_cache_file:
            self._load_from_file(config.pattern_cache_file)

    # ── CRUD Operations ──────────────────────────────────────────────────

    def add(self, pattern: LearnedPattern) -> None:
        """
        Add or update a pattern in the store.

        If the pattern ID already exists, merges statistics.
        If capacity is exceeded, evicts the least reliable pattern.
        """
        existing = self._patterns.get(pattern.pattern_id)

        if existing:
            # Merge: update stats, keep the best data
            existing.success_count += pattern.success_count
            existing.failure_count += pattern.failure_count
            existing.total_attempts += pattern.total_attempts
            if pattern.last_success > existing.last_success:
                existing.last_success = pattern.last_success
            if pattern.last_failure > existing.last_failure:
                existing.last_failure = pattern.last_failure
            existing.last_used = max(existing.last_used, pattern.last_used)

            # Update average confidence (running average)
            if pattern.avg_confidence > 0 and pattern.success_count > 0:
                total_successes = existing.success_count
                if total_successes > 0:
                    existing.avg_confidence = (
                        (existing.avg_confidence * (total_successes - pattern.success_count) +
                         pattern.avg_confidence * pattern.success_count)
                        / total_successes
                    )

            # Merge tags
            for tag in pattern.tags:
                if tag not in existing.tags:
                    existing.tags.append(tag)

            self._dirty = True
            return

        # New pattern — check capacity
        if len(self._patterns) >= self._max_patterns:
            self._evict_one()

        # Store
        self._patterns[pattern.pattern_id] = pattern

        # Update indices
        self._by_type.setdefault(pattern.pattern_type, []).append(pattern.pattern_id)
        self._by_server.setdefault(pattern.server_type, []).append(pattern.pattern_id)

        self._dirty = True
        self._log.debug(
            f"Pattern stored: {pattern.pattern_id} "
            f"(type={pattern.pattern_type.value}, server={pattern.server_type})"
        )

    def get(self, pattern_id: str) -> Optional[LearnedPattern]:
        """Retrieve a pattern by ID."""
        return self._patterns.get(pattern_id)

    def remove(self, pattern_id: str) -> bool:
        """Remove a pattern by ID."""
        pattern = self._patterns.pop(pattern_id, None)
        if not pattern:
            return False

        # Clean indices
        type_list = self._by_type.get(pattern.pattern_type, [])
        if pattern_id in type_list:
            type_list.remove(pattern_id)

        server_list = self._by_server.get(pattern.server_type, [])
        if pattern_id in server_list:
            server_list.remove(pattern_id)

        self._dirty = True
        return True

    def clear(self) -> None:
        """Remove all patterns."""
        self._patterns.clear()
        self._by_type.clear()
        self._by_server.clear()
        self._dirty = True

    # ── Query Operations ─────────────────────────────────────────────────

    def find_by_type(
        self,
        pattern_type: PatternType,
        min_reliability: float = 0.0,
    ) -> List[LearnedPattern]:
        """
        Find patterns by type, optionally filtered by reliability.

        Returns patterns sorted by reliability score (best first).
        """
        ids = self._by_type.get(pattern_type, [])
        patterns = [
            self._patterns[pid]
            for pid in ids
            if pid in self._patterns
            and self._patterns[pid].reliability_score >= min_reliability
        ]
        patterns.sort(key=lambda p: p.reliability_score, reverse=True)
        return patterns

    def find_by_server(
        self,
        server_type: str,
        min_reliability: float = 0.0,
        include_universal: bool = True,
    ) -> List[LearnedPattern]:
        """
        Find patterns applicable to a specific server type.

        Args:
            server_type: The server type to query.
            min_reliability: Minimum reliability score threshold.
            include_universal: Also include patterns with server_type="*".

        Returns:
            Matching patterns sorted by reliability (best first).
        """
        ids: Set[str] = set()

        # Server-specific patterns
        server_ids = self._by_server.get(server_type, [])
        ids.update(server_ids)

        # Universal patterns
        if include_universal:
            universal_ids = self._by_server.get("*", [])
            ids.update(universal_ids)

        patterns = [
            self._patterns[pid]
            for pid in ids
            if pid in self._patterns
            and self._patterns[pid].reliability_score >= min_reliability
        ]

        patterns.sort(key=lambda p: p.reliability_score, reverse=True)
        return patterns

    def find_best_for_server(
        self,
        server_type: str,
        pattern_type: Optional[PatternType] = None,
        top_n: int = 5,
    ) -> List[LearnedPattern]:
        """
        Find the top-N best patterns for a given server.

        Args:
            server_type: Target server type.
            pattern_type: Optional filter by pattern type.
            top_n: Maximum number of results.

        Returns:
            Best patterns sorted by reliability.
        """
        candidates = self.find_by_server(server_type, min_reliability=0.1)

        if pattern_type:
            candidates = [p for p in candidates if p.pattern_type == pattern_type]

        # Filter out unreliable patterns
        candidates = [p for p in candidates if not p.is_unreliable]

        return candidates[:top_n]

    def get_all(self) -> List[LearnedPattern]:
        """Return all stored patterns."""
        return list(self._patterns.values())

    @property
    def size(self) -> int:
        """Number of stored patterns."""
        return len(self._patterns)

    # ── Eviction ─────────────────────────────────────────────────────────

    def _evict_one(self) -> None:
        """
        Evict the least valuable pattern to make room.

        Eviction priority:
            1. Unreliable patterns (low success rate)
            2. Stale patterns (not used recently)
            3. Lowest reliability score
        """
        if not self._patterns:
            return

        # Find eviction candidate
        candidates = list(self._patterns.values())

        # Priority 1: unreliable
        unreliable = [p for p in candidates if p.is_unreliable]
        if unreliable:
            victim = min(unreliable, key=lambda p: p.reliability_score)
            self.remove(victim.pattern_id)
            self._log.debug(f"Evicted unreliable pattern: {victim.pattern_id}")
            return

        # Priority 2: stale
        stale = [p for p in candidates if p.is_stale]
        if stale:
            victim = min(stale, key=lambda p: p.reliability_score)
            self.remove(victim.pattern_id)
            self._log.debug(f"Evicted stale pattern: {victim.pattern_id}")
            return

        # Priority 3: lowest overall score
        victim = min(candidates, key=lambda p: p.reliability_score)
        self.remove(victim.pattern_id)
        self._log.debug(f"Evicted lowest-score pattern: {victim.pattern_id}")

    def prune(self, max_age_hours: float = 72.0) -> int:
        """
        Remove all stale and unreliable patterns.

        Args:
            max_age_hours: Remove patterns not used in this many hours.

        Returns:
            Number of patterns removed.
        """
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        to_remove: List[str] = []

        for pid, pattern in self._patterns.items():
            # Remove if unreliable with enough data
            if pattern.is_unreliable and pattern.total_attempts >= 5:
                to_remove.append(pid)
                continue

            # Remove if not used in max_age_hours
            last_activity = max(pattern.last_success, pattern.last_used, pattern.first_seen)
            if (now - last_activity) > max_age_seconds:
                to_remove.append(pid)
                continue

        for pid in to_remove:
            self.remove(pid)

        if to_remove:
            self._log.info(f"Pruned {len(to_remove)} stale/unreliable pattern(s)")

        return len(to_remove)

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self, filepath: Optional[str] = None) -> bool:
        """
        Save patterns to disk as JSON.

        Args:
            filepath: Override file path (uses config default if None).

        Returns:
            True if saved successfully.
        """
        path = filepath or self._config.pattern_cache_file
        if not path:
            return False

        try:
            data = {
                "version": __version__,
                "saved_at": time.time(),
                "pattern_count": self.size,
                "patterns": [p.to_dict() for p in self._patterns.values()],
            }

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            self._dirty = False
            self._log.debug(f"Saved {self.size} patterns to {path}")
            return True

        except Exception as exc:
            self._log.error(f"Failed to save patterns: {exc}")
            return False

    def _load_from_file(self, filepath: str) -> bool:
        """Load patterns from a JSON file."""
        if not os.path.exists(filepath):
            self._log.debug(f"No pattern cache file found: {filepath}")
            return False

        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)

            patterns_data = data.get("patterns", [])
            loaded = 0

            for pdata in patterns_data:
                try:
                    pattern = LearnedPattern.from_dict(pdata)
                    self._patterns[pattern.pattern_id] = pattern

                    self._by_type.setdefault(pattern.pattern_type, []).append(pattern.pattern_id)
                    self._by_server.setdefault(pattern.server_type, []).append(pattern.pattern_id)
                    loaded += 1
                except Exception as exc:
                    self._log.debug(f"Skipping malformed pattern: {exc}")

            self._dirty = False
            self._log.info(f"Loaded {loaded} patterns from {filepath}")
            return True

        except Exception as exc:
            self._log.error(f"Failed to load patterns: {exc}")
            return False

    # ── Statistics ────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, Any]:
        """Return comprehensive statistics about the pattern store."""
        if not self._patterns:
            return {
                "total_patterns": 0,
                "by_type": {},
                "by_server": {},
                "avg_reliability": 0.0,
                "capacity_used": "0%",
            }

        patterns = list(self._patterns.values())

        type_counts: Dict[str, int] = {}
        for ptype, ids in self._by_type.items():
            type_counts[ptype.value] = len(ids)

        server_counts: Dict[str, int] = {}
        for stype, ids in self._by_server.items():
            server_counts[stype] = len(ids)

        avg_reliability = sum(p.reliability_score for p in patterns) / len(patterns)

        stale_count = sum(1 for p in patterns if p.is_stale)
        unreliable_count = sum(1 for p in patterns if p.is_unreliable)
        total_successes = sum(p.success_count for p in patterns)
        total_failures = sum(p.failure_count for p in patterns)

        capacity_pct = (len(self._patterns) / self._max_patterns) * 100

        return {
            "total_patterns": len(self._patterns),
            "max_capacity": self._max_patterns,
            "capacity_used": f"{capacity_pct:.1f}%",
            "by_type": type_counts,
            "by_server": server_counts,
            "avg_reliability": round(avg_reliability, 3),
            "stale_patterns": stale_count,
            "unreliable_patterns": unreliable_count,
            "total_successes": total_successes,
            "total_failures": total_failures,
            "has_unsaved_changes": self._dirty,
        }


class PatternLearner:
    """
    High-level pattern learning engine.

    Observes extraction outcomes and automatically learns/updates patterns.
    Provides pattern-based extraction suggestions for future requests.

    Learning flow:
        1. Before extraction: query known patterns → suggest strategies
        2. After extraction: record outcome → update pattern stats
        3. Periodically: prune stale patterns → maintain quality

    This is the "brain" that makes the extractor improve over time.
    """

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
    ) -> None:
        self._config = config
        self._log = logger
        self._store = PatternStore(config, logger)
        self._session_successes: int = 0
        self._session_failures: int = 0

    # ── Pattern Generation ───────────────────────────────────────────────

    def _generate_pattern_id(
        self,
        pattern_type: PatternType,
        server_type: str,
        discriminator: str,
    ) -> str:
        """
        Generate a unique, deterministic pattern ID.

        Same inputs → same ID, enabling deduplication and merging.
        """
        raw = f"{pattern_type.value}:{server_type}:{discriminator}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _create_url_pattern(
        self,
        url: str,
        server_type: str,
    ) -> LearnedPattern:
        """
        Create a URL regex pattern from a successful extraction URL.

        Generalizes the URL into a reusable regex:
            - Preserves domain and path structure
            - Replaces specific IDs/hashes with wildcards
            - Keeps file extensions
        """
        parsed = urllib.parse.urlparse(url)
        domain = parsed.netloc

        # Generalize path: replace long alphanumeric segments with wildcards
        path_parts = parsed.path.split("/")
        generalized_parts: List[str] = []

        for part in path_parts:
            if not part:
                generalized_parts.append("")
                continue

            # Keep short structural parts (e.g., "e", "v", "embed", "video")
            if len(part) <= 6 and part.isalpha():
                generalized_parts.append(re.escape(part))
            # Keep file extensions
            elif re.match(r'^[\w\-]+\.\w{2,4}$', part):
                name, ext = part.rsplit(".", 1)
                generalized_parts.append(r'[\w\-]+\.' + re.escape(ext))
            # Replace hashes/IDs with wildcard
            elif len(part) > 6 and re.match(r'^[a-zA-Z0-9\-_]+$', part):
                generalized_parts.append(r'[a-zA-Z0-9\-_]+')
            else:
                generalized_parts.append(re.escape(part))

        generalized_path = "/".join(generalized_parts)

        # Build regex pattern
        pattern_regex = (
            r'https?://'
            + re.escape(domain).replace(r'\-', r'[\-\.]')
            + generalized_path
        )

        pattern_id = self._generate_pattern_id(
            PatternType.URL_REGEX, server_type, f"{domain}:{generalized_path}"
        )

        return LearnedPattern(
            pattern_id=pattern_id,
            pattern_type=PatternType.URL_REGEX,
            server_type=server_type,
            pattern_data={
                "regex": pattern_regex,
                "domain": domain,
                "original_url_sample": url[:100],
                "path_structure": generalized_path,
            },
        )

    def _create_js_variable_pattern(
        self,
        variable_name: str,
        server_type: str,
        context: Dict[str, Any],
    ) -> LearnedPattern:
        """Create a pattern for a JS variable that contained a video URL."""
        pattern_id = self._generate_pattern_id(
            PatternType.JS_VARIABLE, server_type, variable_name
        )

        return LearnedPattern(
            pattern_id=pattern_id,
            pattern_type=PatternType.JS_VARIABLE,
            server_type=server_type,
            pattern_data={
                "variable_name": variable_name,
                "extraction_regex": rf'(?:var|let|const)\s+{re.escape(variable_name)}\s*=\s*["\']([^"\']+)["\']',
                "context_hints": context,
            },
        )

    def _create_api_pattern(
        self,
        api_url: str,
        server_type: str,
        method: str = "GET",
        response_key: str = "",
    ) -> LearnedPattern:
        """Create a pattern for a successful API endpoint."""
        parsed = urllib.parse.urlparse(api_url)

        # Generalize the API path
        path_parts = parsed.path.split("/")
        generalized = []
        for part in path_parts:
            if not part:
                generalized.append("")
            elif len(part) > 8 and re.match(r'^[a-zA-Z0-9]+$', part):
                generalized.append("{video_id}")
            else:
                generalized.append(part)

        api_template = "/".join(generalized)

        pattern_id = self._generate_pattern_id(
            PatternType.API_ENDPOINT, server_type, f"{parsed.netloc}:{api_template}"
        )

        return LearnedPattern(
            pattern_id=pattern_id,
            pattern_type=PatternType.API_ENDPOINT,
            server_type=server_type,
            pattern_data={
                "api_template": api_template,
                "domain": parsed.netloc,
                "method": method,
                "response_key": response_key,
                "sample_url": api_url[:100],
            },
        )

    def _create_server_preference_pattern(
        self,
        server_type: str,
        server_name: str,
        confidence: int,
    ) -> LearnedPattern:
        """Create a pattern recording which server produced good results."""
        pattern_id = self._generate_pattern_id(
            PatternType.SERVER_PREFERENCE, "*", server_type
        )

        return LearnedPattern(
            pattern_id=pattern_id,
            pattern_type=PatternType.SERVER_PREFERENCE,
            server_type="*",
            pattern_data={
                "preferred_server_type": server_type,
                "preferred_server_name": server_name,
                "last_confidence": confidence,
            },
        )

    def _create_extraction_method_pattern(
        self,
        method: str,
        server_type: str,
        details: Dict[str, Any],
    ) -> LearnedPattern:
        """Create a pattern for a successful extraction method."""
        # Map methods to pattern types
        type_map: Dict[str, PatternType] = {
            "packed_js": PatternType.PACKED_JS,
            "json_config": PatternType.JSON_CONFIG,
            "iframe_follow": PatternType.IFRAME_CHAIN,
            "dom_selector": PatternType.DOM_SELECTOR,
            "network_intercept": PatternType.NETWORK_INTERCEPT,
        }

        pattern_type = type_map.get(method, PatternType.URL_REGEX)

        pattern_id = self._generate_pattern_id(
            pattern_type, server_type, f"{method}:{json.dumps(details, sort_keys=True)[:80]}"
        )

        return LearnedPattern(
            pattern_id=pattern_id,
            pattern_type=pattern_type,
            server_type=server_type,
            pattern_data={
                "method": method,
                "details": details,
            },
        )

    # ── Recording Outcomes ───────────────────────────────────────────────

    def record_success(
        self,
        stream: VideoStream,
        server: ServerInfo,
    ) -> None:
        """
        Record a successful extraction — learn from it.

        Creates/updates multiple patterns from the successful result:
            1. URL structure pattern
            2. Extraction method pattern
            3. Server preference pattern
            4. API endpoint pattern (if applicable)

        Args:
            stream: The successfully extracted VideoStream.
            server: The server it was extracted from.
        """
        now = time.time()
        self._session_successes += 1

        self._log.debug(
            f"Learning from success: server={server.name} "
            f"conf={stream.confidence} url={stream.url[:60]}"
        )

        # ── 1. URL structure pattern ─────────────────────────────────
        url_pattern = self._create_url_pattern(stream.url, server.server_type)
        url_pattern.success_count = 1
        url_pattern.total_attempts = 1
        url_pattern.last_success = now
        url_pattern.last_used = now
        url_pattern.avg_confidence = float(stream.confidence)
        self._store.add(url_pattern)

        # ── 2. Extraction method pattern ─────────────────────────────
        extraction_method = stream.metadata.get("extraction_method", "")
        if extraction_method:
            # Parse method string (e.g., "doodstream:pass_md5")
            method_parts = extraction_method.split(":")
            method_name = method_parts[-1] if len(method_parts) > 1 else method_parts[0]

            method_pattern = self._create_extraction_method_pattern(
                method=method_name,
                server_type=server.server_type,
                details={
                    "full_method": extraction_method,
                    "handler": stream.metadata.get("handler", ""),
                    "format": stream.format.value,
                },
            )
            method_pattern.success_count = 1
            method_pattern.total_attempts = 1
            method_pattern.last_success = now
            method_pattern.last_used = now
            method_pattern.avg_confidence = float(stream.confidence)
            self._store.add(method_pattern)

        # ── 3. Server preference pattern ─────────────────────────────
        pref_pattern = self._create_server_preference_pattern(
            server_type=server.server_type,
            server_name=server.name,
            confidence=stream.confidence,
        )
        pref_pattern.success_count = 1
        pref_pattern.total_attempts = 1
        pref_pattern.last_success = now
        pref_pattern.last_used = now
        pref_pattern.avg_confidence = float(stream.confidence)
        self._store.add(pref_pattern)

        # ── 4. API pattern (if applicable) ───────────────────────────
        api_url = stream.metadata.get("api_url", "")
        if api_url:
            api_pattern = self._create_api_pattern(
                api_url=api_url,
                server_type=server.server_type,
                method=stream.metadata.get("api_method", "GET"),
                response_key=stream.metadata.get("api_response_key", ""),
            )
            api_pattern.success_count = 1
            api_pattern.total_attempts = 1
            api_pattern.last_success = now
            api_pattern.last_used = now
            api_pattern.avg_confidence = float(stream.confidence)
            self._store.add(api_pattern)

        # Auto-save if configured
        if self._config.pattern_cache_file and self._store._dirty:
            if self._session_successes % 5 == 0:  # save every 5 successes
                self._store.save()

    def record_failure(
        self,
        server: ServerInfo,
        handler_name: str,
        error: str = "",
    ) -> None:
        """
        Record a failed extraction attempt.

        Updates failure counts for relevant patterns so they get
        deprioritized in future attempts.

        Args:
            server: The server that failed.
            handler_name: Which handler was used.
            error: Error description.
        """
        now = time.time()
        self._session_failures += 1

        self._log.debug(
            f"Recording failure: server={server.name} "
            f"handler={handler_name} error={error[:50]}"
        )

        # Find and update relevant patterns
        relevant = self._store.find_by_server(server.server_type)

        for pattern in relevant:
            # Only penalize patterns that are likely related
            if self._is_pattern_relevant_to_failure(pattern, handler_name, server):
                pattern.failure_count += 1
                pattern.total_attempts += 1
                pattern.last_failure = now
                pattern.last_used = now

    def _is_pattern_relevant_to_failure(
        self,
        pattern: LearnedPattern,
        handler_name: str,
        server: ServerInfo,
    ) -> bool:
        """Check if a pattern is relevant to a specific failure."""
        # Server-specific patterns are always relevant
        if pattern.server_type == server.server_type:
            return True

        # Method patterns matching the handler
        method = pattern.pattern_data.get("method", "")
        if handler_name.lower() in method.lower():
            return True

        # Handler name in details
        details_handler = pattern.pattern_data.get("details", {}).get("handler", "")
        if details_handler and handler_name.lower() == details_handler.lower():
            return True

        return False

    # ── Suggestions ──────────────────────────────────────────────────────

    def suggest_strategies(
        self,
        server: ServerInfo,
        top_n: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Suggest extraction strategies based on learned patterns.

        Returns a prioritized list of strategies to try for a given server.

        Args:
            server: The target server.
            top_n: Maximum number of suggestions.

        Returns:
            List of strategy dicts with keys:
                - pattern_id: str
                - pattern_type: str
                - strategy: str (human-readable description)
                - reliability: float
                - details: dict
        """
        patterns = self._store.find_best_for_server(
            server.server_type, top_n=top_n * 2  # fetch extra for filtering
        )

        suggestions: List[Dict[str, Any]] = []

        for pattern in patterns:
            suggestion = self._pattern_to_suggestion(pattern)
            if suggestion:
                suggestions.append(suggestion)

        # Sort by reliability
        suggestions.sort(key=lambda s: s["reliability"], reverse=True)

        result = suggestions[:top_n]

        if result:
            self._log.debug(
                f"Suggesting {len(result)} strategies for {server.name}:"
            )
            for i, s in enumerate(result):
                self._log.debug(
                    f"  [{i+1}] {s['strategy'][:50]} "
                    f"(reliability={s['reliability']:.2f})"
                )

        return result

    def suggest_server_order(
        self,
        servers: List[ServerInfo],
    ) -> List[ServerInfo]:
        """
        Reorder servers based on learned preferences.

        Servers that have historically produced better results are
        moved to the front of the list.

        Args:
            servers: Detected servers.

        Returns:
            Reordered server list (best first).
        """
        if not servers or self._store.size == 0:
            return servers

        # Get server preference patterns
        preferences = self._store.find_by_type(
            PatternType.SERVER_PREFERENCE, min_reliability=0.1
        )

        if not preferences:
            return servers

        # Build a preference score map
        pref_scores: Dict[str, float] = {}
        for pref in preferences:
            server_type = pref.pattern_data.get("preferred_server_type", "")
            if server_type:
                pref_scores[server_type] = pref.reliability_score

        # Score and sort servers
        def server_sort_key(srv: ServerInfo) -> float:
            learned_score = pref_scores.get(srv.server_type, 0.0)
            base_score = srv.confidence / 100.0
            return learned_score * 0.6 + base_score * 0.4

        reordered = sorted(servers, key=server_sort_key, reverse=True)

        # Log reordering if it changed anything
        original_order = [s.name for s in servers]
        new_order = [s.name for s in reordered]
        if original_order != new_order:
            self._log.debug(
                f"Server order adjusted by learner: {' → '.join(new_order[:5])}"
            )

        return reordered

    def get_url_patterns_for_server(
        self,
        server_type: str,
    ) -> List[re.Pattern]:
        """
        Get compiled regex patterns for known video URLs from a server.

        These can be used as an additional extraction signal —
        if a URL in the page matches a known successful pattern,
        it's more likely to be a real video URL.

        Args:
            server_type: The server type.

        Returns:
            List of compiled regex patterns.
        """
        url_patterns = self._store.find_by_server(
            server_type,
            min_reliability=0.3,
        )

        compiled: List[re.Pattern] = []
        for pattern in url_patterns:
            if pattern.pattern_type != PatternType.URL_REGEX:
                continue
            regex_str = pattern.pattern_data.get("regex", "")
            if regex_str:
                try:
                    compiled.append(re.compile(regex_str, re.I))
                except re.error:
                    pass

        return compiled

    def _pattern_to_suggestion(
        self,
        pattern: LearnedPattern,
    ) -> Optional[Dict[str, Any]]:
        """Convert a LearnedPattern to a human-readable suggestion dict."""
        strategy = ""
        details: Dict[str, Any] = {}

        if pattern.pattern_type == PatternType.URL_REGEX:
            strategy = (
                f"Look for URLs matching: {pattern.pattern_data.get('domain', '?')} "
                f"path structure"
            )
            details["regex"] = pattern.pattern_data.get("regex", "")

        elif pattern.pattern_type == PatternType.JS_VARIABLE:
            var_name = pattern.pattern_data.get("variable_name", "?")
            strategy = f"Check JS variable '{var_name}' for video URL"
            details["variable"] = var_name
            details["regex"] = pattern.pattern_data.get("extraction_regex", "")

        elif pattern.pattern_type == PatternType.API_ENDPOINT:
            template = pattern.pattern_data.get("api_template", "?")
            method = pattern.pattern_data.get("method", "GET")
            strategy = f"{method} API: {template}"
            details["api_template"] = template
            details["method"] = method
            details["response_key"] = pattern.pattern_data.get("response_key", "")

        elif pattern.pattern_type == PatternType.PACKED_JS:
            strategy = "Unpack P.A.C.K.E.R. obfuscated JS and scan for URLs"
            details["method"] = pattern.pattern_data.get("method", "packed_js")

        elif pattern.pattern_type == PatternType.JSON_CONFIG:
            strategy = "Parse JSON player configuration for source URLs"
            details["method"] = "json_config"

        elif pattern.pattern_type == PatternType.IFRAME_CHAIN:
            strategy = "Follow iframe chain to extract video from embedded player"
            details["method"] = "iframe_chain"

        elif pattern.pattern_type == PatternType.SERVER_PREFERENCE:
            srv_name = pattern.pattern_data.get("preferred_server_name", "?")
            strategy = f"Prefer server: {srv_name}"
            details["server"] = srv_name

        elif pattern.pattern_type == PatternType.HEADER_REQUIREMENT:
            strategy = "Include specific headers for this server"
            details["headers"] = pattern.pattern_data.get("headers", {})

        elif pattern.pattern_type == PatternType.NETWORK_INTERCEPT:
            strategy = "Use headless browser network interception"
            details["method"] = "network_intercept"

        else:
            strategy = f"Apply learned pattern: {pattern.pattern_type.value}"

        if not strategy:
            return None

        return {
            "pattern_id": pattern.pattern_id,
            "pattern_type": pattern.pattern_type.value,
            "strategy": strategy,
            "reliability": round(pattern.reliability_score, 3),
            "success_rate": round(pattern.success_rate, 3),
            "total_attempts": pattern.total_attempts,
            "details": details,
        }

    # ── Maintenance ──────────────────────────────────────────────────────

    def maintenance(self) -> Dict[str, Any]:
        """
        Run periodic maintenance on the pattern store.

        - Prunes stale and unreliable patterns
        - Saves to disk if configured
        - Returns maintenance report
        """
        report: Dict[str, Any] = {
            "before_count": self._store.size,
        }

        # Prune
        pruned = self._store.prune(max_age_hours=72.0)
        report["pruned"] = pruned

        # Save
        if self._config.pattern_cache_file and self._store._dirty:
            saved = self._store.save()
            report["saved"] = saved

        report["after_count"] = self._store.size
        report["session_successes"] = self._session_successes
        report["session_failures"] = self._session_failures

        return report

    def get_stats(self) -> Dict[str, Any]:
        """Get comprehensive learner statistics."""
        store_stats = self._store.get_stats()
        store_stats["session_successes"] = self._session_successes
        store_stats["session_failures"] = self._session_failures
        return store_stats

    @property
    def store(self) -> PatternStore:
        """Direct access to the pattern store."""
        return self._store


# ═══════════════════════════════════════════════════════════════════════════════
# END OF PART 6
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# PART 7 — FALLBACK ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
#
# Resilient multi-strategy fallback system that orchestrates extraction
# attempts across different approaches, escalating from lightweight to
# heavyweight when simpler methods fail. Implements retry chains,
# strategy switching, and intelligent escalation.
# ═══════════════════════════════════════════════════════════════════════════════


class StrategyResult:
    """
    Encapsulates the outcome of a single strategy attempt.

    Tracks whether the strategy succeeded, what streams it found,
    how long it took, and what errors occurred.
    """

    def __init__(
        self,
        strategy: ExtractionStrategy,
        success: bool,
        streams: Optional[List[VideoStream]] = None,
        error: Optional[str] = None,
        elapsed: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.strategy = strategy
        self.success = success
        self.streams = streams or []
        self.error = error
        self.elapsed = elapsed
        self.metadata = metadata or {}
        self.timestamp: float = time.time()

    @property
    def stream_count(self) -> int:
        return len(self.streams)

    @property
    def best_confidence(self) -> int:
        """Highest confidence among found streams."""
        if not self.streams:
            return 0
        return max(s.confidence for s in self.streams)

    @property
    def is_high_quality(self) -> bool:
        """Check if this result is good enough to stop escalating."""
        return self.success and self.best_confidence >= 65

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy.name,
            "success": self.success,
            "stream_count": self.stream_count,
            "best_confidence": self.best_confidence,
            "error": self.error,
            "elapsed": round(self.elapsed, 3),
            "is_high_quality": self.is_high_quality,
            "metadata": self.metadata,
        }


class EscalationRule:
    """
    Defines when and how to escalate from one strategy to another.

    Each rule specifies:
        - Which strategy it applies to
        - Conditions under which escalation occurs
        - What strategy to escalate to
        - Optional delay before escalation
    """

    def __init__(
        self,
        from_strategy: ExtractionStrategy,
        to_strategy: ExtractionStrategy,
        condition: str = "failure",
        min_confidence_threshold: int = 50,
        max_retries_before_escalate: int = 1,
        delay_seconds: float = 0.0,
        description: str = "",
    ) -> None:
        self.from_strategy = from_strategy
        self.to_strategy = to_strategy
        self.condition = condition
        self.min_confidence_threshold = min_confidence_threshold
        self.max_retries_before_escalate = max_retries_before_escalate
        self.delay_seconds = delay_seconds
        self.description = description or (
            f"{from_strategy.name} → {to_strategy.name} on {condition}"
        )

    def should_escalate(
        self,
        result: StrategyResult,
        attempt_count: int,
    ) -> bool:
        """
        Determine if escalation should occur based on the result.

        Args:
            result: The outcome of the current strategy.
            attempt_count: How many times this strategy has been tried.

        Returns:
            True if escalation should occur.
        """
        # Always escalate on total failure (no streams at all)
        if self.condition == "failure" and not result.success:
            return attempt_count >= self.max_retries_before_escalate

        # Escalate if confidence is below threshold
        if self.condition == "low_confidence":
            if result.best_confidence < self.min_confidence_threshold:
                return attempt_count >= self.max_retries_before_escalate

        # Escalate if no streams found even though request succeeded
        if self.condition == "empty_result":
            if result.success and result.stream_count == 0:
                return attempt_count >= self.max_retries_before_escalate

        # Escalate on any non-high-quality result
        if self.condition == "not_high_quality":
            if not result.is_high_quality:
                return attempt_count >= self.max_retries_before_escalate

        return False


class RetryPolicy:
    """
    Defines retry behavior for a specific strategy.

    Controls how many times a strategy is retried, with what delays,
    and under what conditions retries are attempted.
    """

    def __init__(
        self,
        strategy: ExtractionStrategy,
        max_retries: int = 2,
        base_delay: float = 1.0,
        backoff_factor: float = 2.0,
        max_delay: float = 10.0,
        retry_on_empty: bool = True,
        retry_on_low_confidence: bool = True,
        low_confidence_threshold: int = 40,
        rotate_identity: bool = False,
    ) -> None:
        self.strategy = strategy
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.backoff_factor = backoff_factor
        self.max_delay = max_delay
        self.retry_on_empty = retry_on_empty
        self.retry_on_low_confidence = retry_on_low_confidence
        self.low_confidence_threshold = low_confidence_threshold
        self.rotate_identity = rotate_identity

    def should_retry(
        self,
        result: StrategyResult,
        attempt: int,
    ) -> bool:
        """
        Determine if a retry should be attempted.

        Args:
            result: The outcome of the latest attempt.
            attempt: Current attempt number (1-based).

        Returns:
            True if another retry should be attempted.
        """
        if attempt >= self.max_retries:
            return False

        # Retry on failure
        if not result.success:
            return True

        # Retry on empty results
        if self.retry_on_empty and result.stream_count == 0:
            return True

        # Retry on low confidence
        if (self.retry_on_low_confidence and
                result.best_confidence < self.low_confidence_threshold):
            return True

        return False

    def get_delay(self, attempt: int) -> float:
        """
        Calculate delay before the next retry.

        Uses exponential backoff with jitter.
        """
        delay = self.base_delay * (self.backoff_factor ** attempt)
        delay = min(delay, self.max_delay)
        # Add jitter (±20%)
        jitter = delay * 0.2 * (random.random() * 2 - 1)
        return max(0.1, delay + jitter)


class StrategyExecutor:
    """
    Executes a single extraction strategy with its full retry policy.

    Handles:
        - Strategy-specific setup (identity rotation, header changes)
        - Retry loop with backoff
        - Result aggregation across retries
        - Error isolation
    """

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
        http_client: HttpClient,
        handler_registry: HandlerRegistry,
        parser: AdaptiveParser,
        pattern_learner: PatternLearner,
    ) -> None:
        self._config = config
        self._log = logger
        self._http = http_client
        self._handlers = handler_registry
        self._parser = parser
        self._learner = pattern_learner

    def execute(
        self,
        strategy: ExtractionStrategy,
        server: ServerInfo,
        page_html: Optional[str] = None,
        page_url: str = "",
        retry_policy: Optional[RetryPolicy] = None,
    ) -> StrategyResult:
        """
        Execute a strategy with full retry handling.

        Args:
            strategy: Which strategy to execute.
            server: Target server.
            page_html: Pre-fetched HTML.
            page_url: Original page URL.
            retry_policy: Retry configuration (uses defaults if None).

        Returns:
            StrategyResult with all discovered streams.
        """
        policy = retry_policy or self._default_retry_policy(strategy)

        self._log.info(
            f"Executing strategy: {strategy.name} "
            f"(max_retries={policy.max_retries})"
        )

        best_result: Optional[StrategyResult] = None
        all_streams: List[VideoStream] = []
        seen_urls: Set[str] = set()
        last_error: Optional[str] = None

        for attempt in range(1, policy.max_retries + 1):
            self._log.debug(
                f"Strategy {strategy.name} — attempt {attempt}/{policy.max_retries}"
            )

            # Rotate identity if configured
            if policy.rotate_identity and attempt > 1:
                self._http.rotate_identity()
                self._log.debug("Identity rotated for retry")

            # Execute the strategy
            start_time = time.time()
            try:
                result = self._execute_strategy(
                    strategy, server, page_html, page_url
                )
            except Exception as exc:
                elapsed = time.time() - start_time
                last_error = str(exc)
                result = StrategyResult(
                    strategy=strategy,
                    success=False,
                    error=last_error,
                    elapsed=elapsed,
                    metadata={"attempt": attempt, "exception": type(exc).__name__},
                )
                self._log.debug(
                    f"Strategy {strategy.name} attempt {attempt} exception: {exc}"
                )

            # Aggregate streams
            for stream in result.streams:
                if stream.url not in seen_urls:
                    seen_urls.add(stream.url)
                    all_streams.append(stream)

            # Track best result
            if best_result is None or result.best_confidence > best_result.best_confidence:
                best_result = result

            # Check if result is good enough to stop
            if result.is_high_quality:
                self._log.debug(
                    f"Strategy {strategy.name} produced high-quality result "
                    f"(confidence={result.best_confidence}), stopping retries"
                )
                break

            # Check if we should retry
            if not policy.should_retry(result, attempt):
                self._log.debug(
                    f"Strategy {strategy.name} — no retry warranted after attempt {attempt}"
                )
                break

            # Delay before retry
            if attempt < policy.max_retries:
                delay = policy.get_delay(attempt)
                self._log.debug(f"Retry delay: {delay:.2f}s")
                time.sleep(delay)

        # Build final aggregated result
        final_success = len(all_streams) > 0
        final_elapsed = (
            best_result.elapsed if best_result else 0.0
        )

        final_result = StrategyResult(
            strategy=strategy,
            success=final_success,
            streams=all_streams,
            error=last_error if not final_success else None,
            elapsed=final_elapsed,
            metadata={
                "total_attempts": min(attempt, policy.max_retries),
                "total_streams_found": len(all_streams),
                "best_confidence": max((s.confidence for s in all_streams), default=0),
            },
        )

        self._log.info(
            f"Strategy {strategy.name} final: "
            f"{'✓' if final_success else '✗'} | "
            f"{len(all_streams)} stream(s) | "
            f"best_conf={final_result.best_confidence}"
        )

        return final_result

    def _execute_strategy(
        self,
        strategy: ExtractionStrategy,
        server: ServerInfo,
        page_html: Optional[str],
        page_url: str,
    ) -> StrategyResult:
        """
        Dispatch to the appropriate strategy implementation.
        """
        start_time = time.time()

        dispatch: Dict[ExtractionStrategy, Callable[..., StrategyResult]] = {
            ExtractionStrategy.DIRECT_PARSE: self._strategy_direct_parse,
            ExtractionStrategy.IFRAME_FOLLOW: self._strategy_iframe_follow,
            ExtractionStrategy.SCRIPT_SCAN: self._strategy_script_scan,
            ExtractionStrategy.SERVER_API: self._strategy_server_api,
            ExtractionStrategy.HEADLESS_BROWSER: self._strategy_headless,
        }

        handler = dispatch.get(strategy)
        if not handler:
            return StrategyResult(
                strategy=strategy,
                success=False,
                error=f"Unknown strategy: {strategy.name}",
                elapsed=time.time() - start_time,
            )

        return handler(server, page_html, page_url)

    # ── Strategy Implementations ─────────────────────────────────────────

    def _strategy_direct_parse(
        self,
        server: ServerInfo,
        page_html: Optional[str],
        page_url: str,
    ) -> StrategyResult:
        """
        Strategy 1: Direct HTML parsing of the server's embed page.

        The most lightweight approach — parse the page for video URLs
        without following any links or making additional requests.
        """
        start_time = time.time()
        streams: List[VideoStream] = []

        embed_url = server.url
        if not embed_url.startswith("http"):
            return StrategyResult(
                strategy=ExtractionStrategy.DIRECT_PARSE,
                success=False,
                error="No valid embed URL",
                elapsed=time.time() - start_time,
            )

        # Fetch the embed page
        html = page_html
        if not html:
            response = self._http.get(embed_url, referer=page_url or self._config.base_url)
            if not response:
                return StrategyResult(
                    strategy=ExtractionStrategy.DIRECT_PARSE,
                    success=False,
                    error="Failed to fetch embed page",
                    elapsed=time.time() - start_time,
                )
            html = response.text

        # Use the handler registry for extraction
        handler = self._handlers.find_handler(server)
        streams = handler.extract(server, html)

        # Also apply any learned URL patterns
        learned_patterns = self._learner.get_url_patterns_for_server(server.server_type)
        if learned_patterns:
            for pattern in learned_patterns:
                for match in pattern.finditer(html):
                    url = match.group(0)
                    if url.startswith("http") and not any(s.url == url for s in streams):
                        streams.append(VideoStream(
                            url=url,
                            format=detect_stream_format(url),
                            quality=detect_quality_from_url(url),
                            confidence=55,
                            server_name=server.name,
                            headers={"Referer": embed_url},
                            metadata={
                                "extraction_method": "learned_pattern",
                                "handler": "PatternLearner",
                            },
                        ))

        return StrategyResult(
            strategy=ExtractionStrategy.DIRECT_PARSE,
            success=len(streams) > 0,
            streams=streams,
            elapsed=time.time() - start_time,
            metadata={"handler_used": handler.get_name()},
        )

    def _strategy_iframe_follow(
        self,
        server: ServerInfo,
        page_html: Optional[str],
        page_url: str,
    ) -> StrategyResult:
        """
        Strategy 2: Follow iframe chains to find the actual player page.

        Fetches the embed page, finds iframes, follows them (up to 3 levels
        deep), and extracts video URLs from each level.
        """
        start_time = time.time()
        streams: List[VideoStream] = []
        seen_urls: Set[str] = set()

        embed_url = server.url
        if not embed_url.startswith("http"):
            return StrategyResult(
                strategy=ExtractionStrategy.IFRAME_FOLLOW,
                success=False,
                error="No valid embed URL",
                elapsed=time.time() - start_time,
            )

        # BFS-style iframe traversal
        urls_to_visit: List[Tuple[str, int, str]] = [(embed_url, 0, page_url)]
        max_depth = 3
        max_visits = 8

        visit_count = 0

        while urls_to_visit and visit_count < max_visits:
            current_url, depth, referer = urls_to_visit.pop(0)

            if current_url in seen_urls:
                continue
            seen_urls.add(current_url)
            visit_count += 1

            self._log.debug(
                f"Iframe follow: depth={depth} visit={visit_count} → {current_url[:70]}"
            )

            # Fetch page
            if visit_count == 1 and page_html and current_url == embed_url:
                html = page_html
            else:
                response = self._http.get(current_url, referer=referer)
                if not response:
                    continue
                html = response.text

            # Extract video URLs from this level
            handler = self._handlers.find_handler(server)
            level_streams = handler.extract(
                ServerInfo(
                    name=server.name,
                    url=current_url,
                    server_type=server.server_type,
                    confidence=server.confidence,
                    metadata={**server.metadata, "depth": depth},
                ),
                html,
            )

            for stream in level_streams:
                stream.metadata["follow_depth"] = depth
                stream.metadata["extraction_method"] = (
                    stream.metadata.get("extraction_method", "") + f":iframe_d{depth}"
                )
                # Slightly reduce confidence for deeper levels
                stream.confidence = max(10, stream.confidence - (depth * 5))
                streams.append(stream)

            # If we found high-confidence streams, stop going deeper
            if any(s.confidence >= 70 for s in level_streams):
                self._log.debug(
                    f"High-confidence stream found at depth {depth}, stopping iframe traversal"
                )
                break

            # Find iframes to follow
            if depth < max_depth:
                iframes = self._parser.iframe_extractor.extract_iframes(html, current_url)
                for iframe in iframes:
                    classified = self._parser.iframe_extractor.classify_iframe(iframe)
                    classification = classified.get("classification", {})

                    # Skip ads
                    if classification.get("is_ad", False):
                        continue

                    iframe_url = iframe["url"]
                    if iframe_url not in seen_urls and iframe_url.startswith("http"):
                        urls_to_visit.append((iframe_url, depth + 1, current_url))

        return StrategyResult(
            strategy=ExtractionStrategy.IFRAME_FOLLOW,
            success=len(streams) > 0,
            streams=streams,
            elapsed=time.time() - start_time,
            metadata={
                "levels_visited": visit_count,
                "max_depth_reached": max(
                    (s.metadata.get("follow_depth", 0) for s in streams),
                    default=0,
                ),
            },
        )

    def _strategy_script_scan(
        self,
        server: ServerInfo,
        page_html: Optional[str],
        page_url: str,
    ) -> StrategyResult:
        """
        Strategy 3: Deep script analysis with JS unpacking.

        Fetches the embed page and performs thorough script analysis
        including P.A.C.K.E.R. decoding, base64 decoding, and
        JSON config extraction.
        """
        start_time = time.time()
        streams: List[VideoStream] = []

        embed_url = server.url
        if not embed_url.startswith("http"):
            return StrategyResult(
                strategy=ExtractionStrategy.SCRIPT_SCAN,
                success=False,
                error="No valid embed URL",
                elapsed=time.time() - start_time,
            )

        # Fetch page
        html = page_html
        if not html:
            response = self._http.get(embed_url, referer=page_url or self._config.base_url)
            if not response:
                return StrategyResult(
                    strategy=ExtractionStrategy.SCRIPT_SCAN,
                    success=False,
                    error="Failed to fetch embed page",
                    elapsed=time.time() - start_time,
                )
            html = response.text

        # Full adaptive parse
        parse_result = self._parser.parse_page(html, embed_url)

        for finding in parse_result.get("video_urls", []):
            url = finding.get("url", "")
            if not url or not url.startswith("http"):
                continue

            # Skip if already found
            if any(s.url == url for s in streams):
                continue

            confidence = finding.get("confidence", 45)
            was_obfuscated = finding.get("was_obfuscated", False)

            if was_obfuscated:
                confidence = min(100, confidence + 10)

            streams.append(VideoStream(
                url=url,
                format=detect_stream_format(url),
                quality=detect_quality_from_url(url),
                confidence=confidence,
                server_name=server.name,
                headers={"Referer": embed_url},
                metadata={
                    "extraction_method": f"script_scan:{finding.get('pattern', 'unknown')}",
                    "handler": "ScriptAnalyzer",
                    "was_obfuscated": was_obfuscated,
                    "source": finding.get("source", ""),
                },
            ))

        # Also try fetching and scanning external JS files
        external_streams = self._scan_external_scripts(html, embed_url, server)
        for stream in external_streams:
            if not any(s.url == stream.url for s in streams):
                streams.append(stream)

        return StrategyResult(
            strategy=ExtractionStrategy.SCRIPT_SCAN,
            success=len(streams) > 0,
            streams=streams,
            elapsed=time.time() - start_time,
            metadata={
                "scripts_analyzed": parse_result.get("scripts_analyzed", 0),
                "video_urls_found": len(parse_result.get("video_urls", [])),
            },
        )

    def _strategy_server_api(
        self,
        server: ServerInfo,
        page_html: Optional[str],
        page_url: str,
    ) -> StrategyResult:
        """
        Strategy 4: Try server-specific API endpoints.

        Uses handler registry with try_all=True to attempt every
        compatible handler, including API-based extraction.
        """
        start_time = time.time()

        # Use all matching handlers
        streams = self._handlers.extract_from_server(
            server, page_html, try_all=True
        )

        # Also try learned API patterns
        api_patterns = self._learner.store.find_by_server(
            server.server_type, min_reliability=0.3
        )

        for pattern in api_patterns:
            if pattern.pattern_type != PatternType.API_ENDPOINT:
                continue

            api_template = pattern.pattern_data.get("api_template", "")
            if not api_template:
                continue

            # Try to extract video ID
            video_id = GenericHandler._extract_video_id(server.url)
            if not video_id:
                continue

            # Build API URL
            parsed = urllib.parse.urlparse(server.url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            api_path = api_template.replace("{video_id}", video_id)
            api_url = f"{base}{api_path}"

            self._log.debug(f"Trying learned API pattern: {api_url[:70]}")

            method = pattern.pattern_data.get("method", "GET")
            if method.upper() == "POST":
                response = self._http.post(
                    api_url,
                    data={"r": page_url, "d": parsed.netloc},
                    referer=server.url,
                )
            else:
                response = self._http.get(api_url, referer=server.url)

            if response:
                try:
                    data = response.json()
                    response_key = pattern.pattern_data.get("response_key", "")

                    # Navigate to response key if specified
                    target = data
                    if response_key:
                        for key in response_key.split("."):
                            if isinstance(target, dict):
                                target = target.get(key, {})

                    # Extract URLs from the target data
                    urls_found = self._extract_urls_from_api_data(target)
                    for url_info in urls_found:
                        url = url_info.get("url", "")
                        if url and url.startswith("http"):
                            if not any(s.url == url for s in streams):
                                streams.append(VideoStream(
                                    url=url,
                                    format=detect_stream_format(url),
                                    quality=url_info.get("quality", Quality.UNKNOWN),
                                    confidence=65,
                                    server_name=server.name,
                                    headers={"Referer": server.url},
                                    metadata={
                                        "extraction_method": "learned_api",
                                        "api_url": api_url,
                                        "pattern_id": pattern.pattern_id,
                                    },
                                ))
                except (json.JSONDecodeError, ValueError, AttributeError):
                    pass

        return StrategyResult(
            strategy=ExtractionStrategy.SERVER_API,
            success=len(streams) > 0,
            streams=streams,
            elapsed=time.time() - start_time,
            metadata={"handlers_tried": True, "api_patterns_tried": len(api_patterns)},
        )

    def _strategy_headless(
        self,
        server: ServerInfo,
        page_html: Optional[str],
        page_url: str,
    ) -> StrategyResult:
        """
        Strategy 5: Headless browser (placeholder — implemented in Part 8).

        This is a stub that returns a failure result. The actual
        implementation is injected by the HeadlessEngine in Part 8.
        """
        start_time = time.time()

        if not PLAYWRIGHT_AVAILABLE:
            return StrategyResult(
                strategy=ExtractionStrategy.HEADLESS_BROWSER,
                success=False,
                error="Playwright not installed — headless extraction unavailable",
                elapsed=time.time() - start_time,
                metadata={"playwright_available": False},
            )

        # Placeholder — Part 8 replaces this method
        return StrategyResult(
            strategy=ExtractionStrategy.HEADLESS_BROWSER,
            success=False,
            error="Headless engine not initialized (see Part 8)",
            elapsed=time.time() - start_time,
            metadata={"placeholder": True},
        )

    # ── Helper Methods ───────────────────────────────────────────────────

    def _scan_external_scripts(
        self,
        html: str,
        embed_url: str,
        server: ServerInfo,
    ) -> List[VideoStream]:
        """
        Fetch and scan external JavaScript files referenced in the page.

        Some servers load their player config from external .js files.
        """
        streams: List[VideoStream] = []

        try:
            soup = BeautifulSoup(html, self._config.html_parser)
            external_scripts = soup.find_all("script", src=True)

            # Filter to likely-relevant scripts
            relevant_scripts: List[str] = []
            for script in external_scripts:
                src = script["src"]
                src_lower = src.lower()

                # Skip known framework/library scripts
                skip_patterns = [
                    "jquery", "bootstrap", "analytics", "facebook",
                    "google", "recaptcha", "adsense", "tracking",
                    "pixel", "widget", "social", "comment",
                ]
                if any(skip in src_lower for skip in skip_patterns):
                    continue

                # Prioritize scripts with video-related names
                video_hints = [
                    "player", "video", "stream", "embed", "source",
                    "config", "setup", "play", "core", "main", "app",
                ]
                if any(hint in src_lower for hint in video_hints):
                    relevant_scripts.insert(0, src)  # priority
                else:
                    relevant_scripts.append(src)

            # Limit to top 4 most relevant
            for script_url in relevant_scripts[:4]:
                full_url = normalize_url(script_url, embed_url)
                if not full_url.startswith("http"):
                    continue

                self._log.debug(f"Fetching external script: {full_url[:70]}")

                response = self._http.get(full_url, referer=embed_url)
                if not response:
                    continue

                js_content = response.text

                # Unpack and scan
                expanded = self._parser.unpacker.unpack_all(js_content)

                # Look for video URLs
                url_patterns = [
                    re.compile(r'["\'](\s*https?://[^"\'<>\s]+\.(?:mp4|m3u8)[^"\'<>\s]*)\s*["\']', re.I),
                    re.compile(r'(?:file|src|source|url)\s*[:=]\s*["\'](\s*https?://[^"\'<>\s]+)\s*["\']', re.I),
                ]

                for pattern in url_patterns:
                    for match in pattern.finditer(expanded):
                        url = normalize_url(match.group(1).strip(), embed_url)
                        if url.startswith("http") and URLSignalAnalyzer.is_likely_video(url, 15):
                            streams.append(VideoStream(
                                url=url,
                                format=detect_stream_format(url),
                                quality=detect_quality_from_url(url),
                                confidence=50,
                                server_name=server.name,
                                headers={"Referer": embed_url},
                                metadata={
                                    "extraction_method": "external_script_scan",
                                    "script_url": full_url[:80],
                                },
                            ))

        except Exception as exc:
            self._log.debug(f"External script scan error: {exc}")

        return streams

    def _extract_urls_from_api_data(self, data: Any) -> List[Dict[str, Any]]:
        """Extract URLs from API response data (recursive)."""
        results: List[Dict[str, Any]] = []

        if isinstance(data, str):
            if data.startswith("http"):
                results.append({"url": data, "quality": Quality.UNKNOWN})

        elif isinstance(data, dict):
            for key in ("file", "src", "source", "url", "stream", "link", "download"):
                val = data.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    label = str(data.get("label", data.get("quality", "")))
                    quality = detect_quality_from_url(val)
                    if not quality or quality == Quality.UNKNOWN:
                        quality = GenericHandler._label_to_quality(label)
                    results.append({"url": val, "quality": quality, "label": label})

            for _key, val in data.items():
                if isinstance(val, (dict, list)):
                    results.extend(self._extract_urls_from_api_data(val))

        elif isinstance(data, list):
            for item in data:
                results.extend(self._extract_urls_from_api_data(item))

        return results

    @staticmethod
    def _default_retry_policy(strategy: ExtractionStrategy) -> RetryPolicy:
        """
        Get the default retry policy for a strategy.
        """
        defaults: Dict[ExtractionStrategy, Dict[str, Any]] = {
            ExtractionStrategy.DIRECT_PARSE: {
                "max_retries": 2,
                "base_delay": 0.5,
                "backoff_factor": 1.5,
                "rotate_identity": True,
            },
            ExtractionStrategy.IFRAME_FOLLOW: {
                "max_retries": 2,
                "base_delay": 1.0,
                "backoff_factor": 2.0,
                "rotate_identity": True,
            },
            ExtractionStrategy.SCRIPT_SCAN: {
                "max_retries": 1,
                "base_delay": 0.5,
                "backoff_factor": 1.5,
                "rotate_identity": False,
            },
            ExtractionStrategy.SERVER_API: {
                "max_retries": 2,
                "base_delay": 1.0,
                "backoff_factor": 2.0,
                "rotate_identity": True,
            },
            ExtractionStrategy.HEADLESS_BROWSER: {
                "max_retries": 1,
                "base_delay": 2.0,
                "backoff_factor": 2.0,
                "rotate_identity": False,
            },
        }

        params = defaults.get(strategy, {})
        return RetryPolicy(strategy=strategy, **params)


class FallbackEngine:
    """
    Master fallback orchestrator that manages the full escalation chain.

    Escalation order:
        1. DIRECT_PARSE → Lightweight HTML/JS parsing
        2. IFRAME_FOLLOW → Follow iframe chains
        3. SCRIPT_SCAN → Deep script analysis with unpacking
        4. SERVER_API → Server-specific API calls
        5. HEADLESS_BROWSER → Full browser rendering

    The engine:
        - Tries strategies in escalation order
        - Skips strategies unlikely to work (based on learned patterns)
        - Stops early when high-confidence results are found
        - Aggregates streams from all attempted strategies
        - Records outcomes for pattern learning
    """

    # Default escalation chain
    _DEFAULT_ESCALATION: List[EscalationRule] = [
        EscalationRule(
            from_strategy=ExtractionStrategy.DIRECT_PARSE,
            to_strategy=ExtractionStrategy.IFRAME_FOLLOW,
            condition="not_high_quality",
            min_confidence_threshold=65,
            max_retries_before_escalate=1,
            description="Direct parse → iframe follow (if low quality)",
        ),
        EscalationRule(
            from_strategy=ExtractionStrategy.IFRAME_FOLLOW,
            to_strategy=ExtractionStrategy.SCRIPT_SCAN,
            condition="not_high_quality",
            min_confidence_threshold=65,
            max_retries_before_escalate=1,
            description="Iframe follow → script scan (if low quality)",
        ),
        EscalationRule(
            from_strategy=ExtractionStrategy.SCRIPT_SCAN,
            to_strategy=ExtractionStrategy.SERVER_API,
            condition="not_high_quality",
            min_confidence_threshold=60,
            max_retries_before_escalate=1,
            description="Script scan → server API (if low quality)",
        ),
        EscalationRule(
            from_strategy=ExtractionStrategy.SERVER_API,
            to_strategy=ExtractionStrategy.HEADLESS_BROWSER,
            condition="failure",
            min_confidence_threshold=50,
            max_retries_before_escalate=1,
            delay_seconds=1.0,
            description="Server API → headless browser (last resort)",
        ),
    ]

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
        http_client: HttpClient,
        handler_registry: HandlerRegistry,
        parser: AdaptiveParser,
        pattern_learner: PatternLearner,
        heuristic_scorer: HeuristicScorer,
    ) -> None:
        self._config = config
        self._log = logger
        self._http = http_client
        self._handlers = handler_registry
        self._parser = parser
        self._learner = pattern_learner
        self._scorer = heuristic_scorer

        self._executor = StrategyExecutor(
            config, logger, http_client, handler_registry, parser, pattern_learner
        )

        self._escalation_rules = list(self._DEFAULT_ESCALATION)
        self._headless_callback: Optional[Callable] = None

    def set_headless_callback(
        self,
        callback: Callable[[ServerInfo, Optional[str], str], StrategyResult],
    ) -> None:
        """
        Register the headless browser extraction callback.

        Called by HeadlessEngine (Part 8) to inject its implementation.
        """
        self._headless_callback = callback
        self._log.debug("Headless browser callback registered")

    def execute_fallback_chain(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
        page_url: str = "",
        stop_on_high_quality: bool = True,
        skip_headless: bool = False,
    ) -> ExtractionResult:
        """
        Execute the full fallback chain for a single server.

        Tries each strategy in escalation order, aggregating results
        and stopping early if high-confidence streams are found.

        Args:
            server: The target server.
            page_html: Pre-fetched HTML of the embed page.
            page_url: Original page URL.
            stop_on_high_quality: Stop chain on high-quality result.
            skip_headless: Skip the headless browser strategy.

        Returns:
            ExtractionResult with all discovered streams.
        """
        self._log.section(f"Fallback Chain: {server.name}")

        start_time = time.time()
        all_streams: List[VideoStream] = []
        seen_urls: Set[str] = set()
        strategies_tried: List[str] = []
        errors: List[str] = []
        strategy_results: List[StrategyResult] = []

        # Determine strategy order
        strategy_order = self._build_strategy_order(server, skip_headless)

        self._log.info(
            f"Strategy order: {' → '.join(s.name for s in strategy_order)}"
        )

        current_strategy_idx = 0

        while current_strategy_idx < len(strategy_order):
            strategy = strategy_order[current_strategy_idx]
            strategies_tried.append(strategy.name)

            self._log.info(
                f"── Strategy [{current_strategy_idx + 1}/{len(strategy_order)}]: "
                f"{strategy.name} ──"
            )

            # Execute strategy
            if strategy == ExtractionStrategy.HEADLESS_BROWSER and self._headless_callback:
                result = self._headless_callback(server, page_html, page_url)
            else:
                result = self._executor.execute(
                    strategy, server, page_html, page_url
                )

            strategy_results.append(result)

            # Collect streams
            new_streams = 0
            for stream in result.streams:
                if stream.url not in seen_urls:
                    seen_urls.add(stream.url)
                    all_streams.append(stream)
                    new_streams += 1

            if result.error:
                errors.append(f"{strategy.name}: {result.error}")

            self._log.info(
                f"Strategy {strategy.name}: "
                f"{'✓' if result.success else '✗'} | "
                f"+{new_streams} new stream(s) | "
                f"total={len(all_streams)}"
            )

            # Check if we should stop
            if stop_on_high_quality and result.is_high_quality:
                self._log.info(
                    f"High-quality result achieved (confidence={result.best_confidence}). "
                    f"Stopping chain."
                )
                break

            # Check escalation rules
            should_continue = False
            for rule in self._escalation_rules:
                if rule.from_strategy == strategy:
                    if rule.should_escalate(result, 1):
                        # Find the escalation target in our order
                        try:
                            target_idx = strategy_order.index(rule.to_strategy)
                            if target_idx > current_strategy_idx:
                                if rule.delay_seconds > 0:
                                    self._log.debug(
                                        f"Escalation delay: {rule.delay_seconds}s"
                                    )
                                    time.sleep(rule.delay_seconds)
                                current_strategy_idx = target_idx
                                should_continue = True
                                self._log.debug(
                                    f"Escalating: {rule.description}"
                                )
                                break
                        except ValueError:
                            pass

            if should_continue:
                continue

            # No escalation rule matched — move to next strategy
            current_strategy_idx += 1

        # Score all collected streams
        if all_streams:
            all_streams = self._scorer.rank_streams(all_streams)

        elapsed = time.time() - start_time

        # Record learning outcomes
        if all_streams:
            best = all_streams[0]
            self._learner.record_success(best, server)
        else:
            last_handler = strategies_tried[-1] if strategies_tried else "unknown"
            self._learner.record_failure(
                server, last_handler,
                error=errors[-1] if errors else "No streams found"
            )

        result = ExtractionResult(
            success=len(all_streams) > 0,
            streams=all_streams,
            servers_found=[server],
            strategies_tried=strategies_tried,
            errors=errors,
            elapsed_time=elapsed,
            metadata={
                "strategy_results": [r.to_dict() for r in strategy_results],
                "total_strategies_attempted": len(strategies_tried),
                "server_name": server.name,
                "server_type": server.server_type,
            },
        )

        self._log.info(
            f"Fallback chain complete: "
            f"{'✓' if result.success else '✗'} | "
            f"{len(all_streams)} stream(s) | "
            f"{len(strategies_tried)} strategies | "
            f"{elapsed:.2f}s"
        )

        return result

    def execute_multi_server(
        self,
        servers: List[ServerInfo],
        page_html: Optional[str] = None,
        page_url: str = "",
        max_servers: int = 5,
        stop_on_first_success: bool = False,
        skip_headless: bool = False,
    ) -> ExtractionResult:
        """
        Execute fallback chains across multiple servers.

        Tries servers in priority order, aggregating results from all.

        Args:
            servers: Available servers (already sorted by preference).
            page_html: Pre-fetched main page HTML.
            page_url: Main page URL.
            max_servers: Maximum number of servers to try.
            stop_on_first_success: Stop after first server succeeds.
            skip_headless: Skip headless browser strategy.

        Returns:
            Combined ExtractionResult.
        """
        self._log.section(f"Multi-Server Extraction ({len(servers)} servers)")

        start_time = time.time()
        all_streams: List[VideoStream] = []
        seen_urls: Set[str] = set()
        all_strategies: List[str] = []
        all_errors: List[str] = []
        servers_processed: List[ServerInfo] = []

        # Apply learned server ordering
        ordered_servers = self._learner.suggest_server_order(servers)

        for i, server in enumerate(ordered_servers[:max_servers]):
            self._log.info(
                f"\n{'─' * 40}\n"
                f"Server [{i + 1}/{min(len(ordered_servers), max_servers)}]: "
                f"{server.name} (type={server.server_type})\n"
                f"{'─' * 40}"
            )

            servers_processed.append(server)

            result = self.execute_fallback_chain(
                server=server,
                page_html=None,  # each server needs its own embed page
                page_url=page_url,
                stop_on_high_quality=True,
                skip_headless=skip_headless,
            )

            # Collect results
            for stream in result.streams:
                if stream.url not in seen_urls:
                    seen_urls.add(stream.url)
                    all_streams.append(stream)

            all_strategies.extend(
                f"{server.name}:{s}" for s in result.strategies_tried
            )
            all_errors.extend(result.errors)

            # Check if we should stop
            if stop_on_first_success and result.success:
                self._log.info(
                    f"First successful server found ({server.name}), stopping"
                )
                break

            # If we have high-confidence results, reduce remaining server attempts
            if all_streams:
                best_conf = max(s.confidence for s in all_streams)
                if best_conf >= 80:
                    self._log.info(
                        f"High-confidence stream found (conf={best_conf}), "
                        f"limiting remaining servers"
                    )
                    max_servers = min(max_servers, i + 2)

        # Final ranking
        if all_streams:
            all_streams = self._scorer.rank_streams(all_streams)

        elapsed = time.time() - start_time

        final_result = ExtractionResult(
            success=len(all_streams) > 0,
            streams=all_streams,
            servers_found=servers_processed,
            strategies_tried=all_strategies,
            errors=all_errors,
            elapsed_time=elapsed,
            metadata={
                "servers_attempted": len(servers_processed),
                "total_servers_available": len(servers),
            },
        )

        self._log.section("Multi-Server Extraction Complete")
        self._log.info(
            f"Result: {'✓' if final_result.success else '✗'} | "
            f"{len(all_streams)} total stream(s) | "
            f"{len(servers_processed)} servers tried | "
            f"{elapsed:.2f}s"
        )

        if final_result.best_stream:
            best = final_result.best_stream
            self._log.info(
                f"Best stream: score={best.score:.1f} "
                f"conf={best.confidence} "
                f"fmt={best.format.value} "
                f"q={best.quality.value} "
                f"srv={best.server_name}"
            )

        return final_result

    def _build_strategy_order(
        self,
        server: ServerInfo,
        skip_headless: bool = False,
    ) -> List[ExtractionStrategy]:
        """
        Build the strategy execution order for a server.

        May reorder based on learned patterns (e.g., if we know
        this server type always needs packed JS decoding, put
        SCRIPT_SCAN first).
        """
        default_order = [
            ExtractionStrategy.DIRECT_PARSE,
            ExtractionStrategy.IFRAME_FOLLOW,
            ExtractionStrategy.SCRIPT_SCAN,
            ExtractionStrategy.SERVER_API,
        ]

        if not skip_headless and PLAYWRIGHT_AVAILABLE:
            default_order.append(ExtractionStrategy.HEADLESS_BROWSER)

        # Check if learned patterns suggest a different order
        suggestions = self._learner.suggest_strategies(server, top_n=3)
        if suggestions:
            best_suggestion = suggestions[0]
            pattern_type = best_suggestion.get("pattern_type", "")

            # Map pattern types to strategies
            type_to_strategy: Dict[str, ExtractionStrategy] = {
                "packed_js": ExtractionStrategy.SCRIPT_SCAN,
                "json_config": ExtractionStrategy.SCRIPT_SCAN,
                "iframe_chain": ExtractionStrategy.IFRAME_FOLLOW,
                "api_endpoint": ExtractionStrategy.SERVER_API,
                "network_intercept": ExtractionStrategy.HEADLESS_BROWSER,
                "url_regex": ExtractionStrategy.DIRECT_PARSE,
                "js_variable": ExtractionStrategy.SCRIPT_SCAN,
                "dom_selector": ExtractionStrategy.DIRECT_PARSE,
            }

            suggested_strategy = type_to_strategy.get(pattern_type)
            if (suggested_strategy and suggested_strategy in default_order
                    and best_suggestion.get("reliability", 0) > 0.5):
                # Move suggested strategy to front
                default_order.remove(suggested_strategy)
                default_order.insert(0, suggested_strategy)
                self._log.debug(
                    f"Strategy order adjusted by learner: "
                    f"{suggested_strategy.name} moved to front "
                    f"(reliability={best_suggestion['reliability']:.2f})"
                )

        return default_order

    def get_escalation_stats(self) -> Dict[str, Any]:
        """Return statistics about the escalation configuration."""
        return {
            "escalation_rules": [
                {
                    "from": rule.from_strategy.name,
                    "to": rule.to_strategy.name,
                    "condition": rule.condition,
                    "description": rule.description,
                }
                for rule in self._escalation_rules
            ],
            "headless_available": PLAYWRIGHT_AVAILABLE,
            "headless_callback_set": self._headless_callback is not None,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# END OF PART 7
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# PART 8 — HEADLESS ENGINE (PLAYWRIGHT)
# ═══════════════════════════════════════════════════════════════════════════════
#
# Last-resort extraction engine using a headless browser (Playwright).
# Renders the page fully (including JavaScript execution), intercepts
# network requests to capture video stream URLs, and returns the best
# discovered stream. Only invoked when all lighter strategies fail.
# ═══════════════════════════════════════════════════════════════════════════════


class NetworkCapture:
    """
    Stores and analyzes network requests captured during headless browsing.

    Categorizes requests into:
        - Video streams (mp4, m3u8, webm, etc.)
        - API calls (potential video source endpoints)
        - Media segments (HLS/DASH chunks)
        - Other (ignored)
    """

    def __init__(self, logger: ExtractorLogger) -> None:
        self._log = logger
        self._video_requests: List[Dict[str, Any]] = []
        self._api_requests: List[Dict[str, Any]] = []
        self._segment_requests: List[Dict[str, Any]] = []
        self._all_requests: List[Dict[str, Any]] = []
        self._lock_active: bool = True

    # ── Capture patterns ─────────────────────────────────────────────────

    _VIDEO_URL_PATTERN = re.compile(
        r'\.(mp4|m3u8|webm|flv|mkv|avi|mov)(\?|$|#)',
        re.I,
    )

    _VIDEO_CONTENT_TYPES = {
        "video/mp4", "video/webm", "video/x-flv",
        "video/ogg", "video/quicktime",
        "application/vnd.apple.mpegurl",
        "application/x-mpegurl",
        "application/octet-stream",
        "binary/octet-stream",
    }

    _API_PATTERNS = re.compile(
        r'(/api/|/ajax/|/source|/embed/|/pass_md5/|/get_video|'
        r'/stream|/video|/play|/token|/dl/)',
        re.I,
    )

    _SEGMENT_PATTERN = re.compile(
        r'\.(ts|m4s|fmp4|seg\d+)(\?|$)',
        re.I,
    )

    _AD_PATTERNS = re.compile(
        r'(doubleclick|googlesyndication|adserver|popunder|popads|'
        r'juicyads|exoclick|trafficjunky|clickadu|propeller|'
        r'adsterra|hilltopads|ad[sx]?\.|banner|tracking|analytics|'
        r'facebook\.com/tr|google-analytics)',
        re.I,
    )

    def record_request(
        self,
        url: str,
        method: str = "GET",
        resource_type: str = "",
        headers: Optional[Dict[str, str]] = None,
        status: Optional[int] = None,
        content_type: Optional[str] = None,
        content_length: Optional[int] = None,
    ) -> None:
        """
        Record a captured network request.

        Automatically classifies the request into the appropriate category.
        """
        if not self._lock_active:
            return

        # Skip obvious ads/trackers
        if self._AD_PATTERNS.search(url):
            return

        entry: Dict[str, Any] = {
            "url": url,
            "method": method,
            "resource_type": resource_type,
            "headers": headers or {},
            "status": status,
            "content_type": content_type,
            "content_length": content_length,
            "timestamp": time.time(),
        }

        self._all_requests.append(entry)

        # Classify
        is_video = False

        # Check URL pattern
        if self._VIDEO_URL_PATTERN.search(url):
            is_video = True

        # Check content type
        if content_type:
            ct_lower = content_type.lower().split(";")[0].strip()
            if ct_lower in self._VIDEO_CONTENT_TYPES:
                is_video = True

        # Check content length (videos are typically > 500KB)
        if content_length and content_length > 500_000:
            if resource_type in ("media", "video", "xhr", "fetch", "other"):
                is_video = True

        # Check resource type
        if resource_type in ("media", "video"):
            is_video = True

        if is_video:
            self._video_requests.append(entry)
            self._log.debug(
                f"🎬 Video request captured: {method} {url[:80]} "
                f"[{content_type or '?'}, {content_length or '?'} bytes]"
            )
            return

        # Check for HLS/DASH segments
        if self._SEGMENT_PATTERN.search(url):
            self._segment_requests.append(entry)
            return

        # Check for API calls
        if self._API_PATTERNS.search(url):
            self._api_requests.append(entry)
            self._log.debug(f"📡 API request captured: {method} {url[:80]}")
            return

    def record_response(
        self,
        url: str,
        status: int,
        content_type: str = "",
        content_length: int = 0,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Update a recorded request with response information.

        Called when the response is received for a previously recorded request.
        """
        if not self._lock_active:
            return

        # Check if this response reveals a video we didn't catch on request
        ct_lower = content_type.lower().split(";")[0].strip()
        is_video = ct_lower in self._VIDEO_CONTENT_TYPES
        is_large = content_length > 500_000

        if (is_video or is_large) and status in (200, 206):
            # Check if already in video_requests
            already_captured = any(r["url"] == url for r in self._video_requests)
            if not already_captured:
                entry: Dict[str, Any] = {
                    "url": url,
                    "method": "GET",
                    "resource_type": "media",
                    "headers": headers or {},
                    "status": status,
                    "content_type": content_type,
                    "content_length": content_length,
                    "timestamp": time.time(),
                    "captured_on": "response",
                }
                self._video_requests.append(entry)
                self._log.debug(
                    f"🎬 Video response captured: {url[:80]} "
                    f"[{content_type}, {content_length} bytes]"
                )

    def stop(self) -> None:
        """Stop recording new requests."""
        self._lock_active = False

    def resume(self) -> None:
        """Resume recording requests."""
        self._lock_active = True

    def clear(self) -> None:
        """Clear all captured data."""
        self._video_requests.clear()
        self._api_requests.clear()
        self._segment_requests.clear()
        self._all_requests.clear()

    # ── Analysis ─────────────────────────────────────────────────────────

    @property
    def video_urls(self) -> List[str]:
        """All captured video URLs."""
        return [r["url"] for r in self._video_requests]

    @property
    def api_urls(self) -> List[str]:
        """All captured API URLs."""
        return [r["url"] for r in self._api_requests]

    @property
    def video_request_count(self) -> int:
        return len(self._video_requests)

    @property
    def total_request_count(self) -> int:
        return len(self._all_requests)

    def get_best_video_urls(self) -> List[Dict[str, Any]]:
        """
        Return video URLs ranked by quality indicators.

        Ranking factors:
            - Format preference (mp4 > m3u8 > others)
            - Content length (larger = likely higher quality)
            - Master playlist detection (m3u8 master > index)
            - URL quality hints (1080, 720, etc.)
        """
        if not self._video_requests:
            return []

        scored: List[Tuple[float, Dict[str, Any]]] = []

        for req in self._video_requests:
            url = req["url"]
            score = 0.0

            # Format scoring
            fmt = detect_stream_format(url)
            format_scores = {
                StreamFormat.MP4: 100,
                StreamFormat.M3U8: 70,
                StreamFormat.WEBM: 50,
                StreamFormat.FLV: 30,
                StreamFormat.UNKNOWN: 20,
            }
            score += format_scores.get(fmt, 10)

            # Content length bonus
            cl = req.get("content_length") or 0
            if cl > 100_000_000:       # > 100MB
                score += 50
            elif cl > 50_000_000:      # > 50MB
                score += 40
            elif cl > 10_000_000:      # > 10MB
                score += 30
            elif cl > 1_000_000:       # > 1MB
                score += 15

            # Quality from URL
            quality = detect_quality_from_url(url)
            score += quality.priority

            # Master playlist bonus
            url_lower = url.lower()
            if "master" in url_lower and ".m3u8" in url_lower:
                score += 20
            elif "index" in url_lower and ".m3u8" in url_lower:
                score += 10

            # Status code bonus
            status = req.get("status")
            if status and status == 200:
                score += 10

            # Segment penalty — individual segments are not the stream URL
            if self._SEGMENT_PATTERN.search(url):
                score -= 80

            scored.append((score, {**req, "score": score, "format": fmt.value, "quality": quality.value}))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        return [item[1] for item in scored]

    def get_master_playlist(self) -> Optional[str]:
        """
        Find the HLS master playlist URL if one was captured.

        The master playlist is preferred over individual quality playlists
        because it allows the consumer to select quality.
        """
        for req in self._video_requests:
            url = req["url"].lower()
            if ".m3u8" in url:
                if "master" in url or "playlist" in url:
                    return req["url"]

        # Fallback: return first m3u8
        for req in self._video_requests:
            if ".m3u8" in req["url"].lower():
                return req["url"]

        return None

    def extract_referer_chain(self) -> List[str]:
        """
        Build the referer chain from captured requests.

        Useful for constructing the correct Referer header when
        downloading the video.
        """
        referers: List[str] = []
        seen: Set[str] = set()

        for req in self._all_requests:
            referer = req.get("headers", {}).get("referer", "")
            if referer and referer not in seen:
                seen.add(referer)
                referers.append(referer)

        return referers

    def get_stats(self) -> Dict[str, Any]:
        """Return capture statistics."""
        return {
            "total_requests": self.total_request_count,
            "video_requests": self.video_request_count,
            "api_requests": len(self._api_requests),
            "segment_requests": len(self._segment_requests),
            "unique_video_urls": len(set(self.video_urls)),
        }


class HeadlessEngine:
    """
    Playwright-based headless browser engine for video extraction.

    This is the nuclear option — used only when all lighter strategies fail.
    It fully renders the page, executes JavaScript, and intercepts network
    traffic to capture video stream URLs.

    Features:
        - Ad/tracker blocking (blocks known ad domains)
        - Network request interception and classification
        - Stealth configuration (avoids bot detection)
        - Configurable timeout and wait strategies
        - Resource filtering (blocks images/fonts/css for speed)
        - Automatic iframe navigation
    """

    # Domains to block (ads, trackers)
    _BLOCKED_DOMAINS: Set[str] = {
        "doubleclick.net", "googlesyndication.com", "google-analytics.com",
        "googletagmanager.com", "facebook.com", "facebook.net",
        "adserver.com", "popads.net", "popcash.net", "popunder.net",
        "juicyads.com", "exoclick.com", "trafficjunky.net",
        "clickadu.com", "propellerads.com", "adsterra.com",
        "hilltopads.net", "mc.yandex.ru", "top.mail.ru",
        "cdn.onesignal.com", "onesignal.com",
        "quantserve.com", "scorecardresearch.com",
        "outbrain.com", "taboola.com", "mgid.com",
    }

    # Resource types to block (for performance)
    _BLOCKED_RESOURCE_TYPES: Set[str] = {
        "image", "font", "stylesheet",
    }

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
    ) -> None:
        self._config = config
        self._log = logger
        self._available = PLAYWRIGHT_AVAILABLE

        if not self._available:
            self._log.warning(
                "Playwright not installed. Headless extraction disabled. "
                "Install with: pip install playwright && playwright install chromium"
            )

    @property
    def is_available(self) -> bool:
        """Check if Playwright is installed and usable."""
        return self._available

    def extract(
        self,
        server: ServerInfo,
        page_html: Optional[str] = None,
        page_url: str = "",
    ) -> StrategyResult:
        """
        Extract video URLs using headless browser.

        Args:
            server: Target server.
            page_html: Ignored (browser fetches its own page).
            page_url: Original page URL (used as referer context).

        Returns:
            StrategyResult with captured streams.
        """
        start_time = time.time()

        if not self._available:
            return StrategyResult(
                strategy=ExtractionStrategy.HEADLESS_BROWSER,
                success=False,
                error="Playwright not available",
                elapsed=time.time() - start_time,
            )

        embed_url = server.url
        if not embed_url.startswith("http"):
            return StrategyResult(
                strategy=ExtractionStrategy.HEADLESS_BROWSER,
                success=False,
                error="No valid embed URL for headless browsing",
                elapsed=time.time() - start_time,
            )

        self._log.info(f"Launching headless browser for: {embed_url[:70]}")

        capture = NetworkCapture(self._log)
        streams: List[VideoStream] = []

        try:
            streams = self._run_browser(embed_url, page_url, capture)
        except Exception as exc:
            self._log.error(f"Headless engine error: {exc}")
            return StrategyResult(
                strategy=ExtractionStrategy.HEADLESS_BROWSER,
                success=False,
                error=str(exc),
                elapsed=time.time() - start_time,
                metadata={"capture_stats": capture.get_stats()},
            )

        elapsed = time.time() - start_time

        # Update server name on streams
        for stream in streams:
            stream.server_name = server.name
            stream.metadata["server_type"] = server.server_type

        self._log.info(
            f"Headless extraction complete: {len(streams)} stream(s) "
            f"in {elapsed:.2f}s | "
            f"Requests: {capture.total_request_count} total, "
            f"{capture.video_request_count} video"
        )

        return StrategyResult(
            strategy=ExtractionStrategy.HEADLESS_BROWSER,
            success=len(streams) > 0,
            streams=streams,
            elapsed=elapsed,
            metadata={
                "capture_stats": capture.get_stats(),
                "browser_used": "chromium",
            },
        )

    def _run_browser(
        self,
        embed_url: str,
        page_url: str,
        capture: NetworkCapture,
    ) -> List[VideoStream]:
        """
        Core browser automation logic.

        Launches Chromium, navigates to the page, waits for video
        network activity, and extracts stream URLs.
        """
        streams: List[VideoStream] = []

        with sync_playwright() as pw:
            # ── Launch browser ───────────────────────────────────────
            browser = pw.chromium.launch(
                headless=self._config.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-web-security",
                    "--disable-setuid-sandbox",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-zygote",
                    "--single-process",
                    "--disable-extensions",
                ],
            )

            self._log.debug("Browser launched")

            try:
                # ── Create stealth context ───────────────────────────
                context = self._create_stealth_context(browser, page_url)

                self._log.debug("Stealth context created")

                # ── Create page and setup interception ───────────────
                page = context.new_page()
                self._setup_interception(page, capture)

                self._log.debug("Network interception configured")

                # ── Navigate to embed page ───────────────────────────
                self._log.debug(f"Navigating to: {embed_url[:70]}")

                try:
                    page.goto(
                        embed_url,
                        timeout=self._config.browser_timeout,
                        wait_until="domcontentloaded",
                    )
                except Exception as nav_exc:
                    self._log.warning(f"Navigation issue (may be ok): {nav_exc}")

                # ── Wait for video activity ──────────────────────────
                streams = self._wait_for_video(page, capture, embed_url)

                # ── If no streams yet, try clicking play button ──────
                if not streams:
                    self._log.debug("No streams yet, trying to trigger playback...")
                    self._try_trigger_playback(page)

                    # Wait again after triggering
                    additional_streams = self._wait_for_video(
                        page, capture, embed_url, timeout_ms=8000
                    )
                    streams.extend(additional_streams)

                # ── Try extracting from page content ─────────────────
                if not streams:
                    self._log.debug("Trying page content extraction...")
                    content_streams = self._extract_from_page_content(
                        page, embed_url
                    )
                    streams.extend(content_streams)

                # ── Check iframes in the rendered DOM ────────────────
                if not streams:
                    self._log.debug("Checking rendered iframes...")
                    iframe_streams = self._extract_from_iframes(
                        page, capture, embed_url
                    )
                    streams.extend(iframe_streams)

            finally:
                browser.close()
                self._log.debug("Browser closed")

        return streams

    def _create_stealth_context(
        self,
        browser: "Browser",
        referer_url: str,
    ) -> "BrowserContext":
        """
        Create a browser context with stealth/anti-detection settings.

        Configures:
            - Realistic viewport and user agent
            - Timezone and locale
            - WebGL and media codecs
            - Permission handling
        """
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )

        context = browser.new_context(
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            screen={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            color_scheme="dark",
            java_script_enabled=True,
            bypass_csp=True,
            ignore_https_errors=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "DNT": "1",
                "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            },
        )

        # Add stealth scripts to every page
        context.add_init_script("""
            // Override webdriver detection
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined,
            });
            
            // Override chrome detection
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {},
            };
            
            // Override permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // Override plugins (non-empty = not headless)
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5],
            });
            
            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en'],
            });
            
            // Override platform
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32',
            });
            
            // Override hardware concurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8,
            });
            
            // Prevent iframe detection of parent window
            try {
                Object.defineProperty(window, 'parent', {
                    get: () => window,
                });
            } catch(e) {}
        """)

        return context

    def _setup_interception(
        self,
        page: "Page",
        capture: NetworkCapture,
    ) -> None:
        """
        Set up network request/response interception on the page.

        Blocks ads/trackers and unnecessary resources while capturing
        all video-related network activity.
        """

        def handle_route(route: Any) -> None:
            """Route handler for blocking ads and unnecessary resources."""
            request = route.request
            url = request.url
            resource_type = request.resource_type

            # Block ads
            try:
                domain = extract_domain(url)
                if any(blocked in domain for blocked in self._BLOCKED_DOMAINS):
                    route.abort()
                    return
            except Exception:
                pass

            # Block unnecessary resource types
            if resource_type in self._BLOCKED_RESOURCE_TYPES:
                route.abort()
                return

            # Allow everything else
            try:
                route.continue_()
            except Exception:
                pass

        def handle_request(request: Any) -> None:
            """Capture outgoing requests."""
            try:
                capture.record_request(
                    url=request.url,
                    method=request.method,
                    resource_type=request.resource_type,
                    headers=dict(request.headers) if request.headers else {},
                )
            except Exception:
                pass

        def handle_response(response: Any) -> None:
            """Capture incoming responses."""
            try:
                headers = {}
                try:
                    headers = dict(response.headers) if response.headers else {}
                except Exception:
                    pass

                content_type = headers.get("content-type", "")
                content_length = 0
                try:
                    content_length = int(headers.get("content-length", 0))
                except (ValueError, TypeError):
                    pass

                capture.record_response(
                    url=response.url,
                    status=response.status,
                    content_type=content_type,
                    content_length=content_length,
                    headers=headers,
                )
            except Exception:
                pass

        # Register handlers
        page.route("**/*", handle_route)
        page.on("request", handle_request)
        page.on("response", handle_response)

    def _wait_for_video(
        self,
        page: "Page",
        capture: NetworkCapture,
        embed_url: str,
        timeout_ms: Optional[int] = None,
    ) -> List[VideoStream]:
        """
        Wait for video network activity and build streams from captures.

        Uses a polling approach: checks periodically for new video requests
        until timeout or sufficient captures are found.
        """
        timeout = timeout_ms or self._config.intercept_timeout
        poll_interval = 500  # ms
        elapsed = 0
        last_video_count = 0
        stable_count = 0
        stable_threshold = 3  # stop if video count unchanged for N polls

        self._log.debug(f"Waiting for video activity (timeout={timeout}ms)...")

        while elapsed < timeout:
            page.wait_for_timeout(poll_interval)
            elapsed += poll_interval

            current_count = capture.video_request_count

            if current_count > 0:
                if current_count == last_video_count:
                    stable_count += 1
                else:
                    stable_count = 0
                    last_video_count = current_count

                # If captures are stable, we likely have everything
                if stable_count >= stable_threshold:
                    self._log.debug(
                        f"Video captures stable ({current_count} URLs) "
                        f"after {elapsed}ms"
                    )
                    break

                # If we have multiple captures, we can stop sooner
                if current_count >= 3 and stable_count >= 2:
                    break

            last_video_count = current_count

        # Build streams from captures
        return self._captures_to_streams(capture, embed_url)

    def _captures_to_streams(
        self,
        capture: NetworkCapture,
        embed_url: str,
    ) -> List[VideoStream]:
        """Convert network captures to VideoStream objects."""
        streams: List[VideoStream] = []
        seen_urls: Set[str] = set()

        best_videos = capture.get_best_video_urls()

        for video in best_videos:
            url = video["url"]

            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Skip segment files — we want the playlist/full file
            if re.search(r'\.(ts|m4s|seg\d+)(\?|$)', url, re.I):
                continue

            fmt = detect_stream_format(url)
            quality = detect_quality_from_url(url)

            # Determine confidence based on capture quality
            confidence = 70
            if video.get("content_type"):
                ct = video["content_type"].lower()
                if "video/" in ct or "mpegurl" in ct:
                    confidence = 85
            if video.get("content_length") and video["content_length"] > 1_000_000:
                confidence = min(95, confidence + 10)

            # Determine referer chain
            referer_chain = capture.extract_referer_chain()
            best_referer = embed_url
            if referer_chain:
                best_referer = referer_chain[-1]

            streams.append(VideoStream(
                url=url,
                format=fmt,
                quality=quality,
                confidence=confidence,
                server_name="headless",
                headers={
                    "Referer": best_referer,
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    ),
                },
                metadata={
                    "extraction_method": "network_intercept",
                    "handler": "HeadlessEngine",
                    "content_type": video.get("content_type", ""),
                    "content_length": video.get("content_length", 0),
                    "capture_score": video.get("score", 0),
                    "referer_chain": referer_chain[:5],
                },
            ))

        # Also check for master playlist
        master = capture.get_master_playlist()
        if master and master not in seen_urls:
            streams.append(VideoStream(
                url=master,
                format=StreamFormat.M3U8,
                quality=Quality.UNKNOWN,
                confidence=80,
                server_name="headless",
                headers={"Referer": embed_url},
                metadata={
                    "extraction_method": "network_intercept:master_playlist",
                    "handler": "HeadlessEngine",
                },
            ))

        return streams

    def _try_trigger_playback(self, page: "Page") -> None:
        """
        Try to trigger video playback by clicking play buttons
        or interacting with the player.
        """
        # Common play button selectors
        play_selectors = [
            # Generic play buttons
            'button[class*="play"]',
            'div[class*="play"]',
            'span[class*="play"]',
            '.play-button',
            '.btn-play',
            '#play-btn',
            '.vjs-big-play-button',          # Video.js
            '.plyr__control--overlaid',       # Plyr
            '.jw-icon-playback',              # JW Player
            '[aria-label="Play"]',
            '[title="Play"]',

            # Generic large centered buttons (likely play)
            '.overlay-button',
            '.center-button',
            '.video-overlay',

            # Click the video element itself
            'video',
            '.video-container',
            '.player-container',
            '#player',
            '.player',

            # SupJav specific patterns
            '.content-player',
            '#video-player',
            '.video-wrapper',
        ]

        for selector in play_selectors:
            try:
                element = page.query_selector(selector)
                if element and element.is_visible():
                    self._log.debug(f"Clicking play element: {selector}")
                    element.click(force=True)
                    page.wait_for_timeout(1500)

                    # Check if clicking triggered a new page/popup
                    if len(page.context.pages) > 1:
                        # Close popup/ad pages
                        for extra_page in page.context.pages[1:]:
                            try:
                                extra_page.close()
                            except Exception:
                                pass

                    return  # Only click the first matching element

            except Exception:
                continue

        # Fallback: try clicking the center of the page
        try:
            viewport = page.viewport_size
            if viewport:
                center_x = viewport["width"] // 2
                center_y = viewport["height"] // 2
                self._log.debug(f"Clicking page center: ({center_x}, {center_y})")
                page.mouse.click(center_x, center_y)
                page.wait_for_timeout(1500)
        except Exception:
            pass

    def _extract_from_page_content(
        self,
        page: "Page",
        embed_url: str,
    ) -> List[VideoStream]:
        """
        Extract video URLs from the rendered page content (DOM).

        After JavaScript execution, some URLs may be in the DOM
        that weren't in the original HTML.
        """
        streams: List[VideoStream] = []

        try:
            # Get rendered HTML
            html = page.content()

            # Quick scan for video URLs
            url_patterns = [
                re.compile(r'src="(https?://[^"]+\.(?:mp4|m3u8)[^"]*)"', re.I),
                re.compile(r"src='(https?://[^']+\.(?:mp4|m3u8)[^']*)'", re.I),
                re.compile(r'"(https?://[^"]+\.(?:mp4|m3u8)\?[^"]*)"', re.I),
                re.compile(r'source\s+src="(https?://[^"]+)"', re.I),
            ]

            seen: Set[str] = set()
            for pattern in url_patterns:
                for match in pattern.finditer(html):
                    url = match.group(1).strip()
                    if url not in seen and URLSignalAnalyzer.is_likely_video(url, 10):
                        seen.add(url)
                        streams.append(VideoStream(
                            url=url,
                            format=detect_stream_format(url),
                            quality=detect_quality_from_url(url),
                            confidence=60,
                            server_name="headless",
                            headers={"Referer": embed_url},
                            metadata={
                                "extraction_method": "headless:dom_scan",
                                "handler": "HeadlessEngine",
                            },
                        ))

            # Try evaluating JavaScript to get player source
            js_extractions = [
                "document.querySelector('video')?.src",
                "document.querySelector('video source')?.src",
                "document.querySelector('video')?.currentSrc",
                "window.jwplayer?.()?.getPlaylistItem?.()?.file",
                "window.player?.getMediaElement?.()?.src",
                "window.Clappr?.playerInfo?.options?.source",
            ]

            for js_code in js_extractions:
                try:
                    result = page.evaluate(js_code)
                    if result and isinstance(result, str) and result.startswith("http"):
                        if result not in seen:
                            seen.add(result)
                            streams.append(VideoStream(
                                url=result,
                                format=detect_stream_format(result),
                                quality=detect_quality_from_url(result),
                                confidence=75,
                                server_name="headless",
                                headers={"Referer": embed_url},
                                metadata={
                                    "extraction_method": "headless:js_eval",
                                    "handler": "HeadlessEngine",
                                    "js_source": js_code[:50],
                                },
                            ))
                except Exception:
                    continue

        except Exception as exc:
            self._log.debug(f"Page content extraction error: {exc}")

        return streams

    def _extract_from_iframes(
        self,
        page: "Page",
        capture: NetworkCapture,
        embed_url: str,
    ) -> List[VideoStream]:
        """
        Navigate into rendered iframes and extract video content.

        After JavaScript execution, iframes may have been dynamically
        added or modified.
        """
        streams: List[VideoStream] = []

        try:
            frames = page.frames
            self._log.debug(f"Found {len(frames)} frame(s) in rendered page")

            for i, frame in enumerate(frames):
                if frame == page.main_frame:
                    continue

                frame_url = frame.url
                if not frame_url or frame_url == "about:blank":
                    continue

                self._log.debug(f"Checking frame[{i}]: {frame_url[:70]}")

                # Skip ad frames
                if NetworkCapture._AD_PATTERNS.search(frame_url):
                    continue

                try:
                    # Get iframe content
                    frame_html = frame.content()

                    # Scan for video URLs in iframe
                    url_patterns = [
                        re.compile(r'"(https?://[^"]+\.(?:mp4|m3u8)\?[^"]*)"', re.I),
                        re.compile(r"'(https?://[^']+\.(?:mp4|m3u8)\?[^']*)'", re.I),
                        re.compile(r'src="(https?://[^"]+\.(?:mp4|m3u8)[^"]*)"', re.I),
                        re.compile(
                            r'(?:file|src|source|url)\s*[:=]\s*"(https?://[^"]+)"',
                            re.I,
                        ),
                    ]

                    seen: Set[str] = set()
                    for pattern in url_patterns:
                        for match in pattern.finditer(frame_html):
                            url = match.group(1).strip()
                            if (url not in seen and
                                    URLSignalAnalyzer.is_likely_video(url, 10)):
                                seen.add(url)
                                streams.append(VideoStream(
                                    url=url,
                                    format=detect_stream_format(url),
                                    quality=detect_quality_from_url(url),
                                    confidence=65,
                                    server_name="headless",
                                    headers={"Referer": frame_url},
                                    metadata={
                                        "extraction_method": "headless:iframe_scan",
                                        "handler": "HeadlessEngine",
                                        "iframe_url": frame_url[:80],
                                        "frame_index": i,
                                    },
                                ))

                    # Try JS evaluation inside iframe
                    for js_code in [
                        "document.querySelector('video')?.src",
                        "document.querySelector('video')?.currentSrc",
                    ]:
                        try:
                            result = frame.evaluate(js_code)
                            if (result and isinstance(result, str)
                                    and result.startswith("http")
                                    and result not in seen):
                                seen.add(result)
                                streams.append(VideoStream(
                                    url=result,
                                    format=detect_stream_format(result),
                                    quality=detect_quality_from_url(result),
                                    confidence=75,
                                    server_name="headless",
                                    headers={"Referer": frame_url},
                                    metadata={
                                        "extraction_method": "headless:iframe_js_eval",
                                        "handler": "HeadlessEngine",
                                    },
                                ))
                        except Exception:
                            continue

                except Exception as frame_exc:
                    self._log.debug(f"Frame[{i}] extraction error: {frame_exc}")

        except Exception as exc:
            self._log.debug(f"Iframe extraction error: {exc}")

        return streams


class HeadlessEngineConnector:
    """
    Connector that integrates the HeadlessEngine with the FallbackEngine.

    Creates the headless callback function and registers it with the
    fallback engine, completing the escalation chain.
    """

    def __init__(
        self,
        config: ExtractorConfig,
        logger: ExtractorLogger,
        fallback_engine: FallbackEngine,
    ) -> None:
        self._config = config
        self._log = logger
        self._fallback = fallback_engine
        self._headless = HeadlessEngine(config, logger)

        # Register callback if Playwright is available
        if self._headless.is_available:
            self._fallback.set_headless_callback(self._headless_callback)
            self._log.info("HeadlessEngine connected to FallbackEngine")
        else:
            self._log.warning(
                "HeadlessEngine not connected (Playwright unavailable)"
            )

    def _headless_callback(
        self,
        server: ServerInfo,
        page_html: Optional[str],
        page_url: str,
    ) -> StrategyResult:
        """
        Callback function passed to FallbackEngine for headless extraction.

        This is what gets called when the escalation chain reaches
        HEADLESS_BROWSER strategy.
        """
        return self._headless.extract(server, page_html, page_url)

    @property
    def engine(self) -> HeadlessEngine:
        """Direct access to the HeadlessEngine."""
        return self._headless

    @property
    def is_available(self) -> bool:
        """Check if headless extraction is available."""
        return self._headless.is_available


# ═══════════════════════════════════════════════════════════════════════════════
# END OF PART 8
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# PART 9 — MAIN EXTRACTOR (ORCHESTRATOR)
# ═══════════════════════════════════════════════════════════════════════════════
#
# The SupjavExtractor class — central orchestrator that wires together every
# subsystem (parser, server detection, handlers, heuristic scorer, pattern
# learner, fallback engine, headless engine) into a single, coherent
# extraction pipeline.
# ═══════════════════════════════════════════════════════════════════════════════


class PipelineStage(Enum):
    """Named stages of the extraction pipeline for tracking/logging."""
    INIT = "initialization"
    FETCH_PAGE = "fetch_main_page"
    PARSE_PAGE = "parse_main_page"
    DETECT_SERVERS = "detect_servers"
    SELECT_SERVERS = "select_servers"
    EXTRACT_STREAMS = "extract_streams"
    SCORE_STREAMS = "score_streams"
    VALIDATE_STREAMS = "validate_streams"
    FINALIZE = "finalize"


@dataclass
class PipelineContext:
    """
    Mutable context object that flows through the entire pipeline.

    Each stage reads from and writes to this context, enabling
    downstream stages to use results from upstream stages.
    """
    # Input
    input_url: str = ""
    preferred_server: Optional[str] = None
    preferred_quality: Optional[Quality] = None
    preferred_format: Optional[StreamFormat] = None
    debug: bool = False
    skip_headless: bool = False
    validate_streams: bool = True
    max_servers: int = 5

    # Pipeline state
    current_stage: PipelineStage = PipelineStage.INIT
    start_time: float = field(default_factory=time.time)
    stage_timings: Dict[str, float] = field(default_factory=dict)

    # Fetched data
    main_page_html: Optional[str] = None
    main_page_url: str = ""                       # may differ from input_url after redirects

    # Parsed data
    page_metadata: Dict[str, Any] = field(default_factory=dict)
    parse_result: Optional[Dict[str, Any]] = None

    # Server data
    detected_servers: List[ServerInfo] = field(default_factory=list)
    selected_servers: List[ServerInfo] = field(default_factory=list)
    preferred_server_match: Optional[ServerInfo] = None

    # Stream data
    raw_streams: List[VideoStream] = field(default_factory=list)
    scored_streams: List[VideoStream] = field(default_factory=list)
    validated_streams: List[VideoStream] = field(default_factory=list)
    best_stream: Optional[VideoStream] = None

    # Diagnostics
    strategies_tried: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def record_stage_time(self, stage: PipelineStage, elapsed: float) -> None:
        """Record how long a pipeline stage took."""
        self.stage_timings[stage.value] = round(elapsed, 3)

    @property
    def elapsed_total(self) -> float:
        """Total elapsed time since pipeline start."""
        return time.time() - self.start_time

    @property
    def has_streams(self) -> bool:
        return len(self.raw_streams) > 0 or len(self.scored_streams) > 0

    def add_error(self, stage: PipelineStage, error: str) -> None:
        self.errors.append(f"[{stage.value}] {error}")

    def add_warning(self, stage: PipelineStage, warning: str) -> None:
        self.warnings.append(f"[{stage.value}] {warning}")


class URLNormalizer:
    """
    Normalizes and validates SupJav input URLs.

    Handles various URL formats users might provide:
        - Full URLs (https://supjav.com/...)
        - URLs without scheme (supjav.com/...)
        - Mobile URLs (m.supjav.com/...)
        - URLs with language prefixes (/en/, /ja/, /zh/)
        - Short/share URLs
    """

    _SUPJAV_PATTERN = re.compile(
        r'(?:https?://)?(?:www\.|m\.)?supjav\.com/(.+)',
        re.I,
    )

    _LANGUAGE_PREFIXES = {"en", "ja", "zh", "ko"}

    @classmethod
    def normalize(cls, url: str) -> Optional[str]:
        """
        Normalize a SupJav URL to canonical form.

        Args:
            url: Raw user-provided URL.

        Returns:
            Normalized URL or None if not a valid SupJav URL.
        """
        url = url.strip()

        # Add scheme if missing
        if not url.startswith(("http://", "https://")):
            if "supjav.com" in url.lower():
                url = "https://" + url
            else:
                return None

        # Validate it's a SupJav URL
        match = cls._SUPJAV_PATTERN.match(url)
        if not match:
            return None

        path = match.group(1)

        # Normalize: strip trailing slashes, fix double slashes
        path = re.sub(r'/+', '/', path).strip("/")

        # Reconstruct canonical URL
        return f"https://supjav.com/{path}"

    @classmethod
    def is_supjav_url(cls, url: str) -> bool:
        """Quick check if URL belongs to SupJav."""
        return bool(cls._SUPJAV_PATTERN.match(url.strip()))

    @classmethod
    def extract_slug(cls, url: str) -> Optional[str]:
        """
        Extract the video slug/identifier from a SupJav URL.

        Example:
            https://supjav.com/en/123456-video-title → "123456-video-title"
        """
        match = cls._SUPJAV_PATTERN.match(url.strip())
        if not match:
            return None

        path = match.group(1).strip("/")

        # Remove language prefix
        parts = path.split("/")
        if parts and parts[0].lower() in cls._LANGUAGE_PREFIXES:
            parts = parts[1:]

        return "/".join(parts) if parts else None


class SupjavExtractor:
    """
    The main orchestrator class — the single entry point for video extraction.

    Wires together all subsystems and executes the full pipeline:

        1. INIT         — validate URL, configure subsystems
        2. FETCH_PAGE   — download the main SupJav page
        3. PARSE_PAGE   — extract metadata, iframes, scripts
        4. DETECT       — identify available video servers
        5. SELECT       — choose servers to try (preference-aware)
        6. EXTRACT      — run fallback chain on each server
        7. SCORE        — rank all discovered streams
        8. VALIDATE     — HEAD-check top candidates
        9. FINALIZE     — select best stream, build result

    Usage:
        extractor = SupjavExtractor(debug=True)
        result = extractor.extract("https://supjav.com/en/some-video")
        if result.success:
            print(result.best_stream.url)
    """

    def __init__(
        self,
        debug: bool = False,
        config: Optional[ExtractorConfig] = None,
        **config_overrides: Any,
    ) -> None:
        """
        Initialize the extractor with all subsystems.

        Args:
            debug: Enable verbose debug logging.
            config: Pre-built config (overrides other params).
            **config_overrides: Individual config field overrides.
        """
        # ── Configuration ────────────────────────────────────────────
        if config:
            self._config = config
            if debug:
                self._config.debug = True
                self._config.log_level = "DEBUG"
        else:
            config_overrides["debug"] = debug
            self._config = ExtractorConfig(**config_overrides)

        # ── Core subsystems ──────────────────────────────────────────
        self._log = ExtractorLogger(self._config)
        self._http = HttpClient(self._config, self._log)

        # ── Parser ───────────────────────────────────────────────────
        self._parser = AdaptiveParser(self._config, self._log)

        # ── Heuristic scorer ─────────────────────────────────────────
        self._scorer = HeuristicScorer(self._config, self._log)

        # ── Deduplicator ─────────────────────────────────────────────
        self._deduplicator = StreamDeduplicator(self._log)

        # ── Validator ────────────────────────────────────────────────
        self._validator = StreamValidator(self._http, self._log)

        # ── Quality matcher ──────────────────────────────────────────
        self._quality_matcher = QualityMatcher()

        # ── Pattern learner ──────────────────────────────────────────
        self._learner = PatternLearner(self._config, self._log)

        # ── Server detection ─────────────────────────────────────────
        self._server_detector = ServerDetectionEngine(
            self._config, self._log, self._http, self._parser
        )

        # ── Handler registry ─────────────────────────────────────────
        self._handler_registry = HandlerRegistry(
            self._config, self._log, self._http, self._parser
        )

        # ── Fallback engine ──────────────────────────────────────────
        self._fallback_engine = FallbackEngine(
            self._config, self._log, self._http,
            self._handler_registry, self._parser,
            self._learner, self._scorer,
        )

        # ── Headless engine (connects to fallback) ───────────────────
        self._headless_connector = HeadlessEngineConnector(
            self._config, self._log, self._fallback_engine
        )

        # ── Pipeline state ───────────────────────────────────────────
        self._extraction_count: int = 0

        self._log.section(f"SupJav Extractor v{__version__} Ready")
        self._log.info(f"Playwright: {'✓' if PLAYWRIGHT_AVAILABLE else '✗'}")
        self._log.info(f"HTML Parser: {self._config.html_parser}")
        self._log.info(
            f"Handlers: {self._handler_registry.get_stats()['total_handlers']} registered"
        )

    # ═════════════════════════════════════════════════════════════════════
    # PUBLIC API
    # ═════════════════════════════════════════════════════════════════════

    def extract(
        self,
        url: str,
        preferred_server: Optional[str] = None,
        preferred_quality: Optional[str] = None,
        preferred_format: Optional[str] = None,
        debug: Optional[bool] = None,
        skip_headless: bool = False,
        validate: bool = True,
        max_servers: int = 5,
    ) -> ExtractionResult:
        """
        Extract video stream URL(s) from a SupJav page.

        This is the primary public method — call this to extract videos.

        Args:
            url: SupJav video page URL.
            preferred_server: Preferred server name (e.g., "streamtape").
            preferred_quality: Preferred quality (e.g., "1080p").
            preferred_format: Preferred format (e.g., "mp4").
            debug: Override debug mode for this extraction.
            skip_headless: Skip headless browser even as last resort.
            validate: Whether to HEAD-validate top stream candidates.
            max_servers: Maximum number of servers to try.

        Returns:
            ExtractionResult with all discovered streams and the best one.
        """
        # ── Setup pipeline context ───────────────────────────────────
        ctx = PipelineContext(
            input_url=url,
            preferred_server=preferred_server,
            preferred_quality=self._parse_quality_preference(preferred_quality),
            preferred_format=self._parse_format_preference(preferred_format),
            debug=debug if debug is not None else self._config.debug,
            skip_headless=skip_headless,
            validate_streams=validate,
            max_servers=max_servers,
        )

        self._extraction_count += 1
        request_id = generate_request_id()

        self._log.section(
            f"Extraction #{self._extraction_count} [{request_id}]"
        )
        self._log.info(f"URL: {url}")
        if preferred_server:
            self._log.info(f"Preferred server: {preferred_server}")
        if preferred_quality:
            self._log.info(f"Preferred quality: {preferred_quality}")

        # ── Execute pipeline ─────────────────────────────────────────
        try:
            result = self._execute_pipeline(ctx)
        except Exception as exc:
            self._log.critical(f"Pipeline crashed: {exc}")
            result = ExtractionResult(
                success=False,
                errors=[f"Pipeline crash: {str(exc)}"],
                elapsed_time=ctx.elapsed_total,
                metadata={
                    "request_id": request_id,
                    "crash": True,
                    "exception": type(exc).__name__,
                },
            )

        # ── Attach metadata ──────────────────────────────────────────
        result.metadata["request_id"] = request_id
        result.metadata["input_url"] = url
        result.metadata["extraction_number"] = self._extraction_count
        result.metadata["stage_timings"] = ctx.stage_timings
        result.metadata["page_metadata"] = ctx.page_metadata

        # ── Summary ──────────────────────────────────────────────────
        self._log.section("Extraction Complete")
        self._log_result_summary(result)

        return result

    # ═════════════════════════════════════════════════════════════════════
    # PIPELINE STAGES
    # ═════════════════════════════════════════════════════════════════════

    def _execute_pipeline(self, ctx: PipelineContext) -> ExtractionResult:
        """
        Execute the full extraction pipeline sequentially.

        Each stage is isolated — failures in one stage produce
        warnings but don't necessarily abort the pipeline.
        """
        # ── Stage 1: INIT ────────────────────────────────────────────
        if not self._stage_init(ctx):
            return ExtractionResult(
                success=False,
                errors=ctx.errors,
                elapsed_time=ctx.elapsed_total,
            )

        # ── Stage 2: FETCH_PAGE ──────────────────────────────────────
        if not self._stage_fetch_page(ctx):
            return ExtractionResult(
                success=False,
                errors=ctx.errors,
                elapsed_time=ctx.elapsed_total,
            )

        # ── Stage 3: PARSE_PAGE ──────────────────────────────────────
        self._stage_parse_page(ctx)

        # ── Stage 4: DETECT_SERVERS ──────────────────────────────────
        self._stage_detect_servers(ctx)

        # ── Stage 5: SELECT_SERVERS ──────────────────────────────────
        self._stage_select_servers(ctx)

        # ── Stage 6: EXTRACT_STREAMS ─────────────────────────────────
        self._stage_extract_streams(ctx)

        # ── Stage 7: SCORE_STREAMS ───────────────────────────────────
        self._stage_score_streams(ctx)

        # ── Stage 8: VALIDATE_STREAMS ────────────────────────────────
        if ctx.validate_streams:
            self._stage_validate_streams(ctx)

        # ── Stage 9: FINALIZE ────────────────────────────────────────
        return self._stage_finalize(ctx)

    def _stage_init(self, ctx: PipelineContext) -> bool:
        """
        Stage 1: Validate URL and initialize pipeline context.

        Returns False if the URL is invalid (hard failure).
        """
        stage = PipelineStage.INIT
        ctx.current_stage = stage
        stage_start = time.time()

        self._log.info(f"[{stage.value}] Validating input URL...")

        # Normalize URL
        normalized = URLNormalizer.normalize(ctx.input_url)

        if not normalized:
            # Allow non-SupJav URLs as direct embed URLs
            if ctx.input_url.startswith("http"):
                self._log.warning(
                    f"URL is not a SupJav page — treating as direct embed URL"
                )
                ctx.main_page_url = ctx.input_url
            else:
                ctx.add_error(stage, f"Invalid URL: {ctx.input_url}")
                self._log.error(f"Invalid URL: {ctx.input_url}")
                ctx.record_stage_time(stage, time.time() - stage_start)
                return False
        else:
            ctx.main_page_url = normalized
            self._log.debug(f"Normalized URL: {normalized}")

        slug = URLNormalizer.extract_slug(ctx.input_url)
        if slug:
            self._log.debug(f"Video slug: {slug}")
            ctx.page_metadata["slug"] = slug

        ctx.record_stage_time(stage, time.time() - stage_start)
        return True

    def _stage_fetch_page(self, ctx: PipelineContext) -> bool:
        """
        Stage 2: Fetch the main SupJav page HTML.

        Returns False if the page cannot be fetched (hard failure).
        """
        stage = PipelineStage.FETCH_PAGE
        ctx.current_stage = stage
        stage_start = time.time()

        self._log.info(f"[{stage.value}] Fetching page...")

        response = self._http.get(ctx.main_page_url)

        if not response:
            ctx.add_error(stage, f"Failed to fetch page: {ctx.main_page_url}")
            self._log.error(f"Failed to fetch: {ctx.main_page_url}")
            ctx.record_stage_time(stage, time.time() - stage_start)
            return False

        ctx.main_page_html = response.text
        ctx.main_page_url = response.url  # update in case of redirect

        self._log.info(
            f"Page fetched: {len(ctx.main_page_html)} bytes | "
            f"Final URL: {ctx.main_page_url[:70]}"
        )

        ctx.record_stage_time(stage, time.time() - stage_start)
        return True

    def _stage_parse_page(self, ctx: PipelineContext) -> None:
        """
        Stage 3: Parse the main page for metadata, iframes, and scripts.

        This is a soft stage — failures produce warnings but don't abort.
        """
        stage = PipelineStage.PARSE_PAGE
        ctx.current_stage = stage
        stage_start = time.time()

        self._log.info(f"[{stage.value}] Parsing page content...")

        try:
            parse_result = self._parser.parse_page(
                ctx.main_page_html, ctx.main_page_url
            )
            ctx.parse_result = parse_result
            ctx.page_metadata.update(parse_result.get("metadata", {}))

            self._log.info(
                f"Parsed: {len(parse_result.get('iframes', []))} iframes, "
                f"{len(parse_result.get('video_urls', []))} video URLs, "
                f"{parse_result.get('scripts_analyzed', 0)} scripts"
            )

            # Convert any direct video URLs found to streams
            for finding in parse_result.get("video_urls", []):
                url = finding.get("url", "")
                if url and url.startswith("http"):
                    ctx.raw_streams.append(VideoStream(
                        url=url,
                        format=detect_stream_format(url),
                        quality=detect_quality_from_url(url),
                        confidence=finding.get("confidence", 40),
                        server_name="main_page",
                        headers={"Referer": ctx.main_page_url},
                        metadata={
                            "extraction_method": f"main_page:{finding.get('pattern', 'parse')}",
                            "handler": "AdaptiveParser",
                            "source": finding.get("source", ""),
                            "was_obfuscated": finding.get("was_obfuscated", False),
                        },
                    ))

        except Exception as exc:
            ctx.add_warning(stage, f"Parse error: {exc}")
            self._log.warning(f"Page parse error: {exc}")

        ctx.record_stage_time(stage, time.time() - stage_start)

    def _stage_detect_servers(self, ctx: PipelineContext) -> None:
        """
        Stage 4: Detect all available video hosting servers.
        """
        stage = PipelineStage.DETECT_SERVERS
        ctx.current_stage = stage
        stage_start = time.time()

        self._log.info(f"[{stage.value}] Detecting video servers...")

        try:
            servers = self._server_detector.detect_from_html(
                ctx.main_page_html, ctx.main_page_url
            )
            ctx.detected_servers = servers

            if not servers:
                ctx.add_warning(stage, "No servers detected on page")
                self._log.warning("No video servers detected")

                # Fallback: create a pseudo-server from iframes
                if ctx.parse_result:
                    iframes = ctx.parse_result.get("iframes", [])
                    for iframe in iframes:
                        classification = iframe.get("classification", {})
                        if classification.get("is_ad"):
                            continue

                        server_hint = classification.get("server_hint", "unknown")
                        ctx.detected_servers.append(ServerInfo(
                            name=server_hint if server_hint != "unknown" else extract_domain(iframe["url"]),
                            url=iframe["url"],
                            server_type=server_hint,
                            confidence=45,
                            priority=40,
                            metadata={"source": "iframe_fallback"},
                        ))

                    if ctx.detected_servers:
                        self._log.info(
                            f"Created {len(ctx.detected_servers)} server(s) from iframes"
                        )

        except Exception as exc:
            ctx.add_warning(stage, f"Server detection error: {exc}")
            self._log.warning(f"Server detection error: {exc}")

        ctx.record_stage_time(stage, time.time() - stage_start)

    def _stage_select_servers(self, ctx: PipelineContext) -> None:
        """
        Stage 5: Select and order servers for extraction attempts.

        Applies:
            - User preference matching
            - Learned pattern ordering
            - Confidence/reliability sorting
        """
        stage = PipelineStage.SELECT_SERVERS
        ctx.current_stage = stage
        stage_start = time.time()

        self._log.info(f"[{stage.value}] Selecting servers...")

        if not ctx.detected_servers:
            ctx.add_warning(stage, "No servers available for selection")
            ctx.record_stage_time(stage, time.time() - stage_start)
            return

        # Apply learned ordering
        ordered = self._learner.suggest_server_order(ctx.detected_servers)

        # Check for preferred server
        if ctx.preferred_server:
            preferred = self._server_detector.find_preferred_server(
                ordered, ctx.preferred_server
            )
            if preferred:
                ctx.preferred_server_match = preferred
                # Move preferred to front
                ordered = [preferred] + [s for s in ordered if s != preferred]
                self._log.info(f"Preferred server found: {preferred.name}")
            else:
                ctx.add_warning(
                    stage,
                    f"Preferred server '{ctx.preferred_server}' not found"
                )

        # Limit to max_servers
        ctx.selected_servers = ordered[:ctx.max_servers]

        self._log.info(
            f"Selected {len(ctx.selected_servers)} server(s) for extraction: "
            f"{', '.join(s.name for s in ctx.selected_servers)}"
        )

        ctx.record_stage_time(stage, time.time() - stage_start)

    def _stage_extract_streams(self, ctx: PipelineContext) -> None:
        """
        Stage 6: Extract video streams from selected servers.

        Uses the FallbackEngine for multi-server, multi-strategy extraction.
        """
        stage = PipelineStage.EXTRACT_STREAMS
        ctx.current_stage = stage
        stage_start = time.time()

        self._log.info(f"[{stage.value}] Extracting streams...")

        if not ctx.selected_servers:
            # No servers detected — try direct extraction from page
            if ctx.raw_streams:
                self._log.info(
                    f"Using {len(ctx.raw_streams)} stream(s) from page parse"
                )
            else:
                ctx.add_warning(stage, "No servers to extract from")

                # Last-ditch: try headless on the main page itself
                if not ctx.skip_headless and PLAYWRIGHT_AVAILABLE:
                    self._log.info("Attempting headless extraction on main page...")
                    pseudo_server = ServerInfo(
                        name="main_page",
                        url=ctx.main_page_url,
                        server_type="unknown",
                        confidence=30,
                    )
                    headless_result = self._headless_connector.engine.extract(
                        pseudo_server, ctx.main_page_html, ctx.main_page_url
                    )
                    if headless_result.streams:
                        ctx.raw_streams.extend(headless_result.streams)
                        ctx.strategies_tried.append("headless:main_page")

            ctx.record_stage_time(stage, time.time() - stage_start)
            return

        # Run multi-server extraction
        extraction_result = self._fallback_engine.execute_multi_server(
            servers=ctx.selected_servers,
            page_html=None,     # servers need their own embed pages
            page_url=ctx.main_page_url,
            max_servers=ctx.max_servers,
            stop_on_first_success=False,
            skip_headless=ctx.skip_headless,
        )

        # Merge results
        ctx.raw_streams.extend(extraction_result.streams)
        ctx.strategies_tried.extend(extraction_result.strategies_tried)
        ctx.errors.extend(extraction_result.errors)

        self._log.info(
            f"Extraction yielded {len(extraction_result.streams)} stream(s) "
            f"from {len(ctx.selected_servers)} server(s)"
        )

        ctx.record_stage_time(stage, time.time() - stage_start)

    def _stage_score_streams(self, ctx: PipelineContext) -> None:
        """
        Stage 7: Score, deduplicate, and rank all discovered streams.
        """
        stage = PipelineStage.SCORE_STREAMS
        ctx.current_stage = stage
        stage_start = time.time()

        self._log.info(f"[{stage.value}] Scoring {len(ctx.raw_streams)} stream(s)...")

        if not ctx.raw_streams:
            ctx.record_stage_time(stage, time.time() - stage_start)
            return

        # Deduplicate
        deduped = self._deduplicator.deduplicate(ctx.raw_streams)

        # Score each stream
        for stream in deduped:
            context = {
                "extraction_method": stream.metadata.get("extraction_method", ""),
                "follow_depth": stream.metadata.get("follow_depth", 0),
                "was_obfuscated": stream.metadata.get("was_obfuscated", False),
                "pattern_match": stream.metadata.get("pattern_match", False),
            }
            self._scorer.score_video_stream(stream, context)

        # Rank
        ctx.scored_streams = self._scorer.rank_streams(deduped)

        self._log.info(f"Scored and ranked {len(ctx.scored_streams)} unique stream(s)")

        ctx.record_stage_time(stage, time.time() - stage_start)

    def _stage_validate_streams(self, ctx: PipelineContext) -> None:
        """
        Stage 8: Validate top stream candidates via HEAD requests.
        """
        stage = PipelineStage.VALIDATE_STREAMS
        ctx.current_stage = stage
        stage_start = time.time()

        if not ctx.scored_streams:
            ctx.record_stage_time(stage, time.time() - stage_start)
            return

        self._log.info(
            f"[{stage.value}] Validating top {min(5, len(ctx.scored_streams))} stream(s)..."
        )

        ctx.validated_streams = self._validator.validate_and_boost(
            ctx.scored_streams, max_checks=5
        )

        # Re-rank after validation boosts
        ctx.validated_streams = self._scorer.rank_streams(ctx.validated_streams)

        validated_count = sum(
            1 for s in ctx.validated_streams
            if s.metadata.get("validated", False)
        )

        self._log.info(
            f"Validation: {validated_count}/{min(5, len(ctx.scored_streams))} "
            f"confirmed accessible"
        )

        ctx.record_stage_time(stage, time.time() - stage_start)

    def _stage_finalize(self, ctx: PipelineContext) -> ExtractionResult:
        """
        Stage 9: Build the final ExtractionResult.

        Selects the best stream considering user preferences,
        and packages everything into the output format.
        """
        stage = PipelineStage.FINALIZE
        ctx.current_stage = stage
        stage_start = time.time()

        self._log.info(f"[{stage.value}] Finalizing result...")

        final_streams = ctx.validated_streams or ctx.scored_streams or ctx.raw_streams

        # Select best stream with preference awareness
        best_stream: Optional[VideoStream] = None

        if final_streams:
            # Apply quality preference
            if ctx.preferred_quality:
                best_stream = self._quality_matcher.find_best_quality_match(
                    final_streams, ctx.preferred_quality
                )
            
            # Apply format preference
            if not best_stream and ctx.preferred_format:
                format_matches = [
                    s for s in final_streams if s.format == ctx.preferred_format
                ]
                if format_matches:
                    best_stream = self._scorer.select_best(
                        format_matches,
                        preferred_format=ctx.preferred_format,
                        preferred_quality=ctx.preferred_quality,
                    )

            # Default: select by score
            if not best_stream:
                best_stream = self._scorer.select_best(
                    final_streams,
                    preferred_format=ctx.preferred_format,
                    preferred_quality=ctx.preferred_quality,
                )

            ctx.best_stream = best_stream

            # Record successful pattern
            if best_stream:
                # Find the server this stream came from
                matching_servers = [
                    s for s in ctx.detected_servers
                    if s.name == best_stream.server_name
                ]
                if matching_servers:
                    self._learner.record_success(best_stream, matching_servers[0])
                elif ctx.detected_servers:
                    self._learner.record_success(best_stream, ctx.detected_servers[0])

        # Build final result
        result = ExtractionResult(
            success=best_stream is not None,
            streams=final_streams,
            best_stream=best_stream,
            servers_found=ctx.detected_servers,
            strategies_tried=ctx.strategies_tried,
            errors=ctx.errors,
            elapsed_time=ctx.elapsed_total,
            metadata={
                "warnings": ctx.warnings,
                "stage_timings": ctx.stage_timings,
                "page_metadata": ctx.page_metadata,
                "servers_selected": len(ctx.selected_servers),
                "streams_before_dedup": len(ctx.raw_streams),
                "streams_after_dedup": len(ctx.scored_streams),
                "learner_stats": self._learner.get_stats(),
            },
        )

        ctx.record_stage_time(stage, time.time() - stage_start)

        return result

    # ═════════════════════════════════════════════════════════════════════
    # UTILITY METHODS
    # ═════════════════════════════════════════════════════════════════════

    def _log_result_summary(self, result: ExtractionResult) -> None:
        """Log a human-readable summary of the extraction result."""
        if result.success:
            best = result.best_stream
            self._log.info(
                f"✅ SUCCESS | "
                f"{len(result.streams)} stream(s) found | "
                f"Best: score={best.score:.1f} "
                f"fmt={best.format.value} "
                f"q={best.quality.value} "
                f"srv={best.server_name}"
            )
            self._log.info(f"   URL: {best.url[:100]}")

            if best.headers:
                self._log.debug(f"   Headers required: {list(best.headers.keys())}")
        else:
            self._log.error(
                f"❌ FAILED | "
                f"Tried {len(result.strategies_tried)} strategies | "
                f"{len(result.errors)} errors"
            )
            for error in result.errors[:5]:
                self._log.error(f"   • {error}")

        self._log.info(f"⏱️  Total time: {result.elapsed_time:.2f}s")

        # Stage timings
        timings = result.metadata.get("stage_timings", {})
        if timings:
            timing_str = " → ".join(
                f"{k}:{v:.1f}s" for k, v in timings.items()
            )
            self._log.debug(f"   Stages: {timing_str}")

    @staticmethod
    def _parse_quality_preference(quality_str: Optional[str]) -> Optional[Quality]:
        """Parse a quality preference string to Quality enum."""
        if not quality_str:
            return None

        quality_map = {
            "2160p": Quality.Q4K, "4k": Quality.Q4K, "uhd": Quality.Q4K,
            "1080p": Quality.Q1080, "1080": Quality.Q1080, "fhd": Quality.Q1080,
            "720p": Quality.Q720, "720": Quality.Q720, "hd": Quality.Q720,
            "480p": Quality.Q480, "480": Quality.Q480, "sd": Quality.Q480,
            "360p": Quality.Q360, "360": Quality.Q360,
        }

        return quality_map.get(quality_str.lower().strip(), None)

    @staticmethod
    def _parse_format_preference(format_str: Optional[str]) -> Optional[StreamFormat]:
        """Parse a format preference string to StreamFormat enum."""
        if not format_str:
            return None

        format_map = {
            "mp4": StreamFormat.MP4,
            "m3u8": StreamFormat.M3U8, "hls": StreamFormat.M3U8,
            "webm": StreamFormat.WEBM,
            "flv": StreamFormat.FLV,
        }

        return format_map.get(format_str.lower().strip(), None)

    # ═════════════════════════════════════════════════════════════════════
    # DIAGNOSTIC / ADVANCED API
    # ═════════════════════════════════════════════════════════════════════

    def get_servers(self, url: str) -> List[ServerInfo]:
        """
        Standalone server detection — returns available servers without extraction.

        Args:
            url: SupJav page URL.

        Returns:
            List of detected ServerInfo objects.
        """
        normalized = URLNormalizer.normalize(url) or url
        response = self._http.get(normalized)
        if not response:
            return []
        return self._server_detector.detect_from_html(response.text, normalized)

    def extract_from_embed(
        self,
        embed_url: str,
        server_type: Optional[str] = None,
    ) -> ExtractionResult:
        """
        Extract directly from an embed URL (skip SupJav page parsing).

        Useful when you already know the embed URL and server type.

        Args:
            embed_url: Direct embed page URL.
            server_type: Server type hint (e.g., "doodstream").

        Returns:
            ExtractionResult.
        """
        self._log.section(f"Direct Embed Extraction: {embed_url[:70]}")

        # Create a pseudo-server
        fingerprint = ServerFingerprintDatabase.identify_url(embed_url)
        server = ServerInfo(
            name=fingerprint.canonical_name if fingerprint else extract_domain(embed_url),
            url=embed_url,
            server_type=(
                server_type or
                (fingerprint.name if fingerprint else "unknown")
            ),
            confidence=70,
            priority=60,
        )

        # Run fallback chain
        result = self._fallback_engine.execute_fallback_chain(
            server=server,
            page_url=embed_url,
        )

        return result

    def get_stats(self) -> Dict[str, Any]:
        """Return comprehensive extractor statistics."""
        return {
            "version": __version__,
            "extraction_count": self._extraction_count,
            "handlers": self._handler_registry.get_stats(),
            "learner": self._learner.get_stats(),
            "server_detection": self._server_detector.get_detection_stats(),
            "fallback_engine": self._fallback_engine.get_escalation_stats(),
            "headless_available": PLAYWRIGHT_AVAILABLE,
            "config": {
                "html_parser": self._config.html_parser,
                "request_timeout": self._config.request_timeout,
                "max_retries": self._config.max_retries,
                "preferred_quality": self._config.preferred_quality.value,
            },
        }

    def maintenance(self) -> Dict[str, Any]:
        """Run maintenance tasks (pattern pruning, cache cleanup)."""
        learner_report = self._learner.maintenance()
        self._server_detector.clear_cache()

        return {
            "learner": learner_report,
            "server_cache_cleared": True,
        }

    @property
    def config(self) -> ExtractorConfig:
        """Access the configuration."""
        return self._config

    @property
    def learner(self) -> PatternLearner:
        """Access the pattern learner."""
        return self._learner


# ═══════════════════════════════════════════════════════════════════════════════
# END OF PART 9
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# PART 10 — FINAL OUTPUT INTERFACE
# ═══════════════════════════════════════════════════════════════════════════════
#
# Clean public API layer that wraps the SupjavExtractor with:
#   - Simple function-based entry points
#   - Error-safe execution with graceful degradation
#   - Structured output formatting (dict, JSON, pretty-print)
#   - CLI interface for command-line usage
#   - Session management for batch extractions
# ═══════════════════════════════════════════════════════════════════════════════


class OutputFormatter:
    """
    Formats ExtractionResult into various output representations.

    Supports:
        - Minimal dict (just the essentials)
        - Full dict (all data)
        - JSON string
        - Pretty-printed human-readable text
        - Markdown table
    """

    @staticmethod
    def minimal(result: ExtractionResult) -> Dict[str, Any]:
        """
        Minimal output — only what's needed to use the stream.

        Returns:
            {
                "success": bool,
                "url": str or None,
                "format": str,
                "quality": str,
                "headers": dict,
            }
        """
        if not result.success or not result.best_stream:
            return {
                "success": False,
                "url": None,
                "format": None,
                "quality": None,
                "headers": {},
                "error": result.errors[0] if result.errors else "No streams found",
            }

        best = result.best_stream
        return {
            "success": True,
            "url": best.url,
            "format": best.format.value,
            "quality": best.quality.value,
            "headers": best.headers,
        }

    @staticmethod
    def standard(result: ExtractionResult) -> Dict[str, Any]:
        """
        Standard output — essential data with some diagnostics.

        Returns a middle-ground between minimal and full.
        """
        output: Dict[str, Any] = {
            "success": result.success,
            "elapsed_time": round(result.elapsed_time, 2),
        }

        if result.best_stream:
            best = result.best_stream
            output["best_stream"] = {
                "url": best.url,
                "format": best.format.value,
                "quality": best.quality.value,
                "confidence": best.confidence,
                "score": round(best.score, 2),
                "server": best.server_name,
                "headers": best.headers,
            }
        else:
            output["best_stream"] = None

        output["streams_count"] = len(result.streams)
        output["servers_found"] = [
            {"name": s.name, "type": s.server_type}
            for s in result.servers_found
        ]
        output["errors"] = result.errors

        # Include alternative streams (top 5)
        if len(result.streams) > 1:
            output["alternatives"] = [
                {
                    "url": s.url,
                    "format": s.format.value,
                    "quality": s.quality.value,
                    "confidence": s.confidence,
                    "server": s.server_name,
                }
                for s in sorted(result.streams, key=lambda x: x.score, reverse=True)[1:6]
            ]

        return output

    @staticmethod
    def full(result: ExtractionResult) -> Dict[str, Any]:
        """
        Full output — complete data dump for debugging.

        Includes all streams, all metadata, stage timings, etc.
        """
        return result.to_dict()

    @staticmethod
    def to_json(
        result: ExtractionResult,
        mode: str = "standard",
        indent: int = 2,
    ) -> str:
        """
        Convert result to JSON string.

        Args:
            result: The ExtractionResult.
            mode: "minimal", "standard", or "full".
            indent: JSON indentation.

        Returns:
            JSON string.
        """
        formatters = {
            "minimal": OutputFormatter.minimal,
            "standard": OutputFormatter.standard,
            "full": OutputFormatter.full,
        }

        formatter = formatters.get(mode, OutputFormatter.standard)
        data = formatter(result)

        return json.dumps(data, indent=indent, ensure_ascii=False, default=str)

    @staticmethod
    def pretty_print(result: ExtractionResult) -> str:
        """
        Human-readable pretty-printed output.

        Returns a formatted string suitable for terminal display.
        """
        lines: List[str] = []
        sep = "═" * 70

        lines.append(sep)
        lines.append("  SUPJAV EXTRACTOR — RESULT")
        lines.append(sep)
        lines.append("")

        # Status
        status = "✅ SUCCESS" if result.success else "❌ FAILED"
        lines.append(f"  Status:       {status}")
        lines.append(f"  Time:         {result.elapsed_time:.2f}s")
        lines.append(f"  Streams:      {len(result.streams)} found")
        lines.append(f"  Servers:      {len(result.servers_found)} detected")
        lines.append(f"  Strategies:   {len(result.strategies_tried)} tried")
        lines.append("")

        # Best stream
        if result.best_stream:
            best = result.best_stream
            lines.append("  ┌─── BEST STREAM ─────────────────────────────────────┐")
            lines.append(f"  │ URL:        {best.url[:55]}")
            if len(best.url) > 55:
                lines.append(f"  │             {best.url[55:110]}")
                if len(best.url) > 110:
                    lines.append(f"  │             {best.url[110:165]}...")
            lines.append(f"  │ Format:     {best.format.value}")
            lines.append(f"  │ Quality:    {best.quality.value}")
            lines.append(f"  │ Confidence: {best.confidence}/100")
            lines.append(f"  │ Score:      {best.score:.1f}")
            lines.append(f"  │ Server:     {best.server_name}")
            if best.headers:
                lines.append(f"  │ Headers:    {len(best.headers)} required")
                for k, v in best.headers.items():
                    lines.append(f"  │   {k}: {v[:50]}")
            lines.append("  └────────────────────────────────────────────────────┘")
            lines.append("")

        # Alternative streams
        alternatives = sorted(result.streams, key=lambda s: s.score, reverse=True)
        if len(alternatives) > 1:
            lines.append("  ALTERNATIVES:")
            for i, stream in enumerate(alternatives[1:6], 2):
                lines.append(
                    f"    #{i}: [{stream.format.value}] {stream.quality.value} "
                    f"conf={stream.confidence} srv={stream.server_name}"
                )
                lines.append(f"        {stream.url[:65]}")
            lines.append("")

        # Servers
        if result.servers_found:
            lines.append("  SERVERS DETECTED:")
            for srv in result.servers_found:
                lines.append(
                    f"    • {srv.name:<20s} type={srv.server_type:<12s} conf={srv.confidence}"
                )
            lines.append("")

        # Errors
        if result.errors:
            lines.append("  ERRORS:")
            for error in result.errors[:10]:
                lines.append(f"    ⚠ {error[:70]}")
            lines.append("")

        # Stage timings
        timings = result.metadata.get("stage_timings", {})
        if timings:
            lines.append("  STAGE TIMINGS:")
            for stage_name, elapsed in timings.items():
                bar_len = min(40, int(elapsed * 10))
                bar = "█" * bar_len
                lines.append(f"    {stage_name:<25s} {elapsed:>6.2f}s {bar}")
            lines.append("")

        lines.append(sep)

        return "\n".join(lines)

    @staticmethod
    def markdown_table(result: ExtractionResult) -> str:
        """
        Format result as a Markdown table.

        Useful for embedding in documentation or reports.
        """
        lines: List[str] = []

        lines.append("## Extraction Result")
        lines.append("")
        lines.append(f"**Status:** {'✅ Success' if result.success else '❌ Failed'}")
        lines.append(f"**Time:** {result.elapsed_time:.2f}s")
        lines.append("")

        if result.streams:
            lines.append("### Streams")
            lines.append("")
            lines.append("| # | Format | Quality | Confidence | Score | Server | URL |")
            lines.append("|---|--------|---------|------------|-------|--------|-----|")

            for i, stream in enumerate(
                sorted(result.streams, key=lambda s: s.score, reverse=True)[:10],
                1,
            ):
                url_short = stream.url[:50] + "..." if len(stream.url) > 50 else stream.url
                lines.append(
                    f"| {i} | {stream.format.value} | {stream.quality.value} | "
                    f"{stream.confidence} | {stream.score:.1f} | "
                    f"{stream.server_name} | `{url_short}` |"
                )

        return "\n".join(lines)


class ExtractionSession:
    """
    Manages a session of multiple extractions with shared state.

    Benefits of session-based extraction:
        - Shared HTTP session (connection pooling)
        - Pattern learning across extractions
        - Batch processing support
        - Session-level statistics
    """

    def __init__(
        self,
        debug: bool = False,
        **config_overrides: Any,
    ) -> None:
        """
        Initialize an extraction session.

        Args:
            debug: Enable debug logging.
            **config_overrides: Config overrides.
        """
        self._extractor = SupjavExtractor(debug=debug, **config_overrides)
        self._results: List[Tuple[str, ExtractionResult]] = []
        self._start_time = time.time()

    def extract(
        self,
        url: str,
        **kwargs: Any,
    ) -> ExtractionResult:
        """
        Extract from a single URL within this session.

        Args:
            url: SupJav video page URL.
            **kwargs: Passed to SupjavExtractor.extract().

        Returns:
            ExtractionResult.
        """
        result = self._extractor.extract(url, **kwargs)
        self._results.append((url, result))
        return result

    def extract_batch(
        self,
        urls: List[str],
        delay: float = 2.0,
        stop_on_failure: bool = False,
        **kwargs: Any,
    ) -> List[Tuple[str, ExtractionResult]]:
        """
        Extract from multiple URLs sequentially.

        Args:
            urls: List of SupJav URLs.
            delay: Delay between extractions (seconds).
            stop_on_failure: Stop batch on first failure.
            **kwargs: Passed to each extraction.

        Returns:
            List of (url, ExtractionResult) tuples.
        """
        results: List[Tuple[str, ExtractionResult]] = []

        for i, url in enumerate(urls):
            self._extractor._log.section(
                f"Batch [{i + 1}/{len(urls)}]: {url[:60]}"
            )

            result = self.extract(url, **kwargs)
            results.append((url, result))

            if stop_on_failure and not result.success:
                self._extractor._log.warning(
                    f"Batch stopped on failure at URL #{i + 1}"
                )
                break

            # Delay between extractions (skip after last)
            if i < len(urls) - 1:
                actual_delay = delay + random.uniform(0, delay * 0.5)
                time.sleep(actual_delay)

        return results

    def get_session_stats(self) -> Dict[str, Any]:
        """Get session-level statistics."""
        total = len(self._results)
        successes = sum(1 for _, r in self._results if r.success)
        failures = total - successes
        elapsed = time.time() - self._start_time

        total_streams = sum(len(r.streams) for _, r in self._results)

        avg_time = (
            sum(r.elapsed_time for _, r in self._results) / total
            if total > 0 else 0
        )

        return {
            "total_extractions": total,
            "successes": successes,
            "failures": failures,
            "success_rate": round(successes / total, 3) if total > 0 else 0,
            "total_streams_found": total_streams,
            "avg_extraction_time": round(avg_time, 2),
            "session_elapsed": round(elapsed, 2),
            "extractor_stats": self._extractor.get_stats(),
        }

    def get_all_results(self) -> List[Tuple[str, ExtractionResult]]:
        """Get all extraction results from this session."""
        return list(self._results)

    def get_all_best_urls(self) -> List[Dict[str, Any]]:
        """
        Get the best stream URL from each extraction.

        Returns:
            List of {"url": input_url, "stream_url": best_url, "success": bool}
        """
        output: List[Dict[str, Any]] = []
        for url, result in self._results:
            entry: Dict[str, Any] = {
                "input_url": url,
                "success": result.success,
            }
            if result.best_stream:
                entry["stream_url"] = result.best_stream.url
                entry["format"] = result.best_stream.format.value
                entry["quality"] = result.best_stream.quality.value
                entry["headers"] = result.best_stream.headers
            else:
                entry["stream_url"] = None
                entry["error"] = result.errors[0] if result.errors else "Unknown"

            output.append(entry)

        return output

    def maintenance(self) -> Dict[str, Any]:
        """Run session maintenance."""
        return self._extractor.maintenance()

    @property
    def extractor(self) -> SupjavExtractor:
        """Access the underlying extractor."""
        return self._extractor


# ─── Top-Level Convenience Functions ──────────────────────────────────────────

# Module-level singleton extractor (lazy-initialized)
_default_extractor: Optional[SupjavExtractor] = None


def _get_default_extractor(debug: bool = False) -> SupjavExtractor:
    """Get or create the default module-level extractor."""
    global _default_extractor
    if _default_extractor is None:
        _default_extractor = SupjavExtractor(debug=debug)
    return _default_extractor


def extract(
    url: str,
    preferred_server: Optional[str] = None,
    preferred_quality: Optional[str] = None,
    preferred_format: Optional[str] = None,
    debug: bool = False,
    skip_headless: bool = False,
    validate: bool = True,
    output_format: str = "standard",
) -> Dict[str, Any]:
    """
    🎯 PRIMARY PUBLIC API — Extract video stream from a SupJav URL.

    This is the simplest way to use the extractor. Call this function
    with a URL and get back a dictionary with the video stream.

    Args:
        url: SupJav video page URL.
        preferred_server: Server name preference (e.g., "streamtape").
        preferred_quality: Quality preference (e.g., "1080p", "720p").
        preferred_format: Format preference (e.g., "mp4", "m3u8").
        debug: Enable verbose debug logging.
        skip_headless: Skip Playwright headless browser.
        validate: Validate stream URLs with HEAD requests.
        output_format: "minimal", "standard", or "full".

    Returns:
        Dictionary with extraction result. Format depends on output_format.

    Example:
        >>> result = extract("https://supjav.com/en/123456-video")
        >>> if result["success"]:
        ...     print(result["best_stream"]["url"])
        ...     print(result["best_stream"]["headers"])  # Use these headers!
    """
    try:
        extractor = _get_default_extractor(debug=debug)

        raw_result = extractor.extract(
            url=url,
            preferred_server=preferred_server,
            preferred_quality=preferred_quality,
            preferred_format=preferred_format,
            debug=debug,
            skip_headless=skip_headless,
            validate=validate,
        )

        # Format output
        formatters = {
            "minimal": OutputFormatter.minimal,
            "standard": OutputFormatter.standard,
            "full": OutputFormatter.full,
        }

        formatter = formatters.get(output_format, OutputFormatter.standard)
        return formatter(raw_result)

    except Exception as exc:
        return {
            "success": False,
            "url": None,
            "error": f"Extraction failed: {str(exc)}",
            "exception": type(exc).__name__,
        }


def extract_raw(
    url: str,
    preferred_server: Optional[str] = None,
    debug: bool = False,
    **kwargs: Any,
) -> ExtractionResult:
    """
    Extract and return the raw ExtractionResult object.

    For advanced users who need full access to all data structures.

    Args:
        url: SupJav video page URL.
        preferred_server: Server preference.
        debug: Debug mode.
        **kwargs: Additional args for SupjavExtractor.extract().

    Returns:
        ExtractionResult object.
    """
    extractor = _get_default_extractor(debug=debug)
    return extractor.extract(url, preferred_server=preferred_server, debug=debug, **kwargs)


def extract_url(
    url: str,
    debug: bool = False,
) -> Optional[str]:
    """
    Simplest possible API — returns just the video URL or None.

    Args:
        url: SupJav video page URL.
        debug: Debug mode.

    Returns:
        Video stream URL string, or None if extraction failed.

    Example:
        >>> video_url = extract_url("https://supjav.com/en/123456-video")
        >>> if video_url:
        ...     print(f"Download from: {video_url}")
    """
    try:
        result = extract(url, debug=debug, output_format="minimal")
        if result.get("success"):
            return result.get("url")
        return None
    except Exception:
        return None


def extract_with_headers(
    url: str,
    preferred_server: Optional[str] = None,
    debug: bool = False,
) -> Optional[Tuple[str, Dict[str, str]]]:
    """
    Extract video URL along with required HTTP headers.

    Many video servers require specific headers (especially Referer)
    for the stream URL to work. This function returns both.

    Args:
        url: SupJav video page URL.
        preferred_server: Server preference.
        debug: Debug mode.

    Returns:
        Tuple of (video_url, headers_dict), or None if failed.

    Example:
        >>> result = extract_with_headers("https://supjav.com/en/123456-video")
        >>> if result:
        ...     video_url, headers = result
        ...     # Use headers when downloading:
        ...     # requests.get(video_url, headers=headers, stream=True)
    """
    try:
        raw_result = extract_raw(url, preferred_server=preferred_server, debug=debug)
        if raw_result.success and raw_result.best_stream:
            return raw_result.best_stream.url, raw_result.best_stream.headers
        return None
    except Exception:
        return None


def get_all_streams(
    url: str,
    debug: bool = False,
) -> List[Dict[str, Any]]:
    """
    Get all discovered streams ranked by quality.

    Returns every stream the extractor found, not just the best one.
    Useful when you want to present choices to the user.

    Args:
        url: SupJav video page URL.
        debug: Debug mode.

    Returns:
        List of stream dicts sorted by score (best first).

    Example:
        >>> streams = get_all_streams("https://supjav.com/en/123456-video")
        >>> for s in streams:
        ...     print(f"{s['quality']} | {s['format']} | {s['server']} | {s['url'][:60]}")
    """
    try:
        raw_result = extract_raw(url, debug=debug)
        return [
            {
                "url": s.url,
                "format": s.format.value,
                "quality": s.quality.value,
                "confidence": s.confidence,
                "score": round(s.score, 2),
                "server": s.server_name,
                "headers": s.headers,
            }
            for s in sorted(raw_result.streams, key=lambda x: x.score, reverse=True)
        ]
    except Exception:
        return []


def get_servers(
    url: str,
    debug: bool = False,
) -> List[Dict[str, Any]]:
    """
    Detect available servers without performing extraction.

    Quick way to see what servers are available before extracting.

    Args:
        url: SupJav video page URL.
        debug: Debug mode.

    Returns:
        List of server dicts.

    Example:
        >>> servers = get_servers("https://supjav.com/en/123456-video")
        >>> for s in servers:
        ...     print(f"{s['name']} ({s['type']}) — confidence: {s['confidence']}")
    """
    try:
        extractor = _get_default_extractor(debug=debug)
        server_infos = extractor.get_servers(url)
        return [
            {
                "name": s.name,
                "type": s.server_type,
                "url": s.url,
                "confidence": s.confidence,
                "priority": s.priority,
            }
            for s in server_infos
        ]
    except Exception:
        return []


def pretty_extract(
    url: str,
    preferred_server: Optional[str] = None,
    debug: bool = False,
) -> str:
    """
    Extract and return a pretty-printed human-readable result.

    Ideal for terminal/CLI usage.

    Args:
        url: SupJav video page URL.
        preferred_server: Server preference.
        debug: Debug mode.

    Returns:
        Formatted string.
    """
    try:
        raw_result = extract_raw(
            url, preferred_server=preferred_server, debug=debug
        )
        return OutputFormatter.pretty_print(raw_result)
    except Exception as exc:
        return f"❌ Extraction failed: {exc}"


# ─── CLI Interface ───────────────────────────────────────────────────────────

def _build_cli_parser() -> "argparse.ArgumentParser":
    """Build the command-line argument parser."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="supjav_extractor",
        description=(
            f"SupJav AI-Assisted Video Extractor v{__version__}\n"
            "Extract video stream URLs from supjav.com pages."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s https://supjav.com/en/123456-video\n"
            "  %(prog)s https://supjav.com/en/123456-video --server streamtape\n"
            "  %(prog)s https://supjav.com/en/123456-video --quality 1080p --format mp4\n"
            "  %(prog)s https://supjav.com/en/123456-video --debug --output full\n"
            "  %(prog)s https://supjav.com/en/123456-video --json\n"
            "  %(prog)s --servers-only https://supjav.com/en/123456-video\n"
        ),
    )

    parser.add_argument(
        "url",
        help="SupJav video page URL",
    )

    parser.add_argument(
        "--server", "-s",
        dest="preferred_server",
        default=None,
        help="Preferred server name (e.g., streamtape, doodstream, mixdrop)",
    )

    parser.add_argument(
        "--quality", "-q",
        dest="preferred_quality",
        default=None,
        help="Preferred quality (e.g., 1080p, 720p, 480p)",
    )

    parser.add_argument(
        "--format", "-f",
        dest="preferred_format",
        default=None,
        help="Preferred format (e.g., mp4, m3u8)",
    )

    parser.add_argument(
        "--output", "-o",
        dest="output_format",
        choices=["minimal", "standard", "full", "pretty", "json", "markdown"],
        default="pretty",
        help="Output format (default: pretty)",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON (shortcut for --output json)",
    )

    parser.add_argument(
        "--url-only",
        action="store_true",
        help="Output only the video URL (or nothing if failed)",
    )

    parser.add_argument(
        "--servers-only",
        action="store_true",
        help="Only detect servers, don't extract streams",
    )

    parser.add_argument(
        "--all-streams",
        action="store_true",
        help="Show all discovered streams, not just the best",
    )

    parser.add_argument(
        "--debug", "-d",
        action="store_true",
        help="Enable debug logging",
    )

    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip stream URL validation (faster)",
    )

    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Skip headless browser extraction",
    )

    parser.add_argument(
        "--max-servers",
        type=int,
        default=5,
        help="Maximum number of servers to try (default: 5)",
    )

    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    return parser


def cli_main(argv: Optional[List[str]] = None) -> int:
    """
    CLI entry point.

    Args:
        argv: Command-line arguments (uses sys.argv if None).

    Returns:
        Exit code (0 = success, 1 = failure).
    """
    import argparse

    parser = _build_cli_parser()
    args = parser.parse_args(argv)

    # ── Servers-only mode ────────────────────────────────────────────
    if args.servers_only:
        servers = get_servers(args.url, debug=args.debug)
        if not servers:
            print("No servers detected.")
            return 1

        if args.json or args.output_format == "json":
            print(json.dumps(servers, indent=2))
        else:
            print(f"\nDetected {len(servers)} server(s):\n")
            for i, s in enumerate(servers, 1):
                print(
                    f"  [{i}] {s['name']:<20s} "
                    f"type={s['type']:<12s} "
                    f"conf={s['confidence']:>3d} "
                    f"| {s['url'][:60]}"
                )
            print()
        return 0

    # ── URL-only mode ────────────────────────────────────────────────
    if args.url_only:
        video_url = extract_url(args.url, debug=args.debug)
        if video_url:
            print(video_url)
            return 0
        else:
            return 1

    # ── All-streams mode ─────────────────────────────────────────────
    if args.all_streams:
        streams = get_all_streams(args.url, debug=args.debug)
        if not streams:
            print("No streams found.")
            return 1

        if args.json or args.output_format == "json":
            print(json.dumps(streams, indent=2))
        else:
            print(f"\nFound {len(streams)} stream(s):\n")
            for i, s in enumerate(streams, 1):
                print(
                    f"  [{i}] {s['format']:<5s} {s['quality']:<8s} "
                    f"conf={s['confidence']:>3d} "
                    f"score={s['score']:<6.1f} "
                    f"srv={s['server']:<15s}"
                )
                print(f"      {s['url'][:80]}")
                if s.get('headers'):
                    for k, v in s['headers'].items():
                        print(f"      → {k}: {v[:60]}")
                print()
        return 0

    # ── Standard extraction ──────────────────────────────────────────
    output_fmt = args.output_format
    if args.json:
        output_fmt = "json"

    raw_result = extract_raw(
        url=args.url,
        preferred_server=args.preferred_server,
        debug=args.debug,
        preferred_quality=args.preferred_quality,
        preferred_format=args.preferred_format,
        skip_headless=args.no_headless,
        validate=not args.no_validate,
        max_servers=args.max_servers,
    )

    # ── Output formatting ────────────────────────────────────────────
    if output_fmt == "pretty":
        print(OutputFormatter.pretty_print(raw_result))

    elif output_fmt == "json":
        print(OutputFormatter.to_json(raw_result, mode="standard"))

    elif output_fmt == "minimal":
        print(json.dumps(OutputFormatter.minimal(raw_result), indent=2))

    elif output_fmt == "standard":
        print(json.dumps(OutputFormatter.standard(raw_result), indent=2))

    elif output_fmt == "full":
        print(OutputFormatter.to_json(raw_result, mode="full"))

    elif output_fmt == "markdown":
        print(OutputFormatter.markdown_table(raw_result))

    return 0 if raw_result.success else 1


# ─── Module Entry Point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.exit(cli_main())


# ═══════════════════════════════════════════════════════════════════════════════
# END OF PART 10
# ═══════════════════════════════════════════════════════════════════════════════
#
# ┌───────────────────────────────────────────────────────────────────────────┐
# │                    ALL 10 PARTS COMPLETE                                  │
# │                                                                           │
# │  Combine Parts 1–10 into a single .py file for the complete extractor.   │
# │                                                                           │
# │  Quick start:                                                             │
# │    from supjav_extractor import extract                                   │
# │    result = extract("https://supjav.com/en/some-video")                  │
# │    if result["success"]:                                                  │
# │        print(result["best_stream"]["url"])                                │
# │                                                                           │
# │  CLI:                                                                     │
# │    python supjav_extractor.py https://supjav.com/en/some-video           │
# │    python supjav_extractor.py URL --server streamtape --quality 1080p    │
# │    python supjav_extractor.py URL --json                                 │
# │    python supjav_extractor.py URL --url-only                             │
# │                                                                           │
# └───────────────────────────────────────────────────────────────────────────┘
# ═══════════════════════════════════════════════════════════════════════════════