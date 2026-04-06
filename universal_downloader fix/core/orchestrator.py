"""
orchestrator.py — Pengendali utama 3-Phase diagnostic:
  Phase 1: RECON  → Quick scan, fingerprint, decide layers
  Phase 2: SCAN   → Run relevant layers
  Phase 3: REPORT → Analyze & generate prompt-ready report
"""

import asyncio
import time
import logging
import json
import os
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field

from config import DiagnosticConfig, DEFAULT_CONFIG
from core.browser import BrowserEngine, BrowserCaptureResult
from core.session import SessionManager
from utils.media_types import (
    MediaTypes, VIDEO_PLAYERS, CMS_SIGNATURES, 
    CDN_SIGNATURES, DRM_SIGNATURES
)
from utils.embed_services import SERVER_SELECTOR_CSS
from utils.url_utils import URLUtils
from utils.pattern_matcher import PatternMatcher

logger = logging.getLogger(__name__)


@dataclass
class ReconResult:
    """Hasil Phase 1: Reconnaissance"""
    url: str
    final_url: str = ""
    is_accessible: bool = False
    status_code: int = 0
    
    # Fingerprinting
    cms: Optional[str] = None
    framework: Optional[str] = None
    cdns: List[str] = field(default_factory=list)
    video_players: List[str] = field(default_factory=list)
    
    # Quick analysis
    has_video_tags: bool = False
    has_audio_tags: bool = False
    has_iframes: bool = False
    has_lazy_loading: bool = False
    has_streaming_refs: bool = False
    has_api_refs: bool = False
    has_drm_refs: bool = False
    has_service_worker: bool = False
    has_websocket_refs: bool = False
    has_graphql: bool = False
    has_multi_server: bool = False
    
    # JS files found
    js_file_count: int = 0
    js_file_urls: List[str] = field(default_factory=list)
    
    # Server info
    server: str = ""
    powered_by: str = ""
    
    # Recommended layers
    recommended_layers: List[str] = field(default_factory=list)
    skip_reason: Dict[str, str] = field(default_factory=dict)


@dataclass
class DiagnosticResult:
    """Hasil akhir diagnosis — ini yang jadi output/report"""
    diagnosis_id: str = ""
    target_url: str = ""
    timestamp: float = 0
    duration: float = 0
    
    # Phase results
    recon: Optional[ReconResult] = None
    browser_capture: Optional[BrowserCaptureResult] = None
    
    # Aggregated findings
    site_profile: Dict = field(default_factory=dict)
    media_found: List[Dict] = field(default_factory=list)
    api_endpoints: List[Dict] = field(default_factory=list)
    streaming_info: List[Dict] = field(default_factory=list)
    multi_server_analysis: Dict = field(default_factory=dict)
    access_requirements: Dict = field(default_factory=dict)
    extraction_strategy: Dict = field(default_factory=dict)
    reproducible_evidence: Dict = field(default_factory=dict)
    
    # Layer-specific results
    layer_results: Dict[str, Any] = field(default_factory=dict)
    
    # Errors
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


