"""
layer_08_infrastructure.py — Full Infrastructure Fingerprint

- Server technology identification
- CDN identification & mapping
- CMS deep detection
- Framework version detection
- SSL/TLS analysis
- DNS information
- Security headers analysis
- WAF detection (Cloudflare, etc)
- Third-party services identification
"""

import re
import logging
from typing import Dict, List, Set
from urllib.parse import urlparse
from collections import defaultdict

try:
    import tldextract
    HAS_TLDEXTRACT = True
except ImportError:
    HAS_TLDEXTRACT = False

from layers.base import BaseLayer
from core.browser import BrowserCaptureResult
from core.session import SessionManager
from utils.media_types import (
    CMS_SIGNATURES, CDN_SIGNATURES, VIDEO_PLAYERS
)
from utils.url_utils import URLUtils

logger = logging.getLogger(__name__)


class InfrastructureAnalysisLayer(BaseLayer):

    LAYER_NAME = "layer_08_infrastructure"
    LAYER_DESCRIPTION = "Infrastructure & Technology Fingerprint"

    async def execute(self, url, recon, capture, session):
        seen: Set[str] = set()
        base_url = url

        # ── 1. Response header analysis ──
        self._analyze_response_headers(url, session)

        # ── 2. Deep CMS detection ──
        self._deep_cms_detection(capture, base_url, session)

        # ── 3. CDN mapping ──
        self._map_cdns(capture, base_url)

        # ── 4. Security headers & WAF ──
        self._analyze_security(url, session)

        # ── 5. Third-party services ──
        self._identify_third_parties(capture, base_url)

        # ── 6. Video player deep detection ──
        self._deep_player_detection(capture, base_url)

        # ── 7. Media storage analysis ──
        self._analyze_media_storage(capture, base_url)

        # ── 8. DNS & domain info ──
        self._analyze_domain_info(url)

    def _analyze_response_headers(self, url: str, session: SessionManager):
        """Deep analysis of HTTP response headers"""
        resp = session.get(url, timeout=10)
        if not resp:
            return

        headers = resp.headers
        server_info = {
            'server': headers.get('server', ''),
            'x_powered_by': headers.get('x-powered-by', ''),
            'x_generator': headers.get('x-generator', ''),
            'x_cms': headers.get('x-cms', ''),
            'x_runtime': headers.get('x-runtime', ''),
            'x_framework': headers.get('x-aspnet-version', ''),
            'via': headers.get('via', ''),
        }

        # Technology detection from headers
        technologies = []
        h_combined = ' '.join(f"{k}:{v}" for k, v in headers.items()).lower()

        tech_sigs = {
            'nginx': ['nginx'],
            'apache': ['apache'],
            'iis': ['microsoft-iis', 'asp.net'],
            'cloudflare': ['cloudflare', 'cf-ray'],
            'nodejs': ['express', 'node', 'koa'],
            'php': ['php/', 'x-powered-by: php'],
            'python': ['python', 'gunicorn', 'uvicorn', 'django', 'flask'],
            'ruby': ['ruby', 'phusion', 'passenger', 'puma'],
            'java': ['java', 'tomcat', 'jetty', 'spring'],
            'go': ['go', 'gin', 'echo'],
            'varnish': ['varnish', 'x-varnish'],
            'litespeed': ['litespeed'],
            'caddy': ['caddy'],
        }

        for tech_name, indicators in tech_sigs.items():
            if any(ind in h_combined for ind in indicators):
                technologies.append(tech_name)

        self.add_finding(
            category='info',
            subcategory='server_technology',
            data={
                'server_info': server_info,
                'technologies_detected': technologies,
                'all_headers': dict(headers),
            },
            confidence=1.0,
            source='HTTP response headers',
        )

    def _deep_cms_detection(
        self, capture: BrowserCaptureResult,
        base_url: str, session: SessionManager
    ):
        """Deep CMS & framework detection"""
        if not capture or not capture.page_html:
            return

        html = capture.page_html
        html_lower = html.lower()
        detected = {}

        for cms_name, sigs in CMS_SIGNATURES.items():
            score = 0
            matched_indicators = []

            for indicator in sigs['indicators']:
                if indicator.lower() in html_lower:
                    score += 1
                    matched_indicators.append(indicator)

            if score > 0:
                detected[cms_name] = {
                    'score': score,
                    'indicators': matched_indicators,
                    'media_paths': sigs.get('media_paths', []),
                }

        # ── Version detection ──
        version_patterns = {
            'wordpress': [
                r'<meta[^>]+content="WordPress\s+([\d.]+)"',
                r'ver=([\d.]+)',
                r'wp-includes/css/dist/[^"]+\?ver=([\d.]+)',
            ],
            'jquery': [
                r'jquery[.-]?([\d.]+)(?:\.min)?\.js',
                r'jQuery\s+v([\d.]+)',
            ],
            'react': [
                r'react(?:\.production)?[.-]?([\d.]+)',
                r'"react":\s*"[~^]?([\d.]+)"',
            ],
            'vue': [
                r'vue(?:\.runtime)?[.-]?([\d.]+)',
            ],
            'angular': [
                r'angular[.-]?([\d.]+)',
                r'ng-version="([\d.]+)"',
            ],
            'bootstrap': [
                r'bootstrap[.-]?([\d.]+)',
                r'Bootstrap\s+v([\d.]+)',
            ],
        }

        for lib_name, patterns in version_patterns.items():
            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    version = match.group(1)
                    if lib_name not in detected:
                        detected[lib_name] = {
                            'score': 1,
                            'indicators': [pattern],
                            'media_paths': [],
                        }
                    detected[lib_name]['version'] = version
                    break

        # ── Check known CMS paths ──
        for cms_name, sigs in CMS_SIGNATURES.items():
            if cms_name in detected:
                continue
            for media_path in sigs.get('media_paths', []):
                test_url = URLUtils.normalize(media_path, base_url)
                resp = session.head(test_url, max_retries=1, timeout=5)
                if resp and resp.status_code in (200, 301, 302, 403):
                    if cms_name not in detected:
                        detected[cms_name] = {
                            'score': 1,
                            'indicators': [f'path exists: {media_path}'],
                            'media_paths': sigs.get('media_paths', []),
                        }
                    break

        for cms_name, info in detected.items():
            self.add_finding(
                category='info',
                subcategory='cms_framework',
                data={
                    'name': cms_name,
                    'score': info['score'],
                    'version': info.get('version', 'unknown'),
                    'matched_indicators': info['indicators'],
                    'media_paths': info.get('media_paths', []),
                },
                confidence=min(0.5 + info['score'] * 0.15, 1.0),
                source='CMS/framework deep detection',
            )

    def _map_cdns(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Map all CDN domains used"""
        if not capture:
            return

        cdn_domains = defaultdict(lambda: {
            'provider': 'unknown',
            'requests': 0,
            'media_requests': 0,
            'types': set(),
            'sample_urls': [],
        })

        for req in capture.requests:
            domain = URLUtils.get_domain(req.url)
            if not domain:
                continue

            cdn_info = cdn_domains[domain]
            cdn_info['requests'] += 1
            cdn_info['types'].add(req.resource_type)

            if req.is_media:
                cdn_info['media_requests'] += 1

            if len(cdn_info['sample_urls']) < 3:
                cdn_info['sample_urls'].append(req.url)

            # Identify CDN provider
            if cdn_info['provider'] == 'unknown':
                domain_lower = domain.lower()
                # Check response headers
                rh_combined = ' '.join(
                    f"{k}:{v}" for k, v in req.response_headers.items()
                ).lower()
                combined = domain_lower + ' ' + rh_combined

                for cdn_name, indicators in CDN_SIGNATURES.items():
                    if any(ind.lower() in combined for ind in indicators):
                        cdn_info['provider'] = cdn_name
                        break

        for domain, info in cdn_domains.items():
            if info['media_requests'] > 0 or info['provider'] != 'unknown':
                info['types'] = list(info['types'])
                self.add_finding(
                    category='info',
                    subcategory='cdn_domain',
                    data={
                        'domain': domain,
                        'provider': info['provider'],
                        'total_requests': info['requests'],
                        'media_requests': info['media_requests'],
                        'resource_types': info['types'],
                        'sample_urls': info['sample_urls'],
                        'is_same_origin': URLUtils.is_same_domain(
                            f"https://{domain}", base_url
                        ),
                    },
                    confidence=0.9 if info['provider'] != 'unknown' else 0.6,
                    source='CDN mapping',
                )

    def _analyze_security(self, url: str, session: SessionManager):
        """Analyze security headers & WAF detection"""
        resp = session.get(url, timeout=10)
        if not resp:
            return

        headers = resp.headers
        security_headers = {
            'strict-transport-security': headers.get('strict-transport-security', ''),
            'content-security-policy': headers.get('content-security-policy', ''),
            'x-content-type-options': headers.get('x-content-type-options', ''),
            'x-frame-options': headers.get('x-frame-options', ''),
            'x-xss-protection': headers.get('x-xss-protection', ''),
            'referrer-policy': headers.get('referrer-policy', ''),
            'permissions-policy': headers.get('permissions-policy', ''),
            'access-control-allow-origin': headers.get('access-control-allow-origin', ''),
            'access-control-allow-methods': headers.get('access-control-allow-methods', ''),
            'access-control-allow-headers': headers.get('access-control-allow-headers', ''),
        }

        # WAF detection
        waf_detected = None
        waf_sigs = {
            'cloudflare': ['cf-ray', 'cf-cache-status', '__cfduid', 'cf-request-id'],
            'aws_waf': ['x-amzn-requestid', 'x-amz-cf-id'],
            'akamai': ['akamai', 'x-akamai'],
            'sucuri': ['x-sucuri-id', 'sucuri'],
            'incapsula': ['x-iinfo', 'incap_ses'],
            'wordfence': ['wordfence'],
        }

        h_keys = {k.lower() for k in headers.keys()}
        h_combined = ' '.join(f"{k}:{v}" for k, v in headers.items()).lower()

        for waf_name, indicators in waf_sigs.items():
            if any(ind.lower() in h_combined for ind in indicators):
                waf_detected = waf_name
                break

        # CSP media-src analysis
        csp = security_headers.get('content-security-policy', '')
        media_src = ''
        if csp:
            csp_match = re.search(r'media-src\s+([^;]+)', csp)
            if csp_match:
                media_src = csp_match.group(1).strip()

        self.add_finding(
            category='info',
            subcategory='security',
            data={
                'security_headers': {
                    k: v for k, v in security_headers.items() if v
                },
                'waf_detected': waf_detected,
                'cors_enabled': bool(
                    security_headers['access-control-allow-origin']
                ),
                'cors_origin': security_headers['access-control-allow-origin'],
                'csp_media_src': media_src,
                'referrer_policy': security_headers['referrer-policy'],
            },
            confidence=1.0,
            source='security analysis',
        )

    def _identify_third_parties(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Identify third-party services"""
        if not capture:
            return

        target_domain = URLUtils.get_domain(base_url)
        third_parties = defaultdict(lambda: {'type': 'unknown', 'urls': []})

        service_sigs = {
            'google_analytics': {
                'domains': ['google-analytics.com', 'googletagmanager.com', 'analytics.google.com'],
                'type': 'analytics',
            },
            'facebook_pixel': {
                'domains': ['connect.facebook.net', 'facebook.com/tr'],
                'type': 'analytics',
            },
            'google_fonts': {
                'domains': ['fonts.googleapis.com', 'fonts.gstatic.com'],
                'type': 'fonts',
            },
            'google_ads': {
                'domains': ['googlesyndication.com', 'doubleclick.net', 'googleadservices.com'],
                'type': 'advertising',
            },
            'recaptcha': {
                'domains': ['google.com/recaptcha', 'gstatic.com/recaptcha'],
                'type': 'security',
            },
            'youtube_embed': {
                'domains': ['youtube.com/embed', 'youtube-nocookie.com'],
                'type': 'video_embed',
            },
            'vimeo_embed': {
                'domains': ['player.vimeo.com'],
                'type': 'video_embed',
            },
            'stripe': {
                'domains': ['js.stripe.com', 'api.stripe.com'],
                'type': 'payment',
            },
            'hotjar': {
                'domains': ['hotjar.com', 'static.hotjar.com'],
                'type': 'analytics',
            },
            'intercom': {
                'domains': ['widget.intercom.io', 'intercom.io'],
                'type': 'chat',
            },
            'sentry': {
                'domains': ['sentry.io', 'browser.sentry-cdn.com'],
                'type': 'error_tracking',
            },
        }

        for req in capture.requests:
            req_domain = URLUtils.get_domain(req.url)
            if req_domain == target_domain:
                continue

            for service_name, info in service_sigs.items():
                if any(d in req.url.lower() for d in info['domains']):
                    tp = third_parties[service_name]
                    tp['type'] = info['type']
                    if len(tp['urls']) < 3:
                        tp['urls'].append(req.url)
                    break

        for service_name, info in third_parties.items():
            self.add_finding(
                category='info',
                subcategory='third_party_service',
                data={
                    'service': service_name,
                    'type': info['type'],
                    'sample_urls': info['urls'],
                },
                confidence=0.9,
                source='third-party identification',
            )

    def _deep_player_detection(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Deep video player detection with version"""
        if not capture:
            return

        html = capture.page_html or ''
        html_lower = html.lower()

        for player_name, info in VIDEO_PLAYERS.items():
            matched = []
            for indicator in info['indicators']:
                if indicator.lower() in html_lower:
                    matched.append(indicator)

            if matched:
                # Try to detect version
                version = 'unknown'
                version_patterns = [
                    rf'{player_name}[/\-._v]*([\d.]+)',
                    rf'{player_name}.*?version["\s:=]*([\d.]+)',
                ]
                for vp in version_patterns:
                    m = re.search(vp, html, re.IGNORECASE)
                    if m:
                        version = m.group(1)
                        break

                # Check for player JS files
                player_js = [
                    req.url for req in capture.requests
                    if req.resource_type == 'script'
                    and any(ind.lower() in req.url.lower()
                            for ind in info['indicators'])
                ]

                self.add_finding(
                    category='info',
                    subcategory='video_player',
                    data={
                        'player': player_name,
                        'version': version,
                        'matched_indicators': matched,
                        'config_pattern': info.get('config_pattern', ''),
                        'media_keys': info.get('media_keys', []),
                        'player_scripts': player_js[:5],
                    },
                    confidence=0.95,
                    source='video player deep detection',
                )

    def _analyze_media_storage(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Analyze where media files are stored"""
        if not capture:
            return

        storage_patterns = {
            'aws_s3': ['.s3.amazonaws.com', 's3-', '.s3.'],
            'gcs': ['storage.googleapis.com'],
            'azure_blob': ['blob.core.windows.net'],
            'digitalocean_spaces': ['.digitaloceanspaces.com'],
            'backblaze_b2': ['backblazeb2.com', 'f000.backblazeb2.com'],
            'wasabi': ['wasabisys.com', 's3.wasabisys.com'],
            'bunny_storage': ['b-cdn.net', 'bunnycdn'],
            'cloudinary': ['res.cloudinary.com', 'cloudinary.com'],
            'imgix': ['imgix.net'],
            'uploadcare': ['ucarecdn.com'],
        }

        detected_storage = {}

        for req in capture.requests:
            if not req.is_media:
                continue

            url_lower = req.url.lower()
            for storage_name, indicators in storage_patterns.items():
                if any(ind in url_lower for ind in indicators):
                    if storage_name not in detected_storage:
                        detected_storage[storage_name] = {
                            'count': 0, 'sample_urls': []
                        }
                    detected_storage[storage_name]['count'] += 1
                    if len(detected_storage[storage_name]['sample_urls']) < 5:
                        detected_storage[storage_name]['sample_urls'].append(req.url)

        for storage_name, info in detected_storage.items():
            self.add_finding(
                category='info',
                subcategory='media_storage',
                data={
                    'provider': storage_name,
                    'media_count': info['count'],
                    'sample_urls': info['sample_urls'],
                },
                confidence=0.9,
                source='media storage detection',
            )

    def _analyze_domain_info(self, url: str):
        """Analyze domain information"""
        if not HAS_TLDEXTRACT:
            return

        try:
            extracted = tldextract.extract(url)
            self.add_finding(
                category='info',
                subcategory='domain_info',
                data={
                    'domain': extracted.registered_domain,
                    'subdomain': extracted.subdomain,
                    'suffix': extracted.suffix,
                    'fqdn': extracted.fqdn,
                },
                confidence=1.0,
                source='domain info',
            )
        except Exception as e:
            logger.debug(f"Domain info error: {e}")