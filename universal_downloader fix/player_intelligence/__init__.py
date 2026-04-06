# player_intelligence/__init__.py
"""
Player/Framework Intelligence Package — Public API Surface.

Provides advanced, confidence-based, explainable detection of
media player frameworks (JWPlayer, video.js, Clappr, Plyr,
DPlayer, ArtPlayer, generic HLS, custom iframe wrappers)
from raw HTML content.

Quick start:
    from player_intelligence import detect_players

    result = detect_players(html, url)
    if result.detected:
        print(result.best.canonical_name)
        print(result.best.confidence)

Full API:
    detect_players(html, url, context?) → PlayerDetectionResult
    detect_best_player(html, url, **kw) → PlayerDetectionCandidate | None
    detect_wrapper(html, url, ...) → WrapperDetectionResult | None
    extract_player_configs(html, url, family?) → list[ConfigExtractionResult]
    explain_player_detection(result) → list[str]
    score_player_candidate(candidate, context?) → ScoreBreakdown
    detect_specific_player(html, url, family, context?) → PlayerDetectionCandidate | None
    detect_players_multi_pass(html, url, context?, iframe_htmls?) → PlayerDetectionResult
"""

# ADAPTATION POINT: The host repository's bootstrap/startup
# sequence may call an init hook here. If so, add the hook
# function and call it from the repo's startup module.
# Currently no initialization is needed — profiles are loaded
# at import time from profiles.py.

from __future__ import annotations

# ── Core detection API ──
from .detector import (
    detect_players,
    detect_best_player,
    detect_specific_player,
    detect_players_multi_pass,
    extract_player_configs,
    score_player_candidate,
)

# ── Wrapper detection ──
from .wrappers import (
    detect_wrapper,
    is_probable_wrapper_page,
)

# ── Explanation ──
from .explain import (
    explain_player_detection,
    explain_candidate,
    explain_wrapper,
    explain_score_breakdown,
    format_evidence,
    format_result_summary,
)

# ── Key models (for external type usage) ──
from .models import (
    ConfigExtractionResult,
    ConfigType,
    EvidenceCategory,
    MediaHints,
    OutputType,
    PlayerDetectionCandidate,
    PlayerDetectionContext,
    PlayerDetectionResult,
    PlayerEvidence,
    PlayerFamily,
    PlayerProfile,
    ScoreBreakdown,
    ScoreContribution,
    VersionDetectionResult,
    WrapperDetectionResult,
    MarkerRule,
    PlayerVersionPattern,
    PlayerConfigPattern,
    WrapperHints,
)

# ── Profile management ──
from .profiles import (
    get_profile,
    get_all_profiles,
    get_families,
    register_profile,
    get_profiles_for_detection,
    PROFILES,
)

# ── Config extractor registration ──
from .config_extractors import (
    register_extractor,
    parse_js_object,
)

# ── Version utilities ──
from .version_patterns import (
    normalize_version_string,
    detect_version,
    detect_all_versions,
    VERSION_CORE,
    VERSION_FULL,
)

# ── Scoring utilities ──
from .scoring import (
    confidence_tier,
    is_dominant_candidate,
    normalize_confidence,
    CATEGORY_WEIGHT_CAPS,
)


__all__ = [
    # Detection API
    "detect_players",
    "detect_best_player",
    "detect_specific_player",
    "detect_players_multi_pass",
    "extract_player_configs",
    "score_player_candidate",
    # Wrapper
    "detect_wrapper",
    "is_probable_wrapper_page",
    # Explanation
    "explain_player_detection",
    "explain_candidate",
    "explain_wrapper",
    "explain_score_breakdown",
    "format_evidence",
    "format_result_summary",
    # Models
    "ConfigExtractionResult",
    "ConfigType",
    "EvidenceCategory",
    "MediaHints",
    "OutputType",
    "PlayerDetectionCandidate",
    "PlayerDetectionContext",
    "PlayerDetectionResult",
    "PlayerEvidence",
    "PlayerFamily",
    "PlayerProfile",
    "ScoreBreakdown",
    "ScoreContribution",
    "VersionDetectionResult",
    "WrapperDetectionResult",
    "MarkerRule",
    "PlayerVersionPattern",
    "PlayerConfigPattern",
    "WrapperHints",
    # Profiles
    "get_profile",
    "get_all_profiles",
    "get_families",
    "register_profile",
    "get_profiles_for_detection",
    "PROFILES",
    # Config extractors
    "register_extractor",
    "parse_js_object",
    # Version
    "normalize_version_string",
    "detect_version",
    "detect_all_versions",
    "VERSION_CORE",
    "VERSION_FULL",
    # Scoring
    "confidence_tier",
    "is_dominant_candidate",
    "normalize_confidence",
    "CATEGORY_WEIGHT_CAPS",
]


__version__ = "3.0.0"
__package_name__ = "player_intelligence"