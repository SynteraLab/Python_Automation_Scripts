"""
layer_05_streaming.py — Streaming Media Analysis

- Detect HLS streams (m3u8 manifests)
- Detect DASH streams (mpd manifests)
- Parse manifests → extract quality variants
- Detect DRM protection
- Analyze segment patterns
- Detect token/signed URL patterns
- Check manifest accessibility
"""

import re
import json
import logging
from typing import Dict, List, Set, Optional
from urllib.parse import urljoin, urlparse

try:
    import m3u8
    HAS_M3U8 = True
except ImportError:
    HAS_M3U8 = False

try:
    import xmltodict
    HAS_XMLTODICT = True
except ImportError:
    HAS_XMLTODICT = False

from layers.base import BaseLayer
from core.browser import BrowserCaptureResult, CapturedRequest
from core.session import SessionManager
from utils.media_types import MediaTypes, DRM_SIGNATURES
from utils.url_utils import URLUtils

logger = logging.getLogger(__name__)


class StreamingAnalysisLayer(BaseLayer):
    
    LAYER_NAME = "layer_05_streaming"
    LAYER_DESCRIPTION = "Streaming Media (HLS/DASH/DRM) Analysis"
    
    async def execute(self, url, recon, capture, session):
        """Full streaming analysis"""
        
        seen: Set[str] = set()
        base_url = url
        
        # ── 1. Collect streaming URLs ──
        streaming_urls = self._collect_streaming_urls(capture)
        logger.info(f"      Found {len(streaming_urls)} streaming URLs")
        
        # ── 2. Analyze HLS manifests ──
        hls_urls = [u for u in streaming_urls if '.m3u8' in u['url'].lower()]
        for hls in hls_urls:
            self._analyze_hls(hls, session, base_url, seen)
        
        # ── 3. Analyze DASH manifests ──
        dash_urls = [u for u in streaming_urls if '.mpd' in u['url'].lower()]
        for dash in dash_urls:
            self._analyze_dash(dash, session, base_url, seen)
        
        # ── 4. Detect DRM ──
        self._detect_drm(capture, base_url)
        
        # ── 5. Analyze streaming segments ──
        self._analyze_segments(capture)
        
        # ── 6. Check for alternative quality URLs ──
        self._detect_quality_variants(streaming_urls, session, base_url)
    
    def _collect_streaming_urls(
        self, capture: BrowserCaptureResult
    ) -> List[Dict]:
        """Collect semua streaming-related URLs"""
        streaming = []
        seen = set()
        
        if not capture:
            return streaming
        
        for req in capture.requests:
            url_lower = req.url.lower().split('?')[0]
            
            is_streaming = (
                url_lower.endswith('.m3u8') or
                url_lower.endswith('.mpd') or
                url_lower.endswith('.m3u') or
                'mpegurl' in (req.content_type or '').lower() or
                'dash+xml' in (req.content_type or '').lower()
            )
            
            if is_streaming and req.url not in seen:
                seen.add(req.url)
                streaming.append({
                    'url': req.url,
                    'content_type': req.content_type,
                    'status': req.status,
                    'headers': dict(req.headers),
                    'response_headers': dict(req.response_headers),
                    'response_body': req.response_body,
                    'size': req.response_size,
                })
        
        return streaming
    
    # ══════════════════════════════════════════
    #  HLS ANALYSIS
    # ══════════════════════════════════════════
    
    def _analyze_hls(
        self, hls_info: Dict, session: SessionManager,
        base_url: str, seen: Set[str]
    ):
        """Deep analyze HLS manifest"""
        manifest_url = hls_info['url']
        manifest_body = hls_info.get('response_body', '')
        
        # Download manifest jika belum punya body
        if not manifest_body:
            resp = session.get(
                manifest_url, timeout=10,
                extra_headers={
                    'Referer': base_url,
                    'Origin': URLUtils.get_base_url(base_url),
                }
            )
            if resp and resp.body:
                manifest_body = resp.body
        
        if not manifest_body:
            self.add_finding(
                category='streaming',
                subcategory='hls_manifest',
                url=manifest_url,
                data={
                    'accessible': False,
                    'status': hls_info.get('status'),
                    'error': 'Could not download manifest',
                },
                confidence=0.8,
                source='HLS analysis',
            )
            return
        
        # ── Parse manifest ──
        manifest_data = self._parse_hls_manifest(
            manifest_body, manifest_url
        )
        
        # URL expiry analysis
        expiry = URLUtils.detect_url_expiry(manifest_url)
        
        # Build finding
        finding_data = {
            'manifest_url': manifest_url,
            'manifest_type': manifest_data['type'],
            'accessible': True,
            'url_expiry': expiry,
            'request_headers': hls_info.get('headers', {}),
            'response_headers': hls_info.get('response_headers', {}),
        }
        
        if manifest_data['type'] == 'master':
            finding_data['variants'] = manifest_data['variants']
            finding_data['quality_count'] = len(manifest_data['variants'])
            
            self.add_finding(
                category='streaming',
                subcategory='hls_master',
                url=manifest_url,
                data=finding_data,
                confidence=1.0,
                source='HLS master manifest',
            )
            
            # Probe each variant
            for variant in manifest_data['variants']:
                variant_url = variant.get('url', '')
                if variant_url and variant_url not in seen:
                    seen.add(variant_url)
                    self.add_finding(
                        category='streaming',
                        subcategory='hls_variant',
                        url=variant_url,
                        data={
                            'bandwidth': variant.get('bandwidth'),
                            'resolution': variant.get('resolution'),
                            'codecs': variant.get('codecs'),
                            'parent_manifest': manifest_url,
                        },
                        confidence=1.0,
                        source='HLS variant playlist',
                    )
        
        elif manifest_data['type'] == 'media':
            finding_data['segment_count'] = manifest_data['segment_count']
            finding_data['total_duration'] = manifest_data['total_duration']
            finding_data['segment_pattern'] = manifest_data.get('segment_pattern', '')
            finding_data['encryption'] = manifest_data.get('encryption')
            
            self.add_finding(
                category='streaming',
                subcategory='hls_media',
                url=manifest_url,
                data=finding_data,
                confidence=1.0,
                source='HLS media playlist',
            )
    
    def _parse_hls_manifest(self, body: str, manifest_url: str) -> Dict:
        """Parse HLS m3u8 manifest"""
        result = {
            'type': 'unknown',
            'variants': [],
            'segment_count': 0,
            'total_duration': 0,
            'encryption': None,
        }
        
        # ── Menggunakan m3u8 library (jika ada) ──
        if HAS_M3U8:
            try:
                playlist = m3u8.loads(body, uri=manifest_url)
                
                if playlist.is_variant:
                    result['type'] = 'master'
                    for p in playlist.playlists:
                        variant = {
                            'url': p.absolute_uri,
                            'bandwidth': p.stream_info.bandwidth if p.stream_info else None,
                            'resolution': (
                                f"{p.stream_info.resolution[0]}x{p.stream_info.resolution[1]}"
                                if p.stream_info and p.stream_info.resolution else None
                            ),
                            'codecs': p.stream_info.codecs if p.stream_info else None,
                        }
                        result['variants'].append(variant)
                else:
                    result['type'] = 'media'
                    result['segment_count'] = len(playlist.segments)
                    result['total_duration'] = sum(
                        s.duration for s in playlist.segments
                    )
                    
                    # Check encryption
                    for key in playlist.keys:
                        if key and key.method and key.method != 'NONE':
                            result['encryption'] = {
                                'method': key.method,
                                'uri': key.uri,
                                'iv': key.iv,
                            }
                    
                    # Segment pattern
                    if playlist.segments:
                        first_seg = playlist.segments[0].absolute_uri
                        last_seg = playlist.segments[-1].absolute_uri
                        result['segment_pattern'] = self._detect_segment_pattern(
                            [s.absolute_uri for s in playlist.segments[:5]]
                        )
                        result['first_segment'] = first_seg
                        result['last_segment'] = last_seg
                
                return result
                
            except Exception as e:
                logger.debug(f"m3u8 library parse error: {e}")
        
        # ── Fallback: Manual parsing ──
        lines = body.strip().split('\n')
        
        if '#EXT-X-STREAM-INF' in body:
            result['type'] = 'master'
            
            for i, line in enumerate(lines):
                if line.startswith('#EXT-X-STREAM-INF'):
                    # Parse attributes
                    attrs = self._parse_hls_attributes(line)
                    
                    # Next line is URL
                    if i + 1 < len(lines):
                        variant_uri = lines[i + 1].strip()
                        if variant_uri and not variant_uri.startswith('#'):
                            full_url = urljoin(manifest_url, variant_uri)
                            variant = {
                                'url': full_url,
                                'bandwidth': attrs.get('BANDWIDTH'),
                                'resolution': attrs.get('RESOLUTION'),
                                'codecs': attrs.get('CODECS'),
                            }
                            result['variants'].append(variant)
        else:
            result['type'] = 'media'
            duration = 0
            seg_count = 0
            
            for line in lines:
                if line.startswith('#EXTINF:'):
                    try:
                        dur = float(line.split(':')[1].split(',')[0])
                        duration += dur
                    except (ValueError, IndexError):
                        pass
                elif line.startswith('#EXT-X-KEY'):
                    attrs = self._parse_hls_attributes(line)
                    method = attrs.get('METHOD', 'NONE')
                    if method != 'NONE':
                        result['encryption'] = {
                            'method': method,
                            'uri': attrs.get('URI', ''),
                            'iv': attrs.get('IV', ''),
                        }
                elif not line.startswith('#') and line.strip():
                    seg_count += 1
            
            result['segment_count'] = seg_count
            result['total_duration'] = duration
        
        return result
    
    def _parse_hls_attributes(self, line: str) -> Dict:
        """Parse HLS tag attributes"""
        attrs = {}
        # Match KEY=VALUE or KEY="VALUE"
        pattern = r'([A-Z-]+)=(?:"([^"]*)"|([\w.]+))'
        for m in re.finditer(pattern, line):
            key = m.group(1)
            value = m.group(2) if m.group(2) is not None else m.group(3)
            attrs[key] = value
        return attrs
    
    def _detect_segment_pattern(self, segment_urls: List[str]) -> str:
        """Detect pattern dari segment URLs"""
        if not segment_urls:
            return ""
        
        if len(segment_urls) == 1:
            return segment_urls[0]
        
        # Coba detect numbering pattern
        patterns = []
        for url in segment_urls:
            # Replace numbers with {n}
            generalized = re.sub(r'\d+', '{n}', url)
            patterns.append(generalized)
        
        if len(set(patterns)) == 1:
            return patterns[0]
        
        return f"varied ({len(segment_urls)} segments)"
    
    # ══════════════════════════════════════════
    #  DASH ANALYSIS
    # ══════════════════════════════════════════
    
    def _analyze_dash(
        self, dash_info: Dict, session: SessionManager,
        base_url: str, seen: Set[str]
    ):
        """Deep analyze DASH manifest (MPD)"""
        manifest_url = dash_info['url']
        manifest_body = dash_info.get('response_body', '')
        
        if not manifest_body:
            resp = session.get(
                manifest_url, timeout=10,
                extra_headers={
                    'Referer': base_url,
                    'Origin': URLUtils.get_base_url(base_url),
                }
            )
            if resp and resp.body:
                manifest_body = resp.body
        
        if not manifest_body:
            self.add_finding(
                category='streaming',
                subcategory='dash_manifest',
                url=manifest_url,
                data={'accessible': False},
                confidence=0.8,
                source='DASH analysis',
            )
            return
        
        # Parse MPD
        dash_data = self._parse_dash_manifest(manifest_body, manifest_url)
        expiry = URLUtils.detect_url_expiry(manifest_url)
        
        self.add_finding(
            category='streaming',
            subcategory='dash_manifest',
            url=manifest_url,
            data={
                'accessible': True,
                'duration': dash_data.get('duration'),
                'min_buffer_time': dash_data.get('min_buffer_time'),
                'profiles': dash_data.get('profiles'),
                'adaptation_sets': dash_data.get('adaptation_sets', []),
                'drm_systems': dash_data.get('drm_systems', []),
                'url_expiry': expiry,
                'request_headers': dash_info.get('headers', {}),
            },
            confidence=1.0,
            source='DASH MPD analysis',
        )
    
    def _parse_dash_manifest(self, body: str, manifest_url: str) -> Dict:
        """Parse DASH MPD manifest"""
        result = {
            'duration': None,
            'min_buffer_time': None,
            'profiles': None,
            'adaptation_sets': [],
            'drm_systems': [],
        }
        
        if HAS_XMLTODICT:
            try:
                data = xmltodict.parse(body)
                mpd = data.get('MPD', {})
                
                result['duration'] = mpd.get('@mediaPresentationDuration')
                result['min_buffer_time'] = mpd.get('@minBufferTime')
                result['profiles'] = mpd.get('@profiles')
                
                # Parse Periods → AdaptationSets
                periods = mpd.get('Period', [])
                if isinstance(periods, dict):
                    periods = [periods]
                
                for period in periods:
                    adapt_sets = period.get('AdaptationSet', [])
                    if isinstance(adapt_sets, dict):
                        adapt_sets = [adapt_sets]
                    
                    for aset in adapt_sets:
                        adapt_info = {
                            'content_type': aset.get('@contentType', ''),
                            'mime_type': aset.get('@mimeType', ''),
                            'codecs': aset.get('@codecs', ''),
                            'lang': aset.get('@lang', ''),
                            'representations': [],
                        }
                        
                        reps = aset.get('Representation', [])
                        if isinstance(reps, dict):
                            reps = [reps]
                        
                        for rep in reps:
                            adapt_info['representations'].append({
                                'id': rep.get('@id'),
                                'bandwidth': rep.get('@bandwidth'),
                                'width': rep.get('@width'),
                                'height': rep.get('@height'),
                                'codecs': rep.get('@codecs', aset.get('@codecs', '')),
                            })
                        
                        # Check for DRM (ContentProtection)
                        protections = aset.get('ContentProtection', [])
                        if isinstance(protections, dict):
                            protections = [protections]
                        
                        for prot in protections:
                            scheme = prot.get('@schemeIdUri', '')
                            drm_info = {
                                'scheme': scheme,
                                'value': prot.get('@value', ''),
                            }
                            
                            # Identify DRM system
                            if 'edef8ba9' in scheme.lower():
                                drm_info['system'] = 'Widevine'
                            elif '9a04f079' in scheme.lower():
                                drm_info['system'] = 'PlayReady'
                            elif '94ce86fb' in scheme.lower():
                                drm_info['system'] = 'FairPlay'
                            elif '1077efec' in scheme.lower():
                                drm_info['system'] = 'ClearKey'
                            
                            result['drm_systems'].append(drm_info)
                        
                        result['adaptation_sets'].append(adapt_info)
                
                return result
                
            except Exception as e:
                logger.debug(f"DASH XML parse error: {e}")
        
        # ── Fallback: Regex parsing ──
        # Duration
        dur_match = re.search(
            r'mediaPresentationDuration="([^"]+)"', body
        )
        if dur_match:
            result['duration'] = dur_match.group(1)
        
        # Representations (bandwidth, width, height)
        for rep_match in re.finditer(
            r'<Representation[^>]*'
            r'(?:bandwidth="(\d+)")?[^>]*'
            r'(?:width="(\d+)")?[^>]*'
            r'(?:height="(\d+)")?',
            body
        ):
            bw, w, h = rep_match.groups()
            if bw or w or h:
                result['adaptation_sets'].append({
                    'bandwidth': bw,
                    'width': w,
                    'height': h,
                })
        
        # DRM detection
        for drm_match in re.finditer(
            r'<ContentProtection[^>]*schemeIdUri="([^"]+)"', body
        ):
            result['drm_systems'].append({
                'scheme': drm_match.group(1)
            })
        
        return result
    
    # ══════════════════════════════════════════
    #  DRM DETECTION
    # ══════════════════════════════════════════
    
    def _detect_drm(self, capture: BrowserCaptureResult, base_url: str):
        """Detect DRM systems dari network requests & HTML"""
        if not capture:
            return
        
        drm_found = set()
        
        # Check network requests untuk license servers
        for req in capture.requests:
            url_lower = req.url.lower()
            ct = (req.content_type or '').lower()
            
            for drm_name, sigs in DRM_SIGNATURES.items():
                for indicator in sigs['indicators']:
                    if indicator.lower() in url_lower or indicator.lower() in ct:
                        if drm_name not in drm_found:
                            drm_found.add(drm_name)
                            self.add_finding(
                                category='streaming',
                                subcategory='drm_detected',
                                url=req.url,
                                data={
                                    'drm_system': drm_name,
                                    'indicator': indicator,
                                    'request_type': req.resource_type,
                                    'content_type': req.content_type,
                                },
                                confidence=0.9,
                                source=f'DRM detection ({drm_name})',
                            )
                        break
        
        # Check HTML/JS untuk DRM references
        if capture.page_html:
            for drm_name, sigs in DRM_SIGNATURES.items():
                if drm_name in drm_found:
                    continue
                
                pattern = sigs.get('license_pattern', '')
                if pattern and re.search(pattern, capture.page_html, re.IGNORECASE):
                    drm_found.add(drm_name)
                    self.add_finding(
                        category='streaming',
                        subcategory='drm_detected',
                        data={
                            'drm_system': drm_name,
                            'detected_via': 'HTML/JS pattern',
                        },
                        confidence=0.75,
                        source=f'DRM pattern ({drm_name})',
                    )
    
    # ══════════════════════════════════════════
    #  SEGMENT ANALYSIS
    # ══════════════════════════════════════════
    
    def _analyze_segments(self, capture: BrowserCaptureResult):
        """Analyze streaming segments (.ts, .m4s)"""
        if not capture:
            return
        
        segments = []
        for req in capture.requests:
            url_lower = req.url.lower().split('?')[0]
            if url_lower.endswith(('.ts', '.m4s', '.m4v', '.m4a', '.fmp4')):
                segments.append({
                    'url': req.url,
                    'size': req.response_size,
                    'status': req.status,
                    'content_type': req.content_type,
                })
        
        if segments:
            # Detect segment pattern
            seg_urls = [s['url'] for s in segments]
            pattern = self._detect_segment_pattern(seg_urls[:10])
            
            total_size = sum(s['size'] for s in segments)
            
            self.add_finding(
                category='streaming',
                subcategory='segments_captured',
                data={
                    'segment_count': len(segments),
                    'total_size': total_size,
                    'total_size_mb': round(total_size / 1_000_000, 2),
                    'segment_pattern': pattern,
                    'first_segment': segments[0]['url'] if segments else '',
                    'sample_segments': [s['url'] for s in segments[:5]],
                },
                confidence=1.0,
                source='streaming segment analysis',
            )
    
    def _detect_quality_variants(
        self, streaming_urls: List[Dict],
        session: SessionManager, base_url: str
    ):
        """Try to find alternative quality URLs"""
        quality_patterns = [
            ('240p', ['240', '240p', 'low']),
            ('360p', ['360', '360p']),
            ('480p', ['480', '480p', 'sd']),
            ('720p', ['720', '720p', 'hd']),
            ('1080p', ['1080', '1080p', 'fhd', 'fullhd']),
            ('1440p', ['1440', '1440p', '2k']),
            ('2160p', ['2160', '2160p', '4k', 'uhd']),
        ]
        
        for s_info in streaming_urls:
            url = s_info['url']
            
            # Detect quality in URL
            detected_quality = None
            for quality_name, indicators in quality_patterns:
                if any(ind in url.lower() for ind in indicators):
                    detected_quality = quality_name
                    break
            
            if detected_quality:
                self.add_finding(
                    category='info',
                    subcategory='quality_detected',
                    url=url,
                    data={
                        'quality': detected_quality,
                        'url': url,
                    },
                    confidence=0.8,
                    source='quality pattern detection',
                )