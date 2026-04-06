# pyright: reportMissingImports=false, reportOptionalMemberAccess=false
"""
SupJav extractor - handles Cloudflare-protected pages with encrypted server links.

Supports:
- supjav.com pages (works best with browser cookies)
- turbovidhls.com direct URLs
- callistanise.com / VidHide direct URLs

Full flow for supjav.com:
1. Fetch page (browser cookies) -> extract server data-links
2. supremejav.com redirect -> turbovidhls.com (or similar)
3. Extract m3u8/mp4 from the resolved player page

If supremejav redirect fails (Cloudflare blocks it):
- User can get a direct player URL from browser DevTools
- Then download directly: python main.py download "https://turbovidhls.com/t/xxxxx"
"""

import importlib
import base64
import html as html_lib
import re
import requests
from typing import Any, List, Dict, Optional
from urllib.parse import urlparse, urljoin, unquote, urlencode
from bs4 import BeautifulSoup
import logging

from .base import ExtractorBase, ExtractionError
from models.media import MediaInfo, StreamFormat, MediaType, StreamType

logger = logging.getLogger(__name__)

try:
    from curl_cffi import requests as curl_requests
    CURL_CFFI_AVAILABLE = True
except Exception:
    curl_requests = None
    CURL_CFFI_AVAILABLE = False


