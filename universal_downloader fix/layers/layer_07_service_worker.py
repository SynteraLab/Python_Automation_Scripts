"""
layer_07_service_worker.py — Service Worker Analysis

- Detect registered Service Workers
- Download & analyze SW scripts
- Detect cache strategies (media caching)
- Detect request interception / URL rewriting
- Detect push notification endpoints
- Detect offline media capabilities
"""

import re
import logging
from typing import Dict, List, Set

from layers.base import BaseLayer
from core.browser import BrowserCaptureResult, CapturedServiceWorker
from core.session import SessionManager
from utils.media_types import MediaTypes
from utils.url_utils import URLUtils
from utils.pattern_matcher import PatternMatcher

logger = logging.getLogger(__name__)


class ServiceWorkerAnalysisLayer(BaseLayer):

    LAYER_NAME = "layer_07_service_worker"
    LAYER_DESCRIPTION = "Service Worker Interception Analysis"

    async def execute(self, url, recon, capture, session):
        seen: Set[str] = set()
        base_url = url

        if not capture:
            self.add_error("No browser capture data")
            return

        # ── 1. Analyze detected Service Workers ──
        self._analyze_detected_sw(capture, base_url)

        # ── 2. Download & analyze SW script content ──
        for sw in capture.service_workers:
            self._analyze_sw_script(sw, session, base_url, seen)

        # ── 3. Detect SW registration in HTML/JS ──
        self._detect_sw_registration(capture, base_url, seen)

        # ── 4. Detect SW impact on network ──
        self._detect_sw_network_impact(capture, base_url)

    def _analyze_detected_sw(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Report detected Service Workers"""
        for sw in capture.service_workers:
            self.add_finding(
                category='info',
                subcategory='service_worker',
                url=sw.url,
                data={
                    'scope': sw.scope,
                    'status': sw.status,
                    'is_same_origin': URLUtils.is_same_domain(sw.url, base_url),
                },
                confidence=1.0,
                source='Service Worker detection',
            )

    def _analyze_sw_script(
        self, sw: CapturedServiceWorker,
        session: SessionManager, base_url: str, seen: Set[str]
    ):
        """Download dan analyze Service Worker script"""
        if not sw.url:
            return

        resp = session.get(sw.url, timeout=10)
        if not resp or not resp.body:
            return

        content = resp.body
        self._result.raw_data[f'sw_content_{sw.url}'] = content

        # ── Detect cache strategies ──
        cache_strategies = {
            'cache_first': [
                'caches.match', 'cache.match',
                'CacheFirst', 'cacheFirst'
            ],
            'network_first': [
                'NetworkFirst', 'networkFirst',
                'fetch.*catch.*cache'
            ],
            'stale_while_revalidate': [
                'StaleWhileRevalidate', 'staleWhileRevalidate'
            ],
            'cache_only': ['CacheOnly', 'cacheOnly'],
            'network_only': ['NetworkOnly', 'networkOnly'],
        }

        detected_strategies = []
        for strategy_name, indicators in cache_strategies.items():
            for indicator in indicators:
                if indicator.lower() in content.lower():
                    detected_strategies.append(strategy_name)
                    break

        # ── Detect fetch event interception ──
        has_fetch_handler = bool(re.search(
            r'''addEventListener\s*\(\s*['"]fetch['"]''',
            content, re.IGNORECASE
        ))

        # ── Detect URL rewriting ──
        url_rewrite_patterns = [
            r'new\s+Request\s*\(',
            r'respondWith\s*\(',
            r'\.url\s*=',
            r'url\.replace\s*\(',
            r'new\s+URL\s*\(',
        ]
        has_url_rewriting = any(
            re.search(p, content) for p in url_rewrite_patterns
        )

        # ── Detect precached URLs ──
        precache_urls = []
        precache_pattern = re.compile(
            r'''['"]([^'"]+\.(?:mp4|mp3|m3u8|jpg|png|gif|webp|m4a|wav|ogg|pdf))['"]\s*''',
            re.IGNORECASE
        )
        for match in precache_pattern.finditer(content):
            precache_url = URLUtils.normalize(match.group(1), base_url)
            if precache_url and precache_url not in seen:
                seen.add(precache_url)
                precache_urls.append(precache_url)
                self.add_finding(
                    category='media',
                    subcategory=MediaTypes.identify_type(url=precache_url),
                    url=precache_url,
                    data={
                        'service_worker': sw.url,
                        'caching': 'precache',
                    },
                    confidence=0.8,
                    source='Service Worker precache list',
                )

        # ── Detect workbox ──
        has_workbox = 'workbox' in content.lower()

        # ── Media URL patterns in SW ──
        media_matches = PatternMatcher.find_media_urls_only(content, base_url)
        for match in media_matches:
            if match.url not in seen:
                seen.add(match.url)
                self.add_finding(
                    category='media',
                    subcategory=MediaTypes.identify_type(url=match.url),
                    url=match.url,
                    data={'service_worker': sw.url},
                    confidence=match.confidence * 0.8,
                    source=f'Service Worker script [{match.pattern_name}]',
                )

        # ── Report SW analysis ──
        self.add_finding(
            category='info',
            subcategory='sw_analysis',
            url=sw.url,
            data={
                'cache_strategies': detected_strategies,
                'has_fetch_handler': has_fetch_handler,
                'has_url_rewriting': has_url_rewriting,
                'has_workbox': has_workbox,
                'precached_media_count': len(precache_urls),
                'script_size': len(content),
            },
            confidence=1.0,
            source='Service Worker script analysis',
        )

    def _detect_sw_registration(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Find SW registration calls in page source"""
        if not capture.page_html:
            return

        reg_pattern = re.compile(
            r'''navigator\.serviceWorker\.register\s*\(\s*['"]([^'"]+)['"]''',
            re.IGNORECASE
        )

        for match in reg_pattern.finditer(capture.page_html):
            sw_url = URLUtils.normalize(match.group(1), base_url)
            if sw_url and sw_url not in seen:
                seen.add(sw_url)
                # Check if already in detected list
                already_detected = any(
                    sw.url == sw_url for sw in capture.service_workers
                )
                if not already_detected:
                    self.add_finding(
                        category='info',
                        subcategory='sw_registration',
                        url=sw_url,
                        data={
                            'found_in': 'page source',
                            'was_detected_by_browser': False,
                        },
                        confidence=0.85,
                        source='SW registration in HTML',
                    )

    def _detect_sw_network_impact(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Detect if SW modified network requests"""
        if not capture.service_workers:
            return

        # Check for requests served from SW cache
        cached_requests = []
        for req in capture.requests:
            rh = req.response_headers
            if rh:
                # Check common SW cache indicators
                sw_indicators = [
                    rh.get('x-sw-cache', ''),
                    rh.get('x-cache', ''),
                    rh.get('x-from-cache', ''),
                    rh.get('service-worker', ''),
                ]
                if any(ind for ind in sw_indicators):
                    cached_requests.append(req.url)

        if cached_requests:
            self.add_finding(
                category='info',
                subcategory='sw_cached_requests',
                data={
                    'count': len(cached_requests),
                    'sample_urls': cached_requests[:10],
                },
                confidence=0.8,
                source='SW cache detection',
            )