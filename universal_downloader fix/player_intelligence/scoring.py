# player_intelligence/scoring.py
"""
Weighted scoring engine for player detection candidates.

Takes a candidate's evidence list and profile metadata, applies
per-category weight caps, cross-category bonuses, negation
penalties, and context-based adjustments, then produces a
ScoreBreakdown with a normalized confidence value (0.0–1.0).

Scoring philosophy:
    - Multiple evidence categories provide stronger signal than
      many pieces in the same category.
    - Negating evidence penalizes the total score.
    - A known-player hint from the detection context boosts the
      matching candidate.
    - Confidence is clamped to 1.0 and floors at 0.0.
"""

from __future__ import annotations

import logging
from typing import Sequence

from .models import (
    EvidenceCategory,
    PlayerDetectionCandidate,
    PlayerDetectionContext,
    PlayerEvidence,
    PlayerFamily,
    PlayerProfile,
    ScoreBreakdown,
    ScoreContribution,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# CATEGORY WEIGHT CAPS
# ═══════════════════════════════════════════════════════════════

# Maximum total contribution from a single evidence category.
# Prevents a profile with 10 script-src markers from dominating
# by matching several similar <script> tags on one page.

CATEGORY_WEIGHT_CAPS: dict[EvidenceCategory, float] = {
    EvidenceCategory.SCRIPT_SRC: 0.55,
    EvidenceCategory.DOM_CSS: 0.35,
    EvidenceCategory.INLINE_JS: 0.45,
    EvidenceCategory.GLOBAL_VAR: 0.30,
    EvidenceCategory.DATA_ATTR: 0.25,
    EvidenceCategory.CONFIG_SHAPE: 0.30,
    EvidenceCategory.VERSION: 0.15,
    EvidenceCategory.WRAPPER: 0.20,
    EvidenceCategory.URL_HINT: 0.20,
    EvidenceCategory.META: 0.15,
}


# ═══════════════════════════════════════════════════════════════
# CROSS-CATEGORY BONUS TABLE
# ═══════════════════════════════════════════════════════════════

# When evidence spans multiple categories, a bonus is applied.
# This rewards corroboration: a script-src match + an inline JS
# setup call is much more convincing than either alone.

_CROSS_CATEGORY_BONUS_TABLE: dict[int, float] = {
    # number of distinct positive categories → bonus
    1: 0.00,
    2: 0.05,
    3: 0.10,
    4: 0.15,
    5: 0.18,
    6: 0.20,
    7: 0.22,
}

_MAX_CROSS_CATEGORY_BONUS: float = 0.25


def _cross_category_bonus(num_categories: int) -> float:
    """Look up the cross-category bonus for a given count."""
    if num_categories <= 1:
        return 0.0
    return _CROSS_CATEGORY_BONUS_TABLE.get(
        num_categories,
        _MAX_CROSS_CATEGORY_BONUS,
    )


# ═══════════════════════════════════════════════════════════════
# NEGATION PENALTY
# ═══════════════════════════════════════════════════════════════

_NEGATION_PENALTY_MULTIPLIER: float = 1.5
"""
Negating evidence is penalized at 1.5× its weight.
A negating marker with weight 0.20 removes 0.30 from the score.
"""


# ═══════════════════════════════════════════════════════════════
# CONTEXT-BASED ADJUSTMENTS
# ═══════════════════════════════════════════════════════════════

_KNOWN_PLAYER_HINT_BONUS: float = 0.10
"""
Bonus applied when a candidate's family matches the detection
context's known_player_hint. This allows Phase 2 service resolution
to influence player scoring.
"""

_SERVICE_HINT_BONUS: float = 0.05
"""
Smaller bonus applied when a candidate's family name appears
in the detection context's service_hints list.
"""


# ═══════════════════════════════════════════════════════════════
# CONFIDENCE CEILING
# ═══════════════════════════════════════════════════════════════

def compute_confidence_ceiling(profile: PlayerProfile) -> float:
    """
    Determine the confidence ceiling for a profile.

    The ceiling is the denominator used when normalizing raw score
    into a 0.0–1.0 confidence. A lower ceiling makes it easier
    to reach high confidence with fewer evidence pieces.

    Uses the profile's own `confidence_ceiling` attribute, which
    is tuned per player family in profiles.py.
    """
    return max(profile.confidence_ceiling, 0.10)


def normalize_confidence(
    raw_score: float,
    ceiling: float,
) -> float:
    """
    Normalize a raw evidence score into a 0.0–1.0 confidence.

    Formula: confidence = min(1.0, max(0.0, raw_score / ceiling))
    """
    if ceiling <= 0.0:
        return 0.0
    return min(1.0, max(0.0, raw_score / ceiling))


# ═══════════════════════════════════════════════════════════════
# MAIN SCORING FUNCTION
# ═══════════════════════════════════════════════════════════════

def score_candidate(
    candidate: PlayerDetectionCandidate,
    profile: PlayerProfile,
    context: PlayerDetectionContext | None = None,
) -> ScoreBreakdown:
    """
    Score a player detection candidate and produce a full breakdown.

    Pipeline:
    1. Accumulate per-evidence contributions.
    2. Apply per-category weight caps.
    3. Subtract negation penalties.
    4. Add cross-category bonus.
    5. Apply context-based adjustments.
    6. Normalize against the profile's confidence ceiling.
    7. Populate ScoreBreakdown and update candidate fields.

    Parameters
    ----------
    candidate : PlayerDetectionCandidate
        The candidate to score. Will be mutated in-place
        (raw_score, confidence, score_breakdown updated).
    profile : PlayerProfile
        The profile used for ceiling and tuning parameters.
    context : PlayerDetectionContext | None
        Optional detection context for hint-based adjustments.

    Returns
    -------
    ScoreBreakdown
        Full breakdown of the scoring computation.
    """
    contributions: list[ScoreContribution] = []
    category_totals: dict[EvidenceCategory, float] = {}
    negation_total: float = 0.0

    # ── Step 1 & 2: Accumulate contributions with category caps ──

    for evidence in candidate.evidence:
        if evidence.negates:
            # Negating evidence handled separately in step 3
            penalty = evidence.weight * _NEGATION_PENALTY_MULTIPLIER
            negation_total += penalty
            contributions.append(ScoreContribution(
                evidence=evidence,
                raw_contribution=-evidence.weight,
                capped_contribution=-penalty,
                cap_applied=False,
                note=f"Negation penalty: {evidence.weight:.3f} × {_NEGATION_PENALTY_MULTIPLIER} = {penalty:.3f}",
            ))
            continue

        # Positive evidence
        category = evidence.category
        cat_cap = CATEGORY_WEIGHT_CAPS.get(category, 0.50)
        current_cat_total = category_totals.get(category, 0.0)
        remaining_in_cap = max(0.0, cat_cap - current_cat_total)

        raw_contribution = evidence.weight
        capped_contribution = min(raw_contribution, remaining_in_cap)
        was_capped = capped_contribution < raw_contribution

        category_totals[category] = current_cat_total + capped_contribution

        note = ""
        if was_capped:
            if capped_contribution == 0.0:
                note = f"Category {category.value} fully capped at {cat_cap:.2f}"
            else:
                note = (
                    f"Partially capped: {raw_contribution:.3f} → "
                    f"{capped_contribution:.3f} (cap {cat_cap:.2f})"
                )

        contributions.append(ScoreContribution(
            evidence=evidence,
            raw_contribution=raw_contribution,
            capped_contribution=capped_contribution,
            cap_applied=was_capped,
            note=note,
        ))

    # ── Step 2 totals ──

    total_raw = sum(
        c.raw_contribution for c in contributions if c.raw_contribution > 0
    )
    total_after_caps = sum(
        c.capped_contribution for c in contributions if c.capped_contribution > 0
    )

    # ── Step 3: Subtract negation penalties ──

    score_after_negations = max(0.0, total_after_caps - negation_total)

    # ── Step 4: Cross-category bonus ──

    positive_categories = {
        cat for cat, total in category_totals.items() if total > 0.0
    }
    num_categories = len(positive_categories)
    cross_bonus = _cross_category_bonus(num_categories)

    score_after_bonus = score_after_negations + cross_bonus

    # ── Step 5: Context-based adjustments ──

    context_adjustment = _compute_context_adjustment(
        candidate.family, context,
    )
    score_final = score_after_bonus + context_adjustment

    # ── Step 6: Normalize ──

    ceiling = compute_confidence_ceiling(profile)
    confidence = normalize_confidence(score_final, ceiling)

    # ── Step 7: Build breakdown ──

    # Compute theoretical maximum
    max_possible = sum(CATEGORY_WEIGHT_CAPS.values()) + _MAX_CROSS_CATEGORY_BONUS

    breakdown = ScoreBreakdown(
        contributions=contributions,
        total_raw=total_raw,
        total_after_caps=total_after_caps,
        negation_penalty=negation_total,
        cross_category_bonus=cross_bonus,
        max_possible=max_possible,
        confidence_ceiling=ceiling,
        normalized=confidence,
    )

    # ── Update candidate in-place ──

    candidate.raw_score = score_final
    candidate.confidence = confidence
    candidate.score_breakdown = breakdown

    return breakdown


def _compute_context_adjustment(
    family: PlayerFamily,
    context: PlayerDetectionContext | None,
) -> float:
    """
    Compute bonus/penalty from detection context hints.

    Returns a float adjustment to add to the raw score.
    """
    if context is None:
        return 0.0

    adjustment = 0.0

    # Known-player hint match
    if context.has_player_hint and context.known_player_hint == family:
        adjustment += _KNOWN_PLAYER_HINT_BONUS
        logger.debug(
            "Context bonus: known_player_hint=%s matches family=%s → +%.3f",
            context.known_player_hint, family, _KNOWN_PLAYER_HINT_BONUS,
        )

    # Service hint match (check if family value or name appears)
    if context.has_service_hints:
        family_tokens = {family.value}
        for hint in context.service_hints:
            hint_lower = hint.lower().replace('-', '_').replace(' ', '_')
            if hint_lower in family_tokens:
                adjustment += _SERVICE_HINT_BONUS
                logger.debug(
                    "Context bonus: service_hint=%r matches family=%s → +%.3f",
                    hint, family, _SERVICE_HINT_BONUS,
                )
                break  # Only one service-hint bonus per candidate

    return adjustment


# ═══════════════════════════════════════════════════════════════
# BATCH SCORING & RANKING
# ═══════════════════════════════════════════════════════════════

def score_and_rank_candidates(
    candidates: list[PlayerDetectionCandidate],
    profiles: dict[PlayerFamily, PlayerProfile],
    context: PlayerDetectionContext | None = None,
    min_confidence: float = 0.0,
) -> list[PlayerDetectionCandidate]:
    """
    Score all candidates and return them sorted by confidence descending.

    Candidates below `min_confidence` are filtered out.

    Parameters
    ----------
    candidates : list[PlayerDetectionCandidate]
        Candidates to score (mutated in-place with scores).
    profiles : dict[PlayerFamily, PlayerProfile]
        Profile lookup by family.
    context : PlayerDetectionContext | None
        Optional context for hint-based adjustments.
    min_confidence : float
        Minimum confidence threshold for inclusion.

    Returns
    -------
    list[PlayerDetectionCandidate]
        Scored and ranked candidates (highest confidence first).
    """
    for candidate in candidates:
        profile = profiles.get(candidate.family)
        if profile is None:
            logger.warning(
                "No profile found for family %s during scoring",
                candidate.family,
            )
            continue
        score_candidate(candidate, profile, context)

    # Filter by threshold
    qualified = [
        c for c in candidates
        if c.confidence >= min_confidence
    ]

    # Sort: confidence descending, then by evidence count descending
    # as tiebreaker
    qualified.sort(
        key=lambda c: (c.confidence, len(c.positive_evidence)),
        reverse=True,
    )

    return qualified


# ═══════════════════════════════════════════════════════════════
# SCORE COMPARISON UTILITIES
# ═══════════════════════════════════════════════════════════════

def is_dominant_candidate(
    best: PlayerDetectionCandidate,
    second: PlayerDetectionCandidate | None,
    dominance_margin: float = 0.15,
) -> bool:
    """
    Check if the best candidate dominates the second by a margin.

    A dominant candidate is one whose confidence exceeds the
    second-best by at least `dominance_margin`. This is useful
    for deciding whether the detection is unambiguous.
    """
    if second is None:
        return best.confidence > 0.0
    return (best.confidence - second.confidence) >= dominance_margin


def confidence_tier(confidence: float) -> str:
    """
    Classify a confidence value into a human-readable tier.

    Tiers:
        'very_high'  — 0.85+
        'high'       — 0.65–0.84
        'medium'     — 0.40–0.64
        'low'        — 0.20–0.39
        'very_low'   — 0.05–0.19
        'negligible' — <0.05
    """
    if confidence >= 0.85:
        return "very_high"
    if confidence >= 0.65:
        return "high"
    if confidence >= 0.40:
        return "medium"
    if confidence >= 0.20:
        return "low"
    if confidence >= 0.05:
        return "very_low"
    return "negligible"