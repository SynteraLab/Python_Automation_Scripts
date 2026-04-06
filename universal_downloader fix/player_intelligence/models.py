# player_intelligence/models.py
"""
Pure data models for the player/framework intelligence system.

All enums, dataclasses, and type aliases used across the package
are defined here. This module contains zero detection logic —
it is the structural foundation layer only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum, auto, unique
from typing import Any, Callable, Pattern, Sequence


# ═══════════════════════════════════════════════════════════════
# ENUMERATIONS
# ═══════════════════════════════════════════════════════════════

@unique
class PlayerFamily(Enum):
    """
    Canonical player/framework family identifiers.

    Each member represents a normalized player family.  Detection
    produces evidence mapped to one of these families.  UNKNOWN is
    used when markers are found but cannot be attributed.  NONE is
    the absence of any detection.
    """
    JWPLAYER = "jwplayer"
    VIDEOJS = "videojs"
    CLAPPR = "clappr"
    PLYR = "plyr"
    DPLAYER = "dplayer"
    ARTPLAYER = "artplayer"
    GENERIC_HLS = "generic_hls"
    CUSTOM_IFRAME = "custom_iframe"
    UNKNOWN = "unknown"
    NONE = "none"

    @classmethod
    def from_string(cls, value: str) -> PlayerFamily:
        """Resolve a family from a loose string, case-insensitive."""
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        # Direct match
        for member in cls:
            if member.value == normalized:
                return member
        # Alias fallback
        alias_map = _PLAYER_FAMILY_ALIASES
        if normalized in alias_map:
            return alias_map[normalized]
        return cls.UNKNOWN


# Alias lookup for loose string → PlayerFamily resolution
_PLAYER_FAMILY_ALIASES: dict[str, PlayerFamily] = {
    "jw": PlayerFamily.JWPLAYER,
    "jw_player": PlayerFamily.JWPLAYER,
    "jwplayer8": PlayerFamily.JWPLAYER,
    "jwplayer7": PlayerFamily.JWPLAYER,
    "video.js": PlayerFamily.VIDEOJS,
    "video_js": PlayerFamily.VIDEOJS,
    "vjs": PlayerFamily.VIDEOJS,
    "hlsjs": PlayerFamily.GENERIC_HLS,
    "hls.js": PlayerFamily.GENERIC_HLS,
    "hls_js": PlayerFamily.GENERIC_HLS,
    "native_hls": PlayerFamily.GENERIC_HLS,
    "art_player": PlayerFamily.ARTPLAYER,
    "d_player": PlayerFamily.DPLAYER,
    "iframe": PlayerFamily.CUSTOM_IFRAME,
    "iframe_wrapper": PlayerFamily.CUSTOM_IFRAME,
    "custom_wrapper": PlayerFamily.CUSTOM_IFRAME,
}


@unique
class EvidenceCategory(Enum):
    """
    Categories of detection evidence.

    Each piece of evidence collected during marker scanning is
    classified into exactly one category.  Scoring uses these
    categories to apply weight caps and cross-category bonuses.
    """
    SCRIPT_SRC = "script_src"
    DOM_CSS = "dom_css"
    INLINE_JS = "inline_js"
    GLOBAL_VAR = "global_var"
    DATA_ATTR = "data_attr"
    CONFIG_SHAPE = "config_shape"
    VERSION = "version"
    WRAPPER = "wrapper"
    URL_HINT = "url_hint"
    META = "meta"


@unique
class OutputType(Enum):
    """Probable media output types a player may serve."""
    MP4 = "mp4"
    HLS = "hls"
    DASH = "dash"
    IFRAME = "iframe"
    API_BACKED = "api_backed"
    WEBM = "webm"
    FLV = "flv"
    AUDIO = "audio"
    UNKNOWN = "unknown"


@unique
class ConfigType(Enum):
    """
    Classification of config extraction strategies.

    Each PlayerConfigPattern declares which extraction approach
    should be used via this enum.
    """
    SETUP_CALL = "setup_call"          # playerInstance.setup({...})
    JSON_BLOB = "json_blob"            # inline <script> with JSON
    DATA_ATTR = "data_attr"            # data-* attributes on elements
    WINDOW_VAR = "window_var"          # window.playerConfig = {...}
    INIT_BLOCK = "init_block"          # new Player(el, {...})
    SOURCES_ARRAY = "sources_array"    # sources: [{...}, ...]
    EMBED_URL = "embed_url"            # config encoded in embed URL


# ═══════════════════════════════════════════════════════════════
# MARKER & PATTERN MODELS
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class MarkerRule:
    """
    A single detection marker rule.

    Marker rules are the atomic units of player detection.
    Each rule specifies a regex pattern, the evidence category
    it belongs to, a base weight for scoring, and whether it
    is a positive or negative (negating) signal.
    """
    pattern: re.Pattern[str]
    category: EvidenceCategory
    weight: float
    description: str
    negates: bool = False

    def search(self, text: str) -> re.Match[str] | None:
        """Run the marker pattern against text, return match or None."""
        return self.pattern.search(text)

    def find_all(self, text: str) -> list[re.Match[str]]:
        """Return all non-overlapping matches in text."""
        return list(self.pattern.finditer(text))


@dataclass(frozen=True, slots=True)
class PlayerVersionPattern:
    """
    A pattern that can extract a version string from HTML/JS content.

    `version_group` identifies which regex group contains the version.
    `source` documents where this pattern is expected to match
    (e.g., script src, inline comment, global variable assignment).
    """
    pattern: re.Pattern[str]
    version_group: int | str = 1
    source: str = "unknown"
    description: str = ""

    def extract(self, text: str) -> str | None:
        """Attempt to extract a version string from text."""
        m = self.pattern.search(text)
        if m:
            try:
                return m.group(self.version_group)
            except (IndexError, AttributeError):
                return None
        return None


@dataclass(frozen=True, slots=True)
class PlayerConfigPattern:
    """
    A pattern that identifies a config block to extract.

    `extractor_fn` is a string key used to dispatch to the
    appropriate extraction function in config_extractors.py.
    `priority` controls extraction order when multiple patterns
    match — lower number = higher priority.
    """
    pattern: re.Pattern[str]
    extractor_fn: str
    config_type: ConfigType
    description: str = ""
    priority: int = 50

    def matches(self, text: str) -> bool:
        """Check if this config pattern matches anywhere in text."""
        return bool(self.pattern.search(text))


@dataclass(frozen=True, slots=True)
class WrapperHints:
    """
    Static hints attached to a PlayerProfile indicating how
    this player family relates to wrapper/iframe scenarios.

    `is_commonly_wrapped` — this player is often loaded inside
    a custom iframe wrapper (e.g., JWPlayer embeds).

    `wrapper_indicators` — additional patterns that suggest this
    player is being used as the underlying player inside a wrapper.
    """
    is_commonly_wrapped: bool = False
    commonly_wraps_others: bool = False
    wrapper_indicators: tuple[re.Pattern[str], ...] = ()
    iframe_src_hints: tuple[re.Pattern[str], ...] = ()


# ═══════════════════════════════════════════════════════════════
# PLAYER PROFILE
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class PlayerProfile:
    """
    Complete declarative profile for a known player/framework.

    A profile contains all markers, version patterns, config
    patterns, output type hints, and wrapper metadata needed
    to detect and characterize a player family.

    Profiles are immutable once created.  The detection engine
    iterates over all registered profiles during marker scanning.
    """
    family: PlayerFamily
    canonical_name: str
    aliases: tuple[str, ...] = ()
    homepage: str = ""

    # ── Marker rules by category ──
    script_src_markers: tuple[MarkerRule, ...] = ()
    dom_css_markers: tuple[MarkerRule, ...] = ()
    inline_js_markers: tuple[MarkerRule, ...] = ()
    global_variable_markers: tuple[MarkerRule, ...] = ()
    data_attribute_markers: tuple[MarkerRule, ...] = ()

    # ── Version detection ──
    version_patterns: tuple[PlayerVersionPattern, ...] = ()

    # ── Config extraction ──
    config_patterns: tuple[PlayerConfigPattern, ...] = ()

    # ── Output type hints ──
    probable_output_types: tuple[OutputType, ...] = ()

    # ── Wrapper metadata ──
    wrapper_hints: WrapperHints | None = None

    # ── Scoring tuning ──
    confidence_ceiling: float = 0.75

    @property
    def all_markers(self) -> tuple[MarkerRule, ...]:
        """Return all marker rules across all categories."""
        return (
            self.script_src_markers
            + self.dom_css_markers
            + self.inline_js_markers
            + self.global_variable_markers
            + self.data_attribute_markers
        )

    def has_markers_for(self, category: EvidenceCategory) -> bool:
        """Check if this profile defines markers for a given category."""
        mapping = {
            EvidenceCategory.SCRIPT_SRC: self.script_src_markers,
            EvidenceCategory.DOM_CSS: self.dom_css_markers,
            EvidenceCategory.INLINE_JS: self.inline_js_markers,
            EvidenceCategory.GLOBAL_VAR: self.global_variable_markers,
            EvidenceCategory.DATA_ATTR: self.data_attribute_markers,
        }
        markers = mapping.get(category, ())
        return len(markers) > 0


# ═══════════════════════════════════════════════════════════════
# EVIDENCE & RESULTS
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class PlayerEvidence:
    """
    A single piece of detection evidence.

    Produced by marker scanning.  Multiple evidence objects
    are collected per candidate and used in scoring.
    `matched_text` is a short excerpt (truncated if needed)
    showing what the marker pattern actually matched.
    """
    category: EvidenceCategory
    family: PlayerFamily
    weight: float
    matched_text: str
    rule_description: str
    source_location: str = ""
    negates: bool = False

    # Maximum length for matched_text storage
    _MAX_EXCERPT: int = 200

    def __post_init__(self) -> None:
        # Truncate matched_text if excessively long.
        # We use object.__setattr__ because the dataclass is frozen.
        if len(self.matched_text) > self._MAX_EXCERPT:
            object.__setattr__(
                self,
                "matched_text",
                self.matched_text[: self._MAX_EXCERPT] + "…",
            )


@dataclass(frozen=True, slots=True)
class MediaHints:
    """
    Structured hints about media URLs/formats found inside
    an extracted config block.
    """
    urls: tuple[str, ...] = ()
    formats: tuple[OutputType, ...] = ()
    has_drm: bool = False
    has_subtitles: bool = False
    has_thumbnails: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfigExtractionResult:
    """
    Result of extracting a config block from HTML/JS content.

    `raw_text` is the raw matched text.
    `parsed` is the structured dict if JSON/JS parsing succeeded.
    `media_hints` summarizes any media-related info found.
    """
    config_type: ConfigType
    raw_text: str
    parsed: dict[str, Any] | None = None
    source_pattern: str = ""
    media_hints: MediaHints = field(default_factory=MediaHints)
    family: PlayerFamily = PlayerFamily.UNKNOWN
    extraction_error: str | None = None

    @property
    def is_parsed(self) -> bool:
        return self.parsed is not None and len(self.parsed) > 0

    @property
    def has_media(self) -> bool:
        return len(self.media_hints.urls) > 0


@dataclass(slots=True)
class VersionDetectionResult:
    """Result of version detection for a player candidate."""
    version: str | None = None
    raw_match: str = ""
    source: str = ""
    pattern_description: str = ""
    normalized: str | None = None

    @property
    def detected(self) -> bool:
        return self.version is not None


# ═══════════════════════════════════════════════════════════════
# SCORING MODELS
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class ScoreContribution:
    """
    One contribution line in a score breakdown.

    Records which evidence produced how much score contribution,
    and whether it was capped or adjusted.
    """
    evidence: PlayerEvidence
    raw_contribution: float
    capped_contribution: float
    cap_applied: bool = False
    note: str = ""


@dataclass(slots=True)
class ScoreBreakdown:
    """
    Full breakdown of how a candidate's confidence was computed.

    `contributions` lists each evidence item's contribution.
    `total_raw` is the uncapped sum.
    `max_possible` is the theoretical maximum for the profile.
    `normalized` is the final confidence (0.0–1.0).
    """
    contributions: list[ScoreContribution] = field(default_factory=list)
    total_raw: float = 0.0
    total_after_caps: float = 0.0
    negation_penalty: float = 0.0
    cross_category_bonus: float = 0.0
    max_possible: float = 1.0
    confidence_ceiling: float = 0.75
    normalized: float = 0.0

    @property
    def num_categories_hit(self) -> int:
        """Count distinct evidence categories that contributed."""
        categories = {
            c.evidence.category
            for c in self.contributions
            if c.capped_contribution > 0.0
        }
        return len(categories)

    def to_summary_dict(self) -> dict[str, Any]:
        """Compact summary for serialization / logging."""
        return {
            "normalized_confidence": round(self.normalized, 4),
            "total_raw": round(self.total_raw, 4),
            "total_after_caps": round(self.total_after_caps, 4),
            "negation_penalty": round(self.negation_penalty, 4),
            "cross_category_bonus": round(self.cross_category_bonus, 4),
            "categories_hit": self.num_categories_hit,
            "num_contributions": len(self.contributions),
        }


# ═══════════════════════════════════════════════════════════════
# CANDIDATE & WRAPPER RESULTS
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class PlayerDetectionCandidate:
    """
    A candidate player detection with full evidence and scoring.

    One candidate is generated per player family that received
    at least one piece of positive evidence.
    """
    family: PlayerFamily
    canonical_name: str
    evidence: list[PlayerEvidence] = field(default_factory=list)
    detected_version: str | None = None
    version_detail: VersionDetectionResult | None = None
    extracted_configs: list[ConfigExtractionResult] = field(default_factory=list)
    probable_output_types: list[OutputType] = field(default_factory=list)
    raw_score: float = 0.0
    confidence: float = 0.0
    score_breakdown: ScoreBreakdown | None = None

    @property
    def positive_evidence(self) -> list[PlayerEvidence]:
        """Evidence that supports detection (non-negating)."""
        return [e for e in self.evidence if not e.negates]

    @property
    def negative_evidence(self) -> list[PlayerEvidence]:
        """Evidence that weakens detection (negating)."""
        return [e for e in self.evidence if e.negates]

    @property
    def evidence_categories(self) -> set[EvidenceCategory]:
        """Distinct categories of positive evidence."""
        return {e.category for e in self.positive_evidence}

    @property
    def has_config(self) -> bool:
        return any(c.is_parsed for c in self.extracted_configs)

    @property
    def has_media_urls(self) -> bool:
        return any(c.has_media for c in self.extracted_configs)

    def add_evidence(self, ev: PlayerEvidence) -> None:
        """Append a piece of evidence to this candidate."""
        self.evidence.append(ev)


@dataclass(slots=True)
class WrapperDetectionResult:
    """
    Result of wrapper/iframe analysis.

    Captures whether the page is a wrapper, what kind,
    and what underlying player might be behind it.
    """
    is_wrapper: bool = False
    wrapper_type: str = ""       # "custom_iframe", "generic_hls", "relay_page"
    probable_underlying_player: PlayerFamily | None = None
    iframe_chain_depth: int = 0
    iframe_src_hints: list[str] = field(default_factory=list)
    probable_service_hint: str | None = None
    confidence: float = 0.0
    evidence: list[PlayerEvidence] = field(default_factory=list)

    @property
    def has_underlying_player(self) -> bool:
        return (
            self.probable_underlying_player is not None
            and self.probable_underlying_player != PlayerFamily.UNKNOWN
            and self.probable_underlying_player != PlayerFamily.NONE
        )


# ═══════════════════════════════════════════════════════════════
# DETECTION CONTEXT & FINAL RESULT
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class PlayerDetectionContext:
    """
    Optional context passed into the detection engine to influence
    detection behavior or provide external hints.

    `service_hints` may come from Phase 2 service resolution.
    `known_player_hint` can bias scoring if a service is known
    to use a specific player.
    `max_candidates` limits how many candidates to return.
    `extract_configs` controls whether config extraction runs.
    """
    # ADAPTATION POINT: service_hints must align with Phase 2
    # service resolution model's label/hint format.
    service_hints: list[str] = field(default_factory=list)
    known_player_hint: PlayerFamily | None = None
    referer_url: str | None = None
    parent_iframe_url: str | None = None
    iframe_depth: int = 0
    max_candidates: int = 10
    extract_configs: bool = True
    detect_wrappers: bool = True
    min_confidence_threshold: float = 0.05
    debug: bool = False

    @property
    def has_service_hints(self) -> bool:
        return len(self.service_hints) > 0

    @property
    def has_player_hint(self) -> bool:
        return (
            self.known_player_hint is not None
            and self.known_player_hint != PlayerFamily.NONE
            and self.known_player_hint != PlayerFamily.UNKNOWN
        )


@dataclass(slots=True)
class PlayerDetectionResult:
    """
    Top-level result of the player detection pipeline.

    Contains the best candidate, all ranked candidates,
    optional wrapper analysis, the context used, a list
    of human-readable explanation lines, and timing info.
    """
    best: PlayerDetectionCandidate | None = None
    candidates: list[PlayerDetectionCandidate] = field(default_factory=list)
    wrapper: WrapperDetectionResult | None = None
    detection_context: PlayerDetectionContext = field(
        default_factory=PlayerDetectionContext
    )
    explanation: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    url: str = ""
    error: str | None = None

    @property
    def detected(self) -> bool:
        """True if at least one candidate was found."""
        return self.best is not None

    @property
    def best_family(self) -> PlayerFamily:
        """Shorthand for the best candidate's family."""
        if self.best is not None:
            return self.best.family
        return PlayerFamily.NONE

    @property
    def best_confidence(self) -> float:
        """Shorthand for the best candidate's confidence."""
        if self.best is not None:
            return self.best.confidence
        return 0.0

    @property
    def is_wrapper(self) -> bool:
        """True if wrapper analysis detected a wrapper page."""
        return self.wrapper is not None and self.wrapper.is_wrapper

    @property
    def num_candidates(self) -> int:
        return len(self.candidates)

    def get_candidate(self, family: PlayerFamily) -> PlayerDetectionCandidate | None:
        """Look up a specific candidate by family."""
        for c in self.candidates:
            if c.family == family:
                return c
        return None

    def to_summary_dict(self) -> dict[str, Any]:
        """Compact summary for logging / serialization."""
        summary: dict[str, Any] = {
            "detected": self.detected,
            "best_family": self.best_family.value,
            "best_confidence": round(self.best_confidence, 4),
            "num_candidates": self.num_candidates,
            "is_wrapper": self.is_wrapper,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "url": self.url,
        }
        if self.best and self.best.detected_version:
            summary["best_version"] = self.best.detected_version
        if self.wrapper and self.wrapper.is_wrapper:
            summary["wrapper_type"] = self.wrapper.wrapper_type
            if self.wrapper.has_underlying_player:
                summary["underlying_player"] = (
                    self.wrapper.probable_underlying_player.value  # type: ignore[union-attr]
                )
        if self.error:
            summary["error"] = self.error
        return summary