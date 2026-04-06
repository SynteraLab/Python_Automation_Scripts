"""
layer_09_auth_flow.py — Cookie, Auth & Session Flow Analysis

- Track cookie lifecycle
- Detect authentication requirements
- Detect token-based URLs (signed URLs)
- Detect referer/origin requirements
- Session flow reconstruction
- CORS policy analysis
- Rate limiting detection
"""

import re
import time
import logging
from typing import Dict, List, Set
from urllib.parse import urlparse
from collections import defaultdict

from layers.base import BaseLayer
from core.browser import BrowserCaptureResult
from core.session import SessionManager
from utils.url_utils import URLUtils

logger = logging.getLogger(__name__)


class AuthFlowAnalysisLayer(BaseLayer):

    LAYER_NAME = "layer_09_auth_flow"
    LAYER_DESCRIPTION = "Authentication & Session Flow Analysis"

    async def execute(self, url, recon, capture, session):
        seen: Set[str] = set()
        base_url = url

        if not capture:
            self.add_error("No browser capture data")
            return

        # ── 1. Cookie lifecycle analysis ──
        self._analyze_cookie_lifecycle(capture, base_url)

        # ── 2. Token/signed URL detection ──
        self._detect_signed_urls(capture, base_url)

        # ── 3. Referer/Origin requirements ──
        self._detect_referer_requirements(capture, base_url, session)

        # ── 4. Session flow reconstruction ──
        self._reconstruct_session_flow(capture, base_url)

        # ── 5. CORS analysis ──
        self._analyze_cors(capture, base_url)

        # ── 6. Authentication detection ──
        self._detect_authentication(capture, base_url)

        # ── 7. Rate limiting detection ──
        self._detect_rate_limiting(capture)

    def _analyze_cookie_lifecycle(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Analyze cookies — which are needed for media access"""
        if not capture.cookies:
            return

        cookies_by_domain = defaultdict(list)
        for c in capture.cookies:
            domain = c.get('domain', '')
            cookies_by_domain[domain].append(c)

        # Categorize cookies
        session_cookies = []
        persistent_cookies = []
        auth_cookies = []

        auth_names = [
            'session', 'token', 'auth', 'jwt', 'access',
            'login', 'user', 'csrf', 'xsrf', 'sid',
            'connect.sid', 'PHPSESSID', 'JSESSIONID',
            'laravel_session', 'ASP.NET_SessionId',
        ]

        for c in capture.cookies:
            name = c.get('name', '').lower()
            expires = c.get('expires', -1)

            if expires == -1 or expires == 0:
                session_cookies.append(c)
            else:
                persistent_cookies.append(c)

            if any(an in name for an in auth_names):
                auth_cookies.append(c)

        self.add_finding(
            category='info',
            subcategory='cookie_analysis',
            data={
                'total_cookies': len(capture.cookies),
                'session_cookies': len(session_cookies),
                'persistent_cookies': len(persistent_cookies),
                'auth_cookies': [
                    {
                        'name': c.get('name'),
                        'domain': c.get('domain'),
                        'httpOnly': c.get('httpOnly'),
                        'secure': c.get('secure'),
                        'sameSite': c.get('sameSite'),
                    }
                    for c in auth_cookies
                ],
                'domains': dict({
                    d: len(cl) for d, cl in cookies_by_domain.items()
                }),
                'all_cookie_names': [c.get('name') for c in capture.cookies],
            },
            confidence=1.0,
            source='cookie lifecycle analysis',
        )

    def _detect_signed_urls(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Detect signed/tokenized URLs"""
        signed_urls = []

        for req in capture.requests:
            if not req.is_media:
                continue

            expiry = URLUtils.detect_url_expiry(req.url)
            if expiry['has_expiry']:
                signed_urls.append({
                    'url': req.url,
                    'media_type': req.media_type,
                    'url_type': expiry['estimated_type'],
                    'expiry_params': expiry.get('timestamp_params', {}),
                    'token_params': expiry.get('token_params', {}),
                    'signature_params': expiry.get('signature_params', {}),
                    'estimated_expiry_seconds': expiry.get(
                        'estimated_expiry_seconds'
                    ),
                })

        if signed_urls:
            # Detect pattern
            expiry_durations = [
                s.get('estimated_expiry_seconds')
                for s in signed_urls
                if s.get('estimated_expiry_seconds') is not None
            ]

            self.add_finding(
                category='info',
                subcategory='signed_urls',
                data={
                    'count': len(signed_urls),
                    'urls': signed_urls[:10],
                    'estimated_expiry_range': {
                        'min_seconds': min(expiry_durations) if expiry_durations else None,
                        'max_seconds': max(expiry_durations) if expiry_durations else None,
                    },
                    'extraction_note': (
                        'URLs are signed/tokenized. '
                        'New tokens must be generated for each extraction session.'
                    ),
                },
                confidence=1.0,
                source='signed URL detection',
            )

    def _detect_referer_requirements(
        self, capture: BrowserCaptureResult,
        base_url: str, session: SessionManager
    ):
        """Test if media URLs require specific Referer/Origin"""
        if not capture:
            return

        media_reqs = [r for r in capture.requests if r.is_media and r.status == 200]

        if not media_reqs:
            return

        # Test first few media URLs without referer
        test_results = []
        for req in media_reqs[:5]:
            # Test WITHOUT referer
            resp_no_ref = session.head(
                req.url,
                max_retries=1,
                timeout=10,
                extra_headers={
                    'Referer': '',
                    'Origin': '',
                }
            )

            # Test WITH referer
            resp_with_ref = session.head(
                req.url,
                max_retries=1,
                timeout=10,
                extra_headers={
                    'Referer': base_url,
                    'Origin': URLUtils.get_base_url(base_url),
                }
            )

            status_no_ref = resp_no_ref.status_code if resp_no_ref else 0
            status_with_ref = resp_with_ref.status_code if resp_with_ref else 0

            test_results.append({
                'url': req.url[:100],
                'media_type': req.media_type,
                'status_without_referer': status_no_ref,
                'status_with_referer': status_with_ref,
                'referer_required': (
                    status_no_ref in (403, 401, 0) and
                    status_with_ref == 200
                ),
            })

        referer_required = any(t['referer_required'] for t in test_results)

        self.add_finding(
            category='info',
            subcategory='referer_requirement',
            data={
                'referer_required': referer_required,
                'required_referer': base_url if referer_required else None,
                'required_origin': URLUtils.get_base_url(base_url) if referer_required else None,
                'test_results': test_results,
            },
            confidence=0.95,
            source='referer requirement test',
        )

    def _reconstruct_session_flow(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Reconstruct the session flow needed to access media"""
        if not capture:
            return

        # Important requests sorted by time
        important_reqs = sorted(
            [r for r in capture.requests
             if r.is_media or r.is_api or r.resource_type == 'document'],
            key=lambda r: r.timestamp
        )

        flow_steps = []
        for i, req in enumerate(important_reqs[:30]):
            step = {
                'order': i + 1,
                'timestamp_offset': (
                    req.timestamp - important_reqs[0].timestamp
                    if important_reqs else 0
                ),
                'method': req.method,
                'url': req.url,
                'type': (
                    'page' if req.resource_type == 'document' else
                    'api' if req.is_api else
                    'media'
                ),
                'status': req.status,
                'content_type': req.content_type,
                'sets_cookie': bool(
                    req.response_headers.get('set-cookie')
                ),
            }
            flow_steps.append(step)

        if flow_steps:
            self.add_finding(
                category='info',
                subcategory='session_flow',
                data={
                    'total_steps': len(flow_steps),
                    'flow': flow_steps,
                    'reproduction_note': (
                        'Follow these steps in order to reproduce '
                        'the session and access media files.'
                    ),
                },
                confidence=1.0,
                source='session flow reconstruction',
            )

    def _analyze_cors(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Analyze CORS policies on media endpoints"""
        if not capture:
            return

        cors_info = {}
        for req in capture.requests:
            if not req.is_media:
                continue

            rh = req.response_headers
            acao = rh.get('access-control-allow-origin', '')
            if acao:
                domain = URLUtils.get_domain(req.url)
                cors_info[domain] = {
                    'allow_origin': acao,
                    'allow_methods': rh.get('access-control-allow-methods', ''),
                    'allow_headers': rh.get('access-control-allow-headers', ''),
                    'allow_credentials': rh.get('access-control-allow-credentials', ''),
                    'sample_url': req.url,
                }

        if cors_info:
            self.add_finding(
                category='info',
                subcategory='cors_policy',
                data={'domains': cors_info},
                confidence=1.0,
                source='CORS analysis',
            )

    def _detect_authentication(
        self, capture: BrowserCaptureResult, base_url: str
    ):
        """Detect if authentication is required"""
        if not capture:
            return

        auth_indicators = {
            'has_login_form': False,
            'has_auth_headers': False,
            'has_401_responses': False,
            'has_403_responses': False,
            'auth_header_type': None,
        }

        # Check for login forms
        if capture.page_html:
            html_lower = capture.page_html.lower()
            auth_indicators['has_login_form'] = any(
                ind in html_lower for ind in [
                    'type="password"', "type='password'",
                    'login', 'signin', 'sign-in', 'log-in',
                ]
            )

        # Check for auth headers in requests
        for req in capture.requests:
            auth_header = req.headers.get('authorization', '') or req.headers.get('Authorization', '')
            if auth_header:
                auth_indicators['has_auth_headers'] = True
                if auth_header.lower().startswith('bearer'):
                    auth_indicators['auth_header_type'] = 'Bearer Token'
                elif auth_header.lower().startswith('basic'):
                    auth_indicators['auth_header_type'] = 'Basic Auth'
                else:
                    auth_indicators['auth_header_type'] = 'Other'
                break

            if req.status == 401:
                auth_indicators['has_401_responses'] = True
            elif req.status == 403:
                auth_indicators['has_403_responses'] = True

        self.add_finding(
            category='info',
            subcategory='authentication',
            data=auth_indicators,
            confidence=1.0,
            source='authentication detection',
        )

    def _detect_rate_limiting(self, capture: BrowserCaptureResult):
        """Detect rate limiting signals"""
        if not capture:
            return

        rate_limit_info = {
            'detected': False,
            'headers': {},
            'http_429_count': 0,
        }

        for req in capture.requests:
            rh = req.response_headers

            # Common rate limit headers
            rl_headers = {
                'x-ratelimit-limit': rh.get('x-ratelimit-limit', ''),
                'x-ratelimit-remaining': rh.get('x-ratelimit-remaining', ''),
                'x-ratelimit-reset': rh.get('x-ratelimit-reset', ''),
                'retry-after': rh.get('retry-after', ''),
                'x-rate-limit': rh.get('x-rate-limit', ''),
            }

            for k, v in rl_headers.items():
                if v:
                    rate_limit_info['detected'] = True
                    rate_limit_info['headers'][k] = v

            if req.status == 429:
                rate_limit_info['http_429_count'] += 1
                rate_limit_info['detected'] = True

        if rate_limit_info['detected']:
            self.add_finding(
                category='info',
                subcategory='rate_limiting',
                data=rate_limit_info,
                confidence=1.0,
                source='rate limit detection',
            )