"""
resolution_pipeline/bootstrap.py

Wiring and initialization for the resolution pipeline.
Bootstraps all v2 components from config, preserves legacy fallback,
and returns a fully wired ServiceResolutionPipeline ready for use.

Gaps closed:
    G6 — bootstrap wires advanced detection/resolution components
    G7 — legacy fallback preserved when v2 is disabled

External dependencies (ADAPTATION POINTs):
    - Phase 1 extractor registry factory
    - Phase 2 label resolver factory
    - Phase 3 player detector factory
    - Legacy server switch function
    - Config loading from file/env
"""

from __future__ import annotations

import logging
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import (
    ResolutionTrace,
    TraceStepPhase,
)
from .settings import (
    IntegrationSettings,
    default_settings,
    legacy_compatible_settings,
)
from .multi_server_pipeline import ServiceResolutionPipeline
from .server_switcher_adapter import LabelResolverProtocol, LegacySwitchFn


logger = logging.getLogger("resolution_pipeline.bootstrap")


# ──────────────────────────────────────────────
# Component Initialization
# ──────────────────────────────────────────────

def _init_label_resolver(
    settings: IntegrationSettings,
) -> Optional[LabelResolverProtocol]:
    """
    Initialize the Phase 2 label resolver.

    ADAPTATION POINT:
        Replace the body of this function with the real repo's
        label resolver initialization. The returned object must
        satisfy LabelResolverProtocol (i.e., have a .resolve() method).

    Example real-repo wiring:
        from intelligence.label_mapping import LabelResolverV2
        resolver = LabelResolverV2(
            confidence_threshold=settings.label.confidence_threshold,
            overrides_path=settings.label.overrides_path,
        )
        return resolver
    """
    if not settings.effective_label_v2():
        logger.info("Label resolver v2 is disabled — skipping initialization")
        return None

    try:
        from intelligence.service_resolution import LabelResolutionContext
        from intelligence.service_resolution.resolver import LabelResolver
        from intelligence.service_resolution.site_overrides import (
            SiteOverrideManager,
            get_default_override_manager,
        )

        override_manager = get_default_override_manager()
        overrides_path = settings.label.overrides_path
        if overrides_path:
            override_file = Path(overrides_path).expanduser()
            if override_file.exists():
                merged = SiteOverrideManager()
                for site_id, entries in override_manager.get_all_overrides().items():
                    merged.register_site_profile(
                        site_id,
                        [
                            (entry.normalized_label, entry.service_id, entry.strength, entry.notes or "")
                            for entry in entries
                        ],
                    )
                try:
                    raw_data = json.loads(override_file.read_text(encoding="utf-8"))
                    if isinstance(raw_data, dict):
                        for site_id, mapping in raw_data.items():
                            if isinstance(mapping, dict):
                                merged.register_overrides_bulk(site_id, mapping)
                    override_manager = merged
                except Exception as exc:
                    logger.warning("Failed to load site label overrides from %s: %s", override_file, exc)

        base_resolver = LabelResolver(override_manager=override_manager)

        class ResolverAdapter:
            def __init__(self, resolver: LabelResolver) -> None:
                self._resolver = resolver

            def resolve(
                self,
                label: str,
                context: Optional[Dict[str, Any]] = None,
            ) -> List[Dict[str, Any]]:
                context = context or {}
                resolution_context = LabelResolutionContext.build(
                    site_id=context.get("site_domain") or context.get("site_id"),
                    iframe_url=context.get("iframe_url"),
                    page_url=context.get("page_url"),
                    button_text=context.get("button_text"),
                    player_hint=context.get("player_hint"),
                    **(context.get("extra_hints") or {}),
                )
                result = self._resolver.resolve(label, resolution_context)

                candidates: List[Dict[str, Any]] = []
                for candidate in result.candidates:
                    aliases: List[str] = []
                    if candidate.matched_alias is not None:
                        aliases.append(candidate.matched_alias.raw_label)
                    candidates.append(
                        {
                            "service_name": candidate.service.service_id,
                            "confidence": candidate.final_score,
                            "explanation": result.explanation.summary,
                            "aliases_used": aliases,
                        }
                    )
                return candidates

        return ResolverAdapter(base_resolver)
    except ImportError:
        logger.warning("Phase 2 label resolver module not found - skipping")
        return None