class SupJavExtractor(ExtractorBase):
    """
    Extractor for supjav.com and turbovidhls.com.

    Supports:
    - supjav.com (Cloudflare, multi-server, encrypted links)
    - turbovidhls.com direct URLs (JWPlayer + HLS m3u8)
    - callistanise.com / VidHide embed pages
    """

    EXTRACTOR_NAME = "supjav"
    EXTRACTOR_DESCRIPTION = "SupJav.com + TurboVidHLS extractor (multi-server, HLS)"

    URL_PATTERNS = [
        r'https?://(?:www\.)?supjav\.com/\d+\.html',
        r'https?://(?:www\.)?supjav\.com/.+\.html',
        r'https?://(?:www\.)?supjav\.(?:ru|homes|to|net|org)/[\w\-./?%=&+#]+',
        r'https?://(?:www\.)?turbovidhls\.com/(?:t|e|embed|v|d)/.+',
        r'https?://(?:cdn\d*\.)?turboviplay\.com/.+',
        r'https?://(?:www\.)?callistanise\.com/(?:v|e|embed|d)/.+',
        r'https?://(?:www\.)?[a-z0-9-]*vidhide[a-z0-9-]*\.(?:com|net|org)/(?:v|e|embed|d)/.+',
    ]

    SUPREMEJAV_BASE = "https://lk1.supremejav.com/supjav.php"
    SUPJAV_MIRROR_HOSTS = (
        'supjav.com',
        'supjav.ru',
        'supjav.homes',
        'supjav.to',
        'supjav.net',
        'supjav.org',
    )
    PLAYER_HOST_HINTS = (
        'turbovidhls.',
        'turboviplay.',
        'callistanise.',
        'vidhide',
        'supremejav.',
    )
    BROWSER_CANDIDATES = (
        'chrome',
        'brave',
        'edge',
        'firefox',
        'opera',
    )
    COOKIE_DOMAINS = (
        'supjav.com',
        'supjav.ru',
        'supjav.homes',
        'supjav.to',
        'supjav.net',
        'supjav.org',
        'lk1.supremejav.com',
        'supremejav.com',
        'turbovidhls.com',
        'turboviplay.com',
        'callistanise.com',
    )
    COOKIE_DOMAIN_KEYWORDS = (
        'supjav',
        'supremejav',
        'turbovidhls',
        'turboviplay',
        'callistanise',
        'vidhide',
    )
    CLOUDFLARE_COOKIE_NAMES = (
        'cf_clearance',
        '__cf_bm',
        '__cfseq',
        'cf_chl_rc_i',
        'cf_chl_rc_ni',
    )
    SEARCH_RESULT_LIMIT = 3
    JAV_CODE_PATTERN = re.compile(r'\b([A-Za-z]{2,10}-?\d{2,6})\b')
    NON_VIDEO_URL_HINTS = (
        '.jpg',
        '.jpeg',
        '.png',
        '.gif',
        '.webp',
        '.svg',
        'get_slides',
        '/upload-data/logo',
        '/poster.',
        'test-videos.co.uk',
    )

    def __init__(self, session, config: Optional[Dict] = None):
        super().__init__(session, config)
        self._duration_hint: Optional[int] = None
        self._standalone_session: Optional[requests.Session] = None
        self._browser_cookie_sync_attempted = False
        self._browser_cookie_sync_summary = ''
        self._visited_embed_urls: set[str] = set()
        self._impersonated_session: Optional[Any] = None
        self._playlist_probe_cache: Dict[str, bool] = {}

    def _configured_browser_name(self) -> Optional[str]:
        if isinstance(self.config, dict):
            browser = self.config.get('cookies_from_browser')
            if isinstance(browser, str) and browser:
                return browser

        browser = getattr(self.config, 'cookies_from_browser', None)
        if isinstance(browser, str) and browser:
            return browser
        return None

    def _preferred_browsers(self) -> List[str]:
        browsers: List[str] = []
        configured = self._configured_browser_name()
        if configured:
            browsers.append(configured.lower())

        for browser in self.BROWSER_CANDIDATES:
            if browser not in browsers:
                browsers.append(browser)

        return browsers

    @staticmethod
    def _swap_url_host(url: str, target_host: str) -> str:
        parsed = urlparse(url)
        scheme = parsed.scheme or 'https'
        path = parsed.path or '/'
        query = f"?{parsed.query}" if parsed.query else ''
        return f"{scheme}://{target_host}{path}{query}"

    def _supjav_page_candidates(self, url: str) -> List[str]:
        parsed = urlparse(url)
        hostname = (parsed.hostname or '').lower()
        if 'supjav' not in hostname:
            return [url]

        candidates: List[str] = [url]
        seen = {url}

        normalized_host = hostname[4:] if hostname.startswith('www.') else hostname
        if normalized_host in self.SUPJAV_MIRROR_HOSTS:
            canonical = self._swap_url_host(url, normalized_host)
            if canonical not in seen:
                seen.add(canonical)
                candidates.append(canonical)

        for host in self.SUPJAV_MIRROR_HOSTS:
            candidate = self._swap_url_host(url, host)
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)

        return candidates

    @staticmethod
    def _merge_cookie_jar(target_session: requests.Session, cookie_jar: Any) -> int:
        copied = 0
        seen = set()

        try:
            iterator = iter(cookie_jar)
        except TypeError:
            return 0

        for cookie in iterator:
            try:
                name = getattr(cookie, 'name', None)
                value = getattr(cookie, 'value', None)
                if not name or value is None:
                    continue

                key = (
                    getattr(cookie, 'domain', ''),
                    getattr(cookie, 'path', ''),
                    name,
                    str(value),
                )
                if key in seen:
                    continue
                seen.add(key)

                target_session.cookies.set(
                    name,
                    str(value),
                    domain=getattr(cookie, 'domain', None),
                    path=getattr(cookie, 'path', None) or '/',
                )
                copied += 1
            except Exception:
                continue

        return copied

    @staticmethod
    def _merge_cookie_jar_filtered(
        target_session: requests.Session,
        cookie_jar: Any,
        domain_keywords: tuple[str, ...],
    ) -> int:
        copied = 0
        seen = set()

        try:
            iterator = iter(cookie_jar)
        except TypeError:
            return 0

        for cookie in iterator:
            try:
                domain = str(getattr(cookie, 'domain', '') or '').lstrip('.').lower()
                if domain and not any(keyword in domain for keyword in domain_keywords):
                    continue

                name = getattr(cookie, 'name', None)
                value = getattr(cookie, 'value', None)
                if not name or value is None:
                    continue

                key = (domain, getattr(cookie, 'path', ''), name, str(value))
                if key in seen:
                    continue
                seen.add(key)

                target_session.cookies.set(
                    name,
                    str(value),
                    domain=getattr(cookie, 'domain', None),
                    path=getattr(cookie, 'path', None) or '/',
                )
                copied += 1
            except Exception:
                continue

        return copied

    def _sync_browser_cookies(self, target_session: requests.Session, force: bool = False) -> int:
        if self._browser_cookie_sync_attempted and not force:
            return 0

        self._browser_cookie_sync_attempted = True
        self._browser_cookie_sync_summary = ''

        try:
            module_name = 'browser_' + 'cookie3'
            browser_cookie3 = importlib.import_module(module_name)
        except Exception:
            return 0

        loaded_total = 0
        loaded_sources: List[str] = []

        for browser_name in self._preferred_browsers():
            loader = getattr(browser_cookie3, browser_name, None)
            if not callable(loader):
                continue

            browser_total = 0
            domain_counts: List[str] = []
            used_full_jar = False
            for domain in self.COOKIE_DOMAINS:
                try:
                    cookie_jar = loader(domain_name=domain)
                except TypeError:
                    if used_full_jar:
                        break
                    used_full_jar = True
                    try:
                        cookie_jar = loader()
                    except Exception:
                        break
                except Exception:
                    continue

                added = self._merge_cookie_jar(target_session, cookie_jar)
                browser_total += added
                if added:
                    domain_counts.append(f"{domain}:{added}")
                if used_full_jar:
                    break

            if browser_total == 0:
                try:
                    full_cookie_jar = loader()
                    added_from_full = self._merge_cookie_jar_filtered(
                        target_session,
                        full_cookie_jar,
                        self.COOKIE_DOMAIN_KEYWORDS,
                    )
                    browser_total += added_from_full
                    if added_from_full:
                        domain_counts.append(f'full-jar:{added_from_full}')
                except Exception:
                    pass

            if browser_total:
                loaded_total += browser_total
                if domain_counts:
                    loaded_sources.append(f"{browser_name}({', '.join(domain_counts)})")
                else:
                    loaded_sources.append(f"{browser_name}:{browser_total}")

        if loaded_sources:
            self._browser_cookie_sync_summary = ', '.join(loaded_sources)

        return loaded_total

    def _has_cloudflare_cookie(self, session: requests.Session) -> bool:
        for cookie in session.cookies:
            name = getattr(cookie, 'name', '')
            if isinstance(name, str) and (name in self.CLOUDFLARE_COOKIE_NAMES or name.startswith('__cf')):
                return True
        return False

    def _get_browser_cookies(self) -> Optional[Any]:
        """Load cookies from the configured browser, falling back to Chrome."""
        try:
            module_name = 'browser_' + 'cookie3'
            browser_cookie3 = importlib.import_module(module_name)
            browser_name = (self._configured_browser_name() or 'chrome').lower()
            loader = getattr(browser_cookie3, browser_name, None)
            if not callable(loader):
                loader = getattr(browser_cookie3, 'chrome', None)
            if not callable(loader):
                return None
            return loader()
        except Exception:
            return None

    def _make_session(self) -> requests.Session:
        """Return a persistent requests session with configured cookies and proxy."""
        raw_session = getattr(self.session, '_session', None)
        if isinstance(raw_session, requests.Session):
            return raw_session

        if self._standalone_session is None:
            self._standalone_session = requests.Session()
            self._standalone_session.headers.update({
                'User-Agent': self.session.user_agent,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            })
            cj = self._get_browser_cookies()
            if cj:
                for cookie in cj:
                    try:
                        self._standalone_session.cookies.set(
                            cookie.name,
                            cookie.value,
                            domain=cookie.domain,
                            path=cookie.path,
                        )
                    except Exception:
                        continue

        return self._standalone_session

    @staticmethod
    def _merge_proxy_mapping(target_session: Any, proxy_mapping: Any) -> None:
        if not proxy_mapping:
            return
        try:
            target_session.proxies.update(dict(proxy_mapping))
        except Exception:
            return

    def _sync_impersonated_cookie_state(self, source_session: requests.Session) -> None:
        if self._impersonated_session is None:
            return
        try:
            self._merge_cookie_jar(self._impersonated_session, source_session.cookies)
        except Exception:
            return

    def _sync_response_cookies_to_requests(
        self,
        response: Any,
        target_session: requests.Session,
    ) -> None:
        cookie_jar = getattr(response, 'cookies', None)
        if cookie_jar is None:
            return
        self._merge_cookie_jar(target_session, cookie_jar)
        self._sync_impersonated_cookie_state(target_session)

    def _get_impersonated_session(self, seed_session: requests.Session) -> Optional[Any]:
        if not CURL_CFFI_AVAILABLE or curl_requests is None:
            return None

        if self._impersonated_session is None:
            try:
                impersonated = curl_requests.Session(impersonate='chrome124', default_headers=True)
            except Exception:
                return None

            self._merge_proxy_mapping(impersonated, getattr(seed_session, 'proxies', None))
            self._merge_proxy_mapping(impersonated, getattr(self.session, 'proxy', None))

            try:
                impersonated.headers.update({'User-Agent': self.session.user_agent})
            except Exception:
                pass

            self._impersonated_session = impersonated

        self._sync_impersonated_cookie_state(seed_session)
        return self._impersonated_session

    @staticmethod
    def _should_use_impersonated_request(url: str) -> bool:
        host = (urlparse(url).hostname or '').lower()
        return any(token in host for token in ('supjav', 'supremejav', 'turbovidhls', 'turboviplay'))

    def _build_headers(
        self,
        referer: Optional[str] = None,
        accept: str = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        headers = {
            'User-Agent': self.session.user_agent,
            'Accept': accept,
        }
        if referer:
            headers['Referer'] = referer
        if extra_headers:
            headers.update({k: str(v) for k, v in extra_headers.items() if v is not None})
        return headers

    def _http_get(
        self,
        url: str,
        *,
        referer: Optional[str] = None,
        accept: str = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        timeout: int = 30,
        allow_redirects: bool = True,
        session: Optional[requests.Session] = None,
        extra_headers: Optional[Dict[str, str]] = None,
        raise_for_status: bool = True,
    ) -> Any:
        request_session = session or self._make_session()
        request_headers = self._build_headers(referer=referer, accept=accept, extra_headers=extra_headers)

        if self._should_use_impersonated_request(url):
            impersonated = self._get_impersonated_session(request_session)
            if impersonated is not None:
                try:
                    response = impersonated.get(
                        url,
                        headers=request_headers,
                        timeout=timeout,
                        allow_redirects=allow_redirects,
                    )
                    self._sync_response_cookies_to_requests(response, request_session)
                    if raise_for_status:
                        response.raise_for_status()
                    return response
                except Exception as exc:
                    logger.debug(f"curl_cffi request failed for {url}: {exc}")

        response = request_session.get(
            url,
            headers=request_headers,
            timeout=timeout,
            allow_redirects=allow_redirects,
        )
        if raise_for_status:
            response.raise_for_status()
        return response

    @staticmethod
    def _build_supreme_url(base_url: str, **params: str) -> str:
        safe_params = {k: v for k, v in params.items() if isinstance(v, str) and v is not None}
        query = urlencode(safe_params, safe=':/%')
        return f"{base_url}?{query}"

    @staticmethod
    def _looks_like_url_candidate(value: str) -> bool:
        lower = value.lower()
        return (
            value.startswith(('http://', 'https://', '//', '/', './', '../'))
            or 'turbovidhls' in lower
            or 'turboviplay' in lower
            or 'supremejav' in lower
            or '/embed/' in lower
            or lower.startswith(('t/', 'e/', 'v/', 'd/'))
        )

    @staticmethod
    def _try_decode_base64(value: str) -> Optional[str]:
        cleaned = re.sub(r'\s+', '', value.strip().strip('"\''))
        if len(cleaned) < 12:
            return None
        if not re.fullmatch(r'[A-Za-z0-9+/=_-]+', cleaned):
            return None

        normalized = cleaned.replace('-', '+').replace('_', '/')
        normalized += '=' * ((4 - (len(normalized) % 4)) % 4)
        try:
            decoded = base64.b64decode(normalized)
        except Exception:
            return None

        try:
            text = decoded.decode('utf-8', errors='ignore').strip()
        except Exception:
            return None
        return text or None

    @staticmethod
    def _decode_percent_layers(value: str, max_rounds: int = 2) -> str:
        decoded = value
        for _ in range(max_rounds):
            next_value = unquote(decoded)
            if next_value == decoded:
                break
            decoded = next_value
        return decoded

    def _expand_data_link_candidates(self, raw_data_link: str) -> List[str]:
        values: List[str] = []
        seen = set()

        def add(candidate: Optional[str]) -> None:
            if not isinstance(candidate, str):
                return
            cleaned = candidate.strip().strip('"\'')
            if not cleaned or cleaned in seen:
                return
            seen.add(cleaned)
            values.append(cleaned)

        add(raw_data_link)
        add(html_lib.unescape(raw_data_link))

        for existing in list(values):
            add(self._decode_percent_layers(existing))

        for existing in list(values):
            decoded_b64 = self._try_decode_base64(existing)
            if not decoded_b64:
                continue
            add(decoded_b64)
            add(self._decode_percent_layers(decoded_b64))

        return values

    def _build_c_token_candidates(self, data_link_candidates: List[str]) -> List[str]:
        candidates: List[str] = []
        seen = set()

        def add(candidate: Optional[str]) -> None:
            if not isinstance(candidate, str):
                return
            cleaned = candidate.strip()
            if not cleaned or cleaned in seen:
                return
            seen.add(cleaned)
            candidates.append(cleaned)

        for data_link in data_link_candidates:
            add(data_link[::-1])
            add(self._decode_percent_layers(data_link)[::-1])
            add(data_link)

        return candidates

    def _cookies_for_url(
        self,
        url: str,
        session: Optional[requests.Session] = None,
    ) -> Dict[str, str]:
        parsed_url = urlparse(url)
        hostname = (parsed_url.hostname or '').lower()
        if not hostname:
            return {}

        cookie_jar = (session or self._make_session()).cookies
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

                if cookie.name and cookie.value is not None:
                    matched[cookie.name] = str(cookie.value)
            except Exception:
                continue

        return matched

    @staticmethod
    def _looks_like_cloudflare_challenge(html: str) -> bool:
        lower_html = html.lower()
        valid_page_markers = (
            'data-link',
            'btn-server',
            'switch-server',
            'dz_video',
            '<h1',
            'player-wrap',
        )
        if any(marker in lower_html for marker in valid_page_markers):
            return False

        markers = (
            'just a moment',
            'attention required',
            'cf-chl',
            'challenge-platform',
            'turnstile',
        )
        if any(marker in lower_html for marker in markers):
            return True

        looks_minimal_page = (
            len(html) < 2500
            and 'data-link' not in lower_html
            and 'btn-server' not in lower_html
            and 'switch-server' not in lower_html
            and 'dz_video' not in lower_html
            and '<h1' not in lower_html
        )
        if not looks_minimal_page:
            return False

        challenge_hints = (
            'cloudflare',
            'cdn-cgi/challenge',
            'cf-wrapper',
            'cf-browser-verification',
            '__cf_chl',
        )
        return any(hint in lower_html for hint in challenge_hints)

    def _is_cloudflare_response(self, response: Any) -> bool:
        header = str(response.headers.get('cf-mitigated') or '').lower()
        body = response.text or ''
        return header == 'challenge' or self._looks_like_cloudflare_challenge(body)

    def _build_cloudflare_help(self, url: str, session: requests.Session) -> str:
        browser = self._configured_browser_name() or 'chrome'

        if self._has_cloudflare_cookie(session):
            cookie_status = (
                'Browser cookies were loaded, but the Cloudflare clearance looks expired or the page '
                'still requires manual verification.'
            )
        elif self._browser_cookie_sync_summary:
            cookie_status = (
                f"I auto-loaded site cookies ({self._browser_cookie_sync_summary}), but no valid "
                'Cloudflare clearance was found for SupJav.'
            )
        else:
            cookie_status = 'I auto-tried local browser cookies, but no SupJav/Cloudflare cookies were found.'

        mirror_hint = ''
        for candidate in self._supjav_page_candidates(url):
            if candidate != url:
                mirror_hint = f"\n  4. Try mirror URL: {candidate}"
                break

        return (
            'Cloudflare challenge detected on SupJav. '
            + cookie_status
            + f"\n  1. Open this URL in your browser and wait until it loads: {url}"
            + f"\n  2. Re-run with: --cookies-from-browser {browser}"
            + '\n  3. If it still fails, copy the `turbovidhls` URL from DevTools and download that directly'
            + mirror_hint
        )

    def extract(self, url: str) -> MediaInfo:
        """Extract video - route based on URL type."""
        self._duration_hint = None
        self._visited_embed_urls = set()
        hostname = (urlparse(url).hostname or '').lower()

        # Direct turbovidhls/callistanise URL
        if 'turbovidhls.com' in hostname or 'turboviplay.com' in hostname:
            return self._extract_turbovidhls(url)
        if 'callistanise.com' in hostname or 'vidhide' in hostname:
            return self._extract_vidhide(url)

        # SupJav page
        return self._extract_supjav(url)

    # ===== TurboVidHLS Direct =====

    def _extract_turbovidhls(self, url: str) -> MediaInfo:
        """Extract from turbovidhls.com directly."""
        logger.info(f"TurboVidHLS extraction for: {url}")
        clean_url = url.split('#')[0]
        sess = self._make_session()

        resp = self._http_get(
            clean_url,
            referer='https://lk1.supremejav.com/',
            timeout=30,
            session=sess,
        )

        self._duration_hint = self._extract_duration_from_text(resp.text)

        # Try to get title from URL fragment or page
        title = "TurboVidHLS Video"
        if '#' in url and '@' in url:
            fragment = url.split('#')[1]
            title = fragment.split('@')[-1].replace('.mp4', '').replace('.', ' ').strip()
        else:
            m = re.search(r'<title>([^<]+)', resp.text)
            if m:
                title = m.group(1).strip()

        extracted_formats = self._extract_from_player_html(resp.text, resp.url, session=sess)
        formats, placeholder_formats = self._split_placeholder_turbovidhls_formats(
            extracted_formats,
            session=sess,
        )

        if placeholder_formats and not formats:
            print(
                f"  Detected {len(placeholder_formats)} empty TurboVidHLS playlist format(s), "
                "trying related SupJav page..."
            )
            related_media = self._recover_related_supjav_media(resp.text, resp.url, title, sess)
            if related_media and related_media.formats:
                formats = self._deduplicate(related_media.formats)
                formats.sort(key=lambda f: f.quality_score, reverse=True)
                resolved_title = related_media.title if related_media.title != 'Unknown Video' else title
                resolved_duration = related_media.duration or self._duration_hint
                return MediaInfo(
                    id=related_media.id or self._generate_id(url),
                    title=resolved_title,
                    url=url,
                    formats=formats,
                    media_type=MediaType.VIDEO,
                    extractor=self.EXTRACTOR_NAME,
                    thumbnail=related_media.thumbnail,
                    duration=resolved_duration,
                )

        if not formats:
            raise ExtractionError("No video URL found on TurboVidHLS page")

        formats = self._deduplicate(formats)
        formats.sort(key=lambda f: f.quality_score, reverse=True)

        return MediaInfo(
            id=self._generate_id(url),
            title=title,
            url=url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            duration=self._duration_hint,
        )

    @staticmethod
    def _playlist_has_media_entries(playlist_text: str) -> bool:
        if '#EXTINF:' in playlist_text or '#EXT-X-STREAM-INF' in playlist_text:
            return True

        for line in playlist_text.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                return True

        return False

    def _is_placeholder_turbovidhls_format(
        self,
        format_: StreamFormat,
        session: Optional[requests.Session] = None,
    ) -> bool:
        if format_.stream_type != StreamType.HLS:
            return False

        host = (urlparse(format_.url).hostname or '').lower()
        if 'turbovidhls' not in host and 'turboviplay' not in host:
            return False

        cache_key = format_.url
        if cache_key in self._playlist_probe_cache:
            return not self._playlist_probe_cache[cache_key]

        referer = format_.headers.get('Referer') if format_.headers else None
        has_media_entries = False

        try:
            resp = self._http_get(
                format_.url,
                referer=referer,
                accept='*/*',
                timeout=15,
                allow_redirects=True,
                session=session,
                raise_for_status=False,
            )
            if getattr(resp, 'status_code', 0) < 400:
                has_media_entries = self._playlist_has_media_entries(resp.text or '')
        except Exception as exc:
            logger.debug(f"TurboVidHLS playlist probe failed for {format_.url}: {exc}")

        self._playlist_probe_cache[cache_key] = has_media_entries
        return not has_media_entries

    def _split_placeholder_turbovidhls_formats(
        self,
        formats: List[StreamFormat],
        session: Optional[requests.Session] = None,
    ) -> tuple[List[StreamFormat], List[StreamFormat]]:
        usable: List[StreamFormat] = []
        placeholders: List[StreamFormat] = []

        for format_ in formats:
            if self._is_placeholder_turbovidhls_format(format_, session=session):
                placeholders.append(format_)
            else:
                usable.append(format_)

        return usable, placeholders

    def _filter_obvious_non_video_formats(self, formats: List[StreamFormat]) -> List[StreamFormat]:
        filtered: List[StreamFormat] = []
        for format_ in formats:
            lower_url = format_.url.lower()
            if any(token in lower_url for token in self.NON_VIDEO_URL_HINTS):
                continue
            filtered.append(format_)
        return filtered

    @staticmethod
    def _clean_related_search_term(raw_value: str) -> str:
        cleaned = re.sub(r'\.(?:mp4|m3u8|mkv|avi|mov)\b', '', raw_value, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s+', ' ', cleaned)
        return cleaned.strip(' -_.')

    def _related_supjav_search_terms(self, player_title: str, player_html: str) -> List[str]:
        terms: List[str] = []
        seen = set()

        def add(term: Optional[str]) -> None:
            if not isinstance(term, str):
                return
            normalized = self._clean_related_search_term(term)
            if len(normalized) < 3:
                return
            key = normalized.lower()
            if key in seen:
                return
            seen.add(key)
            terms.append(normalized)

        if isinstance(player_title, str):
            for match in self.JAV_CODE_PATTERN.findall(player_title):
                add(match.upper().replace('_', '-'))

        if player_title and player_title != 'TurboVidHLS Video':
            add(player_title)

        return terms

    def _is_supjav_result_page(self, url: Optional[str]) -> bool:
        if not url:
            return False

        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        if host.startswith('www.'):
            host = host[4:]

        return host in self.SUPJAV_MIRROR_HOSTS and parsed.path.lower().endswith('.html')

    def _extract_supjav_result_links(self, html: str, referer: str) -> List[str]:
        links: List[str] = []
        seen = set()

        def add(raw_url: Optional[str]) -> None:
            normalized = self._normalize_redirect_url(raw_url, referer)
            if not normalized or normalized in seen or not self._is_supjav_result_page(normalized):
                return
            seen.add(normalized)
            links.append(normalized)

        soup = BeautifulSoup(html, 'html.parser')
        for anchor in soup.find_all('a', href=True):
            href_value = anchor.attrs.get('href')
            href_str: Optional[str] = href_value if isinstance(href_value, str) else None
            add(href_str)

        for match in re.finditer(
            r'https?://(?:www\.)?supjav\.(?:com|ru|homes|to|net|org)/[^"\'\s<>]+\.html',
            html,
            re.IGNORECASE,
        ):
            add(match.group(0))

        return links[:self.SEARCH_RESULT_LIMIT]

    def _search_supjav_result_pages(
        self,
        query: str,
        session: Optional[requests.Session] = None,
    ) -> List[str]:
        search_url = f"https://supjav.com/?{urlencode({'s': query})}"

        try:
            resp = self._http_get(
                search_url,
                referer='https://supjav.com/',
                timeout=20,
                session=session,
                raise_for_status=False,
            )
        except Exception as exc:
            logger.debug(f"SupJav search failed for {query}: {exc}")
            return []

        if getattr(resp, 'status_code', 0) >= 400:
            return []

        return self._extract_supjav_result_links(resp.text, resp.url)

    def _extract_supjav_page_once(
        self,
        page_url: str,
        session: requests.Session,
    ) -> MediaInfo:
        resp = self._http_get(
            page_url,
            timeout=30,
            session=session,
            raise_for_status=False,
        )

        if self._is_cloudflare_response(resp) or getattr(resp, 'status_code', 0) in {401, 403}:
            self._sync_browser_cookies(session, force=True)
            resp = self._http_get(
                page_url,
                timeout=30,
                session=session,
                raise_for_status=False,
            )

        if self._is_cloudflare_response(resp) or getattr(resp, 'status_code', 0) >= 400:
            raise ExtractionError(f"Could not open related SupJav page: HTTP {getattr(resp, 'status_code', 'error')}")

        html = resp.text
        saved_duration_hint = self._duration_hint
        self._duration_hint = self._extract_duration_from_text(html) or saved_duration_hint

        soup = BeautifulSoup(html, 'html.parser')
        title = self._get_title(soup)
        thumbnail = self._get_thumbnail(soup)
        servers = self._get_servers(soup)

        bg = ''
        dz = soup.find(id='dz_video')
        if dz:
            bg_attr = dz.get('bg', '')
            if isinstance(bg_attr, str):
                bg = bg_attr

        formats: List[StreamFormat] = []
        for server in servers:
            try:
                server_formats = self._process_server(session, resp.url, server, bg)
            except Exception as exc:
                logger.debug(f"Related page server {server.get('name')} failed: {exc}")
                continue
            formats.extend(server_formats)

        if not formats:
            formats.extend(self._extract_direct_player_fallbacks(html, resp.url, session))

        formats = self._filter_obvious_non_video_formats(formats)
        formats = self._deduplicate(formats)
        formats.sort(key=lambda f: f.quality_score, reverse=True)

        return MediaInfo(
            id=self._generate_id(page_url),
            title=title,
            url=page_url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            thumbnail=thumbnail,
            duration=self._duration_hint,
        )

    def _recover_related_supjav_media(
        self,
        player_html: str,
        player_url: str,
        player_title: str,
        session: requests.Session,
    ) -> Optional[MediaInfo]:
        queries = self._related_supjav_search_terms(player_title, player_html)
        if not queries:
            return None

        seen_pages = set()
        for query in queries:
            print(f"  Searching SupJav for: {query}")
            for candidate_url in self._search_supjav_result_pages(query, session=session):
                if candidate_url in seen_pages or candidate_url == player_url:
                    continue

                seen_pages.add(candidate_url)
                print(f"  Trying related page: {candidate_url}")

                try:
                    candidate_media = self._extract_supjav_page_once(candidate_url, session)
                except Exception as exc:
                    logger.debug(f"Related SupJav page failed for {candidate_url}: {exc}")
                    continue

                usable_formats, placeholder_formats = self._split_placeholder_turbovidhls_formats(
                    candidate_media.formats,
                    session=session,
                )
                candidate_media.formats = usable_formats

                if candidate_media.formats:
                    if placeholder_formats:
                        print(
                            f"    Ignored {len(placeholder_formats)} empty TurboVidHLS playlist format(s)"
                        )
                    print(
                        f"    ✓ Related page yielded {len(candidate_media.formats)} alternative format(s)"
                    )
                    return candidate_media

        return None

    # ===== VidHide / Callistanise Direct =====

    def _extract_vidhide(self, url: str) -> MediaInfo:
        """Extract from callistanise.com / VidHide."""
        logger.info(f"VidHide extraction for: {url}")
        sess = self._make_session()
        parsed = urlparse(url)
        page_origin = f"{parsed.scheme}://{parsed.netloc}/" if parsed.scheme and parsed.netloc else None

        resp = self._http_get(
            url,
            referer=page_origin,
            timeout=30,
            allow_redirects=True,
            session=sess,
        )

        self._duration_hint = self._extract_duration_from_text(resp.text)

        formats = self._extract_from_player_html(resp.text, resp.url, session=sess)

        # Try unpacking JS
        if not formats and 'eval(function' in resp.text:
            from extractors.pubjav import unpack_js
            pattern = r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('.*?'\.split\('\|'\)\)\)"
            for match in re.finditer(pattern, resp.text, re.DOTALL):
                unpacked = unpack_js(match.group(0))
                if unpacked:
                    formats.extend(self._extract_m3u8_mp4(unpacked, resp.url, 'VidHide', session=sess))

        if not formats:
            raise ExtractionError("No video URL found on VidHide page")

        return MediaInfo(
            id=self._generate_id(url),
            title="VidHide Video",
            url=url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            duration=self._duration_hint,
        )

    # ===== SupJav Page =====

    def _extract_supjav(self, url: str) -> MediaInfo:
        """Extract from supjav.com page."""
        logger.info(f"SupJav extraction for: {url}")

        sess = self._make_session()
        if not self._has_cloudflare_cookie(sess):
            self._sync_browser_cookies(sess)

        print(f"  Fetching page (Cloudflare)...")
        resp = None
        last_fetch_error = None
        candidate_urls = self._supjav_page_candidates(url)

        for index, candidate_url in enumerate(candidate_urls, start=1):
            if index > 1:
                print(f"  Trying mirror [{index}/{len(candidate_urls)}]: {candidate_url}")

            try:
                candidate_resp = self._http_get(
                    candidate_url,
                    timeout=30,
                    session=sess,
                    raise_for_status=False,
                )
            except Exception as e:
                last_fetch_error = f"{candidate_url}: {e}"
                continue

            if self._is_cloudflare_response(candidate_resp) or candidate_resp.status_code in {401, 403}:
                loaded = self._sync_browser_cookies(sess, force=True)
                if loaded:
                    try:
                        candidate_resp = self._http_get(
                            candidate_url,
                            timeout=30,
                            session=sess,
                            raise_for_status=False,
                        )
                    except Exception as e:
                        last_fetch_error = f"{candidate_url}: {e}"
                        continue

            if self._is_cloudflare_response(candidate_resp):
                continue
            if candidate_resp.status_code >= 400:
                last_fetch_error = f"{candidate_url}: HTTP {candidate_resp.status_code}"
                continue

            resp = candidate_resp
            if candidate_url != url:
                print(f"  ✓ Using mirror page: {candidate_resp.url}")
            break

        if resp is None:
            if last_fetch_error and 'cloudflare' not in str(last_fetch_error).lower():
                logger.debug(f"SupJav fetch failed across mirrors: {last_fetch_error}")
            raise ExtractionError(self._build_cloudflare_help(url, sess))

        html = resp.text
        self._duration_hint = self._extract_duration_from_text(html)

        if self._looks_like_cloudflare_challenge(html):
            raise ExtractionError(self._build_cloudflare_help(url, sess))

        soup = BeautifulSoup(html, 'html.parser')
        title = self._get_title(soup)
        thumbnail = self._get_thumbnail(soup)
        servers = self._get_servers(soup)

        if not servers:
            print(f"  ⚠ Server list tidak ditemukan, mencoba fallback player langsung...")

        bg = ''
        dz = soup.find(id='dz_video')
        if dz:
            bg_attr = dz.get('bg', '')
            if isinstance(bg_attr, str):
                bg = bg_attr

        if servers:
            print(f"  Found {len(servers)} server(s): {', '.join(s['name'] for s in servers)}")

        # Try each server
        formats = []
        for server in servers:
            try:
                print(f"  Checking server {server['name']}...")
                server_formats = self._process_server(sess, resp.url, server, bg)
                formats.extend(server_formats)
                if server_formats:
                    print(f"    ✓ Found {len(server_formats)} format(s)")
            except Exception as e:
                print(f"    ✗ {str(e)[:60]}")

        if not formats:
            formats.extend(self._extract_direct_player_fallbacks(html, resp.url, sess))

        if not formats:
            # Give user instructions to use turbovidhls directly
            print(f"\n  ⚠ supremejav.com redirect blocked by Cloudflare.")
            print(f"  To download manually:")
            print(f"  1. Open {url} in your browser")
            print(f"  2. Press F12 → Network tab → click play")
            print(f"  3. Filter 'turbovidhls' → copy the URL")
            print(f"  4. Run: python main.py download \"<turbovidhls URL>\"")
            raise ExtractionError("Could not resolve server redirect (Cloudflare)")

        formats = self._deduplicate(formats)
        formats.sort(key=lambda f: f.quality_score, reverse=True)

        return MediaInfo(
            id=self._generate_id(url),
            title=title,
            url=url,
            formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            thumbnail=thumbnail,
            duration=self._duration_hint,
        )

    # ===== Server Processing =====

    def _process_server(self, sess: requests.Session, page_url: str,
                        server: Dict, bg: str) -> List[StreamFormat]:
        """Process server: try supremejav redirect → player page."""
        raw_data_link = server.get('data_link')
        if not isinstance(raw_data_link, str) or not raw_data_link.strip():
            return []

        data_link_candidates = self._expand_data_link_candidates(raw_data_link)
        if not data_link_candidates:
            return []

        # Newer pages sometimes place direct player URLs in data-link.
        for candidate in data_link_candidates:
            if not self._looks_like_url_candidate(candidate):
                continue
            direct_player_url = self._normalize_redirect_url(candidate, page_url)
            if not direct_player_url or not self._looks_like_player_redirect(direct_player_url):
                continue

            direct_formats = self._fetch_player_page(sess, direct_player_url, page_url, server['name'])
            if direct_formats:
                return direct_formats

        data_link = data_link_candidates[0]
        c_tokens = self._build_c_token_candidates(data_link_candidates)
        if not c_tokens:
            c_tokens = [data_link[::-1]]

        # Step 1: Visit ?l= to set cookies
        url_l = self._build_supreme_url(self.SUPREMEJAV_BASE, l=data_link, bg=bg)
        try:
            self._http_get(url_l, referer=page_url, timeout=15, session=sess)
        except Exception:
            pass

        # Step 2: Visit ?c= to get redirect (try multiple token variants)
        for c_token in c_tokens:
            url_c = self._build_supreme_url(self.SUPREMEJAV_BASE, c=c_token)
            try:
                r = self._http_get(
                    url_c,
                    referer=url_l,
                    timeout=15,
                    allow_redirects=False,
                    session=sess,
                )
                location = self._response_redirect_target(r, url_c)
            except Exception:
                location = None

            if location:
                logger.debug(f"Redirect to: {location}")
                return self._fetch_player_page(sess, location, url_c, server['name'])

            # Step 3: Try following with allow_redirects=True
            try:
                r2 = self._http_get(
                    url_c,
                    referer=url_l,
                    timeout=15,
                    allow_redirects=True,
                    session=sess,
                )
                if r2.url != url_c and len(r2.text) > 1000:
                    formats = self._extract_from_player_html(r2.text, r2.url, server['name'], session=sess)
                    if formats:
                        return formats

                location = self._extract_redirect_url(r2.text, r2.url)
                if location and location != r2.url:
                    return self._fetch_player_page(sess, location, r2.url, server['name'])
            except Exception:
                pass

        # Step 4: Try visiting supremejav.com first for Cloudflare cookies
        try:
            self._http_get('https://lk1.supremejav.com/', referer=page_url, timeout=10, session=sess)
            for c_token in c_tokens:
                url_c = self._build_supreme_url(self.SUPREMEJAV_BASE, c=c_token)
                r3 = self._http_get(
                    url_c,
                    referer=url_l,
                    timeout=15,
                    allow_redirects=False,
                    session=sess,
                )
                location = self._response_redirect_target(r3, url_c)
                if location:
                    return self._fetch_player_page(sess, location, url_c, server['name'])
        except Exception:
            pass

        return []

    def _extract_direct_player_fallbacks(
        self,
        page_html: str,
        page_url: str,
        sess: requests.Session,
    ) -> List[StreamFormat]:
        """Fallback extraction when server button flow breaks."""
        formats = self._extract_from_player_html(page_html, page_url, 'SupJav fallback', session=sess)
        if formats:
            print(f"  ✓ Fallback parser found {len(formats)} format(s) directly from page")
            return self._deduplicate(formats)

        fallback_formats: List[StreamFormat] = []
        candidate_urls = self._extract_embedded_player_urls(page_html, page_url)
        for candidate_url in candidate_urls:
            try:
                found = self._fetch_player_page(sess, candidate_url, page_url, 'SupJav fallback')
            except Exception:
                found = []
            fallback_formats.extend(found)

        if fallback_formats:
            print(f"  ✓ Player URL fallback found {len(fallback_formats)} format(s)")
        return self._deduplicate(fallback_formats)

    def _fetch_player_page(self, sess: requests.Session, player_url: str,
                            referer: str, server_name: str) -> List[StreamFormat]:
        """Fetch player page and extract formats."""
        clean_url = player_url.split('#')[0]
        return self._follow_iframe(clean_url, referer, server_name, session=sess)

    @staticmethod
    def _source_slug(source_label: str) -> str:
        slug = re.sub(r'[^a-z0-9]+', '-', source_label.lower()).strip('-')
        return slug or 'stream'

    @staticmethod
    def _playlist_variant_name(url: str) -> Optional[str]:
        lower_url = url.lower()
        if '/hls4/' in lower_url or '/stream/' in lower_url:
            return 'hls4'
        if '/hls3/' in lower_url or 'master.txt' in lower_url:
            return 'hls3'
        if '/hls2/' in lower_url:
            return 'hls2'
        return None

    def _extract_duration_from_text(self, text: str) -> Optional[int]:
        match = re.search(r'duration\s*[:=]\s*["\']?(\d+(?:\.\d+)?)', text, re.IGNORECASE)
        if match:
            return int(float(match.group(1)))
        return None

    @staticmethod
    def _normalize_redirect_url(raw_url: object, referer: str) -> Optional[str]:
        if not isinstance(raw_url, str):
            return None

        url = raw_url.strip().strip('"\'')
        if not url or url.lower() in {'null', 'undefined', 'javascript:void(0)', 'about:blank'}:
            return None

        url = url.replace('\\/', '/').replace('&amp;', '&')

        if url.startswith('//'):
            return 'https:' + url
        if url.startswith(('http://', 'https://')):
            return url
        if any(ch.isspace() for ch in url):
            return None
        return urljoin(referer, url)

    def _looks_like_player_redirect(self, url: Optional[str]) -> bool:
        if not url:
            return False

        lower_url = url.lower()
        if any(token in lower_url for token in self.PLAYER_HOST_HINTS):
            return True

        path = urlparse(lower_url).path
        return path.startswith(('/t/', '/v/', '/e/', '/d/', '/embed/'))

    def _response_redirect_target(self, response: requests.Response, referer: str) -> Optional[str]:
        location = response.headers.get('location', '')
        redirect_url = self._normalize_redirect_url(location, response.url or referer)
        if redirect_url:
            return redirect_url
        return self._extract_redirect_url(response.text, response.url or referer)

    def _extract_redirect_url(self, text: str, referer: str) -> Optional[str]:
        if not text:
            return None

        soup = BeautifulSoup(text, 'html.parser')
        meta_refresh = soup.find('meta', attrs={'http-equiv': re.compile(r'refresh', re.IGNORECASE)})
        if meta_refresh:
            content = meta_refresh.get('content')
            if isinstance(content, str):
                match = re.search(r'url=([^;]+)', content, re.IGNORECASE)
                if match:
                    candidate = self._normalize_redirect_url(match.group(1), referer)
                    if candidate:
                        return candidate

        for tag_name, attr_name in (('iframe', 'src'), ('iframe', 'data-src'), ('a', 'href')):
            for tag in soup.find_all(tag_name):
                candidate = self._normalize_redirect_url(tag.get(attr_name), referer)
                if candidate and self._looks_like_player_redirect(candidate):
                    return candidate

        redirect_patterns = (
            r'(?:window|top|self|document)\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
            r'location\.(?:replace|assign)\(\s*["\']([^"\']+)["\']\s*\)',
            r'(https?://[^"\'\s<>]+)',
        )
        for pattern in redirect_patterns:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                candidate = match.group(1)
                normalized = self._normalize_redirect_url(candidate, referer)
                if normalized and self._looks_like_player_redirect(normalized):
                    return normalized

        for candidate in self._extract_embedded_player_urls(text, referer):
            if self._looks_like_player_redirect(candidate):
                return candidate

        return None

    def _extract_embedded_player_urls(self, text: str, referer: str) -> List[str]:
        urls: List[str] = []
        seen = set()

        def add(raw_url: Optional[str]) -> None:
            if not isinstance(raw_url, str):
                return
            candidate = self._normalize_redirect_url(raw_url, referer)
            if not candidate or candidate in seen:
                return
            if not self._looks_like_player_redirect(candidate):
                return
            seen.add(candidate)
            urls.append(candidate)

        absolute_url_pattern = r'(https?://[^"\'\s<>]+)'
        for match in re.finditer(absolute_url_pattern, text):
            add(match.group(1))

        for pattern in (
            r'(?:src|href|data-src|data-url)\s*=\s*["\']([^"\']+)["\']',
            r'(?:src|href|data-src|data-url)\s*:\s*["\']([^"\']+)["\']',
            r'window\.open\(\s*["\']([^"\']+)["\']',
            r'location\.(?:href|assign|replace)\(\s*["\']([^"\']+)["\']',
        ):
            for match in re.finditer(pattern, text, re.IGNORECASE):
                add(match.group(1))

        for encoded in re.findall(r'atob\(\s*["\']([A-Za-z0-9+/=_-]{16,})["\']\s*\)', text):
            decoded = self._try_decode_base64(encoded)
            if not decoded:
                continue
            for match in re.finditer(absolute_url_pattern, decoded):
                add(match.group(1))

        return urls

    @staticmethod
    def _parse_quality_hint(raw_value: Optional[object]) -> tuple[Optional[str], Optional[int], Optional[str]]:
        if raw_value is None:
            return None, None, None

        text = str(raw_value).strip()
        if not text:
            return None, None, None

        match = re.search(r'(\d{3,4})', text)
        if match:
            height = int(match.group(1))
            quality = f"{height}p"
            label = None if text.lower() in {quality.lower(), str(height)} else text
            return quality, height, label

        return None, None, text

    def _stream_type_from_hint(self, url: str, media_hint: Optional[object] = None) -> StreamType:
        hint = str(media_hint or '').lower()
        lower_url = url.lower()
        if any(token in hint for token in ('mpegurl', 'apple.mpegurl', 'x-mpegurl', 'hls')):
            return StreamType.HLS
        if 'dash' in hint or 'mpd' in hint:
            return StreamType.DASH
        if '/playlist' in lower_url:
            return StreamType.HLS
        if '/manifest' in lower_url:
            return StreamType.DASH
        return self._detect_stream_type(url)

    def _extract_video_tag_sources(self, html: str, referer: str) -> List[Dict[str, object]]:
        entries: List[Dict[str, object]] = []
        seen = set()
        soup = BeautifulSoup(html, 'html.parser')

        for video in soup.find_all('video'):
            source_tags = [video, *video.find_all('source')]
            for tag in source_tags:
                raw_url = tag.get('src')
                media_hint = tag.get('type') if isinstance(tag.get('type'), str) else None
                normalized = self._normalize_media_url(raw_url, referer, media_hint=media_hint)
                if not normalized or normalized in seen:
                    continue
                seen.add(normalized)

                quality, height, label = self._parse_quality_hint(
                    tag.get('label')
                    or tag.get('res')
                    or tag.get('size')
                    or tag.get('data-res')
                    or tag.get('height')
                )

                entries.append({
                    'url': normalized,
                    'quality': quality,
                    'height': height,
                    'width': int(height * 16 / 9) if height else None,
                    'label': label,
                    'bitrate': None,
                    'stream_type': self._stream_type_from_hint(normalized, media_hint),
                })

        return entries

    def _extract_source_entries(self, text: str, referer: str) -> List[Dict[str, object]]:
        entries: List[Dict[str, object]] = []
        seen = set()
        key_pattern = r'["\']?(?:file|src|source|url|video_url|videoUrl|streamUrl|hlsUrl|playlist|manifest)["\']?'
        block_pattern = r'\{[^{}]{0,800}?' + key_pattern + r'\s*:\s*["\'][^"\']+["\'][^{}]{0,800}?\}'
        url_pattern = key_pattern + r'\s*:\s*["\']([^"\']+)["\']'

        for match in re.finditer(block_pattern, text, re.IGNORECASE):
            block = match.group(0)
            url_match = re.search(url_pattern, block, re.IGNORECASE)
            if not url_match:
                continue

            media_hint_match = re.search(
                r'["\']?(?:type|mime)["\']?\s*:\s*["\']([^"\']+)["\']',
                block,
                re.IGNORECASE,
            )
            media_hint = media_hint_match.group(1) if media_hint_match else None
            normalized = self._normalize_media_url(url_match.group(1), referer, media_hint=media_hint)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)

            hint_match = re.search(
                r'["\']?(?:label|quality|res|size|height)["\']?\s*:\s*["\']?([^,"\'}\]]+)',
                block,
                re.IGNORECASE,
            )
            bitrate_match = re.search(
                r'["\']?(?:bandwidth|bitrate)["\']?\s*:\s*["\']?(\d{4,8})',
                block,
                re.IGNORECASE,
            )
            width_match = re.search(
                r'["\']?width["\']?\s*:\s*["\']?(\d{3,4})',
                block,
                re.IGNORECASE,
            )

            quality, height, label = self._parse_quality_hint(hint_match.group(1) if hint_match else None)
            bitrate = int(bitrate_match.group(1)) if bitrate_match else None
            if bitrate and bitrate > 10000:
                bitrate //= 1000

            entries.append({
                'url': normalized,
                'quality': quality,
                'height': height,
                'width': int(width_match.group(1)) if width_match else (int(height * 16 / 9) if height else None),
                'label': label,
                'bitrate': bitrate,
                'stream_type': self._stream_type_from_hint(normalized, media_hint),
            })

        return entries

    def _extract_iframe_urls(self, html: str, referer: str) -> List[str]:
        iframe_urls: List[str] = []
        fallback_urls: List[str] = []
        seen = set()
        soup = BeautifulSoup(html, 'html.parser')

        for iframe in soup.find_all('iframe'):
            candidate = self._normalize_redirect_url(iframe.get('src') or iframe.get('data-src'), referer)
            if candidate and candidate not in seen:
                seen.add(candidate)
                lower_candidate = candidate.lower()
                if self._looks_like_player_redirect(candidate) or any(
                    token in lower_candidate for token in ('player', 'embed', 'video', 'stream', 'media', 'hls', 'm3u8', 'mpd')
                ):
                    iframe_urls.append(candidate)
                else:
                    fallback_urls.append(candidate)

        return iframe_urls or fallback_urls

    @staticmethod
    def _entry_str(entry: Dict[str, object], key: str) -> Optional[str]:
        value = entry.get(key)
        return value if isinstance(value, str) else None

    @staticmethod
    def _entry_int(entry: Dict[str, object], key: str) -> Optional[int]:
        value = entry.get(key)
        return value if isinstance(value, int) else None

    @staticmethod
    def _entry_stream_type(entry: Dict[str, object], key: str = 'stream_type') -> Optional[StreamType]:
        value = entry.get(key)
        return value if isinstance(value, StreamType) else None

    def _normalize_media_url(
        self,
        raw_url: object,
        referer: str,
        media_hint: Optional[object] = None,
    ) -> Optional[str]:
        if not isinstance(raw_url, str):
            return None

        url = raw_url.strip().strip('"\'')
        if not url or url in {'null', 'undefined'}:
            return None

        url = url.replace('\\/', '/').replace('&amp;', '&')
        lower_url = url.lower()
        lower_hint = str(media_hint or '').lower()

        if lower_url.startswith(('javascript:', 'blob:', 'data:')):
            return None
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith(('http://', 'https://')):
            return url
        if url.startswith(('/', './', '../')) or url.startswith('stream/'):
            return urljoin(referer, url)
        if any(token in lower_url for token in ('.m3u8', '.mp4', '.mpd', 'master.txt', '/stream/', '/playlist', '/manifest')):
            return urljoin(referer, url)
        if any(token in lower_hint for token in ('mpegurl', 'apple.mpegurl', 'x-mpegurl', 'dash', 'video/', 'audio/')):
            return urljoin(referer, url)
        return None

    def _extract_named_links(self, text: str, referer: str) -> Dict[str, str]:
        named_links: Dict[str, str] = {}
        for key, raw_url in re.findall(r'["\']([A-Za-z0-9_]+)["\']\s*:\s*["\']([^"\']+)["\']', text):
            normalized = self._normalize_media_url(raw_url, referer)
            if normalized:
                named_links[key] = normalized
        return named_links

    def _expand_source_expression(
        self,
        expression: str,
        named_links: Dict[str, str],
        referer: str,
    ) -> List[str]:
        urls: List[str] = []
        for part in expression.split('||'):
            token = part.strip().strip('()[]{}')
            if not token:
                continue

            if token.startswith('links.'):
                candidate = named_links.get(token.split('.', 1)[1])
            elif token in named_links:
                candidate = named_links.get(token)
            else:
                candidate = self._normalize_media_url(token, referer)

            if candidate:
                urls.append(candidate)
        return urls

    def _extract_candidate_urls(self, text: str, referer: str) -> List[str]:
        named_links = self._extract_named_links(text, referer)
        candidates: List[str] = []
        seen = set()
        source_key_pattern = r'(?:file|src|source|url|video_url|videoUrl|streamUrl|hlsUrl|playlist|manifest)'
        source_expression_pattern = r'["\']?' + source_key_pattern + r'["\']?\s*:\s*([^,\]}]+)'

        def add_candidate(raw_url: str) -> None:
            normalized = self._normalize_media_url(raw_url, referer)
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            candidates.append(normalized)

        for match in re.finditer(source_expression_pattern, text, re.IGNORECASE):
            expression = match.group(1).strip()
            expanded = self._expand_source_expression(expression, named_links, referer)
            if expanded:
                for url in expanded:
                    add_candidate(url)
            else:
                add_candidate(expression)

        for pattern in (
            r'data-hash\s*=\s*["\']([^"\']+)["\']',
            r'urlPlay\s*=\s*["\']([^"\']+)["\']',
            r'data-(?:file|src)\s*=\s*["\']([^"\']+)["\']',
        ):
            for match in re.finditer(pattern, text, re.IGNORECASE):
                add_candidate(match.group(1))

        for url in named_links.values():
            add_candidate(url)

        absolute_pattern = r'(https?://[^\s"\'\\]+(?:\.m3u8|\.mp4|\.mpd|master\.txt)(?:\?[^\s"\'\\]*)?)'
        relative_pattern = r'((?:/|\./|\.\./)[^\s"\'\\]+(?:\.m3u8|\.mp4|\.mpd|master\.txt)(?:\?[^\s"\'\\]*)?)'
        manifest_absolute_pattern = r'(https?://[^\s"\'\\]+(?:/playlist|/manifest)[^\s"\'\\]*)'
        manifest_relative_pattern = r'((?:/|\./|\.\./)[^\s"\'\\]+(?:/playlist|/manifest)[^\s"\'\\]*)'

        for match in re.finditer(absolute_pattern, text, re.IGNORECASE):
            add_candidate(match.group(1))
        for match in re.finditer(relative_pattern, text, re.IGNORECASE):
            add_candidate(match.group(1))
        for match in re.finditer(manifest_absolute_pattern, text, re.IGNORECASE):
            add_candidate(match.group(1))
        for match in re.finditer(manifest_relative_pattern, text, re.IGNORECASE):
            add_candidate(match.group(1))

        return candidates

    @staticmethod
    def _should_resolve_master_playlist(playlist_url: str, referer: str) -> bool:
        lower_url = playlist_url.lower()
        if '.m3u8' not in lower_url and 'master.txt' not in lower_url:
            return False

        playlist_host = urlparse(playlist_url).netloc.lower()
        referer_host = urlparse(referer).netloc.lower()
        if playlist_host and playlist_host == referer_host:
            return True

        playlist_path = urlparse(playlist_url).path.lower()
        return '/stream/' in playlist_path or 'master.txt' in playlist_path

    def _extract_urls_from_text(
        self,
        text: str,
        referer: str,
        source_label: str,
        session: Optional[requests.Session] = None,
    ) -> List[StreamFormat]:
        if not self._duration_hint:
            self._duration_hint = self._extract_duration_from_text(text)

        formats: List[StreamFormat] = []
        seen = set()

        for candidate_url in self._extract_candidate_urls(text, referer):
            lower_url = candidate_url.lower()
            if candidate_url in seen:
                continue

            if '.mp4' in lower_url:
                if any(token in lower_url for token in ('/thumb/', '/img/', 'dmm.co.jp', 'litevideo')):
                    continue
                seen.add(candidate_url)
                formats.append(self._make_format(candidate_url, source_label, referer, session=session))
                continue

            if self._should_resolve_master_playlist(candidate_url, referer):
                resolved_formats = self._resolve_m3u8(candidate_url, referer, source_label, session=session)
                if resolved_formats:
                    for resolved_format in resolved_formats:
                        if resolved_format.url in seen:
                            continue
                        seen.add(resolved_format.url)
                        formats.append(resolved_format)
                    continue

            stream_type = self._stream_type_from_hint(candidate_url)
            if stream_type == StreamType.HLS or '.m3u8' in lower_url or 'master.txt' in lower_url:
                seen.add(candidate_url)
                formats.append(self._make_format(
                    candidate_url,
                    source_label,
                    referer,
                    session=session,
                    stream_type=StreamType.HLS,
                ))
                continue

            if stream_type == StreamType.DASH or '.mpd' in lower_url:
                seen.add(candidate_url)
                formats.append(self._make_format(
                    candidate_url,
                    source_label,
                    referer,
                    session=session,
                    stream_type=StreamType.DASH,
                ))

        return formats

    def _follow_iframe(
        self,
        iframe_url: str,
        referer: str,
        source_label: str,
        session: Optional[requests.Session] = None,
        depth: int = 0,
    ) -> List[StreamFormat]:
        normalized_url = self._normalize_redirect_url(iframe_url, referer)
        if not normalized_url or depth > 2:
            return []
        if normalized_url in self._visited_embed_urls:
            return []

        self._visited_embed_urls.add(normalized_url)
        logger.debug(f"Following iframe/player: {normalized_url}")

        try:
            resp = self._http_get(
                normalized_url,
                referer=referer,
                timeout=15,
                allow_redirects=True,
                session=session,
            )
        except Exception as exc:
            logger.debug(f"Failed to fetch iframe/player {normalized_url}: {exc}")
            return []

        self._visited_embed_urls.add(resp.url)

        formats: List[StreamFormat] = []
        redirect_url = self._extract_redirect_url(resp.text, resp.url)
        if redirect_url and redirect_url != resp.url:
            formats.extend(self._follow_iframe(
                redirect_url,
                resp.url,
                source_label,
                session=session,
                depth=depth + 1,
            ))

        formats.extend(self._extract_from_player_html(
            resp.text,
            resp.url,
            source_label,
            session=session,
            depth=depth,
        ))
        return self._deduplicate(formats)

    def _unpack_all_js(self, html: str) -> str:
        unpacked_parts = []
        if 'eval(function' not in html:
            return ''

        try:
            from extractors.pubjav import unpack_js
        except ImportError:
            return ''

        pattern = r"eval\(function\(p,a,c,k,e,d\)\{.*?\}\('.*?'\.split\('\|'\)\)\)"
        for match in re.finditer(pattern, html, re.DOTALL):
            result = unpack_js(match.group(0))
            if result:
                unpacked_parts.append(result)
        return '\n'.join(unpacked_parts)

    # ===== HTML Extraction =====

    def _extract_from_player_html(
        self,
        html: str,
        page_url: str,
        server_name: str = "Stream",
        session: Optional[requests.Session] = None,
        depth: int = 0,
    ) -> List[StreamFormat]:
        """Extract m3u8/mp4 from any player page HTML."""
        if not self._duration_hint:
            self._duration_hint = self._extract_duration_from_text(html)

        formats: List[StreamFormat] = []
        extracted_texts = [html]

        unpacked = self._unpack_all_js(html)
        if unpacked:
            if not self._duration_hint:
                self._duration_hint = self._extract_duration_from_text(unpacked)
            extracted_texts.append(unpacked)

        structured_entries = self._extract_video_tag_sources(html, page_url)
        for text in extracted_texts:
            structured_entries.extend(self._extract_source_entries(text, page_url))

        for text in extracted_texts:
            formats.extend(self._extract_urls_from_text(text, page_url, server_name, session=session))

        for entry in structured_entries:
            candidate_url = str(entry['url'])
            lower_url = candidate_url.lower()
            if '.mp4' in lower_url and ('/thumb/' in lower_url or '/img/' in lower_url):
                continue

            formats.append(self._make_format(
                candidate_url,
                server_name,
                page_url,
                session=session,
                quality=self._entry_str(entry, 'quality'),
                height=self._entry_int(entry, 'height'),
                width=self._entry_int(entry, 'width'),
                bitrate=self._entry_int(entry, 'bitrate'),
                stream_type=self._entry_stream_type(entry),
                label_override=self._entry_str(entry, 'label'),
            ))

        if depth < 2:
            for iframe_url in self._extract_iframe_urls(html, page_url):
                formats.extend(self._follow_iframe(
                    iframe_url,
                    page_url,
                    server_name,
                    session=session,
                    depth=depth + 1,
                ))

        return self._deduplicate(formats)

    def _resolve_m3u8(
        self,
        m3u8_url: str,
        referer: str,
        server_name: str,
        session: Optional[requests.Session] = None,
    ) -> List[StreamFormat]:
        """
        Fetch m3u8 and resolve master playlist to individual quality streams.
        Returns sub-playlist URLs with quality info, or empty if not a master.
        """
        try:
            resp = self._http_get(
                m3u8_url,
                referer=referer,
                accept='*/*',
                timeout=15,
                allow_redirects=True,
                session=session,
            )
            content = resp.text
        except Exception:
            return []

        # Check if it's a master playlist
        if '#EXT-X-STREAM-INF' not in content:
            return []  # Not a master, use as-is

        print(f"    Resolving master playlist...")

        formats: List[StreamFormat] = []
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        for i, line in enumerate(lines):
            if line.startswith('#EXT-X-STREAM-INF:'):
                width, height, bandwidth = None, None, None

                res_match = re.search(r'RESOLUTION=(\d+)x(\d+)', line)
                if res_match:
                    width = int(res_match.group(1))
                    height = int(res_match.group(2))

                bw_match = re.search(r'BANDWIDTH=(\d+)', line)
                if bw_match:
                    bandwidth = int(bw_match.group(1)) // 1000

                if i + 1 < len(lines):
                    sub_url = lines[i + 1].strip()
                    if not sub_url.startswith('#') and sub_url:
                        normalized_sub_url = self._normalize_media_url(
                            sub_url,
                            resp.url,
                            media_hint='application/x-mpegURL',
                        )
                        if not normalized_sub_url:
                            continue

                        quality = f"{height}p" if height else None
                        print(f"    ✓ {quality or 'unknown'} ({bandwidth}kbps)" if bandwidth else f"    ✓ {quality or 'unknown'}")

                        formats.append(self._make_format(
                            normalized_sub_url,
                            server_name,
                            referer,
                            session=session,
                            quality=quality,
                            height=height,
                            width=width,
                            bitrate=bandwidth,
                            stream_type=StreamType.HLS,
                        ))

        return formats

    def _extract_m3u8_mp4(
        self,
        text: str,
        referer: str,
        source_label: str = 'Stream',
        session: Optional[requests.Session] = None,
    ) -> List[StreamFormat]:
        """Extract m3u8/mp4 URLs from text."""
        return self._extract_urls_from_text(text, referer, source_label, session=session)

    def _make_format(
        self,
        url: str,
        server_name: str,
        referer: str,
        *,
        session: Optional[requests.Session] = None,
        quality: Optional[str] = None,
        height: Optional[int] = None,
        width: Optional[int] = None,
        bitrate: Optional[int] = None,
        stream_type: Optional[StreamType] = None,
        label_override: Optional[str] = None,
    ) -> StreamFormat:
        """Create StreamFormat."""
        lower_url = url.lower()
        st = stream_type or self._stream_type_from_hint(url)
        inferred_quality = quality
        inferred_height = height
        inferred_width = width

        if inferred_quality and not inferred_height:
            qm = re.search(r'(\d{3,4})', inferred_quality)
            if qm:
                inferred_height = int(qm.group(1))
        if not inferred_quality:
            qm = re.search(r'(\d{3,4})p', lower_url)
            if qm:
                inferred_height = int(qm.group(1))
                inferred_quality = f"{inferred_height}p"
        if inferred_height and not inferred_width:
            inferred_width = int(inferred_height * 16 / 9)

        playlist_name = self._playlist_variant_name(url)
        if label_override:
            clean_label = label_override.strip()
            label = clean_label if clean_label.lower().startswith(server_name.lower()) else f"{server_name} {clean_label}"
        elif st == StreamType.HLS and inferred_quality:
            label = f"{server_name} HLS {inferred_quality}"
        elif st == StreamType.HLS and playlist_name:
            label = f"{server_name} HLS ({playlist_name})"
        elif st == StreamType.HLS:
            label = f"{server_name} HLS"
        elif st == StreamType.DASH and inferred_quality:
            label = f"{server_name} DASH {inferred_quality}"
        elif st == StreamType.DASH:
            label = f"{server_name} DASH"
        else:
            label = f"{server_name} MP4"

        source_slug = self._source_slug(server_name)
        digest = self._generate_id(url)[:6]
        if st == StreamType.HLS:
            format_id = f"sj-{source_slug}-hls-{inferred_height or 0}-{digest}"
        elif st == StreamType.DASH:
            format_id = f"sj-{source_slug}-dash-{inferred_height or 0}-{digest}"
        else:
            format_id = f"sj-{source_slug}-mp4-{digest}"

        return StreamFormat(
            format_id=format_id,
            url=url,
            ext='mp4',
            quality=inferred_quality,
            height=inferred_height,
            width=inferred_width,
            bitrate=bitrate,
            stream_type=st,
            is_video=True,
            is_audio=True,
            label=label,
            headers={'Referer': referer},
            cookies=self._cookies_for_url(url, session),
        )

    def _is_video_url(self, url: str) -> bool:
        return any(x in url.lower() for x in ['.mp4', '.m3u8', '.mpd', '.webm', '.ts', 'master.txt'])

    # ===== Page Parsing =====

    def _get_title(self, soup: BeautifulSoup) -> str:
        h1 = soup.find('h1')
        if h1:
            heading = h1.get_text()
            if isinstance(heading, str):
                return heading.strip()
        og = soup.find('meta', property='og:title')
        if og:
            content = og.get('content')
            if isinstance(content, str) and content:
                return content.strip()
        return "Unknown Video"

    def _get_thumbnail(self, soup: BeautifulSoup) -> Optional[str]:
        pw = soup.find(class_='player-wrap')
        style_value = pw.get('style') if pw else None
        if isinstance(style_value, str) and style_value:
            m = re.search(r'url\(([^)]+)\)', style_value)
            if m:
                return m.group(1).strip("'\"")
        og = soup.find('meta', property='og:image')
        if og:
            content = og.get('content')
            if isinstance(content, str) and content:
                return content
        return None

    def _get_servers(self, soup: BeautifulSoup) -> List[Dict]:
        servers = []
        seen = set()
        elements = soup.select('.btn-server')
        if not elements:
            elements = soup.select('[data-link]')

        for index, btn in enumerate(elements, start=1):
            link = btn.get('data-link', '')
            name = btn.get_text().strip()
            if not name:
                for attr in ('data-name', 'title', 'aria-label'):
                    value = btn.get(attr)
                    if isinstance(value, str) and value.strip():
                        name = value.strip()
                        break
            if not name:
                name = f"Server {index}"

            if isinstance(link, str) and link and link not in seen:
                seen.add(link)
                servers.append({'name': name, 'data_link': link})

        if servers:
            return servers

        raw_html = str(soup)
        fallback_links = []
        for pattern in (
            r'data-link\s*=\s*["\']([^"\']+)["\']',
            r'["\']data-link["\']\s*:\s*["\']([^"\']+)["\']',
            r'supjav\.php\?l=([^"\'\s&]+)',
        ):
            for match in re.finditer(pattern, raw_html, re.IGNORECASE):
                token = html_lib.unescape(match.group(1)).strip()
                if token and token not in seen:
                    seen.add(token)
                    fallback_links.append(token)

        for index, link in enumerate(fallback_links, start=1):
            servers.append({'name': f'Server {index}', 'data_link': link})

        return servers

    def _deduplicate(self, formats: List[StreamFormat]) -> List[StreamFormat]:
        seen = set()
        unique = []
        for fmt in formats:
            normalized_url = unquote(fmt.url)
            if normalized_url not in seen:
                seen.add(normalized_url)
                unique.append(fmt)
        return unique
