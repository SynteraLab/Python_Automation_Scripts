"""
server_switcher.py — Browser-based server switching engine.

Buka halaman target, deteksi server selector buttons,
klik SETIAP server satu per satu, dan capture network 
traffic + iframe changes per server.

Return: mapping server_name → [media_urls]
"""

import asyncio
import time
import re
import json
import logging
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from urllib.parse import urlparse

try:
    from playwright.async_api import (
        async_playwright, Browser, BrowserContext,
        Page, Response, Error as PlaywrightError,
    )
    HAS_PLAYWRIGHT = True
except ImportError:
    async_playwright = None
    Browser = BrowserContext = Page = Response = object
    PlaywrightError = RuntimeError
    HAS_PLAYWRIGHT = False

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

from config import DiagnosticConfig, DEFAULT_CONFIG
from utils.embed_services import (
    EmbedServiceDetector, SERVER_SELECTOR_CSS,
    SERVER_DATA_ATTRIBUTES
)
from utils.media_types import MediaTypes
from utils.url_utils import URLUtils

logger = logging.getLogger(__name__)


@dataclass
class ServerSource:
    """Satu server/source yang ditemukan"""
    server_name: str
    server_index: int
    server_id: str = ""                # dari data-attribute
    is_active: bool = False            # apakah default/active

    # Media yang ditemukan dari server ini
    iframe_url: str = ""
    embed_service: str = ""            # nama embed service (Vidcloud, dll)
    media_urls: List[Dict] = field(default_factory=list)
    api_calls: List[Dict] = field(default_factory=list)
    streaming_urls: List[str] = field(default_factory=list)

    # Metadata
    data_attributes: Dict[str, str] = field(default_factory=dict)
    quality: str = ""
    language: str = ""
    sub_or_dub: str = ""               # 'sub', 'dub', ''


@dataclass
class ServerSwitchResult:
    """Hasil dari server switching session"""
    url: str
    total_servers_detected: int = 0
    total_servers_probed: int = 0
    servers: List[ServerSource] = field(default_factory=list)
    selector_method: str = ""          # css, data-attr, dropdown, etc
    errors: List[str] = field(default_factory=list)


