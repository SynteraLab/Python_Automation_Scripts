# pyright: reportMissingImports=false, reportOptionalCall=false
"""
Advanced extractor for JavaScript-rendered content using Playwright.
Optional - only available if Playwright is installed.
"""

import re
import json
import asyncio
from typing import List, Dict, Optional, Any
from urllib.parse import urlparse
import logging

from .base import ExtractorBase, ExtractionError
from models.media import MediaInfo, StreamFormat, MediaType, StreamType
from utils.parser import MediaURLExtractor, MetadataExtractor

logger = logging.getLogger(__name__)

try:
    from playwright.async_api import async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    async_playwright = None
    PLAYWRIGHT_AVAILABLE = False


class AdvancedExtractor(ExtractorBase):
    """
    Advanced extractor for sites with JavaScript-rendered content.
    Uses Playwright to render pages and intercept network requests.
    """

    EXTRACTOR_NAME = "advanced"
    EXTRACTOR_DESCRIPTION = "Extractor for JavaScript-rendered pages (requires Playwright)"
    REQUIRES_BROWSER = True
    URL_PATTERNS = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._intercepted_urls: List[Dict] = []

    def extract(self, url: str) -> MediaInfo:
        if not PLAYWRIGHT_AVAILABLE:
            raise ExtractionError(
                "Playwright is required. Install: pip install playwright && playwright install"
            )

        # Run in a separate thread to avoid conflict with
        # the already-running asyncio event loop from CLI
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self._run_in_new_loop, url)
            return future.result()

    def _run_in_new_loop(self, url: str) -> MediaInfo:
        """Run async extraction in a fresh event loop (own thread)."""
        return asyncio.run(self._async_extract(url))

    def _config_value(self, key: str, default=None):
        if isinstance(self.config, dict):
            if key in self.config:
                return self.config.get(key, default)
            extractor_cfg = self.config.get('extractor')
            if isinstance(extractor_cfg, dict):
                return extractor_cfg.get(key, default)
            if extractor_cfg is not None and hasattr(extractor_cfg, key):
                return getattr(extractor_cfg, key)
            return default
        return getattr(self.config, key, default)

    async def _async_extract(self, url: str) -> MediaInfo:
        logger.info(f"Advanced extraction (browser) for: {url}")

        if async_playwright is None:
            raise ExtractionError(
                "Playwright is required. Install: pip install playwright && playwright install"
            )

        async with async_playwright() as p:
            browser_type = str(self._config_value('browser_type', 'chromium') or 'chromium')
            headless = self._config_value('headless', True)
            browser = await getattr(p, browser_type).launch(headless=headless)

            try:
                context = await browser.new_context(
                    user_agent=self.session.user_agent,
                    viewport={'width': 1920, 'height': 1080}
                )
                page = await context.new_page()
                self._intercepted_urls = []
                await self._setup_interception(page)
                await page.goto(url, wait_until='networkidle', timeout=60000)
                await page.wait_for_timeout(3000)
                await self._try_start_video(page)
                await page.wait_for_timeout(5000)
                html = await page.content()
                video_srcs = await self._get_video_sources(page)
            finally:
                await browser.close()

        found_urls = self._process_intercepted_urls()
        for src in video_srcs:
            found_urls.append({
                'url': src,
                'type': self._detect_type_from_url(src),
                'source': 'video_element'
            })

        url_extractor = MediaURLExtractor(url)
        found_urls.extend(url_extractor.extract_from_html(html))

        if not found_urls:
            raise ExtractionError("No media URLs found after rendering page")

        meta = MetadataExtractor(url).extract(html)
        formats = self._create_formats_from_urls(found_urls, url)

        return MediaInfo(
            id=self._generate_id(url),
            title=meta.get('title') or urlparse(url).netloc,
            url=url, formats=formats,
            media_type=MediaType.VIDEO,
            extractor=self.EXTRACTOR_NAME,
            description=meta.get('description'),
            thumbnail=meta.get('thumbnail'),
        )

    async def _setup_interception(self, page) -> None:
        async def handle_request(route, request):
            url = request.url
            if self._is_media_request(url, request.resource_type):
                self._intercepted_urls.append({
                    'url': url, 'type': request.resource_type,
                    'headers': dict(request.headers)
                })
            await route.continue_()
        await page.route('**/*', handle_request)

    def _is_media_request(self, url: str, resource_type: str) -> bool:
        if resource_type in ['media', 'video']:
            return True
        patterns = [r'\.m3u8', r'\.mpd', r'\.mp4', r'\.webm', r'/hls/', r'/dash/',
                     r'/video/', r'/stream/', r'manifest', r'playlist']
        return any(re.search(p, url.lower()) for p in patterns)

    async def _try_start_video(self, page) -> None:
        selectors = [
            '#clickfakeplayer', '#player-option-1', '[id^="player-option-"]',
            '.dooplay_player_option',
            'button.play', '.vjs-big-play-button', '.play-button',
            '#play', '[class*="play"]', 'video'
        ]
        clicked = 0
        for selector in selectors:
            try:
                elements = await page.query_selector_all(selector)
                for el in elements[:1]:
                    try:
                        await el.click(timeout=2500)
                    except Exception:
                        await page.evaluate('(node) => node.click()', el)
                    await page.wait_for_timeout(1200)
                    clicked += 1
                    break
                if clicked >= 2:
                    break
            except Exception:
                continue

    async def _get_video_sources(self, page) -> List[str]:
        try:
            return await page.evaluate('''() => {
                const srcs = [];
                document.querySelectorAll('video').forEach(v => {
                    if (v.src) srcs.push(v.src);
                    if (v.currentSrc) srcs.push(v.currentSrc);
                });
                document.querySelectorAll('video source').forEach(s => {
                    if (s.src) srcs.push(s.src);
                });
                return [...new Set(srcs)];
            }''')
        except Exception:
            return []

    def _process_intercepted_urls(self) -> List[Dict]:
        found = []
        seen = set()
        for item in self._intercepted_urls:
            url = item['url']
            if url in seen:
                continue
            seen.add(url)
            url_lower = url.lower()
            if any(ext in url_lower for ext in ['.m3u8', '.mpd', '.mp4', '.webm', '.ts']):
                found.append({
                    'url': url,
                    'type': self._detect_type_from_url(url),
                    'source': 'network_intercept',
                    'headers': item.get('headers', {})
                })
            elif any(kw in url_lower for kw in ['video', 'stream', 'media', 'hls', 'dash']):
                found.append({
                    'url': url,
                    'type': self._detect_type_from_url(url),
                    'source': 'network_intercept',
                    'headers': item.get('headers', {})
                })
        return found

    def _create_formats_from_urls(self, found_urls: List[Dict], page_url: str) -> List[StreamFormat]:
        formats = []
        seen = set()
        for idx, info in enumerate(found_urls):
            url = info['url']
            if url in seen or info.get('type') == 'thumbnail':
                continue
            seen.add(url)
            stream_type = self._detect_stream_type(url)
            headers = info.get('headers', {})
            headers['Referer'] = page_url
            ext = 'mp4'
            if stream_type == StreamType.HLS:
                ext = 'mp4'
            elif stream_type == StreamType.DASH:
                ext = 'mp4'
            formats.append(StreamFormat(
                format_id=f"adv-{idx}", url=url, ext=ext,
                stream_type=stream_type, headers=headers
            ))
        return formats

    def _detect_type_from_url(self, url: str) -> str:
        url_lower = url.lower()
        if '.m3u8' in url_lower:
            return 'hls'
        elif '.mpd' in url_lower:
            return 'dash'
        elif any(ext in url_lower for ext in ['.mp4', '.webm', '.mkv']):
            return 'direct'
        return 'unknown'
