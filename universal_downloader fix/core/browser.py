"""
browser.py — Playwright Browser Engine dengan:
- Full stealth mode
- Network request/response capture
- WebSocket monitoring
- Service Worker interception
- Auto-interaction (scroll, click, popup dismiss)
- DOM mutation observation
"""

import asyncio
import json
import time
import random
import re
import logging
from typing import (
    Any, Callable, Coroutine, Dict, List, 
    Optional, Set, Tuple
)
from dataclasses import dataclass, field
from urllib.parse import urlparse

try:
    from playwright.async_api import (
        async_playwright,
        Browser,
        BrowserContext,
        Page,
        Request,
        Response,
        Route,
        WebSocket,
        ConsoleMessage,
        Error as PlaywrightError,
    )
    HAS_PLAYWRIGHT = True
except ImportError:
    async_playwright = None
    Browser = BrowserContext = Page = Request = Response = Route = WebSocket = ConsoleMessage = object
    PlaywrightError = RuntimeError
    HAS_PLAYWRIGHT = False

try:
    from playwright_stealth import stealth_async
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
    
from config import DiagnosticConfig, DEFAULT_CONFIG
from utils.media_types import MediaTypes
from utils.url_utils import URLUtils

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
#  DATA CLASSES — Menyimpan hasil capture
# ══════════════════════════════════════════════

@dataclass
class CapturedRequest:
    """Satu network request yang tertangkap"""
    url: str
    method: str
    headers: Dict[str, str]
    post_data: Optional[str]
    resource_type: str          # document, script, image, media, xhr, fetch, etc
    timestamp: float
    
    # Response (diisi setelah response datang)
    status: Optional[int] = None
    response_headers: Dict[str, str] = field(default_factory=dict)
    response_body: Optional[str] = None     # text body (jika kecil)
    response_size: int = 0
    content_type: str = ""
    
    # Analysis flags
    is_media: bool = False
    media_type: str = ""         # image, video, audio, streaming, etc
    is_api: bool = False
    has_expiry: bool = False


@dataclass
class CapturedWebSocket:
    """WebSocket connection yang tertangkap"""
    url: str
    timestamp: float
    messages: List[Dict] = field(default_factory=list)  # {direction, data, time}
    is_closed: bool = False


@dataclass
class CapturedServiceWorker:
    """Service Worker yang terdeteksi"""
    url: str
    scope: str = ""
    status: str = ""


@dataclass 
class DOMChange:
    """Perubahan DOM yang terdeteksi"""
    tag: str
    attribute: str
    value: str
    timestamp: float
    change_type: str  # 'added', 'modified'


@dataclass
class BrowserCaptureResult:
    """Hasil lengkap dari browser capture session"""
    url: str
    final_url: str = ""                 # setelah redirect
    page_title: str = ""
    page_html: str = ""                 # full rendered HTML
    
    # Network
    requests: List[CapturedRequest] = field(default_factory=list)
    
    # WebSocket
    websockets: List[CapturedWebSocket] = field(default_factory=list)
    
    # Service Workers
    service_workers: List[CapturedServiceWorker] = field(default_factory=list)
    
    # DOM Changes (media-related)
    dom_changes: List[DOMChange] = field(default_factory=list)
    
    # Console logs (sering bocorkan URL)
    console_logs: List[Dict] = field(default_factory=list)
    
    # Cookies
    cookies: List[Dict] = field(default_factory=list)
    
    # Local/Session Storage
    local_storage: Dict = field(default_factory=dict)
    session_storage: Dict = field(default_factory=dict)
    
    # Screenshots
    screenshot_path: Optional[str] = None
    
    # Metadata
    load_time: float = 0.0
    total_requests: int = 0
    total_media_requests: int = 0
    errors: List[str] = field(default_factory=list)


# ══════════════════════════════════════════════
#  BROWSER ENGINE
# ══════════════════════════════════════════════

