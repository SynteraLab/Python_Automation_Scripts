"""
layer_04_api_probe.py — API Endpoint Discovery & Probing

- Collect semua API endpoints yang ditemukan layers sebelumnya
- Probe endpoints (hit dan analisis response)
- Detect API patterns (REST, GraphQL)
- Extract media URLs dari API responses
- Detect pagination patterns
- Detect authentication requirements
"""

import json
import re
import logging
from typing import Dict, List, Set, Optional
from urllib.parse import urljoin, urlparse, parse_qs

from layers.base import BaseLayer
from core.browser import BrowserCaptureResult
from core.session import SessionManager
from utils.media_types import MediaTypes
from utils.url_utils import URLUtils
from utils.pattern_matcher import PatternMatcher

logger = logging.getLogger(__name__)


class APIProbeLayer(BaseLayer):
    
    LAYER_NAME = "layer_04_api_probe"
    LAYER_DESCRIPTION = "API Endpoint Discovery & Probing"
    
    async def execute(self, url, recon, capture, session):
        """Discover & probe API endpoints"""
        
        seen: Set[str] = set()
        base_url = url
        
        # ── 1. Collect API endpoints dari semua sumber ──
        endpoints = self._collect_endpoints(capture, base_url)
        logger.info(f"      Collected {len(endpoints)} API endpoints")
        
        # ── 2. Probe setiap endpoint ──
        if self.config.scan.probe_found_apis:
            probed = 0
            max_probes = self.config.scan.max_api_probes
            
            for endpoint in endpoints[:max_probes]:
                self._probe_endpoint(endpoint, session, base_url, seen)
                probed += 1
            
            logger.info(f"      Probed {probed} endpoints")
        
        # ── 3. Detect common API patterns ──
        self._detect_api_patterns(endpoints, base_url, session, seen)
        
        # ── 4. Try common API paths ──
        self._try_common_api_paths(base_url, session, seen)
    
    def _collect_endpoints(
        self, capture: BrowserCaptureResult, base_url: str
    ) -> List[Dict]:
        """Collect semua API endpoints dari browser capture"""
        endpoints = []
        seen_urls = set()
        
        if not capture:
            return endpoints
        
        for req in capture.requests:
            if not req.is_api:
                continue
            
            if req.url in seen_urls:
                continue
            seen_urls.add(req.url)
            
            endpoints.append({
                'url': req.url,
                'method': req.method,
                'content_type': req.content_type,
                'status': req.status,
                'request_headers': dict(req.headers),
                'post_data': req.post_data,
                'response_body': req.response_body,
                'has_json': 'json' in (req.content_type or '').lower(),
            })
        
        return endpoints
    
    def _probe_endpoint(
        self, endpoint: Dict, session: SessionManager,
        base_url: str, seen: Set[str]
    ):
        """Probe satu API endpoint"""
        url = endpoint['url']
        method = endpoint.get('method', 'GET')
        
        # Skip jika bukan GET (bahaya kalau POST random)
        if method != 'GET':
            # Tapi tetap record
            self.add_finding(
                category='api',
                subcategory='non_get_endpoint',
                url=url,
                data={
                    'method': method,
                    'content_type': endpoint.get('content_type', ''),
                    'post_data_sample': (endpoint.get('post_data', '') or '')[:500],
                },
                confidence=0.9,
                source='API probe (recorded, not tested)',
            )
            return
        
        # Probe GET endpoint
        resp = session.get(url, timeout=10, max_retries=1)
        
        if not resp or resp.status_code >= 400:
            status = resp.status_code if resp else 0
            self.add_finding(
                category='api',
                subcategory='endpoint_status',
                url=url,
                data={
                    'status': status,
                    'accessible': False,
                    'requires_auth': status in (401, 403),
                    'error': resp.error if resp else 'no response',
                },
                confidence=0.9,
                source='API probe',
            )
            return
        
        # Analyze response
        self.add_finding(
            category='api',
            subcategory='probed_endpoint',
            url=url,
            data={
                'status': resp.status_code,
                'content_type': resp.content_type,
                'size': resp.size,
                'accessible': True,
            },
            confidence=1.0,
            source='API probe',
        )
        
        # Extract media URLs dari response
        if resp.body and 'json' in resp.content_type.lower():
            try:
                data = json.loads(resp.body)
                self._extract_media_from_json(
                    data, url, base_url, seen
                )
            except json.JSONDecodeError:
                pass
        
        elif resp.body:
            # Text response — regex scan
            matches = PatternMatcher.find_media_urls_only(resp.body, base_url)
            for match in matches:
                if match.url not in seen:
                    seen.add(match.url)
                    self.add_finding(
                        category='media',
                        subcategory=MediaTypes.identify_type(url=match.url),
                        url=match.url,
                        data={'found_in_api': url},
                        confidence=match.confidence * 0.85,
                        source=f'API response probe [{match.pattern_name}]',
                    )
    
    def _extract_media_from_json(
        self, data, api_url: str, base_url: str, 
        seen: Set[str], path: str = "", depth: int = 0
    ):
        """Rekursif extract media URLs dari JSON response"""
        if depth > 15:
            return
        
        if isinstance(data, str):
            if data.startswith(('http://', 'https://', '//')):
                full_url = URLUtils.normalize(data, base_url)
                if full_url and full_url not in seen:
                    media_type = MediaTypes.identify_type(url=full_url)
                    if media_type != 'unknown':
                        seen.add(full_url)
                        self.add_finding(
                            category='media',
                            subcategory=media_type,
                            url=full_url,
                            data={
                                'found_in_api': api_url,
                                'json_path': path,
                            },
                            confidence=0.9,
                            source=f'API JSON [{path}]',
                        )
                    elif any(kw in path.lower() for kw in 
                             ['url', 'src', 'stream', 'video', 'audio',
                              'media', 'file', 'download', 'manifest']):
                        seen.add(full_url)
                        self.add_finding(
                            category='api',
                            subcategory='potential_media_url',
                            url=full_url,
                            data={
                                'found_in_api': api_url,
                                'json_path': path,
                            },
                            confidence=0.7,
                            source=f'API JSON [{path}] (key suggests media)',
                        )
        
        elif isinstance(data, dict):
            for key, value in data.items():
                new_path = f"{path}.{key}" if path else key
                self._extract_media_from_json(
                    value, api_url, base_url, seen, new_path, depth + 1
                )
        
        elif isinstance(data, list):
            for i, item in enumerate(data[:100]):
                new_path = f"{path}[{i}]"
                self._extract_media_from_json(
                    item, api_url, base_url, seen, new_path, depth + 1
                )
    
    def _detect_api_patterns(
        self, endpoints: List[Dict], base_url: str,
        session: SessionManager, seen: Set[str]
    ):
        """Detect API patterns (versioning, pagination, etc)"""
        
        # Group by path pattern
        path_groups = {}
        for ep in endpoints:
            parsed = urlparse(ep['url'])
            # Generalize path: replace numbers with {id}
            generic_path = re.sub(r'/\d+', '/{id}', parsed.path)
            
            if generic_path not in path_groups:
                path_groups[generic_path] = []
            path_groups[generic_path].append(ep)
        
        for pattern, eps in path_groups.items():
            if len(eps) > 1:
                self.add_finding(
                    category='info',
                    subcategory='api_pattern',
                    data={
                        'pattern': pattern,
                        'count': len(eps),
                        'sample_urls': [e['url'] for e in eps[:5]],
                        'methods': list(set(e['method'] for e in eps)),
                    },
                    confidence=0.85,
                    source='API pattern detection',
                )
    
    def _try_common_api_paths(
        self, base_url: str, session: SessionManager, seen: Set[str]
    ):
        """Try common API paths yang mungkin tidak terdeteksi"""
        common_paths = [
            '/api/',
            '/api/v1/',
            '/api/v2/',
            '/graphql',
            '/wp-json/',
            '/wp-json/wp/v2/media',
            '/wp-json/wp/v2/posts',
            '/_next/data/',
            '/api/media',
            '/api/videos',
            '/api/config',
            '/api/player/config',
            '/api/embed/config',
        ]
        
        for path in common_paths:
            full_url = urljoin(base_url, path)
            if full_url in seen:
                continue
            
            resp = session.head(full_url, max_retries=1, timeout=5)
            
            if resp and resp.status_code in (200, 301, 302):
                seen.add(full_url)
                self.add_finding(
                    category='api',
                    subcategory='discovered_path',
                    url=full_url,
                    data={
                        'status': resp.status_code,
                        'content_type': resp.content_type,
                        'method': 'HEAD probe',
                    },
                    confidence=0.6,
                    source='common API path probe',
                )
                
                # Jika 200, coba GET untuk lihat isinya
                if resp.status_code == 200 and 'json' in resp.content_type.lower():
                    get_resp = session.get(full_url, timeout=10)
                    if get_resp and get_resp.body:
                        try:
                            data = json.loads(get_resp.body)
                            self._extract_media_from_json(
                                data, full_url, base_url, seen
                            )
                        except json.JSONDecodeError:
                            pass