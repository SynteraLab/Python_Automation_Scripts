"""
session.py — HTTP Session Manager
Untuk request tanpa browser (lebih cepat, dipakai Layer 1, 3, 4)
Dengan request tracking & cookie persistence
"""

import requests
import time
import random
import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from urllib.parse import urlparse

from config import DiagnosticConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class TrackedResponse:
    """Response yang ditrack"""
    url: str
    method: str
    status_code: int
    headers: Dict[str, str]
    content_type: str
    body: Optional[str]        # text body
    body_bytes: Optional[bytes]  # raw bytes
    size: int
    elapsed: float             # waktu request (detik)
    redirects: List[str]       # chain of redirects
    error: Optional[str] = None


class SessionManager:
    """
    HTTP Session dengan:
    - Request tracking
    - Auto retry
    - Stealth headers
    - Cookie persistence
    - Rate limiting
    """
    
    def __init__(self, config: DiagnosticConfig = None):
        self.config = config or DEFAULT_CONFIG
        self.session = requests.Session()
        self.request_history: List[TrackedResponse] = []
        self._request_count = 0
        self._last_request_time = 0
        
        # Set default headers
        ua = random.choice(self.config.browser.user_agents)
        self.session.headers.update({
            'User-Agent': ua,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        })
        
        # Proxy
        if self.config.proxy:
            self.session.proxies = {
                'http': self.config.proxy,
                'https': self.config.proxy,
            }
        
        # SSL verify (disable jika perlu)
        self.session.verify = True
    
    def get(self, url: str, **kwargs) -> Optional[TrackedResponse]:
        """GET request dengan tracking"""
        return self._request('GET', url, **kwargs)
    
    def post(self, url: str, **kwargs) -> Optional[TrackedResponse]:
        """POST request dengan tracking"""
        return self._request('POST', url, **kwargs)
    
    def head(self, url: str, **kwargs) -> Optional[TrackedResponse]:
        """HEAD request (cek tanpa download body)"""
        return self._request('HEAD', url, **kwargs)
    
    def _request(
        self, 
        method: str, 
        url: str,
        max_retries: int = 3,
        timeout: int = 30,
        capture_body: bool = True,
        max_body_size: int = None,
        extra_headers: Dict = None,
        **kwargs
    ) -> Optional[TrackedResponse]:
        """Execute request dengan retry & tracking"""
        
        # Rate limiting
        self._rate_limit()
        
        # Extra headers
        if extra_headers:
            kwargs.setdefault('headers', {}).update(extra_headers)
        
        # Retry loop
        for attempt in range(max_retries):
            try:
                start = time.time()
                
                response = self.session.request(
                    method,
                    url,
                    timeout=timeout,
                    allow_redirects=True,
                    **kwargs
                )
                
                elapsed = time.time() - start
                
                # Track redirects
                redirects = [r.url for r in response.history]
                
                # Body
                body = None
                body_bytes = None
                max_size = max_body_size or self.config.scan.max_response_body_size
                
                if capture_body and method != 'HEAD':
                    content_length = int(
                        response.headers.get('content-length', 0)
                    )
                    if content_length <= max_size or content_length == 0:
                        body_bytes = response.content
                        if len(body_bytes) <= max_size:
                            try:
                                body = body_bytes.decode(
                                    'utf-8', errors='replace'
                                )
                            except Exception:
                                body = None
                
                tracked = TrackedResponse(
                    url=response.url,
                    method=method,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    content_type=response.headers.get('content-type', ''),
                    body=body,
                    body_bytes=body_bytes,
                    size=len(response.content) if response.content else 0,
                    elapsed=elapsed,
                    redirects=redirects,
                )
                
                self.request_history.append(tracked)
                self._request_count += 1
                
                logger.debug(
                    f"📡 {method} {url} → {response.status_code} "
                    f"({elapsed:.2f}s, {tracked.size} bytes)"
                )
                
                return tracked
                
            except requests.Timeout:
                logger.warning(
                    f"⏱️ Timeout {method} {url} "
                    f"(attempt {attempt+1}/{max_retries})"
                )
                if attempt == max_retries - 1:
                    return TrackedResponse(
                        url=url, method=method, status_code=0,
                        headers={}, content_type='', body=None,
                        body_bytes=None, size=0, elapsed=0,
                        redirects=[], error='timeout'
                    )
                    
            except requests.ConnectionError as e:
                logger.warning(
                    f"🔌 Connection error {url}: {e} "
                    f"(attempt {attempt+1}/{max_retries})"
                )
                if attempt == max_retries - 1:
                    return TrackedResponse(
                        url=url, method=method, status_code=0,
                        headers={}, content_type='', body=None,
                        body_bytes=None, size=0, elapsed=0,
                        redirects=[], error=str(e)
                    )
                time.sleep(2 ** attempt)  # exponential backoff
                
            except Exception as e:
                logger.error(f"❌ Request error {url}: {e}")
                return TrackedResponse(
                    url=url, method=method, status_code=0,
                    headers={}, content_type='', body=None,
                    body_bytes=None, size=0, elapsed=0,
                    redirects=[], error=str(e)
                )
        
        return None
    
    def _rate_limit(self):
        """Rate limiting antar request"""
        now = time.time()
        elapsed = now - self._last_request_time
        min_delay = self.config.browser.random_delay_min
        
        if elapsed < min_delay:
            sleep_time = min_delay - elapsed + random.uniform(0, 0.5)
            time.sleep(sleep_time)
        
        self._last_request_time = time.time()
    
    def check_url_accessible(self, url: str) -> Dict:
        """
        Quick check: apakah URL bisa diakses?
        Return dict dengan status & info.
        """
        resp = self.head(url, max_retries=1, timeout=10)
        if not resp:
            return {'accessible': False, 'error': 'no response'}
        
        return {
            'accessible': resp.status_code in range(200, 400),
            'status_code': resp.status_code,
            'content_type': resp.content_type,
            'size': int(resp.headers.get('content-length', 0)),
            'redirected_to': resp.redirects[-1] if resp.redirects else None,
            'server': resp.headers.get('server', ''),
            'requires_auth': resp.status_code in (401, 403),
        }
    
    def download_text(self, url: str) -> Optional[str]:
        """Download dan return text content"""
        resp = self.get(url, capture_body=True)
        if resp and resp.status_code == 200:
            return resp.body
        return None
    
    def get_cookies_dict(self) -> Dict[str, str]:
        """Return current cookies sebagai dict"""
        return dict(self.session.cookies)
    
    def set_referer(self, referer: str):
        """Set Referer header"""
        self.session.headers['Referer'] = referer
    
    def set_origin(self, origin: str):
        """Set Origin header"""
        self.session.headers['Origin'] = origin
    
    def copy_cookies_from_browser(self, browser_cookies: List[Dict]):
        """Copy cookies dari BrowserEngine ke session"""
        for cookie in browser_cookies:
            self.session.cookies.set(
                cookie.get('name', ''),
                cookie.get('value', ''),
                domain=cookie.get('domain', ''),
                path=cookie.get('path', '/'),
            )
        logger.info(
            f"🍪 Copied {len(browser_cookies)} cookies from browser"
        )
    
    def get_request_summary(self) -> Dict:
        """Summary dari semua request yang dibuat"""
        return {
            'total_requests': self._request_count,
            'successful': sum(
                1 for r in self.request_history 
                if r.status_code in range(200, 300)
            ),
            'failed': sum(
                1 for r in self.request_history 
                if r.error or r.status_code >= 400
            ),
            'total_bytes': sum(
                r.size for r in self.request_history
            ),
            'domains_contacted': list(set(
                urlparse(r.url).netloc 
                for r in self.request_history
            )),
        }