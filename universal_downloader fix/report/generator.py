"""
generator.py — Prompt-Ready Report Generator

Mengubah DiagnosticResult menjadi laporan JSON yang bisa
langsung di-paste ke prompt AI untuk membuat extractor.
"""

import json
import os
import time
import logging
from typing import Dict, List, Any, Optional
from collections import defaultdict

from config import DiagnosticConfig, DEFAULT_CONFIG

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generate prompt-ready diagnostic report"""

    def __init__(self, config: Optional[DiagnosticConfig] = None):
        self.config = config or DEFAULT_CONFIG

    def generate(self, diagnostic_result) -> Dict:
        """
        Generate final report dari DiagnosticResult.
        Output ini LANGSUNG bisa di-paste ke prompt AI.
        """
        result = diagnostic_result
        report = {}

        # ── Header ──
        report['diagnosis_id'] = result.diagnosis_id
        report['target_url'] = result.target_url
        report['scan_timestamp'] = result.timestamp
        report['scan_duration_seconds'] = round(result.duration, 2)

        # ── 1. Site Profile ──
        report['site_profile'] = self._build_site_profile(result)

        # ── 2. Media Found ──
        report['media_found'] = self._build_media_section(result)

        # ── 3. API Endpoints ──
        report['api_endpoints'] = self._build_api_section(result)

        # ── 4. Streaming Info ──
        report['streaming'] = self._build_streaming_section(result)

        # ── 5. Server Map ──
        report['server_map'] = self._build_server_map_section(result)

        # ── 6. Access Requirements ──
        report['access_requirements'] = self._build_access_section(result)

        # ── 7. Extraction Strategy ──
        report['extraction_strategy'] = self._build_strategy(result)

        # ── 8. Reproducible Evidence ──
        report['evidence'] = self._build_evidence(result)

        # ── 9. Warnings & Issues ──
        report['issues'] = self._build_issues(result)

        # ── Summary for Prompt ──
        report['prompt_summary'] = self._build_prompt_summary(report)

        return report

    def _build_site_profile(self, result) -> Dict:
        """Build comprehensive site profile"""
        profile = result.site_profile.copy() if result.site_profile else {}

        # Enrich from layer results
        for layer_name, layer_result in result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue
            for finding in layer_result.findings:
                if finding.category == 'info':
                    if finding.subcategory == 'video_player':
                        profile.setdefault('video_players_detail', []).append(
                            finding.data
                        )
                    elif finding.subcategory == 'cms_framework':
                        profile.setdefault('technologies', []).append(
                            finding.data
                        )
                    elif finding.subcategory == 'media_storage':
                        profile.setdefault('media_storage', []).append(
                            finding.data
                        )
                    elif finding.subcategory == 'server_technology':
                        profile['server_details'] = finding.data

        return profile

    def _build_media_section(self, result) -> Dict:
        """Build media section — all media found across all layers"""
        all_media = []
        seen_urls = set()

        # From aggregated results
        for m in result.media_found:
            url = m.get('url', '')
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_media.append(m)

        # From layer findings
        for layer_name, layer_result in result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue
            for finding in layer_result.findings:
                if finding.category == 'media' and finding.url:
                    if finding.url not in seen_urls:
                        seen_urls.add(finding.url)
                        all_media.append({
                            'url': finding.url,
                            'type': finding.subcategory,
                            'confidence': finding.confidence,
                            'discovered_via': finding.source,
                            'layer': layer_name,
                            'details': finding.data,
                        })

        # Group by type
        by_type = defaultdict(list)
        for m in all_media:
            by_type[m.get('type', 'unknown')].append(m)

        return {
            'total_count': len(all_media),
            'by_type': {k: len(v) for k, v in by_type.items()},
            'items': all_media,
        }

    def _build_api_section(self, result) -> Dict:
        """Build API endpoints section"""
        all_apis = []
        seen = set()

        for ep in result.api_endpoints:
            url = ep.get('url', '')
            if url and url not in seen:
                seen.add(url)
                all_apis.append(ep)

        for layer_name, layer_result in result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue
            for finding in layer_result.findings:
                if finding.category == 'api' and finding.url:
                    if finding.url not in seen:
                        seen.add(finding.url)
                        all_apis.append({
                            'url': finding.url,
                            'type': finding.subcategory,
                            'discovered_via': finding.source,
                            'layer': layer_name,
                            'details': finding.data,
                        })

        return {
            'total_count': len(all_apis),
            'items': all_apis,
        }

    def _build_streaming_section(self, result) -> Dict:
        """Build streaming analysis section"""
        streaming_data: Dict[str, Any] = {
            'hls': [],
            'dash': [],
            'drm': [],
            'segments': [],
        }

        for layer_name, layer_result in result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue
            for finding in layer_result.findings:
                if finding.category == 'streaming':
                    if 'hls' in finding.subcategory:
                        streaming_data['hls'].append({
                            'url': finding.url,
                            'subcategory': finding.subcategory,
                            'details': finding.data,
                        })
                    elif 'dash' in finding.subcategory:
                        streaming_data['dash'].append({
                            'url': finding.url,
                            'subcategory': finding.subcategory,
                            'details': finding.data,
                        })
                    elif 'drm' in finding.subcategory:
                        streaming_data['drm'].append(finding.data)
                    elif 'segment' in finding.subcategory:
                        streaming_data['segments'].append(finding.data)

        streaming_data['has_streaming'] = bool(
            streaming_data['hls'] or streaming_data['dash']
        )
        streaming_data['has_drm'] = bool(streaming_data['drm'])

        return streaming_data

    def _build_server_map_section(self, result) -> Dict:
        """Build server map section untuk report"""
        server_map = {
            'total_servers': 0,
            'servers': [],
            'embed_services_used': [],
        }

        existing = getattr(result, 'multi_server_analysis', None) or {}
        if existing:
            server_map['total_servers'] = existing.get('server_count', 0)
            server_map['servers'] = existing.get('servers', [])
            server_map['embed_services_used'] = existing.get(
                'embed_services', []
            )

            if server_map['servers'] or server_map['total_servers']:
                return server_map

        for layer_name, layer_result in result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue

            for finding in layer_result.findings:
                if finding.subcategory == 'server_map':
                    return finding.data

                if finding.category == 'server' and finding.subcategory in (
                    'probed_server', 'server_option'
                ):
                    server_map['servers'].append({
                        'name': finding.data.get(
                            'server_name',
                            finding.data.get('name', '')
                        ),
                        'embed_service': finding.data.get(
                            'embed_service', 'unknown'
                        ),
                        'iframe_url': finding.data.get(
                            'iframe_url',
                            finding.data.get('embed_url', '')
                        ),
                        'media_count': finding.data.get('media_count', 0),
                        'streaming_count': finding.data.get(
                            'streaming_count', 0
                        ),
                        'quality': finding.data.get('quality', ''),
                        'sub_dub': finding.data.get('sub_or_dub', ''),
                        'extraction_hints': finding.data.get(
                            'extraction_hints', {}
                        ),
                    })

        server_map['total_servers'] = len(server_map['servers'])
        server_map['embed_services_used'] = list(set(
            s['embed_service'] for s in server_map['servers']
            if s['embed_service'] != 'unknown'
        ))

        return server_map

    def _build_access_section(self, result) -> Dict:
        """Build access requirements section"""
        access = result.access_requirements.copy() if result.access_requirements else {}

        # Enrich from layers
        for layer_name, layer_result in result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue
            for finding in layer_result.findings:
                if finding.category == 'info':
                    if finding.subcategory == 'referer_requirement':
                        access['referer_test'] = finding.data
                    elif finding.subcategory == 'signed_urls':
                        access['signed_urls'] = finding.data
                    elif finding.subcategory == 'cookie_analysis':
                        access['cookies_detail'] = finding.data
                    elif finding.subcategory == 'cors_policy':
                        access['cors'] = finding.data
                    elif finding.subcategory == 'authentication':
                        access['authentication'] = finding.data
                    elif finding.subcategory == 'session_flow':
                        access['session_flow'] = finding.data
                    elif finding.subcategory == 'rate_limiting':
                        access['rate_limiting'] = finding.data

        return access

    def _build_strategy(self, result) -> Dict:
        """Build extraction strategy recommendation"""
        strategy = result.extraction_strategy.copy() if result.extraction_strategy else {}

        # Auto-determine based on findings
        has_streaming = False
        has_drm = False
        has_signed_urls = False
        has_lazy = False
        needs_browser = False

        for layer_name, layer_result in result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue
            for f in layer_result.findings:
                if f.category == 'streaming':
                    has_streaming = True
                    if 'drm' in f.subcategory:
                        has_drm = True
                if f.subcategory == 'signed_urls':
                    has_signed_urls = True
                if f.subcategory == 'lazy_loading':
                    has_lazy = True
                if f.subcategory == 'shadow_dom':
                    needs_browser = True

        # Build steps
        steps = []
        libraries = ['requests']
        complexity = 'low'

        if needs_browser or has_lazy:
            steps.append("1. Use Playwright to render page and capture network")
            libraries.append('playwright')
            complexity = 'medium'

        if has_signed_urls:
            steps.append("2. Extract signed URL from API response (URL expires)")
            complexity = 'medium'

        if has_streaming:
            if has_drm:
                steps.append("⚠️ DRM detected — extraction may be restricted")
                complexity = 'very_high'
            else:
                steps.append("3. Download m3u8/mpd manifest")
                steps.append("4. Parse manifest for segment URLs")
                steps.append("5. Download all segments")
                steps.append("6. Merge segments with ffmpeg")
                libraries.extend(['m3u8', 'ffmpeg-python'])
                complexity = 'medium'

        if not steps:
            steps.append("1. Use requests with proper headers/cookies")
            steps.append("2. Download media files directly")

        strategy.update({
            'recommended_method': 'playwright' if needs_browser else 'requests',
            'browser_required': needs_browser or has_lazy,
            'steps': steps,
            'libraries_needed': list(set(libraries)),
            'estimated_complexity': complexity,
            'has_streaming': has_streaming,
            'has_drm': has_drm,
            'has_signed_urls': has_signed_urls,
        })

        return strategy

    def _build_evidence(self, result) -> Dict:
        """Build reproducible evidence"""
        evidence = result.reproducible_evidence.copy() if result.reproducible_evidence else {}

        # Add curl commands from layer findings
        if not evidence.get('curl_commands'):
            evidence['curl_commands'] = []

        if result.browser_capture:
            for req in result.browser_capture.requests:
                if req.is_media and req.status == 200:
                    curl_parts = ['curl']

                    important_headers = ['referer', 'origin', 'cookie',
                                         'authorization', 'user-agent']
                    for key, value in req.headers.items():
                        if key.lower() in important_headers:
                            curl_parts.append(f"-H '{key}: {value}'")

                    curl_parts.append(f"'{req.url}'")
                    curl_cmd = ' \\\n  '.join(curl_parts)

                    evidence['curl_commands'].append({
                        'media_type': req.media_type,
                        'url': req.url,
                        'command': curl_cmd,
                    })

                    if len(evidence['curl_commands']) >= 10:
                        break

        return evidence

    def _build_issues(self, result) -> Dict:
        """Build issues & warnings"""
        issues = {
            'errors': result.errors or [],
            'warnings': result.warnings or [],
            'potential_blockers': [],
        }

        # Check for blockers
        for layer_name, layer_result in result.layer_results.items():
            if not hasattr(layer_result, 'findings'):
                continue
            for f in layer_result.findings:
                if 'drm' in f.subcategory.lower():
                    issues['potential_blockers'].append(
                        'DRM protection detected — may block extraction'
                    )
                if f.subcategory == 'rate_limiting' and f.data.get('detected'):
                    issues['potential_blockers'].append(
                        'Rate limiting detected — add delays between requests'
                    )

        issues['potential_blockers'] = list(set(issues['potential_blockers']))
        return issues

    def _build_prompt_summary(self, report: Dict) -> str:
        """Build a text summary optimized for AI prompts"""
        lines = []
        lines.append("=== DIAGNOSTIC SUMMARY ===")
        lines.append(f"Target: {report.get('target_url', 'N/A')}")

        sp = report.get('site_profile', {})
        lines.append(f"CMS: {sp.get('cms', 'unknown')}")
        lines.append(f"Framework: {sp.get('framework', 'unknown')}")
        lines.append(f"CDNs: {', '.join(sp.get('cdns', [])) or 'none'}")
        lines.append(f"Video Players: {', '.join(sp.get('video_players', [])) or 'none'}")

        mf = report.get('media_found', {})
        lines.append(f"\nMedia Found: {mf.get('total_count', 0)} items")
        for mtype, count in mf.get('by_type', {}).items():
            lines.append(f"  - {mtype}: {count}")

        api = report.get('api_endpoints', {})
        lines.append(f"\nAPI Endpoints: {api.get('total_count', 0)}")

        st = report.get('streaming', {})
        lines.append(f"Streaming: {'Yes' if st.get('has_streaming') else 'No'}")
        lines.append(f"DRM: {'Yes' if st.get('has_drm') else 'No'}")

        sm = report.get('server_map', {})
        if sm.get('total_servers', 0) > 0:
            lines.append(f"\nServer/Sources: {sm['total_servers']} detected")
            lines.append(
                f"Embed Services: {', '.join(sm.get('embed_services_used', [])) or 'none'}"
            )
            for srv in sm.get('servers', []):
                lines.append(
                    f"  [{srv.get('name', '?')}] "
                    f"-> {srv.get('embed_service', '?')} "
                    f"| media={srv.get('media_count', 0)} "
                    f"| stream={srv.get('streaming_count', 0)}"
                )

        strat = report.get('extraction_strategy', {})
        lines.append(f"\nRecommended Method: {strat.get('recommended_method', 'N/A')}")
        lines.append(f"Browser Required: {strat.get('browser_required', False)}")
        lines.append(f"Complexity: {strat.get('estimated_complexity', 'N/A')}")
        lines.append(f"Libraries: {', '.join(strat.get('libraries_needed', []))}")

        if strat.get('steps'):
            lines.append("\nExtraction Steps:")
            for step in strat['steps']:
                lines.append(f"  {step}")

        issues = report.get('issues', {})
        blockers = issues.get('potential_blockers', [])
        if blockers:
            lines.append("\n⚠️ BLOCKERS:")
            for b in blockers:
                lines.append(f"  - {b}")

        return '\n'.join(lines)

    def save(self, report: Dict, output_dir: Optional[str] = None):
        """Save report ke file"""
        out_dir = output_dir or self.config.report.output_dir
        os.makedirs(out_dir, exist_ok=True)

        diag_id = report.get('diagnosis_id', f'diag_{int(time.time())}')

        # ── JSON report ──
        json_path = os.path.join(out_dir, f"{diag_id}.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False, default=str)

        # ── Text summary ──
        txt_path = os.path.join(out_dir, f"{diag_id}_summary.txt")
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(report.get('prompt_summary', ''))

        logger.info(f"💾 Report saved: {json_path}")
        logger.info(f"💾 Summary saved: {txt_path}")

        return json_path, txt_path
