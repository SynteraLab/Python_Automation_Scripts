# player_intelligence/detector.py
"""
Main player detection engine.

Orchestrates the 5-stage detection pipeline:
    Stage 1 — Marker scanning (per profile, all marker categories)
    Stage 2 — Candidate generation (group evidence by player family)
    Stage 3 — Enrichment (config extraction, version detection, output-type inference)
    Stage 4 — Scoring & ranking (weighted evidence → confidence)
    Stage 5 — Wrapper analysis (iframe/HLS/relay detection)

All public API functions are defined here and re-exported
by __init__.py.
"""

from __future__ import annotations

import logging
import time
from typing import Sequence

from .models import (
    ConfigExtractionResult,
    EvidenceCategory,
    OutputType,
    PlayerDetectionCandidate,
    PlayerDetectionContext,
    PlayerDetectionResult,
    PlayerEvidence,
    PlayerFamily,
    PlayerProfile,
    WrapperDetectionResult,
)
from .profiles import (
    PROFILES,
    get_all_profiles,
    get_profile,
    get_profiles_for_detection,
)
from .version_patterns import detect_version
from .config_extractors import extract_configs_for_profile, extract_all_configs
from .scoring import (
    score_and_rank_candidates,
    score_candidate,
    is_dominant_candidate,
    confidence_tier,
)
from .wrappers import detect_wrapper
from .explain import explain_player_detection

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# STAGE 1 — MARKER SCANNING
# ═══════════════════════════════════════════════════════════════

def _scan_markers(
    html: str,
    profiles: Sequence[PlayerProfile],
) -> dict[PlayerFamily, list[PlayerEvidence]]:
    """
    Scan HTML against all marker rules in all profiles.

    Returns a dict mapping each player family to its list of
    collected evidence objects.  Families with no matches are
    not included.
    """
    evidence_map: dict[PlayerFamily, list[PlayerEvidence]] = {}

    for profile in profiles:
        family_evidence: list[PlayerEvidence] = []

        # ── Script src markers ──
        for marker in profile.script_src_markers:
            _run_marker(html, marker, profile.family, family_evidence, "script_src")

        # ── DOM / CSS markers ──
        for marker in profile.dom_css_markers:
            _run_marker(html, marker, profile.family, family_evidence, "dom_css")

        # ── Inline JS markers ──
        for marker in profile.inline_js_markers:
            _run_marker(html, marker, profile.family, family_evidence, "inline_js")

        # ── Global variable markers ──
        for marker in profile.global_variable_markers:
            _run_marker(html, marker, profile.family, family_evidence, "global_var")

        # ── Data attribute markers ──
        for marker in profile.data_attribute_markers:
            _run_marker(html, marker, profile.family, family_evidence, "data_attr")

        if family_evidence:
            evidence_map[profile.family] = family_evidence

    return evidence_map


def _run_marker(
    html: str,
    marker: 'MarkerRule',
    family: PlayerFamily,
    evidence_list: list[PlayerEvidence],
    source_location: str,
) -> None:
    """
    Execute a single marker rule against the HTML and append
    evidence if matched.

    Only the first match per marker rule is recorded to avoid
    evidence inflation from repeated DOM patterns.
    """
    from .models import MarkerRule  # local to avoid circular at module top

    match = marker.search(html)
    if match is None:
        return

    matched_text = match.group(0)

    evidence_list.append(PlayerEvidence(
        category=marker.category,
        family=family,
        weight=marker.weight,
        matched_text=matched_text,
        rule_description=marker.description,
        source_location=source_location,
        negates=marker.negates,
    ))


# ═══════════════════════════════════════════════════════════════
# STAGE 2 — CANDIDATE GENERATION
# ═══════════════════════════════════════════════════════════════

def _generate_candidates(
    evidence_map: dict[PlayerFamily, list[PlayerEvidence]],
) -> list[PlayerDetectionCandidate]:
    """
    Create one PlayerDetectionCandidate per family that has evidence.

    Candidates are created with evidence attached but not yet scored.
    """
    candidates: list[PlayerDetectionCandidate] = []

    for family, evidence_list in evidence_map.items():
        profile = get_profile(family)
        canonical_name = profile.canonical_name if profile else family.value

        candidate = PlayerDetectionCandidate(
            family=family,
            canonical_name=canonical_name,
            evidence=list(evidence_list),
        )
        candidates.append(candidate)

    return candidates


# ═══════════════════════════════════════════════════════════════
# STAGE 3 — ENRICHMENT
# ═══════════════════════════════════════════════════════════════

