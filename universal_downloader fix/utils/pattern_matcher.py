"""
pattern_matcher.py — Regex pattern library untuk menemukan media URLs
Dipakai oleh semua layer sebagai "mata" pencarian
"""

import re
from typing import List, Dict, Set, Tuple
from dataclasses import dataclass, field


@dataclass
class MatchResult:
    """Hasil match dari pattern"""
    url: str
    context: str          # potongan text di sekitar match
    pattern_name: str     # nama pattern yang menemukan
    confidence: float     # 0.0 - 1.0


class PatternMatcher:
    """
    Library regex patterns untuk menemukan URL media 
    tersembunyi di HTML, JavaScript, CSS, dan JSON.
    """
    
    # ══════════════════════════════════════════
    #  URL PATTERNS
    # ══════════════════════════════════════════
    
    # URL absolut (http/https)
    ABSOLUTE_URL = re.compile(
        r'(?:https?:)?//[^\s"\'<>\)\}\]\\,;`]+',
        re.IGNORECASE
    )
    
    # URL relatif ke media files
    RELATIVE_MEDIA_URL = re.compile(
        r'''["\']'''                          # opening quote
        r'('                                   # capture group
        r'/[^\s"\'<>]*?'                      # path starting with /
        r'\.'                                  # dot before extension
        r'(?:mp4|webm|mkv|avi|mov|flv'        # video
        r'|mp3|wav|ogg|aac|flac|m4a'          # audio
        r'|m3u8|mpd|ts|m4s'                   # streaming
        r'|jpg|jpeg|png|gif|webp|svg|avif'    # image
        r'|pdf|doc|docx|xls|xlsx|ppt'         # document
        r'|zip|rar|7z'                         # archive
        r')'
        r'(?:\?[^\s"\'<>]*)?'                 # optional query string
        r')'                                   # end capture
        r'''["\']''',                          # closing quote
        re.IGNORECASE
    )
    
    # ══════════════════════════════════════════
    #  STREAMING PATTERNS
    # ══════════════════════════════════════════
    
    # HLS manifest URL
    HLS_URL = re.compile(
        r'(?:https?:)?//[^\s"\'<>\)]+\.m3u8(?:\?[^\s"\'<>\)]*)?',
        re.IGNORECASE
    )
    
    # DASH manifest URL
    DASH_URL = re.compile(
        r'(?:https?:)?//[^\s"\'<>\)]+\.mpd(?:\?[^\s"\'<>\)]*)?',
        re.IGNORECASE
    )
    
    # Generic streaming source assignment in JS
    STREAM_SOURCE_JS = re.compile(
        r'''(?:'''
        r'\.src\s*[\(=]'                  # .src = or .src(
        r'|\.loadSource\s*\('             # hls.loadSource(
        r'|\.load\s*\('                   # player.load(
        r'|source\s*[:=]\s*'              # source: or source =
        r'|file\s*[:=]\s*'               # file:
        r'|url\s*[:=]\s*'                # url:
        r'|videoUrl\s*[:=]\s*'           # videoUrl:
        r'|streamUrl\s*[:=]\s*'          # streamUrl:
        r'|manifestUrl\s*[:=]\s*'        # manifestUrl:
        r'|mediaUrl\s*[:=]\s*'           # mediaUrl:
        r'|videoSrc\s*[:=]\s*'           # videoSrc:
        r''')\s*["\']([^"\']+)["\']''',
        re.IGNORECASE
    )
    
    # ══════════════════════════════════════════
    #  JAVASCRIPT CONFIG PATTERNS
    # ══════════════════════════════════════════
    
    # JSON-like config objects containing media URLs
    JS_CONFIG_OBJECT = re.compile(
        r'(?:config|options|settings|params|data|setup|init)'
        r'\s*(?:[:=]|\()\s*(\{.+?\})',
        re.IGNORECASE | re.DOTALL
    )
    
    # Variable assignment with URL
    JS_VAR_URL = re.compile(
        r'(?:var|let|const|this\.)\s*'
        r'(\w*(?:url|src|source|path|file|media|video|audio|image|stream|link|endpoint|api)\w*)'
        r'\s*=\s*["\']([^"\']+)["\']',
        re.IGNORECASE
    )
    
    # Template literal with URL
    JS_TEMPLATE_URL = re.compile(
        r'`([^`]*(?:https?://|/api/|/media/|/video/|/stream/)[^`]*)`',
        re.IGNORECASE
    )
    
    # Base64 encoded strings (bisa berisi URL)
    BASE64_STRING = re.compile(
        r'''(?:atob|decode|base64)\s*\(\s*["\']([A-Za-z0-9+/=]{20,})["\']''',
        re.IGNORECASE
    )
    
    # ══════════════════════════════════════════
    #  API ENDPOINT PATTERNS
    # ══════════════════════════════════════════
    
    # Fetch/XHR calls in JavaScript
    FETCH_CALL = re.compile(
        r'fetch\s*\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE
    )
    
    AXIOS_CALL = re.compile(
        r'axios\s*\.\s*(?:get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE
    )
    
    XHR_OPEN = re.compile(
        r'\.open\s*\(\s*["\'](?:GET|POST|PUT)["\']'
        r'\s*,\s*["\']([^"\']+)["\']',
        re.IGNORECASE
    )
    
    # GraphQL endpoint
    GRAPHQL_ENDPOINT = re.compile(
        r'["\']([^"\']*(?:graphql|gql)[^"\']*)["\']',
        re.IGNORECASE
    )
    
    # API base URL pattern
    API_BASE_URL = re.compile(
        r'''(?:'''
        r'apiUrl|apiBase|baseUrl|baseURL|API_URL|API_BASE'
        r'|api_url|api_base|base_url|apiEndpoint'
        r'|API_ENDPOINT|serverUrl|SERVER_URL'
        r''')\s*[:=]\s*["\']([^"\']+)["\']''',
        re.IGNORECASE
    )
    
    # ══════════════════════════════════════════
    #  HTML ATTRIBUTE PATTERNS
    # ══════════════════════════════════════════
    
    # Lazy loading attributes
    LAZY_LOAD_ATTRS = [
        'data-src', 'data-original', 'data-lazy-src', 'data-lazy',
        'data-srcset', 'data-bg', 'data-background', 'data-image',
        'data-url', 'data-video', 'data-poster', 'data-thumb',
        'data-full', 'data-hi-res', 'data-large', 'data-medium',
        'data-original-src', 'data-echo', 'data-unveil',
        'data-layzr', 'data-blazy', 'data-src-retina',
        'loading-src', 'data-aload', 'data-adaptive-img',
    ]
    
    # CSS url() pattern
    CSS_URL = re.compile(
        r'url\s*\(\s*["\']?\s*([^"\')\s]+)\s*["\']?\s*\)',
        re.IGNORECASE
    )
    
    # srcset attribute parsing
    SRCSET_ENTRY = re.compile(
        r'(\S+)\s+(\d+[wx])',
        re.IGNORECASE
    )
    
    # ══════════════════════════════════════════
    #  CONTACT / INFO PATTERNS
    # ══════════════════════════════════════════
    
    EMAIL = re.compile(
        r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}'
    )
    
    PHONE = re.compile(
        r'(?:\+?\d{1,4}[-.\s]?)?\(?\d{1,4}\)?[-.\s]?\d{1,4}[-.\s]?\d{1,9}'
    )
    
    # ══════════════════════════════════════════
    #  MASTER SCAN METHOD
    # ══════════════════════════════════════════
    
    @classmethod
    def scan_text(cls, text: str, base_url: str = "") -> Dict[str, List[MatchResult]]:
        """
        Jalankan SEMUA pattern terhadap text.
        Return dict berisi kategori → list match results.
        """
        from .url_utils import URLUtils
        
        results = {
            'absolute_urls': [],
            'relative_media_urls': [],
            'hls_urls': [],
            'dash_urls': [],
            'stream_sources': [],
            'js_variables': [],
            'js_configs': [],
            'fetch_calls': [],
            'api_endpoints': [],
            'css_urls': [],
            'base64_encoded': [],
            'template_literals': [],
        }
        
        def add_match(category: str, url: str, pattern_name: str, 
                       confidence: float = 0.8, raw_text: str = ""):
            """Helper untuk tambah match result"""
            normalized = URLUtils.normalize(url, base_url)
            if normalized and len(normalized) > 10:
                results[category].append(MatchResult(
                    url=normalized,
                    context=raw_text[:200] if raw_text else "",
                    pattern_name=pattern_name,
                    confidence=confidence
                ))
        
        # 1. Absolute URLs
        for m in cls.ABSOLUTE_URL.finditer(text):
            url = m.group(0).rstrip('.,;:"\'})]')
            add_match('absolute_urls', url, 'ABSOLUTE_URL', 0.9)
        
        # 2. Relative media URLs
        for m in cls.RELATIVE_MEDIA_URL.finditer(text):
            add_match('relative_media_urls', m.group(1), 'RELATIVE_MEDIA_URL', 0.95)
        
        # 3. HLS URLs
        for m in cls.HLS_URL.finditer(text):
            url = m.group(0).rstrip('.,;:"\'})]')
            add_match('hls_urls', url, 'HLS_URL', 1.0)
        
        # 4. DASH URLs
        for m in cls.DASH_URL.finditer(text):
            url = m.group(0).rstrip('.,;:"\'})]')
            add_match('dash_urls', url, 'DASH_URL', 1.0)
        
        # 5. Stream sources in JS
        for m in cls.STREAM_SOURCE_JS.finditer(text):
            add_match('stream_sources', m.group(1), 'STREAM_SOURCE_JS', 0.9,
                       text[max(0, m.start()-50):m.end()+50])
        
        # 6. JS variable URLs
        for m in cls.JS_VAR_URL.finditer(text):
            var_name, url = m.group(1), m.group(2)
            add_match('js_variables', url, f'JS_VAR ({var_name})', 0.85,
                       text[max(0, m.start()-30):m.end()+30])
        
        # 7. Fetch/Axios/XHR calls
        for pattern, name in [(cls.FETCH_CALL, 'fetch'), 
                                (cls.AXIOS_CALL, 'axios'),
                                (cls.XHR_OPEN, 'xhr')]:
            for m in pattern.finditer(text):
                add_match('fetch_calls', m.group(1), f'JS_{name.upper()}', 0.9)
        
        # 8. API endpoints
        for m in cls.API_BASE_URL.finditer(text):
            add_match('api_endpoints', m.group(1), 'API_BASE_URL', 0.85)
        
        for m in cls.GRAPHQL_ENDPOINT.finditer(text):
            add_match('api_endpoints', m.group(1), 'GRAPHQL', 0.8)
        
        # 9. CSS url()
        for m in cls.CSS_URL.finditer(text):
            url = m.group(1)
            if not url.startswith('data:'):
                add_match('css_urls', url, 'CSS_URL', 0.7)
        
        # 10. Base64 encoded
        import base64
        for m in cls.BASE64_STRING.finditer(text):
            try:
                decoded = base64.b64decode(m.group(1)).decode('utf-8', errors='ignore')
                if 'http' in decoded or '/' in decoded:
                    add_match('base64_encoded', decoded, 'BASE64_DECODE', 0.7)
            except Exception:
                pass
        
        # 11. Template literals
        for m in cls.JS_TEMPLATE_URL.finditer(text):
            add_match('template_literals', m.group(1), 'JS_TEMPLATE', 0.6)
        
        return results
    
    @classmethod
    def extract_all_urls(cls, text: str, base_url: str = "") -> Set[str]:
        """Extract semua URL unik dari text (flat set)"""
        scan_results = cls.scan_text(text, base_url)
        all_urls = set()
        for category_matches in scan_results.values():
            for match in category_matches:
                all_urls.add(match.url)
        return all_urls
    
    @classmethod
    def find_media_urls_only(cls, text: str, base_url: str = "") -> List[MatchResult]:
        """Extract hanya URL yang mengarah ke media files"""
        from .media_types import MediaTypes
        
        all_results = cls.scan_text(text, base_url)
        media_matches = []
        seen = set()
        
        for category_matches in all_results.values():
            for match in category_matches:
                if match.url not in seen and MediaTypes.is_media_url(match.url):
                    media_matches.append(match)
                    seen.add(match.url)
        
        # Sort by confidence (tertinggi dulu)
        media_matches.sort(key=lambda m: m.confidence, reverse=True)
        return media_matches