class Orchestrator:
    """
    Pengendali utama diagnostic tool.
    Menjalankan 3 phase: Recon → Scan → Report
    """
    
    def __init__(self, config: Optional[DiagnosticConfig] = None):
        self.config = config or DEFAULT_CONFIG
        self.session = SessionManager(self.config)
        self.browser = BrowserEngine(self.config)
        self.result = DiagnosticResult()
        
        # Layer registry — akan diisi di Part 3+
        self._layers = {}
    
    def register_layer(self, name: str, layer_instance):
        """Register scanning layer"""
        self._layers[name] = layer_instance
    
    # ══════════════════════════════════════════
    #  MAIN ENTRY POINT
    # ══════════════════════════════════════════
    
    async def diagnose(self, url: str) -> DiagnosticResult:
        """
        Main method — jalankan full diagnostic.
        
        Args:
            url: Target URL
            
        Returns:
            DiagnosticResult: Hasil diagnosis lengkap (prompt-ready)
        """
        start_time = time.time()
        
        # Init result
        self.result = DiagnosticResult(
            diagnosis_id=f"diag_{int(time.time())}",
            target_url=url,
            timestamp=time.time(),
        )
        
        logger.info(f"\n{'='*60}")
        logger.info(f"🔬 MEDIA DIAGNOSTIC TOOL")
        logger.info(f"🎯 Target: {url}")
        logger.info(f"{'='*60}\n")
        
        try:
            # ── PHASE 1: RECON ──
            logger.info("━━━ PHASE 1: RECONNAISSANCE ━━━")
            recon = await self._phase_recon(url)
            self.result.recon = recon
            
            if not recon.is_accessible:
                self.result.errors.append(
                    f"Target not accessible: HTTP {recon.status_code}"
                )
                logger.error("❌ Target not accessible. Aborting.")
                return self.result
            
            self._print_recon_summary(recon)
            
            # ── PHASE 2: DEEP SCAN ──
            logger.info("\n━━━ PHASE 2: DEEP SCAN ━━━")
            await self._phase_scan(url, recon)
            
            # ── PHASE 3: ANALYSIS & REPORT ──
            logger.info("\n━━━ PHASE 3: ANALYSIS & REPORT ━━━")
            await self._phase_report()
            
        except Exception as e:
            error_msg = f"Diagnostic error: {str(e)}"
            logger.error(f"❌ {error_msg}")
            self.result.errors.append(error_msg)
            if logger.isEnabledFor(logging.DEBUG):
                import traceback
                traceback.print_exc()
        
        finally:
            self.result.duration = time.time() - start_time
            logger.info(
                f"\n⏱️ Total diagnostic time: "
                f"{self.result.duration:.1f}s"
            )
        
        return self.result
    
    # ══════════════════════════════════════════
    #  PHASE 1: RECONNAISSANCE
    # ══════════════════════════════════════════
    
    async def _phase_recon(self, url: str) -> ReconResult:
        """
        Quick recon untuk:
        1. Cek accessibility
        2. Fingerprint teknologi
        3. Quick HTML scan
        4. Tentukan layers yang relevan
        """
        recon = ReconResult(url=url)
        
        # ── Step 1: Fetch halaman ──
        logger.info("  📡 Fetching page...")
        resp = self.session.get(url, timeout=15)
        
        if not resp or resp.status_code >= 400:
            recon.is_accessible = False
            recon.status_code = resp.status_code if resp else 0
            return recon
        
        recon.is_accessible = True
        recon.status_code = resp.status_code
        recon.final_url = resp.url
        recon.server = resp.headers.get('server', '')
        recon.powered_by = resp.headers.get('x-powered-by', '')
        
        html = resp.body or ""
        headers = resp.headers
        
        # ── Step 2: Fingerprint CMS/Framework ──
        logger.info("  🔍 Fingerprinting technology stack...")
        self._fingerprint_cms(html, headers, recon)
        self._fingerprint_video_players(html, recon)
        self._fingerprint_cdn(html, headers, recon)
        
        # ── Step 3: Quick HTML analysis ──
        logger.info("  📄 Quick HTML analysis...")
        self._quick_html_scan(html, recon)
        
        # ── Step 4: Find JS files ──
        self._find_js_files(html, url, recon)
        
        # ── Step 5: Determine recommended layers ──
        logger.info("  🧠 Determining scan strategy...")
        self._determine_layers(recon)
        
        return recon
    
    def _fingerprint_cms(
        self, html: str, headers: Dict, recon: ReconResult
    ):
        """Identifikasi CMS/Framework"""
        combined = html.lower() + ' '.join(
            f"{k}: {v}" for k, v in headers.items()
        ).lower()
        
        for cms_name, sigs in CMS_SIGNATURES.items():
            for indicator in sigs['indicators']:
                if indicator.lower() in combined:
                    if cms_name in ('react', 'vue', 'angular', 
                                     'nextjs', 'nuxtjs'):
                        recon.framework = cms_name
                    else:
                        recon.cms = cms_name
                    logger.info(f"    ✓ Detected: {cms_name}")
                    break
    
    def _fingerprint_video_players(self, html: str, recon: ReconResult):
        """Identifikasi video players"""
        html_lower = html.lower()
        
        for player_name, sigs in VIDEO_PLAYERS.items():
            for indicator in sigs['indicators']:
                if indicator.lower() in html_lower:
                    recon.video_players.append(player_name)
                    logger.info(f"    ✓ Video player: {player_name}")
                    break
    
    def _fingerprint_cdn(
        self, html: str, headers: Dict, recon: ReconResult
    ):
        """Identifikasi CDN"""
        combined = html.lower() + ' '.join(
            f"{k}: {v}" for k, v in headers.items()
        ).lower()
        
        for cdn_name, indicators in CDN_SIGNATURES.items():
            for indicator in indicators:
                if indicator.lower() in combined:
                    if cdn_name not in recon.cdns:
                        recon.cdns.append(cdn_name)
                        logger.info(f"    ✓ CDN: {cdn_name}")
                    break
    
    def _quick_html_scan(self, html: str, recon: ReconResult):
        """Quick scan HTML untuk indikator"""
        html_lower = html.lower()
        
        # Video/Audio tags
        recon.has_video_tags = '<video' in html_lower
        recon.has_audio_tags = '<audio' in html_lower
        recon.has_iframes = '<iframe' in html_lower
        
        # Lazy loading
        lazy_indicators = [
            'data-src', 'data-original', 'data-lazy', 
            'loading="lazy"', 'lazyload'
        ]
        recon.has_lazy_loading = any(
            ind in html_lower for ind in lazy_indicators
        )
        
        # Streaming
        streaming_indicators = [
            '.m3u8', '.mpd', 'hls.js', 'dash.js', 'shaka',
            'manifest', 'playlist.m3u8', 'mpegurl'
        ]
        recon.has_streaming_refs = any(
            ind in html_lower for ind in streaming_indicators
        )
        
        # API
        api_indicators = [
            'fetch(', 'axios', '/api/', 'xhr', 
            'apiurl', 'api_url', 'endpoint'
        ]
        recon.has_api_refs = any(
            ind in html_lower for ind in api_indicators
        )
        
        # DRM
        drm_indicators = [
            'widevine', 'fairplay', 'playready', 'clearkey',
            'drm', 'eme', 'encrypted'
        ]
        recon.has_drm_refs = any(
            ind in html_lower for ind in drm_indicators
        )
        
        # Service Worker
        sw_indicators = [
            'serviceworker', 'service-worker', 'navigator.serviceWorker',
            'sw.js', 'service_worker'
        ]
        recon.has_service_worker = any(
            ind in html_lower for ind in sw_indicators
        )
        
        # WebSocket
        ws_indicators = [
            'websocket', 'wss://', 'ws://', 'new WebSocket',
            'socket.io', 'sockjs'
        ]
        recon.has_websocket_refs = any(
            ind in html_lower for ind in ws_indicators
        )
        
        # GraphQL
        gql_indicators = ['graphql', '/graphql', 'gql', '__schema']
        recon.has_graphql = any(
            ind in html_lower for ind in gql_indicators
        )

        # Multi-server
        server_indicators = [
            'data-server', 'data-source', 'data-embed',
            'server-item', 'server-list', 'server-select',
            'source-item', 'source-list', 'episodes-servers',
            'player-server', 'player-servers', 'choose-server',
            'changeserver', 'switchserver', 'loadserver',
        ]
        selector_tokens = set()
        for selector in SERVER_SELECTOR_CSS:
            normalized = (
                selector.lower()
                .replace('[', ' ')
                .replace(']', ' ')
                .replace('.', ' ')
                .replace('#', ' ')
                .replace('"', ' ')
                .replace("'", ' ')
                .replace('=', ' ')
                .replace(':', ' ')
                .replace('>', ' ')
            )
            selector_tokens.update(
                token for token in normalized.split()
                if token in ('data-server', 'data-source', 'data-embed')
                or 'server' in token
            )

        recon.has_multi_server = any(
            ind in html_lower for ind in server_indicators
        ) or any(
            token in html_lower for token in selector_tokens
        )

        # Log findings
        findings = []
        if recon.has_video_tags:       findings.append("video")
        if recon.has_audio_tags:       findings.append("audio")
        if recon.has_iframes:          findings.append("iframes")
        if recon.has_lazy_loading:     findings.append("lazy-loading")
        if recon.has_streaming_refs:   findings.append("streaming")
        if recon.has_api_refs:         findings.append("API")
        if recon.has_drm_refs:         findings.append("DRM")
        if recon.has_service_worker:   findings.append("service-worker")
        if recon.has_websocket_refs:   findings.append("websocket")
        if recon.has_graphql:          findings.append("GraphQL")
        if recon.has_multi_server:     findings.append("multi-server")
        
        logger.info(f"    ✓ Indicators found: {', '.join(findings) or 'none'}")
    
    def _find_js_files(self, html: str, base_url: str, recon: ReconResult):
        """Extract semua JS file URLs dari HTML"""
        import re
        
        # <script src="...">
        pattern = re.compile(
            r'<script[^>]+src=["\']([^"\']+\.js(?:\?[^"\']*)?)["\']',
            re.IGNORECASE
        )
        
        js_urls = set()
        for match in pattern.finditer(html):
            js_url = URLUtils.normalize(match.group(1), base_url)
            if js_url:
                js_urls.add(js_url)
        
        recon.js_file_count = len(js_urls)
        recon.js_file_urls = sorted(js_urls)
        logger.info(f"    ✓ JS files found: {len(js_urls)}")
    
    def _determine_layers(self, recon: ReconResult):
        """
        Tentukan layer mana yang perlu dijalankan
        berdasarkan hasil recon.
        
        Semua layer ON by default (config), 
        tapi kita bisa skip yang tidak relevan.
        """
        layers = []
        skips = {}
        
        # Layer 1: Static — SELALU jalan
        layers.append('layer_01_static')
        
        # Layer 2: Dynamic — SELALU jalan (untuk network capture)
        layers.append('layer_02_dynamic')
        
        # Layer 3: JS AST — jalan kalau ada JS files
        if recon.js_file_count > 0:
            layers.append('layer_03_js_ast')
        else:
            skips['layer_03_js_ast'] = 'No JS files found'
        
        # Layer 4: API Probe — jalan kalau ada API refs
        if recon.has_api_refs or recon.has_graphql:
            layers.append('layer_04_api_probe')
        else:
            # Tetap jalan karena API bisa ditemukan di Layer 2/3
            layers.append('layer_04_api_probe')
        
        # Layer 5: Streaming — prioritas tinggi jika ada indikator
        if (recon.has_streaming_refs or recon.has_video_tags or 
                recon.video_players):
            layers.append('layer_05_streaming')
        else:
            # Tetap jalan, bisa ada streaming tersembunyi
            layers.append('layer_05_streaming')
        
        # Layer 6: WebSocket
        if recon.has_websocket_refs:
            layers.append('layer_06_websocket')
        else:
            skips['layer_06_websocket'] = 'No WebSocket indicators'
        
        # Layer 7: Service Worker
        if recon.has_service_worker:
            layers.append('layer_07_service_worker')
        else:
            skips['layer_07_service_worker'] = 'No Service Worker detected'
        
        # Layer 8: Infrastructure — SELALU jalan
        layers.append('layer_08_infrastructure')
        
        # Layer 9: Auth Flow — SELALU jalan
        layers.append('layer_09_auth_flow')
        
        # Layer 10: DOM Mutation — jalan kalau ada lazy/dynamic content
        if recon.has_lazy_loading or recon.has_iframes or recon.framework:
            layers.append('layer_10_dom_mutation')
        else:
            # Tetap jalan, DOM mutations bisa terjadi
            layers.append('layer_10_dom_mutation')

        # Layer 11: Multi-server
        if self.config.scan.multi_server_detection:
            if recon.has_multi_server or recon.has_iframes or recon.video_players:
                layers.append('layer_11_multi_server')
            else:
                layers.append('layer_11_multi_server')  # selalu jalan
        else:
            skips['layer_11_multi_server'] = 'Multi-server detection disabled'
        
        recon.recommended_layers = layers
        recon.skip_reason = skips
        
        logger.info(f"    ✓ Layers to run: {len(layers)}")
        for skip_name, reason in skips.items():
            logger.info(f"    ⏭️ Skip {skip_name}: {reason}")
    
    # ══════════════════════════════════════════
    #  PHASE 2: DEEP SCAN
    # ══════════════════════════════════════════
    
    async def _phase_scan(self, url: str, recon: ReconResult):
        """
        Jalankan deep scan menggunakan browser + registered layers.
        """
        # ── Step 1: Browser capture (Layer 2) ──
        logger.info("  🌐 Starting browser capture...")
        await self.browser.start()
        
        try:
            capture = await self.browser.capture(url)
            self.result.browser_capture = capture
            
            # Copy cookies ke session (untuk layer lain)
            self.session.copy_cookies_from_browser(capture.cookies)
            self.session.set_referer(url)
            self.session.set_origin(URLUtils.get_base_url(url))
            
            logger.info(
                f"  ✅ Browser capture: "
                f"{capture.total_requests} requests, "
                f"{capture.total_media_requests} media"
            )
            
        finally:
            await self.browser.stop()
        
        # ── Step 2: Run each registered layer ──
        for layer_name in recon.recommended_layers:
            if layer_name in self._layers:
                if not self.config.enabled_layers.get(layer_name, True):
                    logger.info(f"  ⏭️ {layer_name}: disabled in config")
                    continue
                
                logger.info(f"  🔍 Running {layer_name}...")
                
                try:
                    layer = self._layers[layer_name]
                    layer_result = await self._run_layer_safe(
                        layer, url, recon, capture
                    )
                    self.result.layer_results[layer_name] = layer_result
                    logger.info(f"  ✅ {layer_name}: complete")
                    
                except Exception as e:
                    error_msg = f"{layer_name} error: {str(e)}"
                    logger.error(f"  ❌ {error_msg}")
                    self.result.errors.append(error_msg)
            else:
                logger.debug(
                    f"  ⚠️ {layer_name}: not registered (will be in later parts)"
                )
    
    async def _run_layer_safe(
        self, layer, url: str, 
        recon: ReconResult, 
        capture: BrowserCaptureResult
    ):
        """Run layer dengan timeout protection"""
        timeout = self.config.scan.max_layer_time
        
        try:
            if asyncio.iscoroutinefunction(layer.run):
                result = await asyncio.wait_for(
                    layer.run(url, recon, capture, self.session),
                    timeout=timeout
                )
            else:
                result = layer.run(url, recon, capture, self.session)
            return result
            
        except asyncio.TimeoutError:
            logger.warning(
                f"  ⏱️ Layer timeout after {timeout}s"
            )
            return {'error': 'timeout', 'timeout': timeout}
    
    # ══════════════════════════════════════════
    #  PHASE 3: ANALYSIS & REPORT
    # ══════════════════════════════════════════
    
    async def _phase_report(self):
        """
        Analyze semua data dan generate prompt-ready report.
        Ini akan di-expand di Part berikutnya 
        ketika semua layers sudah ada.
        """
        logger.info("  📊 Aggregating findings...")

        # ── Build multi-server analysis ──
        self.result.multi_server_analysis = self._build_multi_server_analysis()
        
        # ── Build site profile ──
        self.result.site_profile = self._build_site_profile()
        
        # ── Aggregate media findings ──
        self.result.media_found = self._aggregate_media()
        
        # ── Aggregate API endpoints ──
        self.result.api_endpoints = self._aggregate_apis()

        # ── Aggregate streaming findings ──
        self.result.streaming_info = self._aggregate_streaming()
        
        # ── Build access requirements ──
        self.result.access_requirements = self._build_access_requirements()
        
        # ── Generate extraction strategy ──
        self.result.extraction_strategy = self._build_extraction_strategy()
        
        # ── Generate reproducible evidence ──
        self.result.reproducible_evidence = self._build_evidence()
        
        # ── Save report ──
        self._save_report()
        
        logger.info("  ✅ Report generated")

    def _build_multi_server_analysis(self) -> Dict:
        """Build multi-server summary dari recon + layer results"""
        recon = self.result.recon
        analysis = {
            'detected': bool(recon and recon.has_multi_server),
            'layer_enabled': self.config.scan.multi_server_detection,
            'layer_executed': False,
            'server_count': 0,
            'servers': [],
            'embed_services': [],
            'api_endpoints': [],
            'notes': [],
        }

        layer_result = self.result.layer_results.get('layer_11_multi_server')
        if layer_result is None or not hasattr(layer_result, 'findings'):
            if analysis['detected']:
                analysis['notes'].append(
                    'Multi-server indicators found during recon, but layer 11 did not run.'
                )
            return analysis

        analysis['layer_executed'] = True
        api_seen = set()

        for finding in layer_result.findings:
            if finding.category == 'info' and finding.subcategory == 'server_map':
                analysis['server_count'] = finding.data.get('total_servers', 0)
                analysis['servers'] = finding.data.get('servers', [])
                analysis['embed_services'] = finding.data.get(
                    'embed_services_used', []
                )
                continue

            is_server_api = (
                finding.category == 'api'
                or (
                    finding.category == 'server'
                    and finding.subcategory == 'server_api'
                )
            )
            if is_server_api and finding.url and finding.url not in api_seen:
                api_seen.add(finding.url)
                analysis['api_endpoints'].append({
                    'url': finding.url,
                    'subcategory': finding.subcategory,
                    'source': finding.source,
                    'details': finding.data,
                })

        if analysis['servers'] and not analysis['server_count']:
            analysis['server_count'] = len(analysis['servers'])

        if analysis['server_count'] > 1:
            analysis['detected'] = True

        if analysis['detected'] and not analysis['servers']:
            analysis['notes'].append(
                'Server UI indicators were detected, but no concrete server map was extracted.'
            )

        return analysis
    
    def _build_site_profile(self) -> Dict:
        """Build site profile dari recon + capture"""
        recon = self.result.recon
        if not recon:
            return {}

        multi_server = self.result.multi_server_analysis or {}
        profile = {
            'url': recon.url,
            'final_url': recon.final_url,
            'cms': recon.cms,
            'framework': recon.framework,
            'cdns': recon.cdns,
            'video_players': recon.video_players,
            'server': recon.server,
            'powered_by': recon.powered_by,
            'multi_server': {
                'detected': multi_server.get('detected', recon.has_multi_server),
                'server_count': multi_server.get('server_count', 0),
                'embed_services': multi_server.get('embed_services', []),
                'layer_executed': multi_server.get('layer_executed', False),
            },
            'features_detected': {
                'video': recon.has_video_tags,
                'audio': recon.has_audio_tags,
                'iframes': recon.has_iframes,
                'lazy_loading': recon.has_lazy_loading,
                'streaming': recon.has_streaming_refs,
                'api': recon.has_api_refs,
                'drm': recon.has_drm_refs,
                'service_worker': recon.has_service_worker,
                'websocket': recon.has_websocket_refs,
                'graphql': recon.has_graphql,
                'multi_server': recon.has_multi_server,
            },
            'js_files_count': recon.js_file_count,
        }
        return profile
    
    def _aggregate_media(self) -> List[Dict]:
        """Kumpulkan semua media dari browser capture + layers"""
        media_list = []
        seen_urls = set()
        
        if self.result.browser_capture:
            for req in self.result.browser_capture.requests:
                if req.is_media and req.url not in seen_urls:
                    seen_urls.add(req.url)
                    
                    expiry = URLUtils.detect_url_expiry(req.url)
                    
                    media_list.append({
                        'url': req.url,
                        'type': req.media_type,
                        'content_type': req.content_type,
                        'size': req.response_size,
                        'status': req.status,
                        'discovered_via': f'network_capture ({req.resource_type})',
                        'url_expiry': expiry['has_expiry'],
                        'url_type': expiry['estimated_type'],
                        'request_headers': req.headers,
                        'response_headers': req.response_headers,
                    })
        
        # Dari DOM mutations
        if self.result.browser_capture:
            for change in self.result.browser_capture.dom_changes:
                url = URLUtils.normalize(
                    change.value, self.result.target_url
                )
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    media_list.append({
                        'url': url,
                        'type': MediaTypes.identify_type(url=url),
                        'discovered_via': f'dom_mutation ({change.change_type})',
                        'dom_tag': change.tag,
                        'dom_attribute': change.attribute,
                    })

        for layer_name, layer_result in self.result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue

            for finding in layer_result.findings:
                if finding.category != 'media' or not finding.url:
                    continue
                if finding.url in seen_urls:
                    continue

                seen_urls.add(finding.url)
                media_list.append({
                    'url': finding.url,
                    'type': finding.subcategory,
                    'confidence': finding.confidence,
                    'discovered_via': finding.source,
                    'layer': layer_name,
                    'details': finding.data,
                })
        
        return media_list
    
    def _aggregate_apis(self) -> List[Dict]:
        """Kumpulkan semua API endpoints yang ditemukan"""
        apis = []
        seen = set()
        
        if self.result.browser_capture:
            for req in self.result.browser_capture.requests:
                if req.is_api and req.url not in seen:
                    seen.add(req.url)
                    
                    api_info = {
                        'url': req.url,
                        'method': req.method,
                        'content_type': req.content_type,
                        'status': req.status,
                        'has_json_response': 'json' in req.content_type.lower(),
                    }
                    
                    # Include sample response jika ada
                    if req.response_body:
                        body = req.response_body
                        max_size = self.config.report.max_sample_response_size
                        api_info['sample_response'] = (
                            body[:max_size] if len(body) > max_size 
                            else body
                        )
                    
                    apis.append(api_info)

        for layer_name, layer_result in self.result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue

            for finding in layer_result.findings:
                is_api_finding = (
                    finding.category == 'api'
                    or (
                        finding.category == 'server'
                        and finding.subcategory == 'server_api'
                    )
                )
                if not is_api_finding or not finding.url or finding.url in seen:
                    continue

                seen.add(finding.url)
                apis.append({
                    'url': finding.url,
                    'type': finding.subcategory,
                    'confidence': finding.confidence,
                    'discovered_via': finding.source,
                    'layer': layer_name,
                    'details': finding.data,
                })
        
        return apis

    def _aggregate_streaming(self) -> List[Dict]:
        """Kumpulkan semua streaming findings dari layers"""
        streams = []
        seen = set()

        for layer_name, layer_result in self.result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue

            for finding in layer_result.findings:
                if finding.category != 'streaming' or not finding.url:
                    continue
                if finding.url in seen:
                    continue

                seen.add(finding.url)
                streams.append({
                    'url': finding.url,
                    'type': finding.subcategory,
                    'confidence': finding.confidence,
                    'discovered_via': finding.source,
                    'layer': layer_name,
                    'details': finding.data,
                })

        return streams
    
    def _build_access_requirements(self) -> Dict:
        """Determine headers, cookies, etc. yang diperlukan"""
        requirements = {
            'headers': {},
            'cookies': [],
            'session_flow': [],
            'referer_required': False,
            'origin_required': False,
        }
        
        if self.result.browser_capture:
            cap = self.result.browser_capture
            
            # Cookies
            if cap.cookies:
                requirements['cookies'] = [
                    {
                        'name': c.get('name', ''),
                        'domain': c.get('domain', ''),
                        'path': c.get('path', ''),
                        'httpOnly': c.get('httpOnly', False),
                        'secure': c.get('secure', False),
                    }
                    for c in cap.cookies
                ]
            
            # Detect referer requirement dari media requests
            for req in cap.requests:
                if req.is_media:
                    if 'referer' in {k.lower() for k in req.headers}:
                        requirements['referer_required'] = True
                        requirements['headers']['Referer'] = (
                            req.headers.get('referer', '')
                            or req.headers.get('Referer', '')
                        )
                    if 'origin' in {k.lower() for k in req.headers}:
                        requirements['origin_required'] = True
                        requirements['headers']['Origin'] = (
                            req.headers.get('origin', '')
                            or req.headers.get('Origin', '')
                        )
        
        return requirements
    
    def _build_extraction_strategy(self) -> Dict:
        """Generate rekomendasi extraction strategy"""
        recon = self.result.recon
        multi_server = self.result.multi_server_analysis or {}
        
        strategy = {
            'recommended_method': 'requests',
            'browser_required': False,
            'steps': [],
            'libraries_needed': ['requests'],
            'estimated_complexity': 'low',
            'potential_issues': [],
        }

        if not recon:
            return strategy
        
        # Determine complexity
        if recon.has_drm_refs:
            strategy['estimated_complexity'] = 'very_high'
            strategy['potential_issues'].append(
                'DRM protection detected — may not be extractable'
            )
        elif recon.has_streaming_refs:
            strategy['estimated_complexity'] = 'medium'
            strategy['libraries_needed'].extend(['m3u8', 'ffmpeg'])
            strategy['steps'].append(
                'Download m3u8/mpd manifest'
            )
            strategy['steps'].append(
                'Parse manifest for segment URLs'
            )
            strategy['steps'].append(
                'Download all segments'
            )
            strategy['steps'].append(
                'Merge segments with ffmpeg'
            )
        
        if recon.framework in ('nextjs', 'nuxtjs', 'react'):
            strategy['browser_required'] = True
            strategy['estimated_complexity'] = 'medium'
            strategy['libraries_needed'].append('playwright')
        
        if recon.has_lazy_loading:
            strategy['browser_required'] = True
            strategy['libraries_needed'].append('playwright')
            strategy['steps'].insert(0, 
                'Render page with browser to trigger lazy loading'
            )

        if recon.has_multi_server or multi_server.get('server_count', 0) > 1:
            strategy['browser_required'] = True
            if 'playwright' not in strategy['libraries_needed']:
                strategy['libraries_needed'].append('playwright')
            if strategy['estimated_complexity'] not in ('high', 'very_high'):
                strategy['estimated_complexity'] = 'high'

            multi_server_steps = [
                'Enumerate every available server/source option',
                'Probe each server to capture iframe, API, and media URLs',
                'Choose the best working server based on media evidence',
            ]
            for step in multi_server_steps:
                if step not in strategy['steps']:
                    strategy['steps'].append(step)

            issue = 'Multiple servers/sources detected - media URLs may vary per server'
            if issue not in strategy['potential_issues']:
                strategy['potential_issues'].append(issue)
        
        return strategy
    
    def _build_evidence(self) -> Dict:
        """Generate reproducible evidence (curl commands, etc.)"""
        evidence = {
            'curl_commands': [],
            'request_response_pairs': [],
        }
        
        if not self.result.browser_capture:
            return evidence
        
        # Generate curl commands untuk media requests
        for req in self.result.browser_capture.requests:
            if req.is_media and req.status == 200:
                curl = self._generate_curl(req)
                if curl:
                    evidence['curl_commands'].append({
                        'url': req.url,
                        'media_type': req.media_type,
                        'command': curl
                    })
        
        return evidence
    
    def _generate_curl(self, req) -> str:
        """Generate curl command dari CapturedRequest"""
        parts = ['curl']
        
        # Important headers
        important = [
            'referer', 'origin', 'cookie', 
            'authorization', 'user-agent'
        ]
        for key, value in req.headers.items():
            if key.lower() in important:
                parts.append(f"-H '{key}: {value}'")
        
        parts.append(f"'{req.url}'")
        return ' '.join(parts)
    
    # ══════════════════════════════════════════
    #  SAVE REPORT
    # ══════════════════════════════════════════
    
    def _save_report(self):
        """Save report ke file JSON (prompt-ready format)"""
        self.config.ensure_output_dir()
        
        report = {
            'diagnosis_id': self.result.diagnosis_id,
            'target_url': self.result.target_url,
            'timestamp': self.result.timestamp,
            'duration_seconds': self.result.duration,
            
            'site_profile': self.result.site_profile,
            
            'media_found': self.result.media_found,
            'media_count': len(self.result.media_found),
            
            'api_endpoints': self.result.api_endpoints,
            'api_count': len(self.result.api_endpoints),
            
            'streaming_info': self.result.streaming_info,
            'multi_server_analysis': self.result.multi_server_analysis,
            
            'access_requirements': self.result.access_requirements,
            'extraction_strategy': self.result.extraction_strategy,
            'reproducible_evidence': self.result.reproducible_evidence,
            
            'errors': self.result.errors,
            'warnings': self.result.warnings,
            
            # Browser capture summary
            'browser_capture_summary': None,
        }
        
        if self.result.browser_capture:
            cap = self.result.browser_capture
            report['browser_capture_summary'] = {
                'total_requests': cap.total_requests,
                'total_media_requests': cap.total_media_requests,
                'websockets': len(cap.websockets),
                'service_workers': len(cap.service_workers),
                'dom_changes': len(cap.dom_changes),
                'console_logs': len(cap.console_logs),
                'cookies_count': len(cap.cookies),
                'load_time': cap.load_time,
                'screenshot': cap.screenshot_path,
            }
        
        # Save
        filepath = os.path.join(
            self.config.report.output_dir,
            f"{self.result.diagnosis_id}.json"
        )
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)
        
        logger.info(f"  💾 Report saved: {filepath}")
        
        # Print summary
        self._print_final_summary(report)
    
    # ══════════════════════════════════════════
    #  CONSOLE OUTPUT
    # ══════════════════════════════════════════
    
    def _print_recon_summary(self, recon: ReconResult):
        """Print recon summary ke console"""
        print(f"\n  ┌─ RECON SUMMARY ─────────────────────")
        print(f"  │ Status:     {recon.status_code}")
        print(f"  │ CMS:        {recon.cms or 'unknown'}")
        print(f"  │ Framework:  {recon.framework or 'unknown'}")
        print(f"  │ CDNs:       {', '.join(recon.cdns) or 'none'}")
        print(f"  │ Players:    {', '.join(recon.video_players) or 'none'}")
        print(f"  │ JS Files:   {recon.js_file_count}")
        print(f"  │ Multi-Server: {'yes' if recon.has_multi_server else 'no'}")
        print(f"  │ Layers:     {len(recon.recommended_layers)} active")
        print(f"  └───────────────────────────────────────\n")
    
    def _print_final_summary(self, report: Dict):
        """Print final summary"""
        print(f"\n{'═'*55}")
        print(f" 📋 DIAGNOSTIC REPORT: {report['diagnosis_id']}")
        print(f"{'═'*55}")
        print(f" 🎯 Target:     {report['target_url']}")
        print(f" ⏱️  Duration:   {report['duration_seconds']:.1f}s")
        print(f" 📁 Media:      {report['media_count']} found")
        print(f" 🔌 APIs:       {report['api_count']} found")

        multi_server = report.get('multi_server_analysis', {})
        if multi_server.get('detected'):
            server_text = (
                f"{multi_server.get('server_count', 0)} mapped"
                if multi_server.get('server_count')
                else 'detected'
            )
            print(f" 🔀 Servers:    {server_text}")

        print(f" ❌ Errors:     {len(report['errors'])}")
        
        if report['media_found']:
            print(f"\n ── Media Found ──")
            for m in report['media_found'][:15]:
                t = m.get('type', '?')
                print(f"  [{t:10}] {m['url'][:80]}")
            if len(report['media_found']) > 15:
                print(f"  ... and {len(report['media_found'])-15} more")
        
        if report['api_endpoints']:
            print(f"\n ── API Endpoints ──")
            for a in report['api_endpoints'][:10]:
                print(f"  [{a.get('method','?'):4}] {a['url'][:80]}")
        
        print(f"\n {'═'*55}")
        
        filepath = os.path.join(
            self.config.report.output_dir,
            f"{report['diagnosis_id']}.json"
        )
        print(f" 💾 Full report: {filepath}")
        print(f" 📝 Use this report to create your extractor!")
        print(f"{'═'*55}\n")


# ══════════════════════════════════════════════
#  CONVENIENCE FUNCTION
# ══════════════════════════════════════════════

def run_diagnostic(
    url: str,
    config: Optional[DiagnosticConfig] = None
) -> DiagnosticResult:
    """
    Convenience function — jalankan diagnostic satu baris.
    
    Usage:
        from core.orchestrator import run_diagnostic
        result = run_diagnostic("https://example.com/video/123")
    """
    config = config or DEFAULT_CONFIG
    orchestrator = Orchestrator(config)
    
    return asyncio.run(orchestrator.diagnose(url))
