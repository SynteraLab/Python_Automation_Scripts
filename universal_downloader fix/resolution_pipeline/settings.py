"""
resolution_pipeline/settings.py

Configuration and settings models for the integration pipeline.
All settings have sensible defaults and can be constructed from dicts.

Gaps closed:
    G5 — config toggles and thresholds for v2 systems
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────
# Sub-Settings
# ──────────────────────────────────────────────

@dataclass
class LabelSettings:
    """
    Settings for the label resolution subsystem (Phase 2 integration).
    """
    enable_v2: bool = True
    confidence_threshold: float = 0.6
    ambiguous_policy: str = "best_match"  # "best_match" | "reject" | "prompt" | "first_seen"
    overrides_path: Optional[str] = None
    max_alias_depth: int = 3
    normalize_case: bool = True

    # Known ambiguous_policy values and their semantics
    VALID_POLICIES = ("best_match", "reject", "prompt", "first_seen")

    def validate(self) -> List[str]:
        warnings: List[str] = []
        if not 0.0 <= self.confidence_threshold <= 1.0:
            warnings.append(
                f"label.confidence_threshold={self.confidence_threshold} "
                f"is outside [0.0, 1.0] — clamping recommended"
            )
        if self.ambiguous_policy not in self.VALID_POLICIES:
            warnings.append(
                f"label.ambiguous_policy='{self.ambiguous_policy}' "
                f"is not recognized — valid: {self.VALID_POLICIES}"
            )
        return warnings

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "LabelSettings":
        return cls(
            enable_v2=d.get("enable_v2", cls.enable_v2),
            confidence_threshold=float(d.get("confidence_threshold", cls.confidence_threshold)),
            ambiguous_policy=str(d.get("ambiguous_policy", cls.ambiguous_policy)),
            overrides_path=d.get("overrides_path", cls.overrides_path),
            max_alias_depth=int(d.get("max_alias_depth", cls.max_alias_depth)),
            normalize_case=bool(d.get("normalize_case", cls.normalize_case)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enable_v2": self.enable_v2,
            "confidence_threshold": self.confidence_threshold,
            "ambiguous_policy": self.ambiguous_policy,
            "overrides_path": self.overrides_path,
            "max_alias_depth": self.max_alias_depth,
            "normalize_case": self.normalize_case,
        }


@dataclass
class PlayerSettings:
    """
    Settings for the player/framework detection subsystem (Phase 3 integration).
    """
    enable_v2: bool = True
    detection_depth: str = "full"  # "full" | "shallow" | "disabled"
    min_confidence: float = 0.5
    detect_wrappers: bool = True
    extract_config: bool = True

    VALID_DEPTHS = ("full", "shallow", "disabled")

    def validate(self) -> List[str]:
        warnings: List[str] = []
        if not 0.0 <= self.min_confidence <= 1.0:
            warnings.append(
                f"player.min_confidence={self.min_confidence} "
                f"is outside [0.0, 1.0]"
            )
        if self.detection_depth not in self.VALID_DEPTHS:
            warnings.append(
                f"player.detection_depth='{self.detection_depth}' "
                f"is not recognized — valid: {self.VALID_DEPTHS}"
            )
        return warnings

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlayerSettings":
        return cls(
            enable_v2=d.get("enable_v2", cls.enable_v2),
            detection_depth=str(d.get("detection_depth", cls.detection_depth)),
            min_confidence=float(d.get("min_confidence", cls.min_confidence)),
            detect_wrappers=bool(d.get("detect_wrappers", cls.detect_wrappers)),
            extract_config=bool(d.get("extract_config", cls.extract_config)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enable_v2": self.enable_v2,
            "detection_depth": self.detection_depth,
            "min_confidence": self.min_confidence,
            "detect_wrappers": self.detect_wrappers,
            "extract_config": self.extract_config,
        }


@dataclass
class ResolverSettings:
    """
    Settings for the candidate merger / scoring engine.
    Weights control how much each evidence source contributes
    to the composite score.
    """
    # Scoring weights (should sum to ~1.0 for interpretability)
    domain_weight: float = 0.35
    label_weight: float = 0.30
    player_weight: float = 0.20
    iframe_weight: float = 0.10
    site_hint_weight: float = 0.05

    # Cross-server boosting
    cross_server_boost_factor: float = 0.10
    max_cross_server_boost: float = 0.30

    # Ambiguity detection
    ambiguity_threshold: float = 0.15

    # Tiebreak
    tiebreak_policy: str = "highest_confidence"  # "first_seen" | "highest_confidence" | "most_evidence"

    # Candidate limits
    max_candidates: int = 10
    min_score_threshold: float = 0.05

    VALID_TIEBREAKS = ("first_seen", "highest_confidence", "most_evidence")

    @property
    def weight_sum(self) -> float:
        return (
            self.domain_weight
            + self.label_weight
            + self.player_weight
            + self.iframe_weight
            + self.site_hint_weight
        )

    def validate(self) -> List[str]:
        warnings: List[str] = []
        ws = self.weight_sum
        if abs(ws - 1.0) > 0.05:
            warnings.append(
                f"resolver weight sum={ws:.2f} deviates from 1.0 — "
                f"scores may be hard to interpret"
            )
        if self.tiebreak_policy not in self.VALID_TIEBREAKS:
            warnings.append(
                f"resolver.tiebreak_policy='{self.tiebreak_policy}' "
                f"is not recognized — valid: {self.VALID_TIEBREAKS}"
            )
        if not 0.0 <= self.ambiguity_threshold <= 1.0:
            warnings.append(
                f"resolver.ambiguity_threshold={self.ambiguity_threshold} "
                f"is outside [0.0, 1.0]"
            )
        return warnings

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ResolverSettings":
        return cls(
            domain_weight=float(d.get("domain_weight", cls.domain_weight)),
            label_weight=float(d.get("label_weight", cls.label_weight)),
            player_weight=float(d.get("player_weight", cls.player_weight)),
            iframe_weight=float(d.get("iframe_weight", cls.iframe_weight)),
            site_hint_weight=float(d.get("site_hint_weight", cls.site_hint_weight)),
            cross_server_boost_factor=float(d.get("cross_server_boost_factor", cls.cross_server_boost_factor)),
            max_cross_server_boost=float(d.get("max_cross_server_boost", cls.max_cross_server_boost)),
            ambiguity_threshold=float(d.get("ambiguity_threshold", cls.ambiguity_threshold)),
            tiebreak_policy=str(d.get("tiebreak_policy", cls.tiebreak_policy)),
            max_candidates=int(d.get("max_candidates", cls.max_candidates)),
            min_score_threshold=float(d.get("min_score_threshold", cls.min_score_threshold)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain_weight": self.domain_weight,
            "label_weight": self.label_weight,
            "player_weight": self.player_weight,
            "iframe_weight": self.iframe_weight,
            "site_hint_weight": self.site_hint_weight,
            "cross_server_boost_factor": self.cross_server_boost_factor,
            "max_cross_server_boost": self.max_cross_server_boost,
            "ambiguity_threshold": self.ambiguity_threshold,
            "tiebreak_policy": self.tiebreak_policy,
            "max_candidates": self.max_candidates,
            "min_score_threshold": self.min_score_threshold,
        }


@dataclass
class ReportSettings:
    """
    Settings for report generation.
    """
    include_trace: bool = True
    include_alternatives: bool = True
    include_evidence: bool = True
    include_player: bool = True
    include_score_breakdown: bool = True
    max_alternatives: int = 5
    verbosity: str = "full"  # "full" | "summary" | "minimal"

    VALID_VERBOSITIES = ("full", "summary", "minimal")

    def validate(self) -> List[str]:
        warnings: List[str] = []
        if self.verbosity not in self.VALID_VERBOSITIES:
            warnings.append(
                f"report.verbosity='{self.verbosity}' "
                f"is not recognized — valid: {self.VALID_VERBOSITIES}"
            )
        return warnings

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ReportSettings":
        return cls(
            include_trace=bool(d.get("include_trace", cls.include_trace)),
            include_alternatives=bool(d.get("include_alternatives", cls.include_alternatives)),
            include_evidence=bool(d.get("include_evidence", cls.include_evidence)),
            include_player=bool(d.get("include_player", cls.include_player)),
            include_score_breakdown=bool(d.get("include_score_breakdown", cls.include_score_breakdown)),
            max_alternatives=int(d.get("max_alternatives", cls.max_alternatives)),
            verbosity=str(d.get("verbosity", cls.verbosity)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "include_trace": self.include_trace,
            "include_alternatives": self.include_alternatives,
            "include_evidence": self.include_evidence,
            "include_player": self.include_player,
            "include_score_breakdown": self.include_score_breakdown,
            "max_alternatives": self.max_alternatives,
            "verbosity": self.verbosity,
        }


@dataclass
class DebugSettings:
    """
    Settings for debug tracing and diagnostics.
    """
    enabled: bool = False
    max_trace_depth: int = 50
    output_mode: str = "structured"  # "structured" | "flat" | "verbose"
    log_to_stderr: bool = False

    VALID_MODES = ("structured", "flat", "verbose")

    def validate(self) -> List[str]:
        warnings: List[str] = []
        if self.output_mode not in self.VALID_MODES:
            warnings.append(
                f"debug.output_mode='{self.output_mode}' "
                f"is not recognized — valid: {self.VALID_MODES}"
            )
        return warnings

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DebugSettings":
        return cls(
            enabled=bool(d.get("enabled", cls.enabled)),
            max_trace_depth=int(d.get("max_trace_depth", cls.max_trace_depth)),
            output_mode=str(d.get("output_mode", cls.output_mode)),
            log_to_stderr=bool(d.get("log_to_stderr", cls.log_to_stderr)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_trace_depth": self.max_trace_depth,
            "output_mode": self.output_mode,
            "log_to_stderr": self.log_to_stderr,
        }


# ──────────────────────────────────────────────
# Top-Level Integration Settings
# ──────────────────────────────────────────────

@dataclass
class IntegrationSettings:
    """
    Top-level settings container for the entire resolution pipeline.
    Composes all sub-settings and adds pipeline-level toggles.
    """
    # Sub-settings
    label: LabelSettings = field(default_factory=LabelSettings)
    player: PlayerSettings = field(default_factory=PlayerSettings)
    resolver: ResolverSettings = field(default_factory=ResolverSettings)
    report: ReportSettings = field(default_factory=ReportSettings)
    debug: DebugSettings = field(default_factory=DebugSettings)

    # Pipeline-level settings
    pipeline_version: str = "4.0.0"
    enable_candidate_reporting: bool = True
    legacy_fallback_enabled: bool = True
    enable_label_mapping_v2: bool = True
    enable_player_db_v2: bool = True
    debug_resolution_trace: bool = False

    # Site-specific override path
    site_label_overrides_path: Optional[str] = None

    def is_v2_enabled(self) -> bool:
        """True if any v2 subsystem is enabled."""
        return (
            self.enable_label_mapping_v2
            or self.enable_player_db_v2
            or self.label.enable_v2
            or self.player.enable_v2
        )

    def is_fully_legacy(self) -> bool:
        """True if all v2 systems are disabled."""
        return not self.is_v2_enabled()

    def effective_label_v2(self) -> bool:
        """Consolidated check: is label v2 actually on?"""
        return self.enable_label_mapping_v2 and self.label.enable_v2

    def effective_player_v2(self) -> bool:
        """Consolidated check: is player v2 actually on?"""
        return self.enable_player_db_v2 and self.player.enable_v2

    def effective_debug(self) -> bool:
        """Consolidated check: is debug tracing on?"""
        return self.debug_resolution_trace or self.debug.enabled

    def validate(self) -> List[str]:
        """Validate all settings, return list of warnings."""
        warnings: List[str] = []
        warnings.extend(self.label.validate())
        warnings.extend(self.player.validate())
        warnings.extend(self.resolver.validate())
        warnings.extend(self.report.validate())
        warnings.extend(self.debug.validate())

        if self.legacy_fallback_enabled and self.is_fully_legacy():
            warnings.append(
                "All v2 systems are disabled with legacy_fallback_enabled=True — "
                "pipeline will always use legacy path"
            )

        if not self.legacy_fallback_enabled and self.is_fully_legacy():
            warnings.append(
                "All v2 systems are disabled AND legacy_fallback_enabled=False — "
                "pipeline may produce no results"
            )

        return warnings

    def with_overrides(self, **kwargs: Any) -> "IntegrationSettings":
        """
        Return a shallow copy with top-level fields overridden.
        Does NOT deep-merge sub-settings — for sub-setting overrides,
        construct them directly.
        """
        import dataclasses
        current = dataclasses.asdict(self)
        # Only apply overrides for known top-level fields
        top_level_fields = {
            f.name for f in dataclasses.fields(IntegrationSettings)
            if f.name not in ("label", "player", "resolver", "report", "debug")
        }
        for k, v in kwargs.items():
            if k in top_level_fields:
                current[k] = v
        # Reconstruct — but preserve sub-settings objects (not dicts)
        result = IntegrationSettings(
            label=self.label,
            player=self.player,
            resolver=self.resolver,
            report=self.report,
            debug=self.debug,
        )
        for k, v in kwargs.items():
            if k in top_level_fields:
                object.__setattr__(result, k, v)
        return result

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "IntegrationSettings":
        """
        Construct IntegrationSettings from a flat or nested dict.

        Supports both flat keys (e.g. 'label_confidence_threshold')
        and nested keys (e.g. {'label': {'confidence_threshold': 0.7}}).
        """
        # Extract sub-dicts
        label_d = d.get("label", {})
        player_d = d.get("player", {})
        resolver_d = d.get("resolver", {})
        report_d = d.get("report", {})
        debug_d = d.get("debug", {})

        # Support flat keys mapped into sub-settings
        _FLAT_MAP = {
            "label_confidence_threshold": ("label", "confidence_threshold"),
            "label_ambiguous_policy": ("label", "ambiguous_policy"),
            "player_min_confidence": ("player", "min_confidence"),
            "player_detection_depth": ("player", "detection_depth"),
            "resolver_ambiguity_threshold": ("resolver", "ambiguity_threshold"),
            "resolver_tiebreak_policy": ("resolver", "tiebreak_policy"),
        }

        sub_dicts = {
            "label": dict(label_d),
            "player": dict(player_d),
            "resolver": dict(resolver_d),
            "report": dict(report_d),
            "debug": dict(debug_d),
        }

        for flat_key, (sub_name, sub_key) in _FLAT_MAP.items():
            if flat_key in d:
                sub_dicts[sub_name][sub_key] = d[flat_key]

        return cls(
            label=LabelSettings.from_dict(sub_dicts["label"]),
            player=PlayerSettings.from_dict(sub_dicts["player"]),
            resolver=ResolverSettings.from_dict(sub_dicts["resolver"]),
            report=ReportSettings.from_dict(sub_dicts["report"]),
            debug=DebugSettings.from_dict(sub_dicts["debug"]),
            pipeline_version=str(d.get("pipeline_version", cls.pipeline_version)),
            enable_candidate_reporting=bool(d.get("enable_candidate_reporting", cls.enable_candidate_reporting)),
            legacy_fallback_enabled=bool(d.get("legacy_fallback_enabled", cls.legacy_fallback_enabled)),
            enable_label_mapping_v2=bool(d.get("enable_label_mapping_v2", cls.enable_label_mapping_v2)),
            enable_player_db_v2=bool(d.get("enable_player_db_v2", cls.enable_player_db_v2)),
            debug_resolution_trace=bool(d.get("debug_resolution_trace", cls.debug_resolution_trace)),
            site_label_overrides_path=d.get("site_label_overrides_path", cls.site_label_overrides_path),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label.to_dict(),
            "player": self.player.to_dict(),
            "resolver": self.resolver.to_dict(),
            "report": self.report.to_dict(),
            "debug": self.debug.to_dict(),
            "pipeline_version": self.pipeline_version,
            "enable_candidate_reporting": self.enable_candidate_reporting,
            "legacy_fallback_enabled": self.legacy_fallback_enabled,
            "enable_label_mapping_v2": self.enable_label_mapping_v2,
            "enable_player_db_v2": self.enable_player_db_v2,
            "debug_resolution_trace": self.debug_resolution_trace,
            "site_label_overrides_path": self.site_label_overrides_path,
        }

    def __repr__(self) -> str:
        v2_status = "v2_enabled" if self.is_v2_enabled() else "legacy_only"
        return (
            f"IntegrationSettings(version={self.pipeline_version}, "
            f"mode={v2_status}, "
            f"label_v2={self.effective_label_v2()}, "
            f"player_v2={self.effective_player_v2()}, "
            f"debug={self.effective_debug()})"
        )


# ──────────────────────────────────────────────
# Convenience: default settings
# ──────────────────────────────────────────────

def default_settings() -> IntegrationSettings:
    """Return IntegrationSettings with all defaults."""
    return IntegrationSettings()


def legacy_compatible_settings() -> IntegrationSettings:
    """
    Return settings that disable all v2 features,
    forcing the pipeline into full legacy fallback mode.
    """
    return IntegrationSettings(
        label=LabelSettings(enable_v2=False),
        player=PlayerSettings(enable_v2=False),
        enable_label_mapping_v2=False,
        enable_player_db_v2=False,
        legacy_fallback_enabled=True,
        debug_resolution_trace=False,
        enable_candidate_reporting=False,
    )


def debug_settings() -> IntegrationSettings:
    """
    Return settings with full debug tracing enabled.
    Useful for development and troubleshooting.
    """
    return IntegrationSettings(
        debug=DebugSettings(enabled=True, output_mode="verbose", log_to_stderr=True),
        debug_resolution_trace=True,
        report=ReportSettings(
            include_trace=True,
            include_alternatives=True,
            include_evidence=True,
            include_player=True,
            include_score_breakdown=True,
            verbosity="full",
        ),
        enable_candidate_reporting=True,
    )