# player_intelligence/wrappers.py
"""
Wrapper and iframe detection engine.

Detects three kinds of wrapper pages:
    1. Custom iframe wrappers — pages whose primary content is
       one or more iframes pointing to embed/player URLs.
    2. Generic HLS wrappers — pages that load hls.js and attach
       it to a bare <video> element without any recognized player.
    3. Relay/redirect pages — pages with JS redirects or
       meta-refresh to embed URLs.

When a wrapper is detected, the system attempts underlying-player
inference by inspecting iframe src URLs, checking for co-loaded
player libraries, and consulting service hints from Phase 2.
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

from .models import (
    EvidenceCategory,
    PlayerDetectionCandidate,
    PlayerDetectionContext,
    PlayerEvidence,
    PlayerFamily,
    OutputType,
    WrapperDetectionResult,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# CONSTANTS & PATTERNS
# ═══════════════════════════════════════════════════════════════

_IC = re.IGNORECASE
_ICS = re.IGNORECASE | re.DOTALL


# ── Iframe extraction ──

_IFRAME_SRC_RE = re.compile(
    r'<iframe\s[^>]*?src\s*=\s*["\']([^"\']+)["\']',
    _IC,
)

_IFRAME_DYNAMIC_SRC_RE = re.compile(
    r'(?:iframe|frame)\s*[\.\[]?\s*src\s*=\s*["\']([^"\']+)["\']',
    _IC,
)

_IFRAME_ALLOWFULLSCREEN_RE = re.compile(
    r'<iframe\s[^>]*?allowfullscreen',
    _IC,
)

_IFRAME_SANDBOX_RE = re.compile(
    r'<iframe\s[^>]*?sandbox\s*=',
    _IC,
)

# ── Relay/redirect ──

_WINDOW_LOCATION_RE = re.compile(
    r'window\.location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
    _IC,
)

_META_REFRESH_RE = re.compile(
    r'<meta\s[^>]*?http-equiv\s*=\s*["\']refresh["\'][^>]*?'
    r'content\s*=\s*["\'][^"\']*url\s*=\s*([^"\'>\s]+)',
    _IC,
)

_JS_REDIRECT_RE = re.compile(
    r'(?:document\.location|location\.replace|location\.assign)\s*'
    r'[\(=]\s*["\']([^"\']+)["\']',
    _IC,
)

# ── Content density heuristics ──

_SCRIPT_TAG_RE = re.compile(r'<script[\s>]', _IC)
_BODY_CONTENT_RE = re.compile(
    r'<body[^>]*>(.*?)</body>',
    _ICS,
)
_VISIBLE_TEXT_RE = re.compile(r'<[^>]+>|[\s\n\r]+')
_SIGNIFICANT_HTML_RE = re.compile(
    r'<(?:div|section|article|main|p|h[1-6]|ul|ol|table|form)\b',
    _IC,
)

# ── Generic HLS indicators ──

_HLS_JS_LOAD_RE = re.compile(
    r'["\'][^"\']*hls(?:\.light)?(?:\.min)?\.js[^"\']*["\']',
    _IC,
)

_HLS_ATTACH_RE = re.compile(
    r'\.attachMedia\s*\(\s*(?:document\.(?:getElementById|querySelector)'
    r'\s*\(\s*["\'][^"\']+["\']\s*\)|[a-zA-Z_$][\w$]*)\s*\)',
    _IC,
)

_HLS_LOAD_SOURCE_RE = re.compile(
    r'\.loadSource\s*\(\s*["\']([^"\']+)["\']',
    _IC,
)

_BARE_VIDEO_RE = re.compile(
    r'<video\s[^>]*(?:id|class)\s*=\s*["\'][^"\']+["\'][^>]*>',
    _IC,
)

# ── Known embed domain patterns ──
# ADAPTATION POINT: The host repository may maintain its own
# embed domain registry. These patterns should be merged or
# replaced with that registry during integration.

KNOWN_EMBED_DOMAIN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'youtube\.com/embed/', _IC),
    re.compile(r'youtube-nocookie\.com/embed/', _IC),
    re.compile(r'player\.vimeo\.com/video/', _IC),
    re.compile(r'dailymotion\.com/embed/', _IC),
    re.compile(r'streamable\.com/[eos]/', _IC),
    re.compile(r'ok\.ru/videoembed/', _IC),
    re.compile(r'rutube\.ru/play/embed/', _IC),
    re.compile(r'drive\.google\.com/file/.*?/preview', _IC),
    re.compile(r'docs\.google\.com/file/.*?/preview', _IC),
    re.compile(r'facebook\.com/plugins/video', _IC),
    re.compile(r'embed\.twitch\.tv/', _IC),
    re.compile(r'player\.twitch\.tv/', _IC),
    re.compile(r'rumble\.com/embed/', _IC),
    re.compile(r'bitchute\.com/embed/', _IC),
    re.compile(r'odysee\.com/\$/embed/', _IC),
)


# Patterns in iframe src that hint at a specific underlying player
_IFRAME_PLAYER_HINT_PATTERNS: tuple[tuple[re.Pattern[str], PlayerFamily], ...] = (
    (re.compile(r'jwplayer|jwplatform|jwpcdn', _IC), PlayerFamily.JWPLAYER),
    (re.compile(r'videojs|video-js|vjs', _IC), PlayerFamily.VIDEOJS),
    (re.compile(r'clappr', _IC), PlayerFamily.CLAPPR),
    (re.compile(r'plyr', _IC), PlayerFamily.PLYR),
    (re.compile(r'dplayer', _IC), PlayerFamily.DPLAYER),
    (re.compile(r'artplayer', _IC), PlayerFamily.ARTPLAYER),
    (re.compile(r'\.m3u8', _IC), PlayerFamily.GENERIC_HLS),
)

# Embed-like path segments that suggest a wrapper page
_EMBED_PATH_INDICATORS = re.compile(
    r'/embed/|/player/|/e/|/watch/|/play/|/video/embed|/iframe/',
    _IC,
)

# Service name hints extractable from iframe src domains
_IFRAME_SERVICE_HINT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'youtube\.com|youtu\.be', _IC), "youtube"),
    (re.compile(r'vimeo\.com', _IC), "vimeo"),
    (re.compile(r'dailymotion\.com|dai\.ly', _IC), "dailymotion"),
    (re.compile(r'twitch\.tv', _IC), "twitch"),
    (re.compile(r'facebook\.com|fb\.com', _IC), "facebook"),
    (re.compile(r'streamable\.com', _IC), "streamable"),
    (re.compile(r'rumble\.com', _IC), "rumble"),
    (re.compile(r'odysee\.com', _IC), "odysee"),
    (re.compile(r'ok\.ru', _IC), "ok.ru"),
    (re.compile(r'rutube\.ru', _IC), "rutube"),
)


# ═══════════════════════════════════════════════════════════════
# IFRAME WRAPPER DETECTION
# ═══════════════════════════════════════════════════════════════

def _extract_iframe_srcs(html: str) -> list[str]:
    """Extract all iframe src URLs from HTML."""
    srcs: list[str] = []
    seen: set[str] = set()
    for m in _IFRAME_SRC_RE.finditer(html):
        src = m.group(1).strip()
        if src and src not in seen:
            srcs.append(src)
            seen.add(src)
    # Also check dynamic assignment
    for m in _IFRAME_DYNAMIC_SRC_RE.finditer(html):
        src = m.group(1).strip()
        if src and src not in seen:
            srcs.append(src)
            seen.add(src)
    return srcs


def _estimate_iframe_chain_depth(
    html: str,
    context: PlayerDetectionContext | None,
) -> int:
    """
    Estimate the iframe nesting depth.

    Uses context (if the current page is already inside an iframe)
    plus the number of nested iframe tags found.
    """
    base_depth = 0
    if context and context.iframe_depth > 0:
        base_depth = context.iframe_depth

    # Count iframe tags in page (each is +1 potential depth)
    iframe_count = len(_IFRAME_SRC_RE.findall(html))
    # We count the page itself as depth 1 if it contains iframes
    page_depth = 1 if iframe_count > 0 else 0

    return base_depth + page_depth


def _is_thin_wrapper_page(html: str) -> bool:
    """
    Heuristic: is this page a "thin" wrapper with minimal content
    besides iframe(s)?

    A thin wrapper has:
    - Very little visible text outside <script>/<style>/<iframe> tags
    - Very few significant HTML structural elements
    - Usually < 5000 chars of meaningful body content
    """
    body_match = _BODY_CONTENT_RE.search(html)
    if not body_match:
        # No body tag at all — could be a fragment or bare iframe
        return len(html) < 3000

    body_content = body_match.group(1)

    # Remove script, style, iframe content
    stripped = re.sub(
        r'<(?:script|style|iframe)[^>]*>.*?</(?:script|style|iframe)>',
        '',
        body_content,
        flags=_ICS,
    )
    # Remove all HTML tags
    visible_text = _VISIBLE_TEXT_RE.sub(' ', stripped).strip()

    # Count significant structural elements
    significant_elements = len(_SIGNIFICANT_HTML_RE.findall(stripped))

    # Thin wrapper heuristic thresholds
    if len(visible_text) < 100 and significant_elements < 3:
        return True
    if len(visible_text) < 50:
        return True

    return False


def _detect_iframe_wrapper(
    html: str,
    url: str,
    context: PlayerDetectionContext | None,
) -> WrapperDetectionResult | None:
    """
    Detect if the page is a custom iframe wrapper.

    Criteria (scored):
    - Page contains iframe(s) with embed/player-like src URLs
    - Page is a thin wrapper (minimal non-iframe content)
    - iframe has allowfullscreen attribute
    - iframe src matches known embed domain patterns
    """
    iframe_srcs = _extract_iframe_srcs(html)
    if not iframe_srcs:
        return None

    evidence: list[PlayerEvidence] = []
    total_weight = 0.0

    # ── Check for embed-like iframe srcs ──
    embed_srcs: list[str] = []
    for src in iframe_srcs:
        if _EMBED_PATH_INDICATORS.search(src):
            embed_srcs.append(src)
            ev = PlayerEvidence(
                category=EvidenceCategory.WRAPPER,
                family=PlayerFamily.CUSTOM_IFRAME,
                weight=0.30,
                matched_text=src,
                rule_description="Iframe src contains embed/player path segment",
                source_location="iframe[src]",
            )
            evidence.append(ev)
            total_weight += ev.weight

    # ── Check known embed domains ──
    known_domain_srcs: list[str] = []
    for src in iframe_srcs:
        for domain_re in KNOWN_EMBED_DOMAIN_PATTERNS:
            if domain_re.search(src):
                known_domain_srcs.append(src)
                ev = PlayerEvidence(
                    category=EvidenceCategory.WRAPPER,
                    family=PlayerFamily.CUSTOM_IFRAME,
                    weight=0.25,
                    matched_text=src,
                    rule_description=f"Iframe src matches known embed domain: {domain_re.pattern}",
                    source_location="iframe[src]",
                )
                evidence.append(ev)
                total_weight += ev.weight
                break  # One match per src is enough

    # ── Thin wrapper check ──
    is_thin = _is_thin_wrapper_page(html)
    if is_thin:
        ev = PlayerEvidence(
            category=EvidenceCategory.WRAPPER,
            family=PlayerFamily.CUSTOM_IFRAME,
            weight=0.20,
            matched_text="Page has minimal non-iframe content",
            rule_description="Thin wrapper page heuristic",
            source_location="body",
        )
        evidence.append(ev)
        total_weight += ev.weight

    # ── Allowfullscreen ──
    if _IFRAME_ALLOWFULLSCREEN_RE.search(html):
        ev = PlayerEvidence(
            category=EvidenceCategory.WRAPPER,
            family=PlayerFamily.CUSTOM_IFRAME,
            weight=0.10,
            matched_text="allowfullscreen",
            rule_description="Iframe has allowfullscreen attribute",
            source_location="iframe",
        )
        evidence.append(ev)
        total_weight += ev.weight

    # ── Decide if wrapper ──
    if not evidence:
        return None

    # Need at least one embed-like src OR thin page with iframe
    has_embed_src = len(embed_srcs) > 0 or len(known_domain_srcs) > 0
    if not has_embed_src and not is_thin:
        return None

    # ── Infer underlying player ──
    underlying_player = _infer_underlying_player_from_srcs(iframe_srcs)

    # ── Infer service hint ──
    service_hint = _infer_service_from_srcs(iframe_srcs)

    # ── Chain depth ──
    chain_depth = _estimate_iframe_chain_depth(html, context)

    # ── Confidence ──
    confidence = min(1.0, total_weight / 0.60)

    return WrapperDetectionResult(
        is_wrapper=True,
        wrapper_type="custom_iframe",
        probable_underlying_player=underlying_player,
        iframe_chain_depth=chain_depth,
        iframe_src_hints=iframe_srcs,
        probable_service_hint=service_hint,
        confidence=confidence,
        evidence=evidence,
    )


# ═══════════════════════════════════════════════════════════════
# GENERIC HLS WRAPPER DETECTION
# ═══════════════════════════════════════════════════════════════

def _detect_generic_hls_wrapper(
    html: str,
    candidates: Sequence[PlayerDetectionCandidate],
) -> WrapperDetectionResult | None:
    """
    Detect a generic HLS wrapper page.

    A generic HLS wrapper is a page that:
    - Loads hls.js
    - Uses Hls.loadSource / Hls.attachMedia on a bare video element
    - Does NOT have a recognized named player framework with higher
      confidence than the GENERIC_HLS candidate

    This is NOT triggered if a named player (JWPlayer, video.js, etc.)
    has already been detected with higher confidence, because those
    players often load hls.js internally.
    """
    evidence: list[PlayerEvidence] = []

    # Check for hls.js script
    if not _HLS_JS_LOAD_RE.search(html):
        return None

    evidence.append(PlayerEvidence(
        category=EvidenceCategory.WRAPPER,
        family=PlayerFamily.GENERIC_HLS,
        weight=0.30,
        matched_text="hls.js script loaded",
        rule_description="hls.js library detected",
        source_location="script[src]",
    ))

    # Check for bare video element
    has_bare_video = bool(_BARE_VIDEO_RE.search(html))
    if has_bare_video:
        evidence.append(PlayerEvidence(
            category=EvidenceCategory.WRAPPER,
            family=PlayerFamily.GENERIC_HLS,
            weight=0.15,
            matched_text="Bare <video> element",
            rule_description="Video element without named player class",
            source_location="video",
        ))

    # Check for loadSource / attachMedia calls
    load_source_match = _HLS_LOAD_SOURCE_RE.search(html)
    if load_source_match:
        evidence.append(PlayerEvidence(
            category=EvidenceCategory.WRAPPER,
            family=PlayerFamily.GENERIC_HLS,
            weight=0.20,
            matched_text=load_source_match.group(0)[:150],
            rule_description="Hls.loadSource() call found",
            source_location="inline_js",
        ))

    if _HLS_ATTACH_RE.search(html):
        evidence.append(PlayerEvidence(
            category=EvidenceCategory.WRAPPER,
            family=PlayerFamily.GENERIC_HLS,
            weight=0.15,
            matched_text="attachMedia() call",
            rule_description="Hls.attachMedia() call found",
            source_location="inline_js",
        ))

    # ── Check if a named player already dominates ──
    # If a named player (non-GENERIC_HLS, non-CUSTOM_IFRAME) has
    # confidence > 0.40, this is probably not a "wrapper" but rather
    # a player that uses hls.js internally. Don't flag as wrapper.
    named_player_threshold = 0.40
    for cand in candidates:
        if cand.family not in (
            PlayerFamily.GENERIC_HLS,
            PlayerFamily.CUSTOM_IFRAME,
            PlayerFamily.UNKNOWN,
            PlayerFamily.NONE,
        ) and cand.confidence > named_player_threshold:
            logger.debug(
                "Generic HLS wrapper suppressed: named player %s "
                "has confidence %.3f > threshold %.3f",
                cand.family.value,
                cand.confidence,
                named_player_threshold,
            )
            return None

    # ── Compute confidence ──
    total_weight = sum(e.weight for e in evidence)
    confidence = min(1.0, total_weight / 0.55)

    # Must have at least the script + one other signal
    if len(evidence) < 2:
        return None

    # Extract HLS source URL if found
    source_url: str | None = None
    if load_source_match:
        try:
            source_url = load_source_match.group(1)
        except IndexError:
            pass

    iframe_src_hints: list[str] = []
    if source_url:
        iframe_src_hints.append(source_url)

    return WrapperDetectionResult(
        is_wrapper=True,
        wrapper_type="generic_hls",
        probable_underlying_player=PlayerFamily.GENERIC_HLS,
        iframe_chain_depth=0,
        iframe_src_hints=iframe_src_hints,
        probable_service_hint=None,
        confidence=confidence,
        evidence=evidence,
    )


# ═══════════════════════════════════════════════════════════════
# RELAY / REDIRECT DETECTION
# ═══════════════════════════════════════════════════════════════

def _detect_relay_redirect(
    html: str,
    url: str,
) -> WrapperDetectionResult | None:
    """
    Detect a relay/redirect page.

    A relay page immediately redirects to another URL via:
    - window.location assignment
    - meta-refresh tag
    - location.replace / location.assign calls
    """
    evidence: list[PlayerEvidence] = []
    redirect_targets: list[str] = []

    # window.location
    for m in _WINDOW_LOCATION_RE.finditer(html):
        target = m.group(1).strip()
        if target and target != url:
            redirect_targets.append(target)
            evidence.append(PlayerEvidence(
                category=EvidenceCategory.WRAPPER,
                family=PlayerFamily.CUSTOM_IFRAME,
                weight=0.25,
                matched_text=target[:150],
                rule_description="window.location redirect",
                source_location="inline_js",
            ))

    # meta refresh
    for m in _META_REFRESH_RE.finditer(html):
        target = m.group(1).strip()
        if target and target != url:
            redirect_targets.append(target)
            evidence.append(PlayerEvidence(
                category=EvidenceCategory.WRAPPER,
                family=PlayerFamily.CUSTOM_IFRAME,
                weight=0.20,
                matched_text=target[:150],
                rule_description="meta-refresh redirect",
                source_location="meta",
            ))

    # location.replace / location.assign
    for m in _JS_REDIRECT_RE.finditer(html):
        target = m.group(1).strip()
        if target and target not in redirect_targets and target != url:
            redirect_targets.append(target)
            evidence.append(PlayerEvidence(
                category=EvidenceCategory.WRAPPER,
                family=PlayerFamily.CUSTOM_IFRAME,
                weight=0.25,
                matched_text=target[:150],
                rule_description="JS redirect (location.replace/assign)",
                source_location="inline_js",
            ))

    if not redirect_targets:
        return None

    # Must be a thin page for it to be a relay
    is_thin = _is_thin_wrapper_page(html)
    if not is_thin:
        # If page has substantial content, it's probably not a relay
        return None

    evidence.append(PlayerEvidence(
        category=EvidenceCategory.WRAPPER,
        family=PlayerFamily.CUSTOM_IFRAME,
        weight=0.15,
        matched_text="Thin page with redirect",
        rule_description="Relay page heuristic: minimal content + redirect",
        source_location="body",
    ))

    # Infer underlying player and service from redirect targets
    underlying_player = _infer_underlying_player_from_srcs(redirect_targets)
    service_hint = _infer_service_from_srcs(redirect_targets)

    total_weight = sum(e.weight for e in evidence)
    confidence = min(1.0, total_weight / 0.50)

    return WrapperDetectionResult(
        is_wrapper=True,
        wrapper_type="relay_page",
        probable_underlying_player=underlying_player,
        iframe_chain_depth=0,
        iframe_src_hints=redirect_targets,
        probable_service_hint=service_hint,
        confidence=confidence,
        evidence=evidence,
    )


# ═══════════════════════════════════════════════════════════════
# UNDERLYING PLAYER & SERVICE INFERENCE
# ═══════════════════════════════════════════════════════════════

def _infer_underlying_player_from_srcs(
    srcs: Sequence[str],
) -> PlayerFamily | None:
    """
    Attempt to infer the underlying player from iframe src URLs
    or redirect targets.

    Checks each URL against known player-hint patterns.
    Returns the first match, or None if no match.
    """
    for src in srcs:
        for pattern, family in _IFRAME_PLAYER_HINT_PATTERNS:
            if pattern.search(src):
                logger.debug(
                    "Inferred underlying player %s from src: %s",
                    family.value, src[:100],
                )
                return family
    return None


def _infer_underlying_player_from_candidates(
    candidates: Sequence[PlayerDetectionCandidate],
    exclude_families: set[PlayerFamily] | None = None,
) -> PlayerFamily | None:
    """
    Infer underlying player from co-detected candidates.

    If a named player was detected alongside a wrapper, it is
    likely the underlying player inside the wrapper. Returns
    the highest-confidence named candidate's family.
    """
    if exclude_families is None:
        exclude_families = {
            PlayerFamily.CUSTOM_IFRAME,
            PlayerFamily.UNKNOWN,
            PlayerFamily.NONE,
        }

    best: PlayerDetectionCandidate | None = None
    for cand in candidates:
        if cand.family in exclude_families:
            continue
        if cand.confidence < 0.10:
            continue
        if best is None or cand.confidence > best.confidence:
            best = cand

    return best.family if best else None


def _infer_service_from_srcs(srcs: Sequence[str]) -> str | None:
    """
    Extract a service name hint from iframe/redirect URLs.

    Checks against known service domain patterns and returns
    the first match.
    """
    for src in srcs:
        for pattern, service_name in _IFRAME_SERVICE_HINT_PATTERNS:
            if pattern.search(src):
                return service_name
    return None


# ═══════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════

def detect_wrapper(
    html: str,
    url: str,
    candidates: Sequence[PlayerDetectionCandidate] | None = None,
    context: PlayerDetectionContext | None = None,
) -> WrapperDetectionResult | None:
    """
    Run all wrapper detection strategies and return the best result.

    Strategy order (first definitive match wins):
    1. Relay/redirect detection (highest specificity).
    2. Custom iframe wrapper detection.
    3. Generic HLS wrapper detection.

    If no wrapper is detected, returns None.

    Parameters
    ----------
    html : str
        Page HTML content.
    url : str
        Page URL (used to filter self-referencing redirects).
    candidates : Sequence[PlayerDetectionCandidate] | None
        Player candidates detected so far (used by HLS wrapper
        detection to avoid false positives when a named player
        is already detected).
    context : PlayerDetectionContext | None
        Optional detection context with iframe depth info.

    Returns
    -------
    WrapperDetectionResult | None
        Wrapper detection result, or None if not a wrapper page.
    """
    safe_candidates = list(candidates) if candidates else []

    # ── Strategy 1: Relay/redirect ──
    relay_result = _detect_relay_redirect(html, url)
    if relay_result and relay_result.confidence >= 0.30:
        logger.debug(
            "Relay redirect detected (confidence=%.3f): %s",
            relay_result.confidence,
            relay_result.iframe_src_hints[:2],
        )
        # Try to enhance underlying player from co-detected candidates
        if not relay_result.has_underlying_player and safe_candidates:
            inferred = _infer_underlying_player_from_candidates(safe_candidates)
            if inferred:
                relay_result.probable_underlying_player = inferred
        return relay_result

    # ── Strategy 2: Custom iframe wrapper ──
    iframe_result = _detect_iframe_wrapper(html, url, context)
    if iframe_result and iframe_result.confidence >= 0.25:
        logger.debug(
            "Iframe wrapper detected (confidence=%.3f): %d iframe(s)",
            iframe_result.confidence,
            len(iframe_result.iframe_src_hints),
        )
        # Enhance underlying player inference
        if not iframe_result.has_underlying_player and safe_candidates:
            inferred = _infer_underlying_player_from_candidates(safe_candidates)
            if inferred:
                iframe_result.probable_underlying_player = inferred
        return iframe_result

    # ── Strategy 3: Generic HLS wrapper ──
    hls_result = _detect_generic_hls_wrapper(html, safe_candidates)
    if hls_result and hls_result.confidence >= 0.30:
        logger.debug(
            "Generic HLS wrapper detected (confidence=%.3f)",
            hls_result.confidence,
        )
        return hls_result

    return None


def is_probable_wrapper_page(html: str) -> bool:
    """
    Quick heuristic check: is this page likely a wrapper?

    Cheaper than full detect_wrapper() — useful for fast-path
    decisions before committing to full detection.
    """
    iframe_srcs = _extract_iframe_srcs(html)
    if iframe_srcs:
        for src in iframe_srcs:
            if _EMBED_PATH_INDICATORS.search(src):
                return True

    # Check for redirect patterns
    if _WINDOW_LOCATION_RE.search(html) or _META_REFRESH_RE.search(html):
        if _is_thin_wrapper_page(html):
            return True

    return False