def _init_player_detector(
    settings: IntegrationSettings,
) -> Optional[Callable]:
    """
    Initialize the Phase 3 player detector.

    ADAPTATION POINT:
        Replace the body of this function with the real repo's
        player detector initialization. The returned callable should
        accept HTML string and return a player detection result object.

    Example real-repo wiring:
        from intelligence.player_detection import PlayerDetectorV2
        detector = PlayerDetectorV2(
            detection_depth=settings.player.detection_depth,
            detect_wrappers=settings.player.detect_wrappers,
            extract_config=settings.player.extract_config,
            min_confidence=settings.player.min_confidence,
        )
        return detector.detect
    """
    if not settings.effective_player_v2():
        logger.info("Player detector v2 is disabled — skipping initialization")
        return None

    try:
        from player_intelligence import PlayerDetectionContext, detect_players

        def adapter(html: str, url: str = "") -> Any:
            context = PlayerDetectionContext(
                extract_configs=settings.player.extract_config,
                detect_wrappers=settings.player.detect_wrappers,
                debug=settings.debug.enabled,
                min_confidence_threshold=settings.player.min_confidence,
            )
            result = detect_players(html, url or "about:blank", context=context)
            best = result.best
            config = None
            if best and best.extracted_configs:
                parsed_configs = [item.parsed for item in best.extracted_configs if item.is_parsed]
                if parsed_configs:
                    config = parsed_configs[0]

            return SimpleNamespace(
                player_name=best.canonical_name if best else None,
                name=best.canonical_name if best else None,
                version=best.detected_version if best else None,
                player_version=best.detected_version if best else None,
                framework=best.family.value if best else None,
                config=config,
                config_extracted=config or {},
                wrapper=result.wrapper.wrapper_type if result.wrapper else None,
                wrapper_detected=result.wrapper.wrapper_type if result.wrapper else None,
                confidence=result.best_confidence,
                raw_result=result,
            )

        return adapter
    except ImportError:
        logger.warning("Phase 3 player detector module not found - skipping")
        return None


def _init_legacy_fallback(
    settings: IntegrationSettings,
) -> Optional[LegacySwitchFn]:
    """
    Initialize the legacy server switch function.

    ADAPTATION POINT:
        Replace the body of this function with the real repo's
        legacy server switch function.

    Example real-repo wiring:
        from server_switcher import switch_server
        return switch_server
    """
    if not settings.legacy_fallback_enabled:
        logger.info("Legacy fallback is disabled — skipping initialization")
        return None

    try:
        from utils.embed_services import get_detector

        detector = get_detector()

        def legacy_switch(label: str) -> Optional[str]:
            service = detector.resolve_label_service(label)
            return service.id if service is not None else None

        logger.info("Legacy fallback wired to embed service label resolution")
        return legacy_switch
    except ImportError:
        logger.warning("Legacy server switch module not found — skipping")
        return None


# ──────────────────────────────────────────────
# Settings Loading
# ──────────────────────────────────────────────

def _load_settings(
    config: Any,
) -> IntegrationSettings:
    """
    Load IntegrationSettings from various input types.

    Supports:
        - IntegrationSettings object (pass-through)
        - dict (parsed via from_dict)
        - target project Config object
        - None (defaults)
    """
    if isinstance(config, IntegrationSettings):
        return config

    if isinstance(config, dict):
        return IntegrationSettings.from_dict(config)

    extractor = getattr(config, "extractor", None)
    if extractor is not None:
        ambiguous_policy = getattr(extractor, "ambiguous_label_policy", "best_match")
        if ambiguous_policy == "keep_candidates":
            ambiguous_policy = "best_match"
        settings_dict = {
            "label": {
                "enable_v2": bool(getattr(extractor, "enable_label_mapping_v2", True)),
                "confidence_threshold": float(getattr(extractor, "label_confidence_threshold", 0.6)),
                "ambiguous_policy": str(ambiguous_policy),
                "overrides_path": getattr(extractor, "site_label_overrides_path", None),
            },
            "player": {
                "enable_v2": bool(getattr(extractor, "enable_player_db_v2", True)),
                "min_confidence": 0.5,
                "detect_wrappers": True,
                "extract_config": True,
            },
            "report": {
                "include_alternatives": bool(getattr(extractor, "enable_candidate_reporting", True)),
                "include_trace": bool(getattr(extractor, "debug_resolution_trace", False)),
                "include_evidence": True,
                "include_player": True,
            },
            "debug": {
                "enabled": bool(getattr(extractor, "debug_resolution_trace", False)),
            },
            "legacy_fallback_enabled": True,
        }
        return IntegrationSettings.from_dict(settings_dict)

    if config is None:
        return default_settings()

    logger.warning(
        f"Unrecognized config type {type(config).__name__} — using defaults"
    )
    return default_settings()


