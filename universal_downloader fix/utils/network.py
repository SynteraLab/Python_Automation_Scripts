"""
Network utilities for the universal downloader.
"""

import asyncio
import aiohttp
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional, Dict, Any, AsyncGenerator, Tuple
from pathlib import Path
import logging
import time
import ssl
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Optional imports
try:
    import certifi
    SSL_CA = certifi.where()
except ImportError:
    SSL_CA = None

cookiejar: Any = None
try:
    import http.cookiejar as cookiejar
    COOKIEJAR_AVAILABLE = True
except ImportError:
    COOKIEJAR_AVAILABLE = False

browser_cookie3: Any = None
try:
    import browser_cookie3 as _browser_cookie3  # type: ignore[import-not-found]
    browser_cookie3 = _browser_cookie3
    BROWSER_COOKIES_AVAILABLE = True
except ImportError:
    BROWSER_COOKIES_AVAILABLE = False


class NetworkError(Exception):
    """Base exception for network errors."""
    pass


class HTTPError(NetworkError):
    """HTTP-specific error."""
    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class SessionManager:
    """Manages HTTP sessions with retry logic, cookies, and proxy support."""

    DEFAULT_HEADERS = {
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

    def __init__(
        self,
        user_agent: str = "Mozilla/5.0",
        proxy: Optional[Dict[str, str]] = None,
        cookies_file: Optional[str] = None,
        cookies_from_browser: Optional[str] = None,
        max_retries: int = 5,
        timeout: int = 60
    ):
        self.user_agent = user_agent
        self.proxy = proxy or {}
        self.max_retries = max_retries
        self.timeout = timeout
        self._session = self._create_session()

        if cookies_file:
            self._load_cookies_from_file(cookies_file)
        elif cookies_from_browser:
            self._load_cookies_from_browser(cookies_from_browser)

    def _create_session(self) -> requests.Session:
        """Create a requests session with retry logic."""
        session = requests.Session()

        retry_strategy = Retry(
            total=self.max_retries,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
        )

        adapter = HTTPAdapter(max_retries=retry_strategy, pool_maxsize=20)
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        session.headers.update(self.DEFAULT_HEADERS)
        session.headers['User-Agent'] = self.user_agent

        if self.proxy:
            session.proxies.update(self.proxy)

        return session

    def _load_cookies_from_file(self, cookies_file: str) -> None:
        """Load cookies from Netscape cookies.txt file."""
        if not COOKIEJAR_AVAILABLE:
            logger.warning("http.cookiejar not available")
            return
        try:
            if cookiejar is None:
                return
            cookie_jar = cookiejar.MozillaCookieJar(cookies_file)
            cookie_jar.load(ignore_discard=True, ignore_expires=True)
            self._session.cookies.update(cookie_jar)
            logger.info(f"Loaded cookies from {cookies_file}")
        except Exception as e:
            logger.warning(f"Failed to load cookies from file: {e}")

    def _load_cookies_from_browser(self, browser: str) -> None:
        """Load cookies from browser."""
        if not BROWSER_COOKIES_AVAILABLE:
            logger.warning("browser-cookie3 not installed, skipping browser cookies")
            return
        if browser_cookie3 is None:
            logger.warning("browser-cookie3 not available")
            return

        browser_funcs = {
            'chrome': browser_cookie3.chrome,
            'firefox': browser_cookie3.firefox,
            'edge': browser_cookie3.edge,
            'opera': browser_cookie3.opera,
            'brave': browser_cookie3.brave,
        }

        func = browser_funcs.get(browser.lower())
        if not func:
            logger.warning(f"Unsupported browser: {browser}")
            return

        try:
            cookie_jar = func()
            for cookie in cookie_jar:
                self._session.cookies.set(
                    cookie.name, cookie.value,
                    domain=cookie.domain, path=cookie.path
                )
            logger.info(f"Loaded cookies from {browser}")
        except Exception as e:
            logger.warning(f"Failed to load cookies from browser: {e}")

    def get(self, url: str, headers: Optional[Dict[str, str]] = None,
            params: Optional[Dict[str, Any]] = None, **kwargs) -> requests.Response:
        """Perform GET request."""
        merged_headers = {**self._session.headers, **(headers or {})}
        response = self._session.get(
            url, headers=merged_headers, params=params,
            timeout=self.timeout, **kwargs
        )
        response.raise_for_status()
        return response

    def post(self, url: str, data=None, json=None,
             headers: Optional[Dict[str, str]] = None, **kwargs) -> requests.Response:
        """Perform POST request."""
        merged_headers = {**self._session.headers, **(headers or {})}
        response = self._session.post(
            url, data=data, json=json, headers=merged_headers,
            timeout=self.timeout, **kwargs
        )
        response.raise_for_status()
        return response

    def head(self, url: str, **kwargs) -> requests.Response:
        """Perform HEAD request."""
        return self._session.head(url, timeout=self.timeout, **kwargs)

    def get_file_size(self, url: str, headers: Optional[Dict[str, str]] = None) -> Optional[int]:
        """Get file size from Content-Length header."""
        try:
            response = self.head(url, headers=headers, allow_redirects=True)
            size = response.headers.get('Content-Length')
            return int(size) if size else None
        except Exception:
            return None

    def close(self) -> None:
        """Close the session."""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class AsyncSessionManager:
    """Async HTTP session manager using aiohttp."""

    def __init__(
        self,
        user_agent: str = "Mozilla/5.0",
        proxy: Optional[str] = None,
        max_connections: int = 100,
        timeout: int = 60,
        default_headers: Optional[Dict[str, Any]] = None,
        cookies: Optional[Dict[str, str]] = None,
    ):
        self.user_agent = user_agent
        self.proxy = proxy
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.cookies = cookies or {}
        self.max_connections = max_connections

        ssl_context = ssl.create_default_context()
        if SSL_CA:
            ssl_context.load_verify_locations(SSL_CA)
        self._ssl_context = ssl_context

        self._headers = {'User-Agent': user_agent, 'Accept': '*/*'}
        if default_headers:
            self._headers.update(default_headers)
        self._session: Optional[aiohttp.ClientSession] = None
        self._connector: Optional[aiohttp.TCPConnector] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create async session."""
        if self._session is None or self._session.closed:
            if self._connector is None or self._connector.closed:
                self._connector = aiohttp.TCPConnector(
                    limit=self.max_connections,
                    ssl=self._ssl_context,
                    enable_cleanup_closed=True,
                )
            cookie_jar = aiohttp.CookieJar()
            if self.cookies:
                cookie_jar.update_cookies(self.cookies)
            self._session = aiohttp.ClientSession(
                connector=self._connector,
                headers=self._headers,
                timeout=self.timeout,
                cookie_jar=cookie_jar,
            )
        return self._session

    async def get(self, url: str, headers: Optional[Dict[str, str]] = None,
                  **kwargs) -> aiohttp.ClientResponse:
        """Perform async GET request."""
        session = await self._get_session()
        return await session.get(url, headers=headers, proxy=self.proxy, **kwargs)

    async def download_chunk(self, url: str, start: int, end: int,
                             headers: Optional[Dict[str, str]] = None) -> bytes:
        """Download a specific byte range."""
        range_headers = {**(headers or {}), 'Range': f'bytes={start}-{end}'}
        async with await self.get(url, headers=range_headers) as response:
            if response.status not in [200, 206]:
                raise HTTPError(response.status, await response.text())
            return await response.read()

    async def stream_download(self, url: str, headers: Optional[Dict[str, str]] = None,
                              chunk_size: int = 1024 * 1024) -> AsyncGenerator[bytes, None]:
        """Stream download with chunks."""
        async with await self.get(url, headers=headers) as response:
            if response.status not in [200, 206]:
                raise HTTPError(response.status, await response.text())
            async for chunk in response.content.iter_chunked(chunk_size):
                yield chunk

    async def close(self) -> None:
        """Close the async session."""
        if self._session and not self._session.closed:
            await self._session.close()
        if self._connector and not self._connector.closed:
            await self._connector.close()
        self._session = None
        self._connector = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()


class RateLimiter:
    """Rate limiter for controlling download speed."""

    def __init__(self, bytes_per_second: Optional[int] = None):
        self.bytes_per_second = bytes_per_second
        self.downloaded_bytes = 0
        self.start_time = time.time()

    async def limit(self, chunk_size: int) -> None:
        """Apply rate limiting if configured."""
        if not self.bytes_per_second:
            return
        self.downloaded_bytes += chunk_size
        elapsed = time.time() - self.start_time
        expected_time = self.downloaded_bytes / self.bytes_per_second
        if expected_time > elapsed:
            await asyncio.sleep(expected_time - elapsed)

    def reset(self) -> None:
        """Reset the rate limiter."""
        self.downloaded_bytes = 0
        self.start_time = time.time()


@dataclass
class HTTPDocument:
    """Normalized HTTP response wrapper for extractor strategies."""

    url: str
    status: int
    headers: Dict[str, str]
    body: bytes = b""
    text: Optional[str] = None
    json_data: Any = None

    @property
    def content_type(self) -> str:
        return self.headers.get('Content-Type', '')


class RequestManager:
    """Async-first request manager with retries, caching, and concurrency limits."""

    def __init__(
        self,
        async_session: AsyncSessionManager,
        *,
        max_retries: int = 4,
        backoff_base: float = 0.5,
        max_concurrent: int = 12,
    ):
        self.async_session = async_session
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._cache: Dict[Tuple[str, str], HTTPDocument] = {}

    @classmethod
    def from_legacy_session(
        cls,
        session: SessionManager,
        config: Optional[Dict[str, Any]] = None,
    ) -> 'RequestManager':
        config = config or {}
        proxy = None
        if session.proxy:
            proxy = session.proxy.get('https') or session.proxy.get('http')

        download_cfg = config.get('download') if isinstance(config, dict) else None
        timeout = getattr(download_cfg, 'timeout', None) if download_cfg is not None else None
        if isinstance(download_cfg, dict):
            timeout = download_cfg.get('timeout', timeout)

        async_session = AsyncSessionManager(
            user_agent=session.user_agent,
            proxy=proxy,
            timeout=int(timeout or session.timeout),
            default_headers={str(k): str(v) for k, v in session._session.headers.items()},
            cookies=session._session.cookies.get_dict(),
        )

        max_retries = getattr(download_cfg, 'max_retries', None) if download_cfg is not None else None
        if isinstance(download_cfg, dict):
            max_retries = download_cfg.get('max_retries', max_retries)
        return cls(async_session, max_retries=int(max_retries or session.max_retries))

    async def get_text(self, url: str, headers: Optional[Dict[str, str]] = None) -> HTTPDocument:
        return await self._request('GET', url, headers=headers, want='text')

    async def get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> HTTPDocument:
        return await self._request('GET', url, headers=headers, want='json')

    async def get_bytes(self, url: str, headers: Optional[Dict[str, str]] = None) -> HTTPDocument:
        return await self._request('GET', url, headers=headers, want='bytes')

    async def probe_many(self, urls: list[str], headers: Optional[Dict[str, str]] = None) -> list[HTTPDocument]:
        return await asyncio.gather(*(self.get_text(url, headers=headers) for url in urls))

    async def close(self) -> None:
        await self.async_session.close()

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Dict[str, str]] = None,
        want: str = 'text',
    ) -> HTTPDocument:
        cache_key = (method, url)
        if cache_key in self._cache:
            return self._cache[cache_key]

        async with self._semaphore:
            for attempt in range(self.max_retries + 1):
                try:
                    async with await self.async_session.get(url, headers=headers) as response:
                        body = await response.read()
                        document = HTTPDocument(
                            url=str(response.url),
                            status=response.status,
                            headers=dict(response.headers),
                            body=body,
                        )
                        if response.status >= 400:
                            raise HTTPError(response.status, body.decode(errors='replace')[:200])

                        if want == 'json':
                            document.json_data = await response.json(content_type=None)
                        elif want == 'text':
                            document.text = body.decode(response.charset or 'utf-8', errors='replace')

                        self._cache[cache_key] = document
                        return document
                except (aiohttp.ClientError, asyncio.TimeoutError, HTTPError):
                    if attempt >= self.max_retries:
                        raise
                    delay = (self.backoff_base * (2 ** attempt)) + random.uniform(0.05, 0.25)
                    await asyncio.sleep(delay)

        raise NetworkError(f"Failed to request URL: {url}")
