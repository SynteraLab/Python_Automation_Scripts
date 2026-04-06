"""
url_utils.py — Utility untuk manipulasi & analisis URL
"""

from urllib.parse import (
    urljoin, urlparse, urlunparse, parse_qs, 
    urlencode, unquote, quote
)
from typing import Optional, Dict, List, Tuple
import re
import hashlib
import time


class URLUtils:
    """Kumpulan utility untuk URL manipulation & analysis"""
    
    @staticmethod
    def normalize(url: str, base_url: str = "") -> str:
        """
        Normalisasi URL:
        - Relative → Absolute
        - Hapus fragment (#)
        - Decode percent-encoding
        """
        if not url or url.startswith(('data:', 'blob:', 'javascript:', 'mailto:')):
            return ""
        
        # Convert relative ke absolute
        if base_url and not url.startswith(('http://', 'https://', '//')):
            url = urljoin(base_url, url)
        elif url.startswith('//'):
            url = 'https:' + url
        
        # Parse & rebuild tanpa fragment
        parsed = urlparse(url)
        clean = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            ''  # hapus fragment
        ))
        
        return clean
    
    @staticmethod
    def get_domain(url: str) -> str:
        """Extract domain dari URL"""
        parsed = urlparse(url)
        return parsed.netloc or ""
    
    @staticmethod
    def get_base_url(url: str) -> str:
        """Dapatkan base URL (scheme + domain)"""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    
    @staticmethod
    def get_extension(url: str) -> str:
        """Extract file extension dari URL"""
        path = urlparse(url).path.lower()
        # Hapus trailing slash
        path = path.rstrip('/')
        if '.' in path.split('/')[-1]:
            return '.' + path.split('/')[-1].rsplit('.', 1)[-1]
        return ""
    
    @staticmethod
    def get_query_params(url: str) -> Dict[str, List[str]]:
        """Parse query parameters"""
        return parse_qs(urlparse(url).query)
    
    @staticmethod
    def is_same_domain(url1: str, url2: str) -> bool:
        """Cek apakah 2 URL dari domain yang sama"""
        return URLUtils.get_domain(url1) == URLUtils.get_domain(url2)
    
    @staticmethod
    def is_absolute(url: str) -> bool:
        """Cek apakah URL absolute"""
        return url.startswith(('http://', 'https://'))
    
    @staticmethod
    def has_media_extension(url: str) -> bool:
        """Quick check apakah URL punya ekstensi media"""
        from .media_types import MediaTypes
        return MediaTypes.is_media_url(url)
    
    @staticmethod
    def detect_url_expiry(url: str) -> Dict:
        """
        Deteksi apakah URL memiliki token expiry.
        Cari parameter seperti: exp, expires, token, sig, hash, key, t
        """
        params = URLUtils.get_query_params(url)
        
        expiry_indicators = {
            'timestamp_params': ['exp', 'expires', 'e', 't', 'timestamp', 
                                  'valid_until', 'deadline', 'ttl'],
            'token_params': ['token', 'tok', 'key', 'k', 'auth', 
                             'access_token', 'jwt'],
            'signature_params': ['sig', 'signature', 'hash', 'hmac', 
                                  'sign', 'h', 'md5', 'sha'],
            'policy_params': ['policy', 'Policy'],
        }
        
        result = {
            'has_expiry': False,
            'expiry_params': {},
            'token_params': {},
            'signature_params': {},
            'estimated_type': 'static',  # static | signed | tokenized
        }
        
        for category, param_names in expiry_indicators.items():
            for param in param_names:
                if param in params:
                    result[category] = {param: params[param][0]}
                    result['has_expiry'] = True
        
        # Determine URL type
        if result.get('signature_params') or result.get('token_params'):
            result['estimated_type'] = 'signed'
        if result.get('timestamp_params'):
            # Coba deteksi apakah timestamp unix
            for vals in result.get('timestamp_params', {}).values():
                try:
                    ts = int(vals) if isinstance(vals, str) else int(vals[0])
                    if 1_000_000_000 < ts < 2_000_000_000:
                        remaining = ts - int(time.time())
                        result['estimated_expiry_seconds'] = max(remaining, 0)
                        result['estimated_type'] = 'signed'
                except (ValueError, TypeError):
                    pass
        
        return result
    
    @staticmethod
    def extract_path_pattern(urls: List[str]) -> Optional[str]:
        """
        Dari beberapa URL yang mirip, extract pattern-nya.
        
        Input: [
            "https://cdn.example.com/video/123/720p.mp4",
            "https://cdn.example.com/video/456/720p.mp4",
        ]
        Output: "https://cdn.example.com/video/{id}/720p.mp4"
        """
        if len(urls) < 2:
            return urls[0] if urls else None
        
        parsed = [urlparse(u) for u in urls]
        
        # Pastikan domain sama
        domains = set(p.netloc for p in parsed)
        if len(domains) > 1:
            return None
        
        # Compare paths segment by segment
        paths = [p.path.split('/') for p in parsed]
        min_len = min(len(p) for p in paths)
        
        pattern_parts = []
        for i in range(min_len):
            segments = set(p[i] for p in paths if i < len(p))
            if len(segments) == 1:
                pattern_parts.append(segments.pop())
            else:
                # Cek apakah semua numeric (kemungkinan ID)
                if all(s.isdigit() for s in segments):
                    pattern_parts.append('{id}')
                else:
                    pattern_parts.append('{param}')
        
        base = f"{parsed[0].scheme}://{parsed[0].netloc}"
        return base + '/'.join(pattern_parts)
    
    @staticmethod
    def url_hash(url: str) -> str:
        """Generate short hash dari URL (untuk deduplication)"""
        return hashlib.md5(url.encode()).hexdigest()[:12]