# ──────────────────────────────────────────────
# Wiring
# ──────────────────────────────────────────────

def _wire_pipeline(
    settings: IntegrationSettings,
    label_resolver: Optional[LabelResolverProtocol],
    player_detector: Optional[Callable],
    legacy_switch_fn: Optional[LegacySwitchFn],
) -> ServiceResolutionPipeline:
    """
    Construct a ServiceResolutionPipeline from initialized components.
    """
    return ServiceResolutionPipeline(
        settings=settings,
        label_resolver=label_resolver,
        player_detector=player_detector,
        legacy_switch_fn=legacy_switch_fn,
    )


# ──────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────

def _validate_wiring(
    pipeline: ServiceResolutionPipeline,
    settings: IntegrationSettings,
) -> List[str]:
    """
    Validate the wired pipeline and return a list of warnings.
    """
    warnings: List[str] = []

    # Settings validation
    warnings.extend(settings.validate())

    # Component availability checks
    caps = pipeline.capabilities

    if settings.effective_label_v2() and not caps["label_v2"]:
        warnings.append(
            "Label v2 is enabled in settings but no label resolver was initialized. "
            "Label resolution will be skipped."
        )

    if settings.effective_player_v2() and not caps["player_v2"]:
        warnings.append(
            "Player v2 is enabled in settings but no player detector was initialized. "
            "Player detection will be skipped."
        )

    if settings.legacy_fallback_enabled and not caps["legacy_fallback"]:
        warnings.append(
            "Legacy fallback is enabled in settings but no legacy switch function was initialized. "
            "Legacy fallback will be unavailable."
        )

    if not any([caps["label_v2"], caps["player_v2"], caps["legacy_fallback"]]):
        warnings.append(
            "CRITICAL: No resolution capability is available. "
            "The pipeline will not be able to resolve any services. "
            "Enable at least one v2 system or the legacy fallback."
        )

    return warnings


# ──────────────────────────────────────────────
# Legacy Config Mapping
# ──────────────────────────────────────────────

# ADAPTATION POINT: Map old config keys to new settings keys.
# Extend this mapping with the real repo's legacy config keys.
_LEGACY_CONFIG_MAP: Dict[str, str] = {
    "use_label_v2": "enable_label_mapping_v2",
    "use_player_v2": "enable_player_db_v2",
    "label_threshold": "label_confidence_threshold",
    "ambiguous_policy": "label_ambiguous_policy",
    "debug": "debug_resolution_trace",
    "overrides_path": "site_label_overrides_path",
    "enable_reports": "enable_candidate_reporting",
    "use_legacy": "legacy_fallback_enabled",
}


