"""
resolution_pipeline/static_analysis.py

Structured static evidence extraction from raw page/embed inputs.
Replaces simplistic single-domain-string checks with multi-signal,
scored, explainable evidence production.

Gaps closed:
    G2 — static analysis wired to richer embed intelligence
    G7 — legacy fallback path when v2 is disabled
    G8 — trace steps recorded for every analysis sub-phase

External dependencies (ADAPTATION POINTs):
    - Phase 3 player detector interface for player hint extraction
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .models import (
    AmbiguityLevel,
    DomainSignal,
    EvidenceBundle,
    EvidenceItem,
    PlayerDetectionEvidence,
    RawInputContext,
    ResolutionPath,
    ResolutionTrace,
    SignalSource,
    StaticAnalysisResult,
    TraceStepPhase,
    trace_timer,
)
from .settings import IntegrationSettings


# ──────────────────────────────────────────────
# Domain Intelligence Database
# ──────────────────────────────────────────────
# This maps known domain fragments to service names.
# NOT an extractor — just a signal for candidate scoring.
# The real repo can extend this via config or Phase 2 overrides.

_DOMAIN_SERVICE_MAP: Dict[str, str] = {
    # ADAPTATION POINT: extend or replace with real repo's domain DB
    "fembed": "fembed",
    "femax": "fembed",
    "fcdn": "fembed",
    "dutrag": "fembed",
    "mixdrop": "mixdrop",
    "upstream": "upstream",
    "streamtape": "streamtape",
    "doodstream": "doodstream",
    "dood": "doodstream",
    "filemoon": "filemoon",
    "moonplayer": "filemoon",
    "vidguard": "vidguard",
    "vgfplay": "vidguard",
    "mp4upload": "mp4upload",
    "streamwish": "streamwish",
    "swish": "streamwish",
    "voe": "voe",
    "vidoza": "vidoza",
    "supervideo": "supervideo",
    "okru": "okru",
    "ok.ru": "okru",
    "yourupload": "yourupload",
    "uqload": "uqload",
    "wolfstream": "wolfstream",
    "streamlare": "streamlare",
    "streamsb": "streamsb",
    "sbembed": "streamsb",
    "sbplay": "streamsb",
    "lvturbo": "streamlare",
    "turbo": "turbovideos",
    "burstcloud": "burstcloud",
    "netu": "netu",
    "waaw": "netu",
    "hxfile": "hxfile",
    "hexload": "hxfile",
    "vtube": "vtube",
    "saruch": "saruch",
    "mega": "mega",
    "gdrive": "gdrive",
    "drive.google": "gdrive",
    "sendvid": "sendvid",
    "dailymotion": "dailymotion",
    "youtube": "youtube",
    "youtu.be": "youtube",
}

# Pattern-based domain matching for domains not in the exact map
_DOMAIN_PATTERNS: List[Tuple[re.Pattern, str, float]] = [
    # (compiled pattern, service_name, confidence)
    # ADAPTATION POINT: extend with real repo patterns
    (re.compile(r"fem(?:bed|ax|cdn)", re.IGNORECASE), "fembed", 0.80),
    (re.compile(r"(?:stream|s)tape", re.IGNORECASE), "streamtape", 0.85),
    (re.compile(r"dood(?:stream|\.)", re.IGNORECASE), "doodstream", 0.85),
    (re.compile(r"file\s*moon", re.IGNORECASE), "filemoon", 0.80),
    (re.compile(r"mix\s*drop", re.IGNORECASE), "mixdrop", 0.85),
    (re.compile(r"vid\s*guard", re.IGNORECASE), "vidguard", 0.80),
    (re.compile(r"stream\s*wish", re.IGNORECASE), "streamwish", 0.80),
    (re.compile(r"stream\s*sb|sb(?:embed|play|full)", re.IGNORECASE), "streamsb", 0.80),
    (re.compile(r"up\s*stream", re.IGNORECASE), "upstream", 0.75),
]

# Known embed/iframe attribute patterns
_EMBED_ATTR_PATTERNS: List[Tuple[re.Pattern, str, float]] = [
    # ADAPTATION POINT: extend with real patterns
    (re.compile(r'data-(?:src|url)\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE), "embed_data_src", 0.70),
    (re.compile(r'allowfullscreen', re.IGNORECASE), "embed_fullscreen_hint", 0.20),
]

# Known player script patterns (generic, not service-specific extractors)
_SCRIPT_PLAYER_PATTERNS: List[Tuple[re.Pattern, str, str, float]] = [
    # (pattern, player_name, signal_key, confidence)
    # ADAPTATION POINT: Phase 3 player DB should override these
    (re.compile(r'clappr(?:\.min)?\.js', re.IGNORECASE), "clappr", "script_clappr", 0.85),
    (re.compile(r'plyr(?:\.min)?\.js', re.IGNORECASE), "plyr", "script_plyr", 0.80),
    (re.compile(r'video\.js|videojs', re.IGNORECASE), "videojs", "script_videojs", 0.80),
    (re.compile(r'jwplayer(?:\.min)?\.js', re.IGNORECASE), "jwplayer", "script_jwplayer", 0.85),
    (re.compile(r'flowplayer(?:\.min)?\.js', re.IGNORECASE), "flowplayer", "script_flowplayer", 0.80),
    (re.compile(r'hls(?:\.min)?\.js', re.IGNORECASE), "hlsjs", "script_hlsjs", 0.75),
    (re.compile(r'dash(?:\.all)?(?:\.min)?\.js', re.IGNORECASE), "dashjs", "script_dashjs", 0.75),
    (re.compile(r'shaka[\-_]?player', re.IGNORECASE), "shaka", "script_shaka", 0.75),
    (re.compile(r'mediaelement(?:\.min)?\.js', re.IGNORECASE), "mediaelement", "script_mediaelement", 0.70),
]

# Iframe src extraction regex
_IFRAME_SRC_RE = re.compile(
    r'<iframe[^>]+src\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)

# Embed tag src extraction regex
_EMBED_SRC_RE = re.compile(
    r'<embed[^>]+src\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)

# Object/param extraction regex
_OBJECT_DATA_RE = re.compile(
    r'<object[^>]+data\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)

# Script src extraction regex
_SCRIPT_SRC_RE = re.compile(
    r'<script[^>]+src\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE | re.DOTALL,
)


# ──────────────────────────────────────────────
# Domain Extraction Helpers
# ──────────────────────────────────────────────

def _safe_parse_domain(url: str) -> Optional[str]:
    """
    Extract domain from a URL safely.
    Returns None if parsing fails.
    """
    if not url or not url.strip():
        return None
    try:
        # Handle protocol-relative URLs
        if url.startswith("//"):
            url = "https:" + url
        elif not url.startswith(("http://", "https://")):
            # Might be a bare domain or path
            if "." in url.split("/")[0]:
                url = "https://" + url
            else:
                return None
        parsed = urlparse(url)
        domain = parsed.hostname
        if domain:
            return domain.lower().strip(".")
    except Exception:
        pass
    return None


def _domain_to_service(domain: str) -> Tuple[Optional[str], float]:
    """
    Look up a domain in the service map.
    Returns (service_name, confidence) or (None, 0.0).

    Uses exact substring matching first, then pattern matching.
    """
    domain_lower = domain.lower()

    # Exact substring match against known fragments
    for fragment, service in _DOMAIN_SERVICE_MAP.items():
        if fragment in domain_lower:
            return service, 0.90

    # Pattern-based match
    for pattern, service, confidence in _DOMAIN_PATTERNS:
        if pattern.search(domain_lower):
            return service, confidence

    return None, 0.0


# ──────────────────────────────────────────────
# Evidence Extraction Functions
# ──────────────────────────────────────────────

def _extract_domain_signals(
    urls: List[str],
    page_url: str,
    html: str,
) -> List[DomainSignal]:
    """
    Extract domain signals from explicit URLs and from
    iframe/embed tags found in HTML.
    """
    signals: List[DomainSignal] = []
    seen_domains: set = set()

    # Process explicit URLs
    all_urls = list(urls)
    if page_url:
        all_urls.insert(0, page_url)

    for url in all_urls:
        domain = _safe_parse_domain(url)
        if domain and domain not in seen_domains:
            seen_domains.add(domain)
            service, confidence = _domain_to_service(domain)
            signals.append(DomainSignal(
                domain=domain,
                full_url=url,
                source_type="page" if url == page_url else "explicit",
                inferred_service=service,
                confidence=confidence,
            ))

    # Extract iframe src domains from HTML
    if html:
        for match in _IFRAME_SRC_RE.finditer(html):
            iframe_url = match.group(1)
            domain = _safe_parse_domain(iframe_url)
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                service, confidence = _domain_to_service(domain)
                signals.append(DomainSignal(
                    domain=domain,
                    full_url=iframe_url,
                    source_type="iframe",
                    inferred_service=service,
                    confidence=confidence,
                ))

        # Extract embed src domains from HTML
        for match in _EMBED_SRC_RE.finditer(html):
            embed_url = match.group(1)
            domain = _safe_parse_domain(embed_url)
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                service, confidence = _domain_to_service(domain)
                signals.append(DomainSignal(
                    domain=domain,
                    full_url=embed_url,
                    source_type="embed",
                    inferred_service=service,
                    confidence=confidence,
                ))

        # Extract object data domains from HTML
        for match in _OBJECT_DATA_RE.finditer(html):
            obj_url = match.group(1)
            domain = _safe_parse_domain(obj_url)
            if domain and domain not in seen_domains:
                seen_domains.add(domain)
                service, confidence = _domain_to_service(domain)
                signals.append(DomainSignal(
                    domain=domain,
                    full_url=obj_url,
                    source_type="object",
                    inferred_service=service,
                    confidence=confidence,
                ))

    return signals


def _extract_embed_signals(embed_codes: List[str], html: str) -> List[EvidenceItem]:
    """
    Extract evidence from embed codes and embed-related HTML patterns.
    """
    items: List[EvidenceItem] = []

    all_sources = list(embed_codes)
    if html:
        all_sources.append(html)

    for source in all_sources:
        for pattern, key, confidence in _EMBED_ATTR_PATTERNS:
            for match in pattern.finditer(source):
                raw_value = match.group(0)
                # If we captured a URL group, try to resolve domain
                url_value = match.group(1) if match.lastindex and match.lastindex >= 1 else None
                service = None
                if url_value:
                    domain = _safe_parse_domain(url_value)
                    if domain:
                        service, _ = _domain_to_service(domain)

                items.append(EvidenceItem(
                    source=SignalSource.EMBED,
                    key=key,
                    value=service or url_value or raw_value,
                    weight=0.15,
                    confidence=confidence,
                    raw=raw_value[:200],
                    explanation=f"Embed attribute pattern '{key}' matched",
                ))

    return items


def _extract_iframe_signals(
    iframe_urls: List[str],
    html: str,
) -> List[EvidenceItem]:
    """
    Extract evidence specifically from iframe context.
    Focuses on the relationship between iframe domains and known services.
    """
    items: List[EvidenceItem] = []
    seen: set = set()

    # Explicit iframe URLs
    all_iframe_urls = list(iframe_urls)

    # Also parse iframes from HTML
    if html:
        for match in _IFRAME_SRC_RE.finditer(html):
            url = match.group(1)
            if url not in all_iframe_urls:
                all_iframe_urls.append(url)

    for url in all_iframe_urls:
        domain = _safe_parse_domain(url)
        if not domain or domain in seen:
            continue
        seen.add(domain)

        service, confidence = _domain_to_service(domain)
        if service:
            items.append(EvidenceItem(
                source=SignalSource.IFRAME,
                key="iframe_domain_match",
                value=service,
                weight=0.25,
                confidence=confidence,
                raw=url[:200],
                explanation=f"Iframe src domain '{domain}' maps to service '{service}'",
            ))
        else:
            # Record unknown iframe domain as weak evidence
            items.append(EvidenceItem(
                source=SignalSource.IFRAME,
                key="iframe_domain_unknown",
                value=domain,
                weight=0.05,
                confidence=0.3,
                raw=url[:200],
                explanation=f"Iframe src domain '{domain}' does not match any known service",
            ))

    return items


def _extract_script_signals(html: str, script_urls: List[str]) -> List[EvidenceItem]:
    """
    Extract evidence from script tags and known script URLs.
    Detects known player libraries and framework scripts.
    """
    items: List[EvidenceItem] = []

    # Collect all script sources
    all_scripts: List[str] = list(script_urls)
    if html:
        for match in _SCRIPT_SRC_RE.finditer(html):
            all_scripts.append(match.group(1))

    seen_signals: set = set()

    for script_url in all_scripts:
        for pattern, player_name, signal_key, confidence in _SCRIPT_PLAYER_PATTERNS:
            if pattern.search(script_url) and signal_key not in seen_signals:
                seen_signals.add(signal_key)
                items.append(EvidenceItem(
                    source=SignalSource.SCRIPT,
                    key=signal_key,
                    value=player_name,
                    weight=0.20,
                    confidence=confidence,
                    raw=script_url[:200],
                    explanation=f"Script '{script_url}' matches player '{player_name}'",
                ))

    # Also scan inline scripts in HTML for player patterns
    if html:
        for pattern, player_name, signal_key, confidence in _SCRIPT_PLAYER_PATTERNS:
            if signal_key not in seen_signals and pattern.search(html):
                seen_signals.add(signal_key)
                items.append(EvidenceItem(
                    source=SignalSource.SCRIPT,
                    key=signal_key,
                    value=player_name,
                    weight=0.15,  # Slightly lower for inline detection
                    confidence=confidence * 0.9,
                    raw=f"(inline match for {player_name})",
                    explanation=f"Inline script/HTML matches player pattern '{player_name}'",
                ))

    return items


def _extract_player_hints(
    html: str,
    player_detector: Optional[Callable] = None,
    settings: Optional[IntegrationSettings] = None,
) -> Tuple[List[EvidenceItem], Optional[PlayerDetectionEvidence]]:
    """
    Extract player-related evidence.

    If a Phase 3 player detector is available (ADAPTATION POINT),
    delegate to it. Otherwise, use built-in script pattern detection.

    Returns:
        Tuple of (evidence_items, player_detection_evidence_or_None)
    """
    items: List[EvidenceItem] = []
    player_evidence: Optional[PlayerDetectionEvidence] = None

    if player_detector is not None and settings and settings.effective_player_v2():
        # ADAPTATION POINT: Phase 3 player detector interface
        # Expected signature: player_detector(html) -> phase3_result
        # The phase3_result should have attributes like:
        #   .player_name, .version, .framework, .confidence,
        #   .config, .wrapper
        try:
            phase3_result = player_detector(html)
            if phase3_result is not None:
                player_evidence = PlayerDetectionEvidence(
                    # ADAPTATION POINT: map Phase 3 result fields
                    player_name=getattr(phase3_result, "player_name", None)
                    or getattr(phase3_result, "name", None),
                    player_version=getattr(phase3_result, "version", None)
                    or getattr(phase3_result, "player_version", None),
                    framework=getattr(phase3_result, "framework", None),
                    config_extracted=getattr(phase3_result, "config", {})
                    or getattr(phase3_result, "config_extracted", {}),
                    wrapper_detected=getattr(phase3_result, "wrapper", None)
                    or getattr(phase3_result, "wrapper_detected", None),
                    confidence=getattr(phase3_result, "confidence", 0.0),
                )
                items.extend(player_evidence.to_evidence_bundle().items)
        except Exception:
            # Player detection failure is non-fatal
            items.append(EvidenceItem(
                source=SignalSource.PLAYER,
                key="player_detection_error",
                value="error",
                weight=0.0,
                confidence=0.0,
                raw=None,
                explanation="Phase 3 player detection raised an exception — skipped",
            ))

    return items, player_evidence


def _extract_site_hints(
    context: RawInputContext,
) -> List[EvidenceItem]:
    """
    Extract evidence from site-level context (site domain, user hints).
    """
    items: List[EvidenceItem] = []

    if context.site_domain:
        items.append(EvidenceItem(
            source=SignalSource.SITE_HINT,
            key="site_domain",
            value=context.site_domain,
            weight=0.05,
            confidence=1.0,
            raw=context.site_domain,
            explanation=f"Analysis running in context of site '{context.site_domain}'",
        ))

    if context.user_hints:
        for hint_key, hint_value in context.user_hints.items():
            if isinstance(hint_value, str) and hint_value.strip():
                items.append(EvidenceItem(
                    source=SignalSource.USER_OVERRIDE,
                    key=f"user_hint_{hint_key}",
                    value=hint_value,
                    weight=0.50,  # User hints get high weight
                    confidence=0.95,
                    raw=str(hint_value),
                    explanation=f"User-provided hint: {hint_key}='{hint_value}'",
                ))

    return items


# ──────────────────────────────────────────────
# Main Analysis Entry Point
# ──────────────────────────────────────────────

def analyze_static(
    context: RawInputContext,
    settings: IntegrationSettings,
    trace: Optional[ResolutionTrace] = None,
    player_detector: Optional[Callable] = None,
) -> Tuple[StaticAnalysisResult, Optional[PlayerDetectionEvidence]]:
    """
    Run structured static analysis on a RawInputContext.

    Produces a StaticAnalysisResult with multi-signal evidence,
    not just a single domain match.

    Args:
        context:         Normalized input context.
        settings:        Pipeline settings.
        trace:           Optional resolution trace to record steps into.
        player_detector: Optional Phase 3 player detector callable.
                         ADAPTATION POINT — injected by bootstrap.

    Returns:
        Tuple of (StaticAnalysisResult, PlayerDetectionEvidence or None)
    """
    if trace is None:
        trace = ResolutionTrace()

    # ── Step 1: Domain signals ──
    with trace_timer(trace, TraceStepPhase.STATIC_ANALYSIS, "extract_domain_signals") as t:
        domain_signals = _extract_domain_signals(
            urls=context.iframe_urls + context.script_urls,
            page_url=context.page_url,
            html=context.page_html,
        )
        t.result_summary = f"Found {len(domain_signals)} domain signals"
        inferred = [ds.inferred_service for ds in domain_signals if ds.inferred_service]
        t.details = {
            "domain_count": len(domain_signals),
            "inferred_services": inferred,
        }

    # ── Step 2: Embed signals ──
    with trace_timer(trace, TraceStepPhase.STATIC_ANALYSIS, "extract_embed_signals") as t:
        embed_signals = _extract_embed_signals(
            embed_codes=context.embed_codes,
            html=context.page_html,
        )
        t.result_summary = f"Found {len(embed_signals)} embed signals"
        t.details = {"embed_signal_count": len(embed_signals)}

    # ── Step 3: Iframe signals ──
    with trace_timer(trace, TraceStepPhase.STATIC_ANALYSIS, "extract_iframe_signals") as t:
        iframe_signals = _extract_iframe_signals(
            iframe_urls=context.iframe_urls,
            html=context.page_html,
        )
        t.result_summary = f"Found {len(iframe_signals)} iframe signals"
        t.details = {"iframe_signal_count": len(iframe_signals)}

    # ── Step 4: Script signals ──
    with trace_timer(trace, TraceStepPhase.STATIC_ANALYSIS, "extract_script_signals") as t:
        script_signals = _extract_script_signals(
            html=context.page_html,
            script_urls=context.script_urls,
        )
        t.result_summary = f"Found {len(script_signals)} script signals"
        t.details = {"script_signal_count": len(script_signals)}

    # ── Step 5: Player hints ──
    player_evidence: Optional[PlayerDetectionEvidence] = None
    with trace_timer(trace, TraceStepPhase.STATIC_ANALYSIS, "extract_player_hints") as t:
        player_hint_items, player_evidence = _extract_player_hints(
            html=context.page_html,
            player_detector=player_detector,
            settings=settings,
        )
        t.result_summary = (
            f"Player: {player_evidence.player_name if player_evidence and player_evidence.is_detected else 'none'}"
        )
        t.details = {
            "player_detected": player_evidence.is_detected if player_evidence else False,
            "player_hint_count": len(player_hint_items),
        }

    # ── Step 6: Site hints ──
    with trace_timer(trace, TraceStepPhase.STATIC_ANALYSIS, "extract_site_hints") as t:
        site_hint_items = _extract_site_hints(context)
        t.result_summary = f"Found {len(site_hint_items)} site hints"

    # ── Assemble result ──
    evidence = EvidenceBundle()

    # Add domain-derived evidence
    for ds in domain_signals:
        if ds.inferred_service:
            evidence.add(EvidenceItem(
                source=SignalSource.DOMAIN,
                key="domain_match",
                value=ds.inferred_service,
                weight=0.35,
                confidence=ds.confidence,
                raw=ds.full_url,
                explanation=f"Domain '{ds.domain}' → service '{ds.inferred_service}'",
            ))

    evidence.add_all(embed_signals)
    evidence.add_all(iframe_signals)
    evidence.add_all(script_signals)
    evidence.add_all(player_hint_items)
    evidence.add_all(site_hint_items)

    result = StaticAnalysisResult(
        domain_signals=domain_signals,
        embed_signals=embed_signals,
        iframe_signals=iframe_signals,
        script_signals=script_signals,
        player_hints=player_hint_items,
        evidence=evidence,
    )

    trace.step(
        phase=TraceStepPhase.STATIC_ANALYSIS,
        action="static_analysis_complete",
        result_summary=(
            f"Total: {result.total_signals} signals, "
            f"{evidence.count} evidence items, "
            f"services: {result.inferred_services()}"
        ),
        details={
            "total_signals": result.total_signals,
            "evidence_count": evidence.count,
            "inferred_services": result.inferred_services(),
            "sources_present": [s.value for s in evidence.sources_present()],
        },
    )

    return result, player_evidence