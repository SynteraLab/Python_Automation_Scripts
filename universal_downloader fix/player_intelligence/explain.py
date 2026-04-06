# player_intelligence/explain.py
"""
Human-readable explanation generator for player detection results.

Produces structured debug/trace lines from PlayerDetectionResult,
individual candidates, wrappers, and score breakdowns.

Output is a list of plain strings suitable for logging, debug
panels, or developer inspection.
"""

from __future__ import annotations

from .models import (
    EvidenceCategory,
    PlayerDetectionCandidate,
    PlayerDetectionResult,
    PlayerEvidence,
    PlayerFamily,
    ScoreBreakdown,
    ScoreContribution,
    WrapperDetectionResult,
)
from .scoring import confidence_tier, is_dominant_candidate


# ═══════════════════════════════════════════════════════════════
# MAIN EXPLANATION ENTRY POINT
# ═══════════════════════════════════════════════════════════════

def explain_player_detection(
    result: PlayerDetectionResult,
) -> list[str]:
    """
    Generate a complete human-readable explanation of a detection result.

    Returns a list of strings, one per line.  Suitable for joining
    with newlines for display or logging.
    """
    lines: list[str] = []

    # ── Header ──
    lines.append("=" * 60)
    lines.append("PLAYER DETECTION REPORT")
    lines.append("=" * 60)
    lines.append(f"URL: {result.url}")
    lines.append(f"Elapsed: {result.elapsed_ms:.2f} ms")
    lines.append(f"Candidates found: {result.num_candidates}")
    lines.append("")

    # ── Error ──
    if result.error:
        lines.append(f"ERROR: {result.error}")
        lines.append("")

    # ── Best candidate ──
    if result.best is not None:
        tier = confidence_tier(result.best.confidence)
        lines.append(f"BEST MATCH: {result.best.canonical_name}")
        lines.append(f"  Family:     {result.best.family.value}")
        lines.append(f"  Confidence: {result.best.confidence:.4f} ({tier})")
        if result.best.detected_version:
            lines.append(f"  Version:    {result.best.detected_version}")
        if result.best.probable_output_types:
            types_str = ", ".join(t.value for t in result.best.probable_output_types)
            lines.append(f"  Output:     {types_str}")
        if result.best.has_config:
            lines.append(f"  Configs:    {len(result.best.extracted_configs)} extracted")
        if result.best.has_media_urls:
            total_urls = sum(
                len(c.media_hints.urls) for c in result.best.extracted_configs
            )
            lines.append(f"  Media URLs: {total_urls} found")

        # Dominance check
        second = result.candidates[1] if len(result.candidates) > 1 else None
        if is_dominant_candidate(result.best, second):
            lines.append("  Dominance:  YES (clear winner)")
        elif second:
            lines.append(
                f"  Dominance:  NO (runner-up: {second.canonical_name} "
                f"@ {second.confidence:.4f})"
            )
        lines.append("")
    else:
        lines.append("BEST MATCH: (none)")
        lines.append("")

    # ── All candidates ──
    if result.candidates:
        lines.append("-" * 40)
        lines.append("ALL CANDIDATES (ranked)")
        lines.append("-" * 40)
        for i, cand in enumerate(result.candidates, 1):
            lines.extend(_explain_candidate_brief(cand, rank=i))
        lines.append("")

    # ── Wrapper ──
    if result.wrapper is not None and result.wrapper.is_wrapper:
        lines.append("-" * 40)
        lines.append("WRAPPER DETECTION")
        lines.append("-" * 40)
        lines.extend(explain_wrapper(result.wrapper))
        lines.append("")

    # ── Detailed best candidate ──
    if result.best is not None:
        lines.append("-" * 40)
        lines.append("DETAILED BEST CANDIDATE")
        lines.append("-" * 40)
        lines.extend(explain_candidate(result.best))
        lines.append("")

    # ── Context ──
    ctx = result.detection_context
    if ctx.has_service_hints or ctx.has_player_hint or ctx.iframe_depth > 0:
        lines.append("-" * 40)
        lines.append("DETECTION CONTEXT")
        lines.append("-" * 40)
        if ctx.has_service_hints:
            lines.append(f"  Service hints: {ctx.service_hints}")
        if ctx.has_player_hint:
            lines.append(f"  Player hint:   {ctx.known_player_hint}")
        if ctx.iframe_depth > 0:
            lines.append(f"  Iframe depth:  {ctx.iframe_depth}")
        if ctx.referer_url:
            lines.append(f"  Referer:       {ctx.referer_url}")
        lines.append("")

    lines.append("=" * 60)
    lines.append("END OF REPORT")
    lines.append("=" * 60)

    return lines