def _enrich_candidates(
    candidates: list[PlayerDetectionCandidate],
    html: str,
    extract_configs: bool = True,
) -> None:
    """
    Enrich each candidate with:
    - Detected version
    - Extracted config blocks
    - Probable output types
    - Config-derived evidence

    Mutates candidates in-place.
    """
    for candidate in candidates:
        profile = get_profile(candidate.family)
        if profile is None:
            continue

        # ── 3a: Config extraction ──
        if extract_configs and profile.config_patterns:
            try:
                configs = extract_configs_for_profile(html, profile)
                candidate.extracted_configs = configs

                # Add config-shape evidence for successfully parsed configs
                for cfg in configs:
                    if cfg.is_parsed:
                        candidate.add_evidence(PlayerEvidence(
                            category=EvidenceCategory.CONFIG_SHAPE,
                            family=candidate.family,
                            weight=0.25,
                            matched_text=cfg.source_pattern,
                            rule_description=f"Config parsed: {cfg.source_pattern}",
                            source_location="config_extraction",
                        ))
                    if cfg.has_media:
                        candidate.add_evidence(PlayerEvidence(
                            category=EvidenceCategory.CONFIG_SHAPE,
                            family=candidate.family,
                            weight=0.15,
                            matched_text=f"Media URLs found: {len(cfg.media_hints.urls)}",
                            rule_description="Config contains media URLs",
                            source_location="config_extraction",
                        ))
            except Exception as exc:
                logger.debug(
                    "Config extraction failed for %s: %s",
                    candidate.family.value, exc,
                )

        # ── 3b: Version detection ──
        if profile.version_patterns:
            try:
                version_result = detect_version(html, profile.version_patterns)
                if version_result.detected:
                    candidate.detected_version = version_result.version
                    candidate.version_detail = version_result

                    candidate.add_evidence(PlayerEvidence(
                        category=EvidenceCategory.VERSION,
                        family=candidate.family,
                        weight=0.10,
                        matched_text=version_result.raw_match,
                        rule_description=f"Version detected: {version_result.version} ({version_result.source})",
                        source_location=version_result.source,
                    ))
            except Exception as exc:
                logger.debug(
                    "Version detection failed for %s: %s",
                    candidate.family.value, exc,
                )

        # ── 3c: Output type inference ──
        output_types = _infer_output_types(candidate, profile)
        candidate.probable_output_types = output_types


def _infer_output_types(
    candidate: PlayerDetectionCandidate,
    profile: PlayerProfile,
) -> list[OutputType]:
    """
    Infer probable output types from profile defaults and
    extracted config media hints.

    Config-extracted formats take priority over profile defaults
    because they reflect what the page actually serves.
    """
    # Start with config-derived formats
    config_formats: set[OutputType] = set()
    for cfg in candidate.extracted_configs:
        for fmt in cfg.media_hints.formats:
            config_formats.add(fmt)

    if config_formats:
        # Config-derived formats are concrete evidence
        return sorted(config_formats, key=lambda f: f.value)

    # Fall back to profile defaults
    if profile.probable_output_types:
        return list(profile.probable_output_types)

    return [OutputType.UNKNOWN]


# ═══════════════════════════════════════════════════════════════
# STAGE 4 — SCORING & RANKING
# ═══════════════════════════════════════════════════════════════

def _rank_candidates(
    candidates: list[PlayerDetectionCandidate],
    context: PlayerDetectionContext | None,
) -> list[PlayerDetectionCandidate]:
    """
    Score all candidates, filter by threshold, sort by confidence.

    Uses the scoring engine from scoring.py.
    """
    min_conf = 0.0
    if context is not None:
        min_conf = context.min_confidence_threshold

    return score_and_rank_candidates(
        candidates=candidates,
        profiles=PROFILES,
        context=context,
        min_confidence=min_conf,
    )


# ═══════════════════════════════════════════════════════════════
# STAGE 5 — WRAPPER ANALYSIS
# ═══════════════════════════════════════════════════════════════

def _analyze_wrappers(
    html: str,
    url: str,
    candidates: list[PlayerDetectionCandidate],
    context: PlayerDetectionContext | None,
    do_detect: bool = True,
) -> WrapperDetectionResult | None:
    """
    Run wrapper detection if enabled.
    """
    if not do_detect:
        return None

    return detect_wrapper(
        html=html,
        url=url,
        candidates=candidates,
        context=context,
    )


# ═══════════════════════════════════════════════════════════════
# RESULT ASSEMBLY
# ═══════════════════════════════════════════════════════════════

