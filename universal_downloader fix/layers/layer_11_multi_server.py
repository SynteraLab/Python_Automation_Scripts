"""
layer_11_multi_server.py — Multi-Server / Multi-Source Detection Layer

Tujuan:
- Detect SEMUA alternative server/source pada halaman
- Click setiap server dan capture media per server
- Map server → embed service → media URL
- Identify extraction method per server
- Generate per-server extraction strategy
"""

import re
import json
import logging
from typing import Dict, List, Set

from layers.base import BaseLayer
from core.browser import BrowserCaptureResult
from core.session import SessionManager
from core.server_switcher import ServerSwitcher, ServerSwitchResult, ServerSource
from utils.embed_services import (
    EmbedServiceDetector, EMBED_SERVICES,
    SERVER_SELECTOR_CSS, SERVER_API_PATTERNS,
)
from utils.media_types import MediaTypes
from utils.url_utils import URLUtils
from utils.pattern_matcher import PatternMatcher

logger = logging.getLogger(__name__)


class MultiServerLayer(BaseLayer):

    LAYER_NAME = "layer_11_multi_server"
    LAYER_DESCRIPTION = "Multi-Server / Multi-Source Detection & Probing"

    async def execute(self, url, recon, capture, session):
        seen: Set[str] = set()
        base_url = url

        # ── 1. Static: detect servers from HTML ──
        self._detect_servers_static(capture, base_url, seen)

        # ── 2. Static: detect embed services in page ──
        self._detect_embed_services(capture, base_url, seen)

        # ── 3. Static: detect server API patterns ──
        self._detect_server_apis(capture, base_url, seen)

        # ── 4. Dynamic: browser server switching ──
        switch_result = await self._run_server_switcher(url)

        if switch_result:
            self._process_switch_results(switch_result, base_url, seen)

        # ── 5. Cross-reference: match iframes to services ──
        self._cross_reference_iframes(capture, base_url, seen)

        # ── 6. Summarize server map ──
        self._build_server_map()

    # ══════════════════════════════════════════
    #  STATIC DETECTION
    # ══════════════════════════════════════════

    def _detect_servers_static(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Detect server selectors dari rendered HTML"""
        if not capture or not capture.page_html:
            return

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(capture.page_html, 'lxml')

        for css_selector in SERVER_SELECTOR_CSS:
            try:
                # bs4 doesn't support all CSS selectors, use simple ones
                # Convert CSS to bs4 compatible
                simple_selector = css_selector.replace(':has-text', '')

                elements = soup.select(simple_selector)

                if len(elements) < 2:
                    continue

                servers_found = []
                for i, el in enumerate(elements):
                    text = el.get_text(strip=True)
                    if not text or len(text) > 100:
                        continue

                    # Collect data attributes
                    data_attrs = {}
                    for attr_name, attr_val in el.attrs.items():
                        if attr_name.startswith('data-'):
                            data_attrs[attr_name] = (
                                attr_val if isinstance(attr_val, str)
                                else ' '.join(attr_val)
                            )

                    # Check for href with embed URL
                    href = el.get('href', '')
                    onclick = el.get('onclick', '')

                    server_info = {
                        'name': text,
                        'index': i,
                        'data_attributes': data_attrs,
                        'href': href,
                        'onclick': onclick,
                        'is_active': any(
                            cls in ' '.join(el.get('class', []))
                            for cls in ['active', 'selected', 'current']
                        ),
                        'css_selector': css_selector,
                    }

                    # Detect embed service from data-embed or href
                    embed_url = data_attrs.get('data-embed', '') or href
                    if embed_url:
                        service = EmbedServiceDetector.identify_service_name(embed_url)
                        server_info['embed_service'] = service
                        server_info['embed_url'] = embed_url

                    servers_found.append(server_info)

                if servers_found:
                    self.add_finding(
                        category='server',
                        subcategory='selector_detected',
                        data={
                            'css_selector': css_selector,
                            'server_count': len(servers_found),
                            'servers': servers_found,
                        },
                        confidence=0.95,
                        source=f'static HTML: {css_selector}',
                    )

                    # Juga record setiap server individual
                    for srv in servers_found:
                        self.add_finding(
                            category='server',
                            subcategory='server_option',
                            url=srv.get('embed_url', ''),
                            data=srv,
                            confidence=0.9,
                            source=f'server selector [{srv["name"]}]',
                        )

                    # Found servers, stop looking
                    break

            except Exception:
                continue

    def _detect_embed_services(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Detect all embed services referenced in page"""
        if not capture or not capture.page_html:
            return

        services = EmbedServiceDetector.scan_html_for_services(
            capture.page_html
        )

        for svc in services:
            self.add_finding(
                category='server',
                subcategory='embed_service_detected',
                data=svc,
                confidence=0.85,
                source=f'embed service: {svc["service_name"]}',
            )

        # Also check network requests
        if capture.requests:
            for req in capture.requests:
                if EmbedServiceDetector.is_embed_service(req.url):
                    svc_name = EmbedServiceDetector.identify_service_name(
                        req.url
                    )
                    hints = EmbedServiceDetector.get_extraction_hints(
                        req.url
                    )

                    if req.url not in seen:
                        seen.add(req.url)
                        self.add_finding(
                            category='server',
                            subcategory='embed_service_request',
                            url=req.url,
                            data={
                                'service': svc_name,
                                'resource_type': req.resource_type,
                                'content_type': req.content_type,
                                'status': req.status,
                                'extraction_hints': hints,
                            },
                            confidence=0.9,
                            source=f'embed service network: {svc_name}',
                        )

    def _detect_server_apis(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Detect server-related API calls"""
        if not capture:
            return

        for req in capture.requests:
            if not req.is_api:
                continue

            url_lower = req.url.lower()

            for pattern in SERVER_API_PATTERNS:
                if re.search(pattern, url_lower):
                    if req.url not in seen:
                        seen.add(req.url)

                        api_info = {
                            'url': req.url,
                            'method': req.method,
                            'pattern_matched': pattern,
                            'status': req.status,
                            'content_type': req.content_type,
                        }

                        # Include response body if available
                        if req.response_body:
                            api_info['response_preview'] = (
                                req.response_body[:3000]
                            )

                            # Try extract media from response
                            try:
                                data = json.loads(req.response_body)
                                media_in_resp = self._deep_find_media(
                                    data, base_url
                                )
                                if media_in_resp:
                                    api_info['media_in_response'] = media_in_resp
                            except Exception:
                                pass

                        self.add_finding(
                            category='server',
                            subcategory='server_api',
                            url=req.url,
                            data=api_info,
                            confidence=0.9,
                            source=f'server API pattern: {pattern}',
                        )
                    break

    def _deep_find_media(self, data, base_url, depth=0) -> List[Dict]:
        """Find media URLs in nested data structure"""
        results = []
        if depth > 10:
            return results

        if isinstance(data, str):
            url = URLUtils.normalize(data, base_url)
            if url and (MediaTypes.is_media_url(url) or
                        MediaTypes.is_streaming_url(url)):
                results.append({
                    'url': url,
                    'type': MediaTypes.identify_type(url=url),
                })

        elif isinstance(data, dict):
            for key, value in data.items():
                results.extend(
                    self._deep_find_media(value, base_url, depth + 1)
                )

        elif isinstance(data, list):
            for item in data[:50]:
                results.extend(
                    self._deep_find_media(item, base_url, depth + 1)
                )

        return results

    # ══════════════════════════════════════════
    #  DYNAMIC: BROWSER SERVER SWITCHING
    # ══════════════════════════════════════════

    async def _run_server_switcher(self, url: str) -> Optional[ServerSwitchResult]:
        """Run the ServerSwitcher to click through all servers"""
        switcher = ServerSwitcher(self.config)

        try:
            await switcher.start()
            result = await switcher.detect_and_switch(url)
            return result

        except Exception as e:
            self.add_error(f"ServerSwitcher failed: {str(e)}")
            return None

        finally:
            await switcher.stop()

    def _process_switch_results(
        self, switch_result: ServerSwitchResult,
        base_url: str, seen: Set[str]
    ):
        """Process results from server switching"""

        for server in switch_result.servers:
            # ── Record server ──
            server_data = {
                'server_name': server.server_name,
                'server_index': server.server_index,
                'server_id': server.server_id,
                'is_active': server.is_active,
                'iframe_url': server.iframe_url,
                'embed_service': server.embed_service,
                'quality': server.quality,
                'language': server.language,
                'sub_or_dub': server.sub_or_dub,
                'data_attributes': server.data_attributes,
                'media_count': len(server.media_urls),
                'streaming_count': len(server.streaming_urls),
                'api_calls_count': len(server.api_calls),
            }

            # Extraction hints
            if server.iframe_url:
                hints = EmbedServiceDetector.get_extraction_hints(
                    server.iframe_url
                )
                server_data['extraction_hints'] = hints

            self.add_finding(
                category='server',
                subcategory='probed_server',
                url=server.iframe_url or '',
                data=server_data,
                confidence=1.0,
                source=f'server switch probe: {server.server_name}',
            )

            # ── Record media per server ──
            for media in server.media_urls:
                media_url = media.get('url', '')
                if media_url and media_url not in seen:
                    seen.add(media_url)
                    self.add_finding(
                        category='media',
                        subcategory=media.get('type', 'video'),
                        url=media_url,
                        data={
                            'server': server.server_name,
                            'server_index': server.server_index,
                            'embed_service': server.embed_service,
                            'content_type': media.get('content_type', ''),
                        },
                        confidence=0.95,
                        source=f'server [{server.server_name}] media',
                    )

            # ── Record streaming per server ──
            for stream_url in server.streaming_urls:
                if stream_url not in seen:
                    seen.add(stream_url)
                    self.add_finding(
                        category='streaming',
                        subcategory='server_stream',
                        url=stream_url,
                        data={
                            'server': server.server_name,
                            'embed_service': server.embed_service,
                        },
                        confidence=0.95,
                        source=f'server [{server.server_name}] stream',
                    )

            # ── Record API calls per server ──
            for api in server.api_calls:
                api_url = api.get('url', '')
                if api_url and api_url not in seen:
                    seen.add(api_url)
                    self.add_finding(
                        category='api',
                        subcategory='server_api_call',
                        url=api_url,
                        data={
                            'server': server.server_name,
                            'response_preview': api.get(
                                'body_preview', ''
                            )[:1000],
                        },
                        confidence=0.9,
                        source=f'server [{server.server_name}] API',
                    )

    # ══════════════════════════════════════════
    #  CROSS-REFERENCE
    # ══════════════════════════════════════════

    def _cross_reference_iframes(
        self, capture: BrowserCaptureResult,
        base_url: str, seen: Set[str]
    ):
        """Cross-reference all iframes with embed service database"""
        if not capture:
            return

        # From static layer findings
        from bs4 import BeautifulSoup

        for html_source in [capture.page_html]:
            if not html_source:
                continue

            soup = BeautifulSoup(html_source, 'lxml')

            for iframe in soup.find_all('iframe'):
                src = iframe.get('src', '') or iframe.get('data-src', '')
                if not src or src.startswith(('about:', 'data:', 'javascript:')):
                    continue

                full_url = URLUtils.normalize(src, base_url)
                if not full_url or full_url in seen:
                    continue

                service = EmbedServiceDetector.identify_service(full_url)
                if service:
                    seen.add(full_url)
                    hints = EmbedServiceDetector.get_extraction_hints(
                        full_url
                    )

                    self.add_finding(
                        category='server',
                        subcategory='iframe_embed_identified',
                        url=full_url,
                        data={
                            'service': service.display_name,
                            'extraction_hints': hints,
                            'iframe_width': iframe.get('width', ''),
                            'iframe_height': iframe.get('height', ''),
                        },
                        confidence=0.95,
                        source=f'iframe → {service.display_name}',
                    )

    # ══════════════════════════════════════════
    #  SERVER MAP SUMMARY
    # ══════════════════════════════════════════

    def _build_server_map(self):
        """Build comprehensive server map summary"""
        servers = [
            f for f in self._result.findings
            if f.category == 'server' and f.subcategory == 'probed_server'
        ]

        if not servers:
            # Check for static detections
            servers = [
                f for f in self._result.findings
                if f.category == 'server' and f.subcategory == 'server_option'
            ]

        server_map = []
        for srv_finding in servers:
            d = srv_finding.data
            server_entry = {
                'name': d.get('server_name', d.get('name', 'Unknown')),
                'embed_service': d.get('embed_service', 'unknown'),
                'iframe_url': d.get('iframe_url', d.get('embed_url', '')),
                'quality': d.get('quality', ''),
                'sub_dub': d.get('sub_or_dub', ''),
                'media_found': d.get('media_count', 0),
                'streaming_found': d.get('streaming_count', 0),
                'extraction_hints': d.get('extraction_hints', {}),
            }
            server_map.append(server_entry)

        self.add_finding(
            category='info',
            subcategory='server_map',
            data={
                'total_servers': len(server_map),
                'servers': server_map,
                'embed_services_used': list(set(
                    s['embed_service'] for s in server_map
                    if s['embed_service'] != 'unknown'
                )),
            },
            confidence=1.0,
            source='server map summary',
        )