def _translate_legacy_config(legacy_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Translate legacy config keys to the new settings format.
    """
    translated: Dict[str, Any] = {}

    for key, value in legacy_config.items():
        new_key = _LEGACY_CONFIG_MAP.get(key, key)
        translated[new_key] = value

    return translated


# ──────────────────────────────────────────────
# Bootstrap Entry Points
# ──────────────────────────────────────────────

class BootstrapResult:
    """
    Result of bootstrapping the detection system.

    Contains the wired pipeline, the settings used, and any
    warnings produced during initialization.
    """

    __slots__ = ("pipeline", "settings", "warnings", "capabilities")

    def __init__(
        self,
        pipeline: ServiceResolutionPipeline,
        settings: IntegrationSettings,
        warnings: List[str],
    ) -> None:
        self.pipeline = pipeline
        self.settings = settings
        self.warnings = warnings
        self.capabilities = pipeline.capabilities

    @property
    def is_healthy(self) -> bool:
        """True if no critical warnings were produced."""
        return not any("CRITICAL" in w for w in self.warnings)

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0

    def log_warnings(self) -> None:
        """Log all warnings to the module logger."""
        for warning in self.warnings:
            if "CRITICAL" in warning:
                logger.error(warning)
            else:
                logger.warning(warning)

    def __repr__(self) -> str:
        return (
            f"BootstrapResult(healthy={self.is_healthy}, "
            f"warnings={len(self.warnings)}, "
            f"capabilities={self.capabilities})"
        )


def bootstrap_detection_system(
    config: Any = None,
    label_resolver: Optional[LabelResolverProtocol] = None,
    player_detector: Optional[Callable] = None,
    legacy_switch_fn: Optional[LegacySwitchFn] = None,
) -> BootstrapResult:
    """
    Bootstrap the full resolution pipeline from config.

    This is the primary entry point for system initialization.

    Args:
        config:           Config dict, IntegrationSettings, or None for defaults.
        label_resolver:   Pre-built label resolver (overrides auto-init).
                          ADAPTATION POINT.
        player_detector:  Pre-built player detector (overrides auto-init).
                          ADAPTATION POINT.
        legacy_switch_fn: Pre-built legacy switch fn (overrides auto-init).
                          ADAPTATION POINT.

    Returns:
        BootstrapResult containing the wired pipeline, settings, and warnings.

    Usage:
        result = bootstrap_detection_system({"enable_label_mapping_v2": True})
        if result.is_healthy:
            pipeline = result.pipeline
            decision = pipeline.run(context)
    """
    # ── Load settings ──
    settings = _load_settings(config)

    logger.info(f"Bootstrapping resolution pipeline: {settings}")

    # ── Initialize components (use pre-built if provided) ──
    resolved_label_resolver = label_resolver
    if resolved_label_resolver is None:
        resolved_label_resolver = _init_label_resolver(settings)

    resolved_player_detector = player_detector
    if resolved_player_detector is None:
        resolved_player_detector = _init_player_detector(settings)

    resolved_legacy_fn = legacy_switch_fn
    if resolved_legacy_fn is None:
        resolved_legacy_fn = _init_legacy_fallback(settings)

    # ── Wire pipeline ──
    pipeline = _wire_pipeline(
        settings=settings,
        label_resolver=resolved_label_resolver,
        player_detector=resolved_player_detector,
        legacy_switch_fn=resolved_legacy_fn,
    )

    # ── Validate ──
    warnings = _validate_wiring(pipeline, settings)

    result = BootstrapResult(
        pipeline=pipeline,
        settings=settings,
        warnings=warnings,
    )

    # ── Log ──
    if result.has_warnings:
        result.log_warnings()

    logger.info(f"Bootstrap complete: {result}")

    return result


def bootstrap_from_legacy_config(
    legacy_config: Dict[str, Any],
    legacy_switch_fn: Optional[LegacySwitchFn] = None,
) -> BootstrapResult:
    """
    Bootstrap from a legacy-format config dict.

    Translates legacy keys to the new format and delegates
    to bootstrap_detection_system.

    ADAPTATION POINT: Extend _LEGACY_CONFIG_MAP with the
    real repo's legacy config keys.

    Args:
        legacy_config:    Legacy config dict.
        legacy_switch_fn: Legacy switch function.

    Returns:
        BootstrapResult.
    """
    translated = _translate_legacy_config(legacy_config)
    return bootstrap_detection_system(
        config=translated,
        legacy_switch_fn=legacy_switch_fn,
    )


def bootstrap_legacy_only(
    legacy_switch_fn: Optional[LegacySwitchFn] = None,
) -> BootstrapResult:
    """
    Bootstrap in full legacy mode — all v2 systems disabled.

    Useful for gradual migration: start with this, then enable
    v2 features one at a time.
    """
    settings = legacy_compatible_settings()
    return bootstrap_detection_system(
        config=settings,
        legacy_switch_fn=legacy_switch_fn,
    )


# ──────────────────────────────────────────────
# Quick-Start Convenience
# ──────────────────────────────────────────────

def create_pipeline(
    config: Any = None,
    **kwargs: Any,
) -> ServiceResolutionPipeline:
    """
    Quick-start: bootstrap and return just the pipeline.

    Raises RuntimeError if bootstrap produces critical warnings.

    Args:
        config: Config dict, IntegrationSettings, or None.
        **kwargs: Passed to bootstrap_detection_system.

    Returns:
        ServiceResolutionPipeline ready for .run() calls.
    """
    result = bootstrap_detection_system(config=config, **kwargs)

    if not result.is_healthy:
        critical = [w for w in result.warnings if "CRITICAL" in w]
        logger.error(
            f"Pipeline bootstrap has critical issues: {critical}"
        )
        # Don't raise — let the caller decide.
        # The pipeline will still work for whatever capabilities are available.

    return result.pipeline