def _assemble_result(
    candidates: list[PlayerDetectionCandidate],
    wrapper: WrapperDetectionResult | None,
    context: PlayerDetectionContext,
    url: str,
    elapsed_ms: float,
    error: str | None = None,
) -> PlayerDetectionResult:
    """
    Assemble the final PlayerDetectionResult from pipeline outputs.

    Selects the best candidate, applies max_candidates limit,
    generates explanation lines, and packages everything.
    """
    # Respect max_candidates
    max_cands = context.max_candidates
    trimmed = candidates[:max_cands] if len(candidates) > max_cands else candidates

    # Best candidate
    best = trimmed[0] if trimmed else None

    # Build result
    result = PlayerDetectionResult(
        best=best,
        candidates=trimmed,
        wrapper=wrapper,
        detection_context=context,
        explanation=[],  # Populated below
        elapsed_ms=elapsed_ms,
        url=url,
        error=error,
    )

    # Generate explanation
    result.explanation = explain_player_detection(result)

    return result


# ═══════════════════════════════════════════════════════════════
# PRIMARY PUBLIC API
# ═══════════════════════════════════════════════════════════════

def detect_players(
    html: str,
    url: str,
    context: PlayerDetectionContext | None = None,
) -> PlayerDetectionResult:
    """
    Run the full 5-stage player detection pipeline.

    This is the primary entry point for the player intelligence
    system.  Given raw HTML and a URL, it detects all player
    frameworks present, scores them, analyzes wrappers, and
    returns a rich result object.

    Parameters
    ----------
    html : str
        Raw HTML content of the page.
    url : str
        URL of the page (used for wrapper analysis and context).
    context : PlayerDetectionContext | None
        Optional context with service hints, player hints,
        iframe depth, and detection flags.

    Returns
    -------
    PlayerDetectionResult
        Full detection result with best candidate, all candidates,
        wrapper analysis, explanation, and timing.

    Example
    -------
    >>> result = detect_players(html, "https://example.com/embed/123")
    >>> if result.detected:
    ...     print(f"Player: {result.best.canonical_name}")
    ...     print(f"Confidence: {result.best.confidence:.2f}")
    ...     print(f"Version: {result.best.detected_version}")
    """
    # ADAPTATION POINT: The host repository may wrap this function
    # with additional logging, caching, or error handling.

    t0 = time.monotonic()

    if context is None:
        context = PlayerDetectionContext()

    try:
        # ── Stage 1: Marker scanning ──
        profiles = get_profiles_for_detection()
        evidence_map = _scan_markers(html, profiles)

        logger.debug(
            "Stage 1 complete: %d families with evidence",
            len(evidence_map),
        )

        # ── Stage 2: Candidate generation ──
        candidates = _generate_candidates(evidence_map)

        logger.debug(
            "Stage 2 complete: %d candidates generated",
            len(candidates),
        )

        # ── Stage 3: Enrichment ──
        _enrich_candidates(
            candidates,
            html,
            extract_configs=context.extract_configs,
        )

        logger.debug("Stage 3 complete: candidates enriched")

        # ── Stage 4: Scoring & ranking ──
        ranked = _rank_candidates(candidates, context)

        logger.debug(
            "Stage 4 complete: %d candidates after filtering (best: %s @ %.3f)",
            len(ranked),
            ranked[0].family.value if ranked else "none",
            ranked[0].confidence if ranked else 0.0,
        )

        # ── Stage 5: Wrapper analysis ──
        wrapper = _analyze_wrappers(
            html, url, ranked, context,
            do_detect=context.detect_wrappers,
        )

        if wrapper and wrapper.is_wrapper:
            logger.debug(
                "Stage 5 complete: wrapper=%s confidence=%.3f",
                wrapper.wrapper_type,
                wrapper.confidence,
            )
        else:
            logger.debug("Stage 5 complete: no wrapper detected")

        # ── Assemble result ──
        elapsed = (time.monotonic() - t0) * 1000.0
        return _assemble_result(ranked, wrapper, context, url, elapsed)

    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000.0
        logger.error("Player detection failed: %s", exc, exc_info=True)
        return PlayerDetectionResult(
            detection_context=context,
            url=url,
            elapsed_ms=elapsed,
            error=str(exc),
        )


def detect_best_player(
    html: str,
    url: str,
    **kwargs,
) -> PlayerDetectionCandidate | None:
    """
    Convenience: detect players and return only the best candidate.

    Accepts the same keyword arguments as PlayerDetectionContext.

    Returns None if no player is detected.
    """
    context = PlayerDetectionContext(**kwargs) if kwargs else None
    result = detect_players(html, url, context)
    return result.best


def extract_player_configs(
    html: str,
    url: str,
    family: PlayerFamily | None = None,
) -> list[ConfigExtractionResult]:
    """
    Extract player config blocks from HTML without full detection.

    If `family` is specified, only that player's config patterns
    are used.  Otherwise, all profiles are tried.

    This is useful when you already know (or suspect) the player
    and just want config data.
    """
    # ADAPTATION POINT: The host repository may want to cache
    # extracted configs alongside detection results.
    profiles = get_all_profiles()
    return extract_all_configs(html, profiles, family_filter=family)