class ServerSwitcher:
    """
    Engine untuk mendeteksi dan switch antar server.
    Membuka browser, klik setiap server tab, capture hasilnya.
    """

    def __init__(self, config: DiagnosticConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.playwright = None
        self.browser: Optional[Browser] = None

    async def start(self):
        if not HAS_PLAYWRIGHT or async_playwright is None:
            raise RuntimeError(
                "Playwright is required for server switching diagnostics. "
                "Install it with `pip install playwright` and then run `playwright install chromium`."
            )
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.config.browser.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
            ]
        )
        logger.info("🔀 ServerSwitcher browser started")

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("🔀 ServerSwitcher browser stopped")

    async def detect_and_switch(self, url: str) -> ServerSwitchResult:
        """
        Main method:
        1. Buka halaman
        2. Detect server selectors
        3. Untuk setiap server: klik, capture, record
        4. Return mapping lengkap
        """
        result = ServerSwitchResult(url=url)
        import random

        context = await self.browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            user_agent=random.choice(self.config.browser.user_agents),
            bypass_csp=True,
            service_workers='allow',
        )

        # Stealth
        page = await context.new_page()
        if HAS_STEALTH and self.config.browser.stealth_mode:
            await stealth_async(page)

        try:
            # Navigate
            logger.info(f"  🔀 Navigating to: {url}")
            await page.goto(url, wait_until='domcontentloaded', timeout=30000)

            try:
                await page.wait_for_load_state('networkidle', timeout=10000)
            except PlaywrightError:
                pass

            # Dismiss popups/cookies
            await self._dismiss_overlays(page)

            # ── Step 1: Detect server selectors ──
            servers_found = await self._detect_servers(page, url)
            result.total_servers_detected = len(servers_found)

            if not servers_found:
                logger.info("  🔀 No server selectors detected")

                # Fallback: check if there are iframes with known embed services
                iframes = await self._get_current_iframes(page, url)
                if iframes:
                    fallback = ServerSource(
                        server_name="Default",
                        server_index=0,
                        is_active=True,
                    )
                    for iframe_info in iframes:
                        fallback.iframe_url = iframe_info['url']
                        fallback.embed_service = iframe_info.get('service', 'unknown')
                    result.servers.append(fallback)
                    result.total_servers_detected = 1

                return result

            logger.info(f"  🔀 Found {len(servers_found)} server selectors")

            # ── Step 2: Click each server and capture ──
            for i, server_info in enumerate(servers_found):
                logger.info(
                    f"  🔀 Probing server [{i+1}/{len(servers_found)}]: "
                    f"{server_info['name']}"
                )

                server_source = await self._probe_server(
                    page, server_info, url, i
                )
                result.servers.append(server_source)
                result.total_servers_probed += 1

                # Small delay between servers
                await asyncio.sleep(1.5)

        except Exception as e:
            error_msg = f"ServerSwitcher error: {str(e)}"
            logger.error(f"  ❌ {error_msg}")
            result.errors.append(error_msg)

        finally:
            await context.close()

        return result

    # ──────────────────────────────────────────
    #  SERVER DETECTION
    # ──────────────────────────────────────────

    async def _detect_servers(
        self, page: Page, base_url: str
    ) -> List[Dict]:
        """
        Detect server selector elements pada halaman.
        Return list of {selector, name, data_attrs, is_active, element_handle}
        """
        servers = []
        seen_names = set()

        for css in SERVER_SELECTOR_CSS:
            try:
                elements = page.locator(css)
                count = await elements.count()

                if count < 2:
                    # Perlu minimal 2 element untuk dianggap server list
                    continue

                if count > 30:
                    # Terlalu banyak, mungkin bukan server list
                    continue

                for idx in range(count):
                    el = elements.nth(idx)

                    try:
                        is_visible = await el.is_visible(timeout=500)
                        if not is_visible:
                            continue
                    except Exception:
                        continue

                    # Get text & attributes
                    text = (await el.text_content() or '').strip()
                    if not text or len(text) > 100:
                        continue

                    # Get data attributes
                    data_attrs = {}
                    for attr in SERVER_DATA_ATTRIBUTES:
                        try:
                            val = await el.get_attribute(attr)
                            if val:
                                data_attrs[attr] = val
                        except Exception:
                            pass

                    # Check if active/selected
                    class_attr = await el.get_attribute('class') or ''
                    is_active = any(
                        cls in class_attr.lower()
                        for cls in ['active', 'selected', 'current', 'chosen']
                    )

                    # Deduplicate by name
                    name_key = text.lower().strip()
                    if name_key in seen_names:
                        continue
                    seen_names.add(name_key)

                    servers.append({
                        'css': css,
                        'index': idx,
                        'name': text,
                        'data_attrs': data_attrs,
                        'is_active': is_active,
                        'class': class_attr,
                    })

                if servers:
                    # Berhasil menemukan dengan selector ini
                    logger.info(
                        f"    ✓ Server selector: {css} ({len(servers)} servers)"
                    )
                    break

            except Exception:
                continue

        # ── Fallback: detect from JavaScript ──
        if not servers:
            servers = await self._detect_servers_from_js(page, base_url)

        return servers

    async def _detect_servers_from_js(
        self, page: Page, base_url: str
    ) -> List[Dict]:
        """Fallback: detect server info dari JavaScript variables"""
        try:
            js_servers = await page.evaluate("""
                () => {
                    const results = [];
                    
                    // Check for common server data in window/global
                    const searchKeys = [
                        'servers', 'sources', 'serverList', 'server_list',
                        'embedServers', 'embed_servers', 'videoServers',
                        'playerSources', 'player_sources', 'episodes_servers',
                    ];
                    
                    for (const key of searchKeys) {
                        const val = window[key] || 
                                    (window.__NEXT_DATA__?.props?.pageProps?.[key]);
                        if (val && Array.isArray(val)) {
                            val.forEach((item, i) => {
                                const name = item.name || item.title || 
                                             item.server_name || item.label || 
                                             `Server ${i+1}`;
                                results.push({
                                    name: name,
                                    data_attrs: {
                                        'data-id': String(item.id || item.server_id || i),
                                        'data-embed': item.embed || item.url || item.src || '',
                                    },
                                    is_active: i === 0,
                                    source: 'js_variable',
                                    css: '',
                                    index: i,
                                    class: '',
                                });
                            });
                            break;
                        }
                    }
                    
                    // Check onclick handlers
                    document.querySelectorAll('[onclick*="server"], [onclick*="source"], [onclick*="embed"]')
                        .forEach((el, i) => {
                            const text = el.textContent?.trim();
                            if (text && text.length < 50) {
                                results.push({
                                    name: text,
                                    data_attrs: {},
                                    is_active: false,
                                    source: 'onclick',
                                    css: `[onclick*="server"]:nth-of-type(${i+1})`,
                                    index: i,
                                    class: el.className || '',
                                });
                            }
                        });
                    
                    return results;
                }
            """)

            return js_servers or []

        except Exception as e:
            logger.debug(f"JS server detection failed: {e}")
            return []

    # ──────────────────────────────────────────
    #  SERVER PROBING
    # ──────────────────────────────────────────

    async def _probe_server(
        self, page: Page, server_info: Dict,
        base_url: str, index: int
    ) -> ServerSource:
        """
        Click satu server dan capture semua yang berubah:
        - Network requests baru
        - Iframe src changes
        - Player updates
        """
        server = ServerSource(
            server_name=server_info['name'],
            server_index=index,
            server_id=server_info.get('data_attrs', {}).get('data-id', ''),
            is_active=server_info.get('is_active', False),
            data_attributes=server_info.get('data_attrs', {}),
        )

        # Parse quality/language from name
        name_lower = server_info['name'].lower()
        if any(q in name_lower for q in ['1080', 'fhd', 'full hd']):
            server.quality = '1080p'
        elif any(q in name_lower for q in ['720', 'hd']):
            server.quality = '720p'
        elif any(q in name_lower for q in ['480', 'sd']):
            server.quality = '480p'
        elif any(q in name_lower for q in ['360']):
            server.quality = '360p'

        if 'sub' in name_lower:
            server.sub_or_dub = 'sub'
        elif 'dub' in name_lower:
            server.sub_or_dub = 'dub'

        # ── Capture: record iframe BEFORE click ──
        iframes_before = await self._get_current_iframes(page, base_url)

        # ── Setup per-server network capture ──
        captured_requests: List[Dict] = []
        captured_responses: List[Dict] = []

        async def on_response(response: Response):
            try:
                url = response.url
                ct = response.headers.get('content-type', '')
                status = response.status

                req_info = {
                    'url': url,
                    'status': status,
                    'content_type': ct,
                    'method': response.request.method,
                }

                # Capture response body for API/JSON/streaming
                if ('json' in ct or 'mpegurl' in ct or 'dash' in ct or
                        'xml' in ct or 'text/plain' in ct):
                    try:
                        body = await response.body()
                        if len(body) < 500000:
                            req_info['body'] = body.decode('utf-8', errors='replace')
                    except Exception:
                        pass

                captured_responses.append(req_info)
            except Exception:
                pass

        page.on('response', on_response)

        try:
            # ── Click the server ──
            css = server_info.get('css', '')
            idx = server_info.get('index', 0)

            if css:
                try:
                    el = page.locator(css).nth(idx)
                    await el.click(timeout=3000)
                    logger.info(f"    ✓ Clicked: {server_info['name']}")
                except Exception as e:
                    logger.warning(f"    ⚠ Click failed ({css}): {e}")

                    # Fallback: try by text
                    try:
                        el = page.get_by_text(
                            server_info['name'], exact=False
                        ).first
                        await el.click(timeout=2000)
                    except Exception:
                        server.server_name += " (click failed)"
                        return server

            # ── Wait for changes ──
            await asyncio.sleep(3)

            # Wait for network to settle
            try:
                await page.wait_for_load_state('networkidle', timeout=8000)
            except PlaywrightError:
                pass

            # ── Capture iframe AFTER click ──
            iframes_after = await self._get_current_iframes(page, base_url)

            # Detect new/changed iframes
            before_urls = {f['url'] for f in iframes_before}
            for iframe_info in iframes_after:
                if iframe_info['url'] not in before_urls:
                    server.iframe_url = iframe_info['url']
                    server.embed_service = iframe_info.get('service', 'unknown')
                    logger.info(
                        f"    ✓ New iframe: {server.embed_service} → "
                        f"{iframe_info['url'][:80]}"
                    )

            # If no new iframe but there's one, use current
            if not server.iframe_url and iframes_after:
                current = iframes_after[0]
                server.iframe_url = current['url']
                server.embed_service = current.get('service', 'unknown')

            # ── Analyze captured network traffic ──
            for resp in captured_responses:
                url = resp['url']
                ct = resp.get('content_type', '')

                # Check for media
                media_type = MediaTypes.identify_type(url=url, mime=ct)
                is_streaming = MediaTypes.is_streaming_url(url)

                if media_type != 'unknown' or is_streaming:
                    server.media_urls.append({
                        'url': url,
                        'type': 'streaming' if is_streaming else media_type,
                        'content_type': ct,
                        'status': resp['status'],
                    })

                    if is_streaming:
                        server.streaming_urls.append(url)

                # Check for API calls
                if ('json' in ct and resp.get('body')):
                    server.api_calls.append({
                        'url': url,
                        'body_preview': resp['body'][:2000],
                    })

                    # Extract media URLs from JSON
                    try:
                        json_data = json.loads(resp['body'])
                        self._extract_media_from_json(
                            json_data, server, base_url
                        )
                    except Exception:
                        pass

            # ── Check data-embed attribute for direct URL ──
            embed_url = server.data_attributes.get('data-embed', '')
            if embed_url and not server.iframe_url:
                server.iframe_url = URLUtils.normalize(embed_url, base_url)
                sig = EmbedServiceDetector.identify_service(server.iframe_url)
                if sig:
                    server.embed_service = sig.display_name

        except Exception as e:
            logger.warning(f"    ⚠ Probe error: {e}")

        finally:
            page.remove_listener('response', on_response)

        # Log summary
        media_count = len(server.media_urls)
        stream_count = len(server.streaming_urls)
        api_count = len(server.api_calls)
        logger.info(
            f"    📊 {server.server_name}: "
            f"embed={server.embed_service}, "
            f"media={media_count}, streaming={stream_count}, "
            f"apis={api_count}"
        )

        return server

    # ──────────────────────────────────────────
    #  HELPERS
    # ──────────────────────────────────────────

    async def _get_current_iframes(
        self, page: Page, base_url: str
    ) -> List[Dict]:
        """Get all current iframe sources"""
        try:
            iframes = await page.evaluate("""
                () => {
                    return Array.from(document.querySelectorAll('iframe'))
                        .filter(f => f.src && !f.src.startsWith('about:'))
                        .map(f => ({
                            url: f.src,
                            width: f.width || f.offsetWidth,
                            height: f.height || f.offsetHeight,
                        }));
                }
            """)

            for iframe in iframes:
                iframe['url'] = URLUtils.normalize(iframe['url'], base_url)
                sig = EmbedServiceDetector.identify_service(iframe['url'])
                iframe['service'] = sig.display_name if sig else 'unknown'

            return iframes

        except Exception:
            return []

    def _extract_media_from_json(
        self, data, server: ServerSource, base_url: str, depth=0
    ):
        """Recursively extract media URLs from JSON"""
        if depth > 10:
            return

        if isinstance(data, str):
            url = URLUtils.normalize(data, base_url)
            if url and MediaTypes.is_media_url(url):
                server.media_urls.append({
                    'url': url,
                    'type': MediaTypes.identify_type(url=url),
                    'source': 'api_json',
                })
            if url and MediaTypes.is_streaming_url(url):
                server.streaming_urls.append(url)

        elif isinstance(data, dict):
            # Known keys
            media_keys = [
                'file', 'url', 'src', 'source', 'link', 'stream',
                'video_url', 'video', 'hls', 'dash', 'mp4',
                'sources', 'tracks', 'playlist',
            ]
            for key, value in data.items():
                if key.lower() in media_keys and isinstance(value, str):
                    url = URLUtils.normalize(value, base_url)
                    if url:
                        server.media_urls.append({
                            'url': url,
                            'type': MediaTypes.identify_type(url=url) or 'video',
                            'json_key': key,
                            'source': 'api_json',
                        })
                        if MediaTypes.is_streaming_url(url):
                            server.streaming_urls.append(url)
                else:
                    self._extract_media_from_json(
                        value, server, base_url, depth + 1
                    )

        elif isinstance(data, list):
            for item in data[:50]:
                self._extract_media_from_json(
                    item, server, base_url, depth + 1
                )

    async def _dismiss_overlays(self, page: Page):
        """Dismiss popups/cookie banners"""
        dismiss_selectors = [
            'button:has-text("Accept")',
            'button:has-text("OK")',
            'button:has-text("Got it")',
            '.close-popup', '.popup-close',
            '[data-dismiss="modal"]',
            '.cookie-accept',
        ]
        for sel in dismiss_selectors:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=300):
                    await el.click(timeout=500)
                    await asyncio.sleep(0.3)
            except Exception:
                continue