# ═══════════════════════════════════════════════════════════════
# CANDIDATE EXPLANATION
# ═══════════════════════════════════════════════════════════════

def explain_candidate(
    candidate: PlayerDetectionCandidate,
) -> list[str]:
    """
    Generate detailed explanation for a single candidate.

    Includes all evidence, score breakdown, configs, and version.
    """
    lines: list[str] = []
    tier = confidence_tier(candidate.confidence)

    lines.append(f"Player:     {candidate.canonical_name} ({candidate.family.value})")
    lines.append(f"Confidence: {candidate.confidence:.4f} ({tier})")
    lines.append(f"Raw score:  {candidate.raw_score:.4f}")

    if candidate.detected_version:
        lines.append(f"Version:    {candidate.detected_version}")
        if candidate.version_detail:
            lines.append(f"  Source:   {candidate.version_detail.source}")
            lines.append(f"  Pattern:  {candidate.version_detail.pattern_description}")

    if candidate.probable_output_types:
        types_str = ", ".join(t.value for t in candidate.probable_output_types)
        lines.append(f"Output types: {types_str}")

    # ── Evidence listing ──
    lines.append("")
    pos_evidence = candidate.positive_evidence
    neg_evidence = candidate.negative_evidence
    lines.append(f"Evidence: {len(pos_evidence)} positive, {len(neg_evidence)} negative")
    lines.append(f"Categories hit: {len(candidate.evidence_categories)}")
    lines.append("")

    if pos_evidence:
        lines.append("  Positive evidence:")
        for ev in pos_evidence:
            lines.append(f"    {format_evidence(ev)}")
    if neg_evidence:
        lines.append("  Negative evidence:")
        for ev in neg_evidence:
            lines.append(f"    {format_evidence(ev)}")

    # ── Score breakdown ──
    if candidate.score_breakdown is not None:
        lines.append("")
        lines.extend(explain_score_breakdown(candidate.score_breakdown))

    # ── Extracted configs ──
    if candidate.extracted_configs:
        lines.append("")
        lines.append(f"Extracted configs: {len(candidate.extracted_configs)}")
        for i, cfg in enumerate(candidate.extracted_configs, 1):
            parsed_status = "parsed" if cfg.is_parsed else "raw only"
            media_status = f"{len(cfg.media_hints.urls)} URLs" if cfg.has_media else "no media"
            lines.append(
                f"  [{i}] {cfg.config_type.value} — {cfg.source_pattern} "
                f"({parsed_status}, {media_status})"
            )
            if cfg.extraction_error:
                lines.append(f"      Error: {cfg.extraction_error}")
            if cfg.has_media:
                for url in cfg.media_hints.urls[:5]:
                    lines.append(f"      URL: {url[:120]}")
                formats_str = ", ".join(f.value for f in cfg.media_hints.formats)
                if formats_str:
                    lines.append(f"      Formats: {formats_str}")
                if cfg.media_hints.has_drm:
                    lines.append("      DRM: detected")
                if cfg.media_hints.has_subtitles:
                    lines.append("      Subtitles: detected")

    return lines


def _explain_candidate_brief(
    candidate: PlayerDetectionCandidate,
    rank: int = 0,
) -> list[str]:
    """One-line-ish summary of a candidate for the ranked list."""
    lines: list[str] = []
    tier = confidence_tier(candidate.confidence)
    version = f" v{candidate.detected_version}" if candidate.detected_version else ""
    config_info = f" [{len(candidate.extracted_configs)} configs]" if candidate.extracted_configs else ""
    num_evidence = len(candidate.positive_evidence)
    cat_count = len(candidate.evidence_categories)

    lines.append(
        f"  #{rank}: {candidate.canonical_name}{version} — "
        f"confidence={candidate.confidence:.4f} ({tier}) "
        f"evidence={num_evidence} categories={cat_count}{config_info}"
    )
    return lines


# ═══════════════════════════════════════════════════════════════
# WRAPPER EXPLANATION
# ═══════════════════════════════════════════════════════════════