def score_player_candidate(
    candidate: PlayerDetectionCandidate,
    context: PlayerDetectionContext | None = None,
) -> 'ScoreBreakdown':
    """
    Score (or re-score) a single candidate.

    Useful for re-evaluating a candidate after adding new evidence
    or changing context.
    """
    from .models import ScoreBreakdown  # local import for return type hint

    profile = get_profile(candidate.family)
    if profile is None:
        # Return empty breakdown
        return ScoreBreakdown()

    return score_candidate(candidate, profile, context)


# ═══════════════════════════════════════════════════════════════
# TARGETED DETECTION HELPERS
# ═══════════════════════════════════════════════════════════════

def detect_specific_player(
    html: str,
    url: str,
    family: PlayerFamily,
    context: PlayerDetectionContext | None = None,
) -> PlayerDetectionCandidate | None:
    """
    Run detection against a single player family only.

    Useful when you have a strong hint about which player to
    expect and want to avoid scanning all profiles.

    Returns the candidate if detected, None otherwise.
    """
    profile = get_profile(family)
    if profile is None:
        return None

    if context is None:
        context = PlayerDetectionContext()

    # Stage 1: scan only this profile's markers
    evidence_map = _scan_markers(html, [profile])
    if family not in evidence_map:
        return None

    # Stage 2: single candidate
    candidates = _generate_candidates(evidence_map)
    if not candidates:
        return None

    # Stage 3: enrich
    _enrich_candidates(candidates, html, extract_configs=context.extract_configs)

    # Stage 4: score
    ranked = _rank_candidates(candidates, context)

    return ranked[0] if ranked else None


def detect_players_multi_pass(
    html: str,
    url: str,
    context: PlayerDetectionContext | None = None,
    iframe_htmls: dict[str, str] | None = None,
) -> PlayerDetectionResult:
    """
    Multi-pass detection: first the main page, then any iframe contents.

    If `iframe_htmls` is provided (mapping iframe src → HTML content),
    each iframe is also scanned.  Results from iframes are merged
    into the main result with adjusted iframe_depth in context.

    This is a convenience wrapper for recursive embed scenarios.
    The caller is responsible for fetching iframe contents.
    """
    # ADAPTATION POINT: The host repository's networking layer
    # is responsible for fetching iframe HTML.  This function
    # only processes already-fetched content.

    if context is None:
        context = PlayerDetectionContext()

    # Main page detection
    main_result = detect_players(html, url, context)

    if not iframe_htmls:
        return main_result

    # Iframe pass
    iframe_candidates: list[PlayerDetectionCandidate] = []
    for iframe_src, iframe_html in iframe_htmls.items():
        iframe_ctx = PlayerDetectionContext(
            service_hints=context.service_hints,
            known_player_hint=context.known_player_hint,
            referer_url=url,
            parent_iframe_url=url,
            iframe_depth=context.iframe_depth + 1,
            max_candidates=context.max_candidates,
            extract_configs=context.extract_configs,
            detect_wrappers=False,  # Avoid recursive wrapper detection
            min_confidence_threshold=context.min_confidence_threshold,
            debug=context.debug,
        )
        iframe_result = detect_players(iframe_html, iframe_src, iframe_ctx)
        for cand in iframe_result.candidates:
            # Tag evidence with iframe source
            for ev in cand.evidence:
                # Evidence objects are frozen, so we note iframe origin in explanation
                pass
            iframe_candidates.append(cand)

    if not iframe_candidates:
        return main_result

    # Merge: combine candidates, re-rank
    all_candidates = list(main_result.candidates) + iframe_candidates

    # Deduplicate by family: keep the highest-confidence candidate per family
    family_best: dict[PlayerFamily, PlayerDetectionCandidate] = {}
    for cand in all_candidates:
        existing = family_best.get(cand.family)
        if existing is None or cand.confidence > existing.confidence:
            family_best[cand.family] = cand

    merged_candidates = sorted(
        family_best.values(),
        key=lambda c: c.confidence,
        reverse=True,
    )

    # Rebuild result
    merged_best = merged_candidates[0] if merged_candidates else None

    merged_explanation = list(main_result.explanation)
    merged_explanation.append("")
    merged_explanation.append(f"=== Multi-pass: {len(iframe_htmls)} iframe(s) scanned ===")
    for cand in iframe_candidates:
        merged_explanation.append(
            f"  Iframe candidate: {cand.canonical_name} "
            f"confidence={cand.confidence:.3f}"
        )

    return PlayerDetectionResult(
        best=merged_best,
        candidates=merged_candidates[:context.max_candidates],
        wrapper=main_result.wrapper,
        detection_context=context,
        explanation=merged_explanation,
        elapsed_ms=main_result.elapsed_ms,  # Main page timing only
        url=url,
        error=main_result.error,
    )