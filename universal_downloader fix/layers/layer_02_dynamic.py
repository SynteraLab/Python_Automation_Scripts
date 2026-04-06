"""
layer_02_dynamic.py — Dynamic Rendering Analysis

Analisis data dari browser capture:
- Network requests classification
- Media requests grouping
- API endpoint discovery dari XHR/Fetch
- Response body analysis (cari URL dalam JSON responses)
- Domain analysis (identify CDN, 3rd party, etc)
- Request timing & sequence analysis
- Session flow reconstruction
"""

import logging
import re
import json
from typing import Dict, List, Set
from urllib.parse import urlparse
from collections import defaultdict

from layers.base import BaseLayer
from core.browser import BrowserCaptureResult, CapturedRequest
from core.session import SessionManager
from utils.media_types import MediaTypes
from utils.url_utils import URLUtils
from utils.pattern_matcher import PatternMatcher

logger = logging.getLogger(__name__)


class DynamicAnalysisLayer(BaseLayer):
    
    LAYER_NAME = "layer_02_dynamic"
    LAYER_DESCRIPTION = "Dynamic Rendering & Network Analysis"
    
    async def execute(self, url, recon, capture, session):
        """Analisis semua data dari browser capture"""
        
        if not capture or not capture.requests:
            self.add_error("No browser capture data available")
            return
        
        seen: Set[str] = set()
        
        # ── 1. Classify semua network requests ──
        self._classify_requests(capture, seen)
        
        # ── 2. Analyze API response bodies ──
        self._analyze_response_bodies(capture, url, seen)
        
        # ── 3. Domain mapping ──
        self._analyze_domains(capture)
        
        # ── 4. Request sequence / session flow ──
        self._analyze_request_sequence(capture)
        
        # ── 5. Analyze console logs ──
        self._analyze_console_logs(capture, url, seen)
        
        # ── 6. Analyze WebSocket messages ──
        self._analyze_websocket_data(capture, url, seen)
        
        # ── 7. Analyze DOM mutations ──
        self._analyze_dom_mutations(capture, url, seen)
        
        # ── 8. Analyze storage ──
        self._analyze_storage(capture, url, seen)
        
        # ── 9. Analyze cookies ──
        self._analyze_cookies(capture)
    
    def _classify_requests(self, capture: BrowserCaptureResult, seen: Set[str]):
        """Classify dan categorize semua network requests"""
        
        # Statistik
        stats = defaultdict(int)
        media_by_type = defaultdict(list)
        
        for req in capture.requests:
            stats[req.resource_type] += 1
            
            if req.is_media and req.url not in seen:
                seen.add(req.url)
                media_by_type[req.media_type].append(req)
                
                expiry = URLUtils.detect_url_expiry(req.url)
                
                self.add_finding(
                    category='media',
                    subcategory=req.media_type,
                    url=req.url,
                    data={
                        'content_type': req.content_type,
                        'size': req.response_size,
                        'status': req.status,
                        'resource_type': req.resource_type,
                        'url_expiry': expiry['has_expiry'],
                        'url_type': expiry['estimated_type'],
                        'request_headers': dict(req.headers),
                        'response_headers': dict(req.response_headers),
                    },
                    confidence=1.0,
                    source=f'network capture ({req.resource_type})',
                )
            
            # API endpoints
            if req.is_api and req.url not in seen:
                seen.add(req.url)
                
                self.add_finding(
                    category='api',
                    subcategory=self._classify_api_type(req),
                    url=req.url,
                    data={
                        'method': req.method,
                        'content_type': req.content_type,
                        'status': req.status,
                        'post_data': req.post_data[:500] if req.post_data else None,
                        'has_json_response': 'json' in req.content_type.lower(),
                    },
                    confidence=0.95,
                    source=f'network XHR/Fetch',
                )
        
        # Store stats
        self._result.raw_data['request_stats'] = dict(stats)
        self._result.raw_data['media_by_type'] = {
            k: len(v) for k, v in media_by_type.items()
        }
    
    def _classify_api_type(self, req: CapturedRequest) -> str:
        """Sub-classify API request"""
        url_lower = req.url.lower()
        
        if 'graphql' in url_lower or 'gql' in url_lower:
            return 'graphql'
        elif any(p in url_lower for p in ['/api/', '/v1/', '/v2/', '/v3/']):
            return 'rest_api'
        elif req.content_type and 'json' in req.content_type:
            return 'json_endpoint'
        elif req.content_type and 'xml' in req.content_type:
            return 'xml_endpoint'
        else:
            return 'xhr_generic'
    
    def _analyze_response_bodies(
        self, capture: BrowserCaptureResult, base_url: str, seen: Set[str]
    ):
        """Analyze response bodies dari API calls — cari media URLs"""
        
        for req in capture.requests:
            if not req.response_body:
                continue
            
            body = req.response_body
            
            # ── JSON responses ──
            if 'json' in req.content_type.lower():
                try:
                    data = json.loads(body)
                    urls_found = self._deep_extract_urls(data)
                    
                    for found_url, key_path in urls_found:
                        full_url = URLUtils.normalize(found_url, base_url)
                        if full_url and full_url not in seen:
                            media_type = MediaTypes.identify_type(url=full_url)
                            if media_type != 'unknown':
                                seen.add(full_url)
                                self.add_finding(
                                    category='media',
                                    subcategory=media_type,
                                    url=full_url,
                                    data={
                                        'found_in_api': req.url,
                                        'json_key_path': key_path,
                                        'api_method': req.method,
                                    },
                                    confidence=0.9,
                                    source=f'API response body [{key_path}]',
                                )
                            elif any(kw in key_path.lower() for kw in 
                                     ['url', 'src', 'link', 'stream', 'media',
                                      'video', 'audio', 'image', 'file',
                                      'download', 'manifest', 'playlist']):
                                seen.add(full_url)
                                self.add_finding(
                                    category='api',
                                    subcategory='media_url_in_response',
                                    url=full_url,
                                    data={
                                        'found_in_api': req.url,
                                        'json_key_path': key_path,
                                    },
                                    confidence=0.75,
                                    source=f'API response [{key_path}]',
                                )
                except json.JSONDecodeError:
                    pass
            
            # ── m3u8 / mpd responses ──
            if req.media_type == 'streaming' and body:
                self.add_finding(
                    category='streaming',
                    subcategory='manifest_content',
                    url=req.url,
                    data={
                        'manifest_body': body[:5000],
                        'content_type': req.content_type,
                    },
                    confidence=1.0,
                    source='streaming manifest capture',
                )
            
            # ── Regex scan on all text responses ──
            if isinstance(body, str) and len(body) < 500_000:
                media_matches = PatternMatcher.find_media_urls_only(body, base_url)
                for match in media_matches:
                    if match.url not in seen:
                        seen.add(match.url)
                        self.add_finding(
                            category='media',
                            subcategory=MediaTypes.identify_type(url=match.url),
                            url=match.url,
                            data={'found_in_response': req.url},
                            confidence=match.confidence * 0.8,
                            source=f'response body regex [{match.pattern_name}]',
                        )
    
    def _deep_extract_urls(self, obj, path="", depth=0):
        """Rekursif extract URL dari nested JSON object"""
        results = []
        if depth > 15:
            return results
        
        if isinstance(obj, str):
            if (obj.startswith(('http://', 'https://', '//')) or
                    (obj.startswith('/') and '.' in obj)):
                results.append((obj, path))
        
        elif isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                results.extend(
                    self._deep_extract_urls(value, new_path, depth + 1)
                )
        
        elif isinstance(obj, list):
            for i, item in enumerate(obj[:50]):  # limit
                new_path = f"{path}[{i}]"
                results.extend(
                    self._deep_extract_urls(item, new_path, depth + 1)
                )
        
        return results
    
    def _analyze_domains(self, capture: BrowserCaptureResult):
        """Map semua domain yang dicontact"""
        domain_map = defaultdict(lambda: {
            'requests': 0,
            'media_requests': 0,
            'api_requests': 0,
            'types': set(),
            'sample_urls': [],
        })
        
        for req in capture.requests:
            domain = URLUtils.get_domain(req.url)
            info = domain_map[domain]
            info['requests'] += 1
            info['types'].add(req.resource_type)
            
            if req.is_media:
                info['media_requests'] += 1
            if req.is_api:
                info['api_requests'] += 1
            if len(info['sample_urls']) < 3:
                info['sample_urls'].append(req.url)
        
        # Convert sets to lists for serialization
        for domain, info in domain_map.items():
            info['types'] = list(info['types'])
            
            self.add_finding(
                category='info',
                subcategory='domain',
                data={
                    'domain': domain,
                    'total_requests': info['requests'],
                    'media_requests': info['media_requests'],
                    'api_requests': info['api_requests'],
                    'resource_types': info['types'],
                    'sample_urls': info['sample_urls'],
                },
                confidence=1.0,
                source='domain analysis',
            )
        
        self._result.raw_data['domain_map'] = {
            k: v for k, v in domain_map.items()
        }
    
    def _analyze_request_sequence(self, capture: BrowserCaptureResult):
        """Analyze urutan request untuk reconstruct session flow"""
        media_requests = [r for r in capture.requests if r.is_media]
        api_requests = [r for r in capture.requests if r.is_api]
        
        # Sort by timestamp
        all_important = sorted(
            media_requests + api_requests,
            key=lambda r: r.timestamp
        )
        
        if all_important:
            flow = []
            for i, req in enumerate(all_important[:30]):  # limit
                flow.append({
                    'order': i + 1,
                    'type': 'media' if req.is_media else 'api',
                    'method': req.method,
                    'url': req.url,
                    'status': req.status,
                    'content_type': req.content_type,
                    'timestamp': req.timestamp,
                })
            
            self.add_finding(
                category='info',
                subcategory='session_flow',
                data={'flow': flow},
                confidence=1.0,
                source='request sequence analysis',
            )
    
    def _analyze_console_logs(
        self, capture: BrowserCaptureResult, base_url: str, seen: Set[str]
    ):
        """Cari media URLs di console logs"""
        for log in capture.console_logs:
            text = log.get('text', '')
            if len(text) < 10:
                continue
            
            matches = PatternMatcher.find_media_urls_only(text, base_url)
            for match in matches:
                if match.url not in seen:
                    seen.add(match.url)
                    self.add_finding(
                        category='media',
                        subcategory=MediaTypes.identify_type(url=match.url),
                        url=match.url,
                        confidence=0.7,
                        source='console.log',
                        context=text[:200],
                    )
    
    def _analyze_websocket_data(
        self, capture: BrowserCaptureResult, base_url: str, seen: Set[str]
    ):
        """Analyze WebSocket messages untuk media URLs"""
        for ws in capture.websockets:
            self.add_finding(
                category='info',
                subcategory='websocket',
                url=ws.url,
                data={
                    'messages_count': len(ws.messages),
                    'is_closed': ws.is_closed,
                },
                confidence=1.0,
                source='WebSocket connection',
            )
            
            for msg in ws.messages:
                data = msg.get('data', '')
                if isinstance(data, str) and len(data) > 10:
                    matches = PatternMatcher.find_media_urls_only(data, base_url)
                    for match in matches:
                        if match.url not in seen:
                            seen.add(match.url)
                            self.add_finding(
                                category='media',
                                subcategory=MediaTypes.identify_type(url=match.url),
                                url=match.url,
                                data={
                                    'websocket_url': ws.url,
                                    'direction': msg.get('direction', ''),
                                },
                                confidence=0.8,
                                source='WebSocket message',
                            )
    
    def _analyze_dom_mutations(
        self, capture: BrowserCaptureResult, base_url: str, seen: Set[str]
    ):
        """Analyze DOM changes yang tercapture"""
        for change in capture.dom_changes:
            full_url = URLUtils.normalize(change.value, base_url)
            if full_url and full_url not in seen:
                seen.add(full_url)
                media_type = MediaTypes.identify_type(url=full_url)
                
                self.add_finding(
                    category='media',
                    subcategory=media_type if media_type != 'unknown' else 'dynamic',
                    url=full_url,
                    data={
                        'dom_tag': change.tag,
                        'dom_attribute': change.attribute,
                        'change_type': change.change_type,
                    },
                    confidence=0.85,
                    source=f'DOM mutation ({change.change_type})',
                )
    
    def _analyze_storage(
        self, capture: BrowserCaptureResult, base_url: str, seen: Set[str]
    ):
        """Analyze localStorage/sessionStorage untuk media URLs"""
        for storage_name, storage in [
            ('localStorage', capture.local_storage),
            ('sessionStorage', capture.session_storage),
        ]:
            if not storage:
                continue
            
            for key, value in storage.items():
                if not isinstance(value, str) or len(value) < 10:
                    continue
                
                # Cari URL
                matches = PatternMatcher.find_media_urls_only(value, base_url)
                for match in matches:
                    if match.url not in seen:
                        seen.add(match.url)
                        self.add_finding(
                            category='media',
                            subcategory=MediaTypes.identify_type(url=match.url),
                            url=match.url,
                            data={
                                'storage': storage_name,
                                'key': key,
                            },
                            confidence=0.7,
                            source=f'{storage_name}["{key}"]',
                        )
                
                # Cek apakah value sendiri adalah URL
                if value.startswith(('http://', 'https://')):
                    media_type = MediaTypes.identify_type(url=value)
                    if media_type != 'unknown' and value not in seen:
                        seen.add(value)
                        self.add_finding(
                            category='media',
                            subcategory=media_type,
                            url=value,
                            data={
                                'storage': storage_name,
                                'key': key,
                            },
                            confidence=0.75,
                            source=f'{storage_name}["{key}"]',
                        )
    
    def _analyze_cookies(self, capture: BrowserCaptureResult):
        """Analyze cookies — important untuk reproduction"""
        if capture.cookies:
            important_cookies = []
            for c in capture.cookies:
                important_cookies.append({
                    'name': c.get('name', ''),
                    'domain': c.get('domain', ''),
                    'path': c.get('path', ''),
                    'httpOnly': c.get('httpOnly', False),
                    'secure': c.get('secure', False),
                    'sameSite': c.get('sameSite', ''),
                    'expires': c.get('expires', -1),
                })
            
            self.add_finding(
                category='info',
                subcategory='cookies',
                data={'cookies': important_cookies},
                confidence=1.0,
                source='browser cookies',
            )