class BrowserEngine:
    """
    Playwright-based browser engine.
    Menangkap SEMUA aktivitas network, WebSocket, 
    Service Worker, DOM mutations, dan console logs.
    """
    
    def __init__(self, config: DiagnosticConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        
        # Capture storage
        self._capture = BrowserCaptureResult(url="")
        self._active_websockets: Dict[str, CapturedWebSocket] = {}
        self._request_map: Dict[str, CapturedRequest] = {}  # url → request
        self._seen_urls: Set[str] = set()
    
    # ──────────────────────────────────────────
    #  LIFECYCLE: Start / Stop
    # ──────────────────────────────────────────
    
    async def start(self):
        """Start browser engine"""
        if not HAS_PLAYWRIGHT or async_playwright is None:
            raise RuntimeError(
                "Playwright is required for diagnostic browser capture. "
                "Install it with `pip install playwright` and then run `playwright install chromium`."
            )
        self.playwright = await async_playwright().start()
        
        launch_args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-features=IsolateOrigins,site-per-process',
            '--disable-web-security',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-accelerated-2d-canvas',
            '--disable-gpu',
            '--disable-infobars',
        ]
        
        launch_kwargs = {
            'headless': self.config.browser.headless,
            'args': launch_args,
        }
        
        if self.config.proxy:
            launch_kwargs['proxy'] = {'server': self.config.proxy}
        
        self.browser = await self.playwright.chromium.launch(**launch_kwargs)
        logger.info("🌐 Browser engine started")
    
    async def stop(self):
        """Stop browser engine & cleanup"""
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            logger.info("🛑 Browser engine stopped")
        except Exception as e:
            logger.error(f"Error stopping browser: {e}")
    
    async def _create_context(self) -> BrowserContext:
        """Buat browser context baru dengan stealth settings"""
        bc = self.config.browser
        
        # Pilih random user agent
        user_agent = random.choice(bc.user_agents)
        
        context = await self.browser.new_context(
            viewport={'width': bc.viewport_width, 'height': bc.viewport_height},
            user_agent=user_agent,
            locale=bc.locale,
            timezone_id=bc.timezone,
            
            # Permissions
            permissions=['geolocation'],
            
            # Extra HTTP headers
            extra_http_headers={
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
            },
            
            # Service worker support
            service_workers='allow',
            
            # Bypass CSP (agar bisa inject script)
            bypass_csp=True,
        )
        
        # Override WebDriver detection
        await context.add_init_script("""
            // Override navigator.webdriver
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Override chrome runtime
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
            
            // Override permissions query
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // Override plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            
            // Override languages
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            
            // Override platform
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32'
            });
            
            // Override hardware concurrency  
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8
            });
            
            // Prevent detection via toString
            const originalFunction = Function.prototype.toString;
            Function.prototype.toString = function() {
                if (this === Function.prototype.toString) {
                    return 'function toString() { [native code] }';
                }
                return originalFunction.call(this);
            };
        """)
        
        return context
    
    # ──────────────────────────────────────────
    #  MAIN CAPTURE METHOD
    # ──────────────────────────────────────────
    
    async def capture(self, url: str) -> BrowserCaptureResult:
        """
        Navigasi ke URL dan capture SEMUA:
        - Network requests/responses
        - WebSocket connections
        - Service Workers
        - DOM mutations
        - Console logs
        - Cookies & Storage
        - Screenshot
        
        Return: BrowserCaptureResult
        """
        self._capture = BrowserCaptureResult(url=url)
        self._active_websockets = {}
        self._request_map = {}
        self._seen_urls = set()
        
        start_time = time.time()
        
        try:
            # Create context & page
            self.context = await self._create_context()
            self.page = await self.context.new_page()
            
            # Apply stealth
            if HAS_STEALTH and self.config.browser.stealth_mode:
                await stealth_async(self.page)
                logger.info("🥷 Stealth mode applied")
            
            # Set timeouts
            self.page.set_default_timeout(
                self.config.browser.timeout
            )
            self.page.set_default_navigation_timeout(
                self.config.browser.navigation_timeout
            )
            
            # ── Attach listeners SEBELUM navigasi ──
            await self._attach_network_listeners()
            await self._attach_websocket_listener()
            await self._attach_console_listener()
            await self._attach_service_worker_listener()
            
            # ── Navigate ──
            logger.info(f"🔄 Navigating to: {url}")
            response = await self.page.goto(
                url, 
                wait_until='domcontentloaded',
                timeout=self.config.browser.navigation_timeout
            )
            
            # Wait for network idle
            try:
                await self.page.wait_for_load_state(
                    'networkidle',
                    timeout=self.config.scan.max_network_wait * 1000
                )
            except PlaywrightError:
                logger.warning("⏱️ Network idle timeout — continuing")
            
            # ── Post-load captures ──
            self._capture.final_url = self.page.url
            self._capture.page_title = await self.page.title()
            
            # ── Auto interactions ──
            await self._auto_interact()
            
            # ── Inject DOM mutation observer ──
            await self._inject_dom_observer()
            
            # Wait sebentar untuk capture DOM changes & late requests
            await asyncio.sleep(3)
            
            # ── Collect DOM mutations ──
            await self._collect_dom_mutations()
            
            # ── Get rendered HTML ──
            self._capture.page_html = await self.page.content()
            
            # ── Cookies ──
            self._capture.cookies = await self.context.cookies()
            
            # ── Local & Session Storage ──
            await self._capture_storage()
            
            # ── Screenshot ──
            if self.config.report.take_screenshots:
                await self._take_screenshot()
            
            # ── Finalize ──
            self._capture.load_time = time.time() - start_time
            self._capture.total_requests = len(self._capture.requests)
            self._capture.total_media_requests = sum(
                1 for r in self._capture.requests if r.is_media
            )
            
            logger.info(
                f"✅ Capture complete: {self._capture.total_requests} requests, "
                f"{self._capture.total_media_requests} media, "
                f"{len(self._capture.websockets)} WebSockets, "
                f"{len(self._capture.dom_changes)} DOM changes, "
                f"{self._capture.load_time:.1f}s"
            )
            
        except Exception as e:
            error_msg = f"Capture error: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self._capture.errors.append(error_msg)
        
        finally:
            if self.context:
                await self.context.close()
                self.context = None
                self.page = None
        
        return self._capture
    
    # ──────────────────────────────────────────
    #  NETWORK LISTENERS
    # ──────────────────────────────────────────
    
    async def _attach_network_listeners(self):
        """Pasang listener untuk SEMUA network request & response"""
        
        self.page.on('request', self._on_request)
        self.page.on('response', self._on_response)
        self.page.on('requestfailed', self._on_request_failed)
    
    def _on_request(self, request: Request):
        """Handler ketika request dibuat"""
        url = request.url
        
        # Skip data: URLs
        if url.startswith('data:'):
            return
        
        captured = CapturedRequest(
            url=url,
            method=request.method,
            headers=dict(request.headers) if self.config.scan.capture_request_headers else {},
            post_data=request.post_data,
            resource_type=request.resource_type,
            timestamp=time.time(),
        )
        
        # Check if media
        media_type = MediaTypes.identify_type(url=url)
        if media_type != 'unknown':
            captured.is_media = True
            captured.media_type = media_type
        
        # Check if API call
        if request.resource_type in ('xhr', 'fetch'):
            captured.is_api = True
        
        # Store
        req_id = f"{url}_{id(request)}"
        self._request_map[req_id] = captured
        
        # Juga simpan by URL untuk lookup
        self._request_map[url] = captured
    
    async def _on_response(self, response: Response):
        """Handler ketika response diterima"""
        url = response.url
        
        if url.startswith('data:'):
            return
        
        # Cari matching request
        captured = self._request_map.get(url)
        if not captured:
            # Buat baru jika tidak ada
            captured = CapturedRequest(
                url=url,
                method='GET',
                headers={},
                post_data=None,
                resource_type='other',
                timestamp=time.time(),
            )
        
        # Fill response data
        captured.status = response.status
        captured.content_type = response.headers.get('content-type', '')
        
        if self.config.scan.capture_response_headers:
            captured.response_headers = dict(response.headers)
        
        # Detect media by content-type
        if not captured.is_media:
            media_type = MediaTypes.identify_type(mime=captured.content_type)
            if media_type != 'unknown':
                captured.is_media = True
                captured.media_type = media_type
        
        # Detect streaming by content-type
        ct = captured.content_type.lower()
        if any(s in ct for s in ['mpegurl', 'm3u8', 'dash+xml', 'mpd']):
            captured.is_media = True
            captured.media_type = 'streaming'
        
        # Capture response body untuk API calls & streaming manifests
        if self.config.scan.capture_response_body:
            should_capture_body = (
                captured.is_api or 
                captured.media_type == 'streaming' or
                'json' in ct or
                'xml' in ct or
                'mpegurl' in ct or
                'text/html' not in ct  # skip HTML pages
            )
            
            if should_capture_body:
                try:
                    body = await response.body()
                    if len(body) <= self.config.scan.max_response_body_size:
                        try:
                            captured.response_body = body.decode('utf-8', errors='replace')
                        except Exception:
                            captured.response_body = f"[binary: {len(body)} bytes]"
                    captured.response_size = len(body)
                except Exception:
                    pass
        
        # Detect URL expiry
        expiry = URLUtils.detect_url_expiry(url)
        captured.has_expiry = expiry['has_expiry']
        
        # Add to results (avoid duplicates)
        if url not in self._seen_urls:
            self._seen_urls.add(url)
            self._capture.requests.append(captured)
    
    def _on_request_failed(self, request: Request):
        """Handler ketika request gagal"""
        logger.debug(f"Request failed: {request.url} - {request.failure}")
    
    # ──────────────────────────────────────────
    #  WEBSOCKET LISTENER
    # ──────────────────────────────────────────
    
    async def _attach_websocket_listener(self):
        """Monitor semua WebSocket connections"""
        
        def on_websocket(ws: WebSocket):
            ws_url = ws.url
            logger.info(f"🔌 WebSocket opened: {ws_url}")
            
            captured_ws = CapturedWebSocket(
                url=ws_url,
                timestamp=time.time()
            )
            self._active_websockets[ws_url] = captured_ws
            self._capture.websockets.append(captured_ws)
            
            def on_frame_sent(payload):
                captured_ws.messages.append({
                    'direction': 'sent',
                    'data': payload[:5000],  # limit size
                    'time': time.time()
                })
            
            def on_frame_received(payload):
                captured_ws.messages.append({
                    'direction': 'received',
                    'data': payload[:5000],
                    'time': time.time()
                })
            
            def on_close():
                captured_ws.is_closed = True
                logger.info(f"🔌 WebSocket closed: {ws_url}")
            
            ws.on('framesent', on_frame_sent)
            ws.on('framereceived', on_frame_received)
            ws.on('close', on_close)
        
        self.page.on('websocket', on_websocket)
    
    # ──────────────────────────────────────────
    #  CONSOLE LISTENER
    # ──────────────────────────────────────────
    
    async def _attach_console_listener(self):
        """Capture console.log (sering mengandung URL media)"""
        
        def on_console(msg: ConsoleMessage):
            text = msg.text
            if len(text) > 10:  # skip noise
                self._capture.console_logs.append({
                    'type': msg.type,
                    'text': text[:2000],
                    'time': time.time()
                })
        
        self.page.on('console', on_console)
    
    # ──────────────────────────────────────────
    #  SERVICE WORKER LISTENER
    # ──────────────────────────────────────────
    
    async def _attach_service_worker_listener(self):
        """Detect & monitor Service Workers"""
        
        def on_service_worker(worker):
            sw = CapturedServiceWorker(
                url=worker.url,
            )
            self._capture.service_workers.append(sw)
            logger.info(f"⚙️ Service Worker detected: {worker.url}")
        
        self.context.on('serviceworker', on_service_worker)
    
    # ──────────────────────────────────────────
    #  DOM MUTATION OBSERVER
    # ──────────────────────────────────────────
    
    async def _inject_dom_observer(self):
        """Inject MutationObserver ke halaman"""
        await self.page.evaluate("""
            () => {
                window.__mediadiag_mutations = [];
                
                const observer = new MutationObserver((mutations) => {
                    for (const mutation of mutations) {
                        // New nodes added
                        if (mutation.type === 'childList') {
                            for (const node of mutation.addedNodes) {
                                if (node.nodeType === Node.ELEMENT_NODE) {
                                    // Check media elements
                                    const mediaEls = [
                                        ...Array.from(node.querySelectorAll ? 
                                            node.querySelectorAll('img, video, audio, source, iframe, embed, object') : []),
                                    ];
                                    if (['IMG','VIDEO','AUDIO','SOURCE','IFRAME','EMBED','OBJECT'].includes(node.tagName)) {
                                        mediaEls.push(node);
                                    }
                                    
                                    for (const el of mediaEls) {
                                        const src = el.src || el.getAttribute('data-src') || 
                                                    el.getAttribute('data-original') || '';
                                        if (src && !src.startsWith('data:')) {
                                            window.__mediadiag_mutations.push({
                                                tag: el.tagName,
                                                attribute: 'src',
                                                value: src,
                                                timestamp: Date.now(),
                                                change_type: 'added'
                                            });
                                        }
                                    }
                                }
                            }
                        }
                        
                        // Attribute changes on media elements
                        if (mutation.type === 'attributes') {
                            const target = mutation.target;
                            const attr = mutation.attributeName;
                            if (['src', 'data-src', 'href', 'poster', 'data-original'].includes(attr)) {
                                const value = target.getAttribute(attr) || '';
                                if (value && !value.startsWith('data:')) {
                                    window.__mediadiag_mutations.push({
                                        tag: target.tagName,
                                        attribute: attr,
                                        value: value,
                                        timestamp: Date.now(),
                                        change_type: 'modified'
                                    });
                                }
                            }
                        }
                    }
                });
                
                observer.observe(document.documentElement, {
                    childList: true,
                    subtree: true,
                    attributes: true,
                    attributeFilter: ['src', 'data-src', 'href', 'poster', 
                                      'data-original', 'data-lazy-src', 'srcset']
                });
            }
        """)
    
    async def _collect_dom_mutations(self):
        """Collect mutations yang tercapture oleh observer"""
        try:
            mutations = await self.page.evaluate("""
                () => window.__mediadiag_mutations || []
            """)
            
            for m in mutations:
                self._capture.dom_changes.append(DOMChange(
                    tag=m.get('tag', ''),
                    attribute=m.get('attribute', ''),
                    value=m.get('value', ''),
                    timestamp=m.get('timestamp', 0),
                    change_type=m.get('change_type', '')
                ))
        except Exception as e:
            logger.debug(f"DOM mutation collection error: {e}")
    
    # ──────────────────────────────────────────
    #  AUTO INTERACTION
    # ──────────────────────────────────────────
    
    async def _auto_interact(self):
        """Interaksi otomatis dengan halaman"""
        sc = self.config.scan
        
        # 1. Cookie consent — auto accept
        if sc.auto_accept_cookies:
            await self._dismiss_cookie_banners()
        
        # 2. Popup/modal — auto close
        if sc.auto_close_popups:
            await self._close_popups()
        
        # 3. Play button — auto click
        if sc.auto_click_play:
            await self._click_play_buttons()
        
        # 4. Scroll — trigger lazy loading
        if sc.auto_scroll:
            await self._auto_scroll()
        
        # 5. Tabs/Accordions — reveal hidden content
        if sc.auto_click_tabs:
            await self._click_tabs_accordions()
    
    async def _dismiss_cookie_banners(self):
        """Auto-accept cookie consent banners"""
        selectors = [
            # Common cookie consent buttons
            'button[id*="cookie" i][id*="accept" i]',
            'button[class*="cookie" i][class*="accept" i]',
            'button[id*="consent" i][id*="accept" i]',
            'a[id*="cookie" i][id*="accept" i]',
            '[data-testid*="cookie" i] button',
            '.cookie-banner button',
            '.cookie-consent button',
            '#cookie-notice button',
            '.cc-btn.cc-dismiss',
            '#onetrust-accept-btn-handler',
            '.js-cookie-accept',
            'button:has-text("Accept")',
            'button:has-text("Accept All")',
            'button:has-text("Accept Cookies")',
            'button:has-text("I Agree")',
            'button:has-text("Got it")',
            'button:has-text("OK")',
            'button:has-text("Agree")',
            'button:has-text("Setuju")',          # Indonesian
            'button:has-text("Terima")',           # Indonesian
        ]
        
        for selector in selectors:
            try:
                el = self.page.locator(selector).first
                if await el.is_visible(timeout=500):
                    await el.click(timeout=1000)
                    logger.info(f"🍪 Cookie banner dismissed: {selector}")
                    await asyncio.sleep(0.5)
                    return
            except Exception:
                continue
    
    async def _close_popups(self):
        """Auto-close popups dan modals"""
        close_selectors = [
            '.modal .close', '.modal .btn-close',
            '[class*="popup"] [class*="close"]',
            '[class*="modal"] [class*="close"]',
            '[class*="overlay"] [class*="close"]',
            '.popup-close', '.modal-close',
            'button[aria-label="Close"]',
            'button[aria-label="close"]',
            '.close-button', '.btn-close',
            '[data-dismiss="modal"]',
            '.fancybox-close', '.lightbox-close',
        ]
        
        for selector in close_selectors:
            try:
                el = self.page.locator(selector).first
                if await el.is_visible(timeout=300):
                    await el.click(timeout=500)
                    logger.info(f"❌ Popup closed: {selector}")
                    await asyncio.sleep(0.3)
            except Exception:
                continue
    
    async def _click_play_buttons(self):
        """Click video/audio play buttons"""
        play_selectors = [
            'button[class*="play" i]',
            'div[class*="play" i]',
            '[class*="play-button"]',
            '[class*="play-btn"]',
            '[aria-label*="play" i]',
            '[data-plyr="play"]',
            '.vjs-big-play-button',
            '.jw-icon-display',
            '.ytp-large-play-button',
            'video',  # clicking video element often triggers play
        ]
        
        for selector in play_selectors:
            try:
                el = self.page.locator(selector).first
                if await el.is_visible(timeout=500):
                    await el.click(timeout=1000)
                    logger.info(f"▶️ Play button clicked: {selector}")
                    # Wait for media requests
                    await asyncio.sleep(3)
                    return
            except Exception:
                continue
    
    async def _auto_scroll(self):
        """Scroll halaman untuk trigger lazy loading"""
        max_scrolls = self.config.scan.max_scrolls
        delay = self.config.scan.scroll_delay
        
        logger.info(f"📜 Auto-scrolling ({max_scrolls} scrolls)")
        
        for i in range(max_scrolls):
            previous_height = await self.page.evaluate(
                'document.body.scrollHeight'
            )
            
            # Smooth scroll
            await self.page.evaluate("""
                () => {
                    window.scrollBy({
                        top: window.innerHeight * 0.8,
                        behavior: 'smooth'
                    });
                }
            """)
            
            await asyncio.sleep(delay)
            
            # Check if reached bottom
            new_height = await self.page.evaluate(
                'document.body.scrollHeight'
            )
            scroll_pos = await self.page.evaluate(
                'window.scrollY + window.innerHeight'
            )
            
            if scroll_pos >= new_height - 100:
                if new_height == previous_height:
                    logger.info(
                        f"📜 Reached bottom after {i+1} scrolls"
                    )
                    break
        
        # Scroll back to top
        await self.page.evaluate('window.scrollTo(0, 0)')
        await asyncio.sleep(1)
    
    async def _click_tabs_accordions(self):
        """Click tabs and accordions to reveal hidden content"""
        tab_selectors = [
            '[role="tab"]',
            '.tab', '.nav-tab', '.nav-link',
            '[data-toggle="tab"]',
            '[data-bs-toggle="tab"]',
            '.accordion-header', '.accordion-button',
            '[data-toggle="collapse"]',
            '[data-bs-toggle="collapse"]',
            'details > summary',
        ]
        
        for selector in tab_selectors:
            try:
                tabs = self.page.locator(selector)
                count = await tabs.count()
                for i in range(min(count, 10)):  # max 10 tabs
                    try:
                        tab = tabs.nth(i)
                        if await tab.is_visible(timeout=300):
                            await tab.click(timeout=500)
                            await asyncio.sleep(0.5)
                    except Exception:
                        continue
            except Exception:
                continue
    
    # ──────────────────────────────────────────
    #  STORAGE & SCREENSHOT
    # ──────────────────────────────────────────
    
    async def _capture_storage(self):
        """Capture Local Storage dan Session Storage"""
        try:
            self._capture.local_storage = await self.page.evaluate("""
                () => {
                    const items = {};
                    for (let i = 0; i < localStorage.length; i++) {
                        const key = localStorage.key(i);
                        items[key] = localStorage.getItem(key);
                    }
                    return items;
                }
            """)
        except Exception:
            pass
        
        try:
            self._capture.session_storage = await self.page.evaluate("""
                () => {
                    const items = {};
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const key = sessionStorage.key(i);
                        items[key] = sessionStorage.getItem(key);
                    }
                    return items;
                }
            """)
        except Exception:
            pass
    
    async def _take_screenshot(self):
        """Take full page screenshot"""
        try:
            import os
            self.config.ensure_output_dir()
            
            timestamp = int(time.time())
            filename = f"screenshot_{timestamp}.png"
            filepath = os.path.join(
                self.config.report.output_dir, filename
            )
            
            await self.page.screenshot(
                path=filepath, 
                full_page=True,
                type=self.config.report.screenshot_format
            )
            self._capture.screenshot_path = filepath
            logger.info(f"📸 Screenshot saved: {filepath}")
        except Exception as e:
            logger.error(f"Screenshot error: {e}")
    
    # ──────────────────────────────────────────
    #  UTILITY METHODS
    # ──────────────────────────────────────────
    
    def get_media_requests(self) -> List[CapturedRequest]:
        """Filter hanya request yang media"""
        return [r for r in self._capture.requests if r.is_media]
    
    def get_api_requests(self) -> List[CapturedRequest]:
        """Filter hanya API/XHR/Fetch requests"""
        return [r for r in self._capture.requests if r.is_api]
    
    def get_streaming_requests(self) -> List[CapturedRequest]:
        """Filter hanya streaming requests (m3u8, mpd, ts, m4s)"""
        return [
            r for r in self._capture.requests 
            if r.media_type == 'streaming'
        ]
    
    async def random_delay(self):
        """Random delay untuk stealth"""
        delay = random.uniform(
            self.config.browser.random_delay_min,
            self.config.browser.random_delay_max
        )
        await asyncio.sleep(delay)
