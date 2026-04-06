"""
layer_03_js_ast.py — JavaScript AST Analysis (Level 3)

Deep analysis JavaScript files:
- Download semua JS files
- Parse ke AST (Abstract Syntax Tree) dengan esprima
- Cari variable assignments berisi URL
- Cari function calls (fetch, axios, xhr)
- Cari config objects (player setup, API config)
- Cari string concatenation yang bentuk URL
- Cari base64 encoded strings
- Identify video player initialization patterns
"""

import re
import json
import logging
import base64
from typing import Dict, List, Set, Optional, Any
from urllib.parse import urljoin

try:
    import esprima
    HAS_ESPRIMA = True
except ImportError:
    HAS_ESPRIMA = False

from layers.base import BaseLayer
from core.browser import BrowserCaptureResult
from core.session import SessionManager
from utils.media_types import MediaTypes, VIDEO_PLAYERS
from utils.url_utils import URLUtils
from utils.pattern_matcher import PatternMatcher

logger = logging.getLogger(__name__)


class JSAstAnalysisLayer(BaseLayer):
    
    LAYER_NAME = "layer_03_js_ast"
    LAYER_DESCRIPTION = "JavaScript AST Deep Analysis"
    
    def __init__(self, config=None):
        super().__init__(config)
        self._js_contents: Dict[str, str] = {}  # url → content
    
    async def execute(self, url, recon, capture, session):
        """Download & analisis semua JavaScript files"""
        
        seen: Set[str] = set()
        base_url = url
        
        # ── 1. Collect JS URLs ──
        js_urls = set(recon.js_file_urls) if recon else set()
        
        # Tambahkan JS URLs dari browser capture
        if capture:
            for req in capture.requests:
                if req.resource_type == 'script' and req.url not in js_urls:
                    js_urls.add(req.url)
        
        logger.info(f"      Found {len(js_urls)} JS files to analyze")
        
        # ── 2. Download JS files ──
        max_files = self.config.scan.max_js_files
        max_size = self.config.scan.max_js_file_size
        
        js_list = sorted(js_urls)[:max_files]
        
        for i, js_url in enumerate(js_list):
            logger.debug(f"      Downloading JS [{i+1}/{len(js_list)}]: {js_url[:80]}")
            
            # Cek apakah ada di capture response body
            content = self._get_js_from_capture(capture, js_url)
            
            if not content:
                resp = session.get(
                    js_url, 
                    timeout=15,
                    max_body_size=max_size,
                    extra_headers={'Referer': url}
                )
                if resp and resp.body:
                    content = resp.body
            
            if content and len(content) <= max_size:
                self._js_contents[js_url] = content
        
        logger.info(f"      Downloaded {len(self._js_contents)} JS files")
        
        # ── 3. Analyze setiap JS file ──
        for js_url, content in self._js_contents.items():
            
            # Level 2: Regex analysis (selalu jalan)
            self._regex_analysis(content, base_url, js_url, seen)
            
            # Level 3: AST analysis (jika esprima tersedia)
            if HAS_ESPRIMA and self.config.scan.ast_parsing:
                self._ast_analysis(content, base_url, js_url, seen)
            
            # Video player config detection
            self._detect_player_config(content, base_url, js_url, seen)
        
        # ── 4. Analyze inline scripts (dari capture) ──
        if capture and capture.page_html:
            self._analyze_inline_scripts_ast(
                capture.page_html, base_url, seen
            )
    
    def _get_js_from_capture(
        self, capture: BrowserCaptureResult, js_url: str
    ) -> Optional[str]:
        """Cek apakah JS content ada di browser capture"""
        if not capture:
            return None
        
        for req in capture.requests:
            if req.url == js_url and req.response_body:
                return req.response_body
        return None
    
    # ══════════════════════════════════════════
    #  LEVEL 2: REGEX ANALYSIS
    # ══════════════════════════════════════════
    
    def _regex_analysis(
        self, content: str, base_url: str, 
        source_url: str, seen: Set[str]
    ):
        """Regex-based URL extraction (Level 2)"""
        
        scan_results = PatternMatcher.scan_text(content, base_url)
        
        for category, matches in scan_results.items():
            for match in matches:
                if match.url in seen:
                    continue
                seen.add(match.url)
                
                is_media = MediaTypes.is_media_url(match.url)
                is_streaming = MediaTypes.is_streaming_url(match.url)
                
                if is_streaming:
                    self.add_finding(
                        category='streaming',
                        subcategory='manifest',
                        url=match.url,
                        data={'source_js': source_url},
                        confidence=match.confidence,
                        source=f'JS regex [{match.pattern_name}]',
                        context=match.context,
                    )
                elif is_media:
                    self.add_finding(
                        category='media',
                        subcategory=MediaTypes.identify_type(url=match.url),
                        url=match.url,
                        data={'source_js': source_url},
                        confidence=match.confidence,
                        source=f'JS regex [{match.pattern_name}]',
                        context=match.context,
                    )
                elif category in ('fetch_calls', 'api_endpoints'):
                    self.add_finding(
                        category='api',
                        subcategory='js_endpoint',
                        url=match.url,
                        data={'source_js': source_url},
                        confidence=match.confidence,
                        source=f'JS regex [{match.pattern_name}]',
                        context=match.context,
                    )
        
        # ── Base64 strings ──
        self._decode_base64_strings(content, base_url, source_url, seen)
    
    def _decode_base64_strings(
        self, content, base_url, source_url, seen
    ):
        """Decode base64 strings yang mungkin berisi URL"""
        b64_pattern = re.compile(
            r'''(?:atob|decode|base64|b64)\s*\(\s*['"]([A-Za-z0-9+/=]{16,})['"]''',
            re.IGNORECASE
        )
        
        for match in b64_pattern.finditer(content):
            try:
                decoded = base64.b64decode(match.group(1)).decode('utf-8', errors='ignore')
                if 'http' in decoded or '/' in decoded:
                    full_url = URLUtils.normalize(decoded.strip(), base_url)
                    if full_url and full_url not in seen:
                        seen.add(full_url)
                        media_type = MediaTypes.identify_type(url=full_url)
                        
                        self.add_finding(
                            category='media' if media_type != 'unknown' else 'api',
                            subcategory=media_type if media_type != 'unknown' else 'hidden_url',
                            url=full_url,
                            data={
                                'source_js': source_url,
                                'encoding': 'base64',
                                'original': match.group(1)[:100],
                            },
                            confidence=0.85,
                            source='base64 decode in JS',
                            context=decoded[:200],
                        )
            except Exception:
                pass
    
    # ══════════════════════════════════════════
    #  LEVEL 3: AST ANALYSIS
    # ══════════════════════════════════════════
    
    def _ast_analysis(
        self, content: str, base_url: str,
        source_url: str, seen: Set[str]
    ):
        """AST-based deep analysis menggunakan esprima"""
        
        try:
            # Parse to AST
            tree = esprima.parseScript(
                content, 
                tolerant=True,
                jsx=False,
                range=True
            )
        except Exception as e:
            # Coba sebagai module
            try:
                tree = esprima.parseModule(
                    content,
                    tolerant=True,
                    jsx=False,
                    range=True
                )
            except Exception:
                logger.debug(f"      AST parse failed for {source_url[:60]}: {e}")
                return
        
        # Walk AST tree
        self._walk_ast(tree, content, base_url, source_url, seen)
    
    def _walk_ast(self, node, source, base_url, source_url, seen, depth=0):
        """Recursively walk AST tree"""
        if depth > 30 or not node:
            return
        
        if not hasattr(node, 'type'):
            # Bisa berupa list
            if isinstance(node, list):
                for item in node:
                    self._walk_ast(item, source, base_url, source_url, seen, depth)
            return
        
        node_type = node.type
        
        # ── Variable declarations ──
        if node_type == 'VariableDeclaration':
            for declarator in getattr(node, 'declarations', []):
                self._handle_variable(
                    declarator, source, base_url, source_url, seen
                )
        
        # ── Assignment expressions ──
        elif node_type == 'AssignmentExpression':
            self._handle_assignment(
                node, source, base_url, source_url, seen
            )
        
        # ── Call expressions (fetch, axios, etc) ──
        elif node_type == 'CallExpression':
            self._handle_call(
                node, source, base_url, source_url, seen
            )
        
        # ── Object expressions (config objects) ──
        elif node_type == 'ObjectExpression':
            self._handle_object(
                node, source, base_url, source_url, seen
            )
        
        # ── String literals (catch-all) ──
        elif node_type == 'Literal' and isinstance(getattr(node, 'value', None), str):
            self._handle_string_literal(
                node, base_url, source_url, seen
            )
        
        # ── Template literals ──
        elif node_type == 'TemplateLiteral':
            self._handle_template_literal(
                node, source, base_url, source_url, seen
            )
        
        # Recurse into child nodes
        for attr_name in dir(node):
            if attr_name.startswith('_'):
                continue
            attr = getattr(node, attr_name, None)
            if attr is None:
                continue
            
            if hasattr(attr, 'type'):
                self._walk_ast(attr, source, base_url, source_url, seen, depth + 1)
            elif isinstance(attr, list):
                for item in attr:
                    if hasattr(item, 'type'):
                        self._walk_ast(item, source, base_url, source_url, seen, depth + 1)
    
    def _handle_variable(self, declarator, source, base_url, source_url, seen):
        """Handle variable declaration: var x = "url" """
        var_id = getattr(declarator, 'id', None)
        init = getattr(declarator, 'init', None)
        
        if not var_id or not init:
            return
        
        var_name = getattr(var_id, 'name', '') or ''
        
        # Check if variable name suggests URL
        url_keywords = [
            'url', 'src', 'source', 'path', 'file', 'media',
            'video', 'audio', 'image', 'stream', 'manifest',
            'endpoint', 'api', 'link', 'href', 'poster',
            'thumbnail', 'thumb', 'cdn', 'asset',
        ]
        
        is_interesting = any(
            kw in var_name.lower() for kw in url_keywords
        )
        
        if is_interesting and hasattr(init, 'value') and isinstance(init.value, str):
            value = init.value
            full_url = URLUtils.normalize(value, base_url)
            if full_url and full_url not in seen:
                seen.add(full_url)
                media_type = MediaTypes.identify_type(url=full_url)
                
                self.add_finding(
                    category='media' if media_type != 'unknown' else 'api',
                    subcategory=media_type if media_type != 'unknown' else 'variable_url',
                    url=full_url,
                    data={
                        'variable_name': var_name,
                        'source_js': source_url,
                    },
                    confidence=0.85,
                    source=f'AST variable: {var_name}',
                )
    
    def _handle_assignment(self, node, source, base_url, source_url, seen):
        """Handle assignment: obj.src = "url" """
        left = getattr(node, 'left', None)
        right = getattr(node, 'right', None)
        
        if not left or not right:
            return
        
        # Get property name
        prop_name = ''
        if hasattr(left, 'property') and hasattr(left.property, 'name'):
            prop_name = left.property.name
        elif hasattr(left, 'name'):
            prop_name = left.name
        
        url_props = ['src', 'href', 'url', 'source', 'file', 'poster', 'manifest']
        
        if prop_name.lower() in url_props and hasattr(right, 'value') and isinstance(right.value, str):
            value = right.value
            full_url = URLUtils.normalize(value, base_url)
            if full_url and full_url not in seen:
                seen.add(full_url)
                media_type = MediaTypes.identify_type(url=full_url)
                
                self.add_finding(
                    category='media' if media_type != 'unknown' else 'api',
                    subcategory=media_type if media_type != 'unknown' else 'assignment',
                    url=full_url,
                    data={
                        'property': prop_name,
                        'source_js': source_url,
                    },
                    confidence=0.9,
                    source=f'AST assignment: .{prop_name}',
                )
    
    def _handle_call(self, node, source, base_url, source_url, seen):
        """Handle function calls: fetch("url"), hls.loadSource("url")"""
        callee = getattr(node, 'callee', None)
        arguments = getattr(node, 'arguments', [])
        
        if not callee or not arguments:
            return
        
        # Identify function name
        func_name = ''
        if hasattr(callee, 'name'):
            func_name = callee.name
        elif hasattr(callee, 'property') and hasattr(callee.property, 'name'):
            func_name = callee.property.name
            obj_name = ''
            if hasattr(callee, 'object') and hasattr(callee.object, 'name'):
                obj_name = callee.object.name
            func_name = f"{obj_name}.{func_name}" if obj_name else func_name
        
        # Known API call functions
        api_funcs = ['fetch', 'get', 'post', 'put', 'delete', 'request', 'ajax']
        media_funcs = ['loadSource', 'load', 'setSrc', 'setup', 'initialize',
                       'src', 'play', 'init', 'create', 'attach']
        
        func_lower = func_name.lower().split('.')[-1] if func_name else ''
        
        if func_lower in api_funcs or func_lower in media_funcs:
            first_arg = arguments[0] if arguments else None
            
            if first_arg and hasattr(first_arg, 'value') and isinstance(first_arg.value, str):
                value = first_arg.value
                full_url = URLUtils.normalize(value, base_url)
                
                if full_url and full_url not in seen:
                    seen.add(full_url)
                    
                    cat = 'api' if func_lower in api_funcs else 'media'
                    media_type = MediaTypes.identify_type(url=full_url)
                    
                    self.add_finding(
                        category=cat,
                        subcategory=media_type if media_type != 'unknown' else 'function_call',
                        url=full_url,
                        data={
                            'function': func_name,
                            'source_js': source_url,
                        },
                        confidence=0.9,
                        source=f'AST call: {func_name}()',
                    )
    
    def _handle_object(self, node, source, base_url, source_url, seen):
        """Handle object expressions: {src: "url", file: "url"}"""
        properties = getattr(node, 'properties', [])
        
        media_keys = [
            'src', 'file', 'url', 'source', 'video', 'audio',
            'image', 'poster', 'thumbnail', 'manifest', 'stream',
            'hls', 'dash', 'mp4', 'webm', 'download', 'media',
        ]
        
        for prop in properties:
            key_node = getattr(prop, 'key', None)
            value_node = getattr(prop, 'value', None)
            
            if not key_node or not value_node:
                continue
            
            key_name = getattr(key_node, 'name', '') or getattr(key_node, 'value', '')
            
            if (isinstance(key_name, str) and 
                    key_name.lower() in media_keys and
                    hasattr(value_node, 'value') and 
                    isinstance(value_node.value, str)):
                
                value = value_node.value
                full_url = URLUtils.normalize(value, base_url)
                
                if full_url and full_url not in seen:
                    seen.add(full_url)
                    media_type = MediaTypes.identify_type(url=full_url)
                    
                    self.add_finding(
                        category='media' if media_type != 'unknown' else 'config',
                        subcategory=media_type if media_type != 'unknown' else 'object_property',
                        url=full_url,
                        data={
                            'key': key_name,
                            'source_js': source_url,
                        },
                        confidence=0.9,
                        source=f'AST object: {{{key_name}: "..."}}',
                    )
    
    def _handle_string_literal(self, node, base_url, source_url, seen):
        """Handle string literals yang terlihat seperti URL media"""
        value = node.value
        if not isinstance(value, str) or len(value) < 8:
            return
        
        # Hanya proses yang jelas URL media
        if MediaTypes.is_media_url(value) or MediaTypes.is_streaming_url(value):
            full_url = URLUtils.normalize(value, base_url)
            if full_url and full_url not in seen:
                seen.add(full_url)
                self.add_finding(
                    category='media',
                    subcategory=MediaTypes.identify_type(url=full_url),
                    url=full_url,
                    data={'source_js': source_url},
                    confidence=0.7,
                    source='AST string literal',
                )
    
    def _handle_template_literal(self, node, source, base_url, source_url, seen):
        """Handle template literals: `https://cdn.com/${id}/video.mp4`"""
        quasis = getattr(node, 'quasis', [])
        
        # Gabungkan static parts
        parts = []
        for quasi in quasis:
            cooked = getattr(getattr(quasi, 'value', None), 'cooked', '')
            if cooked:
                parts.append(cooked)
            parts.append('{...}')  # placeholder untuk expression
        
        combined = ''.join(parts).rstrip('{...}')
        
        if ('http' in combined or '/' in combined) and ('.' in combined):
            # Cek apakah pattern mengandung ekstensi media
            if MediaTypes.is_media_url(combined) or MediaTypes.is_streaming_url(combined):
                self.add_finding(
                    category='config',
                    subcategory='url_template',
                    url=combined,
                    data={
                        'is_template': True,
                        'source_js': source_url,
                    },
                    confidence=0.7,
                    source='AST template literal',
                    context=combined[:200],
                )
    
    # ══════════════════════════════════════════
    #  VIDEO PLAYER CONFIG DETECTION
    # ══════════════════════════════════════════
    
    def _detect_player_config(
        self, content: str, base_url: str,
        source_url: str, seen: Set[str]
    ):
        """Detect video player initialization patterns"""
        
        for player_name, player_info in VIDEO_PLAYERS.items():
            config_pattern = player_info.get('config_pattern', '')
            if not config_pattern:
                continue
            
            for match in re.finditer(config_pattern, content, re.DOTALL | re.IGNORECASE):
                groups = match.groups()
                
                for group in groups:
                    if not group:
                        continue
                    
                    # Cek apakah group adalah URL langsung
                    if group.startswith(('http', '/', '.')):
                        full_url = URLUtils.normalize(group, base_url)
                        if full_url and full_url not in seen:
                            seen.add(full_url)
                            self.add_finding(
                                category='media',
                                subcategory='streaming' if MediaTypes.is_streaming_url(full_url) else 'video',
                                url=full_url,
                                data={
                                    'player': player_name,
                                    'source_js': source_url,
                                },
                                confidence=0.95,
                                source=f'player config: {player_name}',
                            )
                    
                    # Cek apakah group adalah JSON config
                    elif group.startswith('{'):
                        try:
                            # Coba parse sebagai JSON (relaxed)
                            cleaned = self._cleanup_js_object(group)
                            config = json.loads(cleaned)
                            
                            # Extract URLs dari config
                            for key in player_info.get('media_keys', []):
                                self._extract_from_config(
                                    config, key, base_url, 
                                    player_name, source_url, seen
                                )
                            
                            # Store full config
                            self.add_finding(
                                category='config',
                                subcategory='player_config',
                                data={
                                    'player': player_name,
                                    'config': config,
                                    'source_js': source_url,
                                },
                                confidence=0.95,
                                source=f'player config: {player_name}',
                            )
                        except (json.JSONDecodeError, ValueError):
                            # Regex fallback
                            urls = PatternMatcher.find_media_urls_only(group, base_url)
                            for url_match in urls:
                                if url_match.url not in seen:
                                    seen.add(url_match.url)
                                    self.add_finding(
                                        category='media',
                                        subcategory=MediaTypes.identify_type(url=url_match.url),
                                        url=url_match.url,
                                        data={
                                            'player': player_name,
                                            'source_js': source_url,
                                        },
                                        confidence=0.85,
                                        source=f'player config regex: {player_name}',
                                    )
    
    def _extract_from_config(
        self, config, key, base_url, 
        player_name, source_url, seen, depth=0
    ):
        """Extract URL dari player config by key"""
        if depth > 5:
            return
        
        if isinstance(config, dict):
            if key in config:
                value = config[key]
                if isinstance(value, str):
                    full_url = URLUtils.normalize(value, base_url)
                    if full_url and full_url not in seen:
                        seen.add(full_url)
                        self.add_finding(
                            category='media',
                            subcategory=MediaTypes.identify_type(url=full_url) or 'video',
                            url=full_url,
                            data={
                                'player': player_name,
                                'config_key': key,
                                'source_js': source_url,
                            },
                            confidence=0.95,
                            source=f'player config [{player_name}][{key}]',
                        )
                elif isinstance(value, list):
                    for item in value:
                        self._extract_from_config(
                            item, 'file', base_url,
                            player_name, source_url, seen, depth + 1
                        )
                        self._extract_from_config(
                            item, 'src', base_url,
                            player_name, source_url, seen, depth + 1
                        )
            
            for v in config.values():
                if isinstance(v, (dict, list)):
                    self._extract_from_config(
                        v, key, base_url,
                        player_name, source_url, seen, depth + 1
                    )
        
        elif isinstance(config, list):
            for item in config:
                self._extract_from_config(
                    item, key, base_url,
                    player_name, source_url, seen, depth + 1
                )
    
    def _cleanup_js_object(self, text: str) -> str:
        """Coba convert JS object literal ke valid JSON"""
        # Remove comments
        text = re.sub(r'//.*?$', '', text, flags=re.MULTILINE)
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.DOTALL)
        
        # Add quotes to unquoted keys
        text = re.sub(r'(\w+)\s*:', r'"\1":', text)
        
        # Replace single quotes with double
        text = text.replace("'", '"')
        
        # Remove trailing commas
        text = re.sub(r',\s*([\]}])', r'\1', text)
        
        return text
    
    def _analyze_inline_scripts_ast(
        self, html: str, base_url: str, seen: Set[str]
    ):
        """Parse inline scripts dari HTML dengan AST"""
        from bs4 import BeautifulSoup
        
        soup = BeautifulSoup(html, 'lxml')
        
        for script in soup.find_all('script'):
            if script.get('src') or script.get('type') == 'application/ld+json':
                continue
            
            text = script.string
            if not text or len(text) < 50:
                continue
            
            if HAS_ESPRIMA and self.config.scan.ast_parsing:
                self._ast_analysis(
                    text, base_url, 'inline_script', seen
                )