def explain_wrapper(
    wrapper: WrapperDetectionResult,
) -> list[str]:
    """Generate explanation lines for a wrapper detection result."""
    lines: list[str] = []

    lines.append(f"  Wrapper type: {wrapper.wrapper_type}")
    lines.append(f"  Confidence:   {wrapper.confidence:.4f}")

    if wrapper.has_underlying_player:
        lines.append(
            f"  Underlying:   {wrapper.probable_underlying_player.value}"  # type: ignore[union-attr]
        )
    else:
        lines.append("  Underlying:   (unknown)")

    if wrapper.probable_service_hint:
        lines.append(f"  Service hint: {wrapper.probable_service_hint}")

    lines.append(f"  Chain depth:  {wrapper.iframe_chain_depth}")

    if wrapper.iframe_src_hints:
        lines.append(f"  Iframe srcs:  {len(wrapper.iframe_src_hints)}")
        for src in wrapper.iframe_src_hints[:5]:
            lines.append(f"    → {src[:150]}")

    if wrapper.evidence:
        lines.append(f"  Evidence:     {len(wrapper.evidence)} pieces")
        for ev in wrapper.evidence:
            lines.append(f"    {format_evidence(ev)}")

    return lines


# ═══════════════════════════════════════════════════════════════
# SCORE BREAKDOWN EXPLANATION
# ═══════════════════════════════════════════════════════════════

def explain_score_breakdown(
    breakdown: ScoreBreakdown,
) -> list[str]:
    """Generate explanation lines for a score breakdown."""
    lines: list[str] = []

    lines.append("Score breakdown:")
    lines.append(f"  Total raw:          {breakdown.total_raw:.4f}")
    lines.append(f"  After category caps:{breakdown.total_after_caps:.4f}")
    lines.append(f"  Negation penalty:   {breakdown.negation_penalty:.4f}")
    lines.append(f"  Cross-cat bonus:    {breakdown.cross_category_bonus:.4f} ({breakdown.num_categories_hit} categories)")
    lines.append(f"  Confidence ceiling: {breakdown.confidence_ceiling:.4f}")
    lines.append(f"  Normalized:         {breakdown.normalized:.4f}")

    if breakdown.contributions:
        lines.append("")
        lines.append("  Contributions:")
        for contrib in breakdown.contributions:
            ev = contrib.evidence
            cap_tag = " [CAPPED]" if contrib.cap_applied else ""
            sign = "-" if contrib.capped_contribution < 0 else "+"
            abs_val = abs(contrib.capped_contribution)
            lines.append(
                f"    {sign}{abs_val:.3f}{cap_tag} "
                f"[{ev.category.value}] {ev.rule_description}"
            )
            if contrib.note:
                lines.append(f"           note: {contrib.note}")

    return lines


# ═══════════════════════════════════════════════════════════════
# EVIDENCE FORMATTING
# ═══════════════════════════════════════════════════════════════

def format_evidence(evidence: PlayerEvidence) -> str:
    """Format a single evidence object as a compact string."""
    neg_tag = " [NEG]" if evidence.negates else ""
    excerpt = evidence.matched_text
    if len(excerpt) > 80:
        excerpt = excerpt[:77] + "..."

    return (
        f"[{evidence.category.value}] "
        f"w={evidence.weight:.2f}{neg_tag} "
        f"— {evidence.rule_description} "
        f'("{excerpt}")'
    )


def format_result_summary(result: PlayerDetectionResult) -> str:
    """One-line summary of a detection result for quick logging."""
    if not result.detected:
        wrapper_tag = ""
        if result.is_wrapper:
            wrapper_tag = f" wrapper={result.wrapper.wrapper_type}"  # type: ignore[union-attr]
        return f"[PlayerDetection] No player detected{wrapper_tag} ({result.elapsed_ms:.1f}ms)"

    best = result.best
    assert best is not None
    tier = confidence_tier(best.confidence)
    version = f" v{best.detected_version}" if best.detected_version else ""
    wrapper_tag = ""
    if result.is_wrapper:
        wrapper_tag = f" wrapper={result.wrapper.wrapper_type}"  # type: ignore[union-attr]

    return (
        f"[PlayerDetection] {best.canonical_name}{version} "
        f"confidence={best.confidence:.3f} ({tier}) "
        f"candidates={result.num_candidates}{wrapper_tag} "
        f"({result.elapsed_ms:.1f}ms)"
    )