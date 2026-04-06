"""
resolution_pipeline/candidate_merger.py

Evidence merging and ranked candidate production.
Core scoring engine that combines signals from all sources
into deterministic, explainable, ranked service candidates.

Gaps closed:
    G3 — candidate/evidence models used for multi-signal merging
    G8 — trace steps recorded throughout scoring

Design:
    - Score is a weighted sum across signal source categories
    - Weights come from ResolverSettings (configurable)
    - Ambiguity is detected by score spread between top candidates
    - Tiebreak policies are explicit and deterministic
    - All scoring is recorded in ScoreBreakdown for traceability
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import (
    AmbiguityLevel,
    CandidateList,
    EvidenceBundle,
    EvidenceItem,
    PlayerDetectionEvidence,
    RankedServiceCandidate,
    ResolutionPath,
    ResolutionTrace,
    ScoreBreakdown,
    SignalSource,
    StaticAnalysisResult,
    TraceStepPhase,
    trace_timer,
)
from .settings import IntegrationSettings, ResolverSettings


# ──────────────────────────────────────────────
# Signal Source → Score Category Mapping
# ──────────────────────────────────────────────

_SOURCE_CATEGORY: Dict[SignalSource, str] = {
    SignalSource.DOMAIN: "domain",
    SignalSource.LABEL: "label",
    SignalSource.PLAYER: "player",
    SignalSource.IFRAME: "iframe",
    SignalSource.SCRIPT: "player",  # Scripts indicating players go into player score
    SignalSource.EMBED: "iframe",   # Embed signals are structurally similar to iframe
    SignalSource.SITE_HINT: "site_hint",
    SignalSource.USER_OVERRIDE: "label",  # User overrides act like very strong labels
    SignalSource.LEGACY_FALLBACK: "label",
    SignalSource.CROSS_SERVER_BOOST: "cross_server_boost",
    SignalSource.CONFIG_PATTERN: "player",
    SignalSource.WRAPPER_DETECTION: "player",
}


def _get_weight_for_category(category: str, weights: ResolverSettings) -> float:
    """Map a score category to its configured weight."""
    return {
        "domain": weights.domain_weight,
        "label": weights.label_weight,
        "player": weights.player_weight,
        "iframe": weights.iframe_weight,
        "site_hint": weights.site_hint_weight,
        "cross_server_boost": 1.0,  # Boost is pre-weighted
    }.get(category, 0.0)


# ──────────────────────────────────────────────
# Evidence Collection
# ──────────────────────────────────────────────

def _collect_all_evidence(
    static_result: Optional[StaticAnalysisResult],
    label_candidates: List[RankedServiceCandidate],
    player_evidence: Optional[PlayerDetectionEvidence],
    site_hints: List[EvidenceItem],
) -> EvidenceBundle:
    """
    Gather evidence from all sources into a single bundle.
    """
    bundle = EvidenceBundle()

    # Static analysis evidence
    if static_result is not None:
        bundle = bundle.merge(static_result.evidence)

    # Label-derived evidence
    for candidate in label_candidates:
        # Each label candidate contributes its evidence
        bundle = bundle.merge(candidate.evidence)
        # Also add a synthetic evidence item for the label resolution itself
        bundle.add(EvidenceItem(
            source=SignalSource.LABEL,
            key="label_candidate",
            value=candidate.service_name,
            weight=candidate.composite_score if candidate.composite_score > 0 else 0.30,
            confidence=min(candidate.composite_score, 1.0) if candidate.composite_score > 0 else 0.5,
            raw=None,
            explanation=f"Label resolver produced candidate '{candidate.service_name}' "
                        f"with score {candidate.composite_score:.3f}",
        ))

    # Player evidence
    if player_evidence is not None and player_evidence.is_detected:
        player_bundle = player_evidence.to_evidence_bundle()
        bundle = bundle.merge(player_bundle)

    # Site hints
    bundle.add_all(site_hints)

    return bundle


def _group_evidence_by_service(bundle: EvidenceBundle) -> Dict[str, EvidenceBundle]:
    """
    Group evidence items by the service they point to.

    Items whose value is a service name get grouped under that service.
    Items whose value is NOT a service name (e.g. player names, domains)
    are grouped under a special '__unattributed__' key.
    """
    groups: Dict[str, EvidenceBundle] = {}

    for item in bundle.items:
        # Determine target service
        target = _resolve_evidence_target(item)
        if target not in groups:
            groups[target] = EvidenceBundle()
        groups[target].add(item)

    return groups


def _resolve_evidence_target(item: EvidenceItem) -> str:
    """
    Determine which service an evidence item points to.

    For DOMAIN, LABEL, IFRAME, EMBED sources: the value is typically the service name.
    For PLAYER, SCRIPT sources: the value is a player name, not a service — goes to __unattributed__.
    For SITE_HINT: depends on the key.
    """
    if item.source in (
        SignalSource.DOMAIN,
        SignalSource.LABEL,
        SignalSource.IFRAME,
        SignalSource.LEGACY_FALLBACK,
        SignalSource.CROSS_SERVER_BOOST,
    ):
        if isinstance(item.value, str) and item.value:
            return item.value

    if item.source == SignalSource.EMBED and item.key == "embed_data_src":
        if isinstance(item.value, str) and item.value:
            return item.value

    if item.source == SignalSource.USER_OVERRIDE:
        if isinstance(item.value, str) and item.value:
            return item.value

    return "__unattributed__"


# ──────────────────────────────────────────────
# Scoring Engine
# ──────────────────────────────────────────────

def _score_candidate(
    service: str,
    evidence: EvidenceBundle,
    unattributed: EvidenceBundle,
    weights: ResolverSettings,
) -> Tuple[float, ScoreBreakdown]:
    """
    Compute the composite score for a single service candidate.

    The score is a weighted sum across signal categories:
        composite = Σ (category_weight × normalized_category_score)

    Each category score is computed from the evidence items
    in that category, combining their effective weights.

    Unattributed evidence (e.g. player/script signals not tied to
    a specific service) contributes a fractional bonus.

    Returns:
        (composite_score, ScoreBreakdown)
    """
    breakdown = ScoreBreakdown()

    # Score per category from attributed evidence
    category_scores: Dict[str, float] = {
        "domain": 0.0,
        "label": 0.0,
        "player": 0.0,
        "iframe": 0.0,
        "site_hint": 0.0,
        "cross_server_boost": 0.0,
    }

    for item in evidence.items:
        category = _SOURCE_CATEGORY.get(item.source, "site_hint")
        score_contribution = item.effective_weight
        category_scores[category] = category_scores.get(category, 0.0) + score_contribution

    # Normalize category scores (cap at 1.0 per category to prevent domination)
    for cat in category_scores:
        category_scores[cat] = min(category_scores[cat], 1.0)

    # Add fractional bonus from unattributed evidence
    # (player/script evidence that supports but doesn't specify a service)
    unattrib_bonus = 0.0
    if not unattributed.is_empty:
        # Distribute a small portion to all candidates
        total_unattrib = min(unattributed.total_effective_weight(), 1.0)
        # The bonus is scaled down — unattributed evidence is indirect
        unattrib_bonus = total_unattrib * 0.15

    # Compute weighted composite
    composite = 0.0
    composite += category_scores["domain"] * weights.domain_weight
    composite += category_scores["label"] * weights.label_weight
    composite += (category_scores["player"] + unattrib_bonus) * weights.player_weight
    composite += category_scores["iframe"] * weights.iframe_weight
    composite += category_scores["site_hint"] * weights.site_hint_weight
    composite += category_scores["cross_server_boost"]  # Pre-weighted

    # Populate breakdown
    breakdown.domain_score = category_scores["domain"] * weights.domain_weight
    breakdown.label_score = category_scores["label"] * weights.label_weight
    breakdown.player_score = (category_scores["player"] + unattrib_bonus) * weights.player_weight
    breakdown.iframe_score = category_scores["iframe"] * weights.iframe_weight
    breakdown.site_hint_score = category_scores["site_hint"] * weights.site_hint_weight
    breakdown.cross_server_boost = category_scores["cross_server_boost"]
    breakdown.penalty = 0.0

    return composite, breakdown


def _apply_tiebreak(
    candidates: List[RankedServiceCandidate],
    policy: str,
) -> List[RankedServiceCandidate]:
    """
    Apply tiebreak policy when multiple candidates have the same score.

    Policies:
        first_seen:          Preserve insertion order (stable sort).
        highest_confidence:  Prefer the candidate with highest max confidence in evidence.
        most_evidence:       Prefer the candidate with the most evidence items.
    """
    if len(candidates) <= 1:
        return candidates

    if policy == "highest_confidence":
        # Secondary sort by max confidence, descending
        candidates.sort(
            key=lambda c: (c.composite_score, c.evidence.max_confidence()),
            reverse=True,
        )
    elif policy == "most_evidence":
        # Secondary sort by evidence count, descending
        candidates.sort(
            key=lambda c: (c.composite_score, c.evidence_count),
            reverse=True,
        )
    else:
        # "first_seen" — stable sort by score only
        candidates.sort(key=lambda c: c.composite_score, reverse=True)

    return candidates


def _assess_ambiguity(
    candidate_list: CandidateList,
    threshold: float,
) -> AmbiguityLevel:
    """
    Assess ambiguity based on score spread between top candidates.
    """
    if candidate_list.count <= 1:
        return AmbiguityLevel.NONE

    spread = candidate_list.score_spread
    return AmbiguityLevel.from_spread(spread, threshold)


# ──────────────────────────────────────────────
# Candidate Ranking
# ──────────────────────────────────────────────

def _rank_candidates(
    scored: List[RankedServiceCandidate],
    settings: ResolverSettings,
) -> CandidateList:
    """
    Sort, tiebreak, filter, and rank a list of scored candidates.
    """
    # Apply tiebreak
    sorted_candidates = _apply_tiebreak(list(scored), settings.tiebreak_policy)

    # Filter below minimum threshold
    filtered = [
        c for c in sorted_candidates
        if c.composite_score >= settings.min_score_threshold
    ]

    # Limit to max candidates
    limited = filtered[: settings.max_candidates]

    # Build CandidateList and assign ranks
    candidate_list = CandidateList(candidates=limited)
    candidate_list.assign_ranks()

    # Assess ambiguity
    candidate_list.ambiguity = _assess_ambiguity(
        candidate_list, settings.ambiguity_threshold
    )

    return candidate_list


# ──────────────────────────────────────────────
# Main Merge Entry Point
# ──────────────────────────────────────────────

def merge_service_evidence(
    static_result: Optional[StaticAnalysisResult],
    label_candidates: List[RankedServiceCandidate],
    player_evidence: Optional[PlayerDetectionEvidence],
    site_hints: List[EvidenceItem],
    settings: IntegrationSettings,
    trace: Optional[ResolutionTrace] = None,
) -> CandidateList:
    """
    Merge evidence from all sources into a ranked CandidateList.

    This is the core scoring engine of the integration pipeline.

    Args:
        static_result:    Output from static analysis (may be None).
        label_candidates: Candidates from label resolution (may be empty).
        player_evidence:  Output from player detection (may be None).
        site_hints:       Additional site-level hints.
        settings:         Pipeline settings with scoring weights.
        trace:            Optional trace to record steps into.

    Returns:
        CandidateList with ranked, scored candidates and ambiguity assessment.
    """
    if trace is None:
        trace = ResolutionTrace()

    # ── Step 1: Collect all evidence ──
    with trace_timer(trace, TraceStepPhase.EVIDENCE_MERGE, "collect_all_evidence") as t:
        all_evidence = _collect_all_evidence(
            static_result, label_candidates, player_evidence, site_hints
        )
        t.result_summary = f"Collected {all_evidence.count} total evidence items"
        t.details = {
            "total_items": all_evidence.count,
            "sources": [s.value for s in all_evidence.sources_present()],
        }

    # ── Step 2: Group by service ──
    with trace_timer(trace, TraceStepPhase.EVIDENCE_MERGE, "group_evidence_by_service") as t:
        grouped = _group_evidence_by_service(all_evidence)
        service_keys = [k for k in grouped if k != "__unattributed__"]
        unattributed = grouped.get("__unattributed__", EvidenceBundle())
        t.result_summary = f"Grouped into {len(service_keys)} services + {unattributed.count} unattributed"
        t.details = {
            "services": service_keys,
            "unattributed_count": unattributed.count,
        }

    if not service_keys:
        # No service-attributed evidence found
        trace.step(
            phase=TraceStepPhase.EVIDENCE_MERGE,
            action="no_candidates",
            result_summary="No service-attributed evidence found — returning empty candidate list",
        )
        return CandidateList(
            candidates=[],
            ambiguity=AmbiguityLevel.NONE,
            score_spread=0.0,
        )

    # ── Step 3: Score each service candidate ──
    scored_candidates: List[RankedServiceCandidate] = []

    with trace_timer(trace, TraceStepPhase.EVIDENCE_MERGE, "score_candidates") as t:
        resolver_settings = settings.resolver

        for service_name in service_keys:
            service_evidence = grouped[service_name]

            composite_score, breakdown = _score_candidate(
                service=service_name,
                evidence=service_evidence,
                unattributed=unattributed,
                weights=resolver_settings,
            )

            # Determine source path
            sources = service_evidence.sources_present()
            has_label = SignalSource.LABEL in sources
            has_domain = SignalSource.DOMAIN in sources
            if has_label and has_domain:
                path = ResolutionPath.V2_FULL
            elif has_label or has_domain:
                path = ResolutionPath.V2_PARTIAL
            else:
                path = ResolutionPath.HYBRID

            candidate = RankedServiceCandidate(
                service_name=service_name,
                composite_score=composite_score,
                evidence=service_evidence,
                score_breakdown=breakdown,
                source_path=path,
            )
            scored_candidates.append(candidate)

        t.result_summary = f"Scored {len(scored_candidates)} candidates"
        t.details = {
            "scores": {
                c.service_name: round(c.composite_score, 4)
                for c in scored_candidates
            },
        }

    # ── Step 4: Rank and assess ambiguity ──
    with trace_timer(trace, TraceStepPhase.EVIDENCE_MERGE, "rank_and_assess") as t:
        candidate_list = _rank_candidates(scored_candidates, resolver_settings)
        t.result_summary = (
            f"Ranked {candidate_list.count} candidates — "
            f"ambiguity={candidate_list.ambiguity.value}, "
            f"spread={candidate_list.score_spread:.4f}"
        )
        t.details = {
            "ranked": [
                {"rank": c.rank, "service": c.service_name, "score": round(c.composite_score, 4)}
                for c in candidate_list.candidates
            ],
            "ambiguity": candidate_list.ambiguity.value,
            "spread": round(candidate_list.score_spread, 4),
        }

    return candidate_list


# ──────────────────────────────────────────────
# Cross-Server Boost (used by multi_server_pipeline)
# ──────────────────────────────────────────────

def apply_cross_server_boost(
    candidate_list: CandidateList,
    service_occurrence_count: Dict[str, int],
    settings: IntegrationSettings,
    trace: Optional[ResolutionTrace] = None,
) -> CandidateList:
    """
    Boost candidates that appear from multiple server entries.

    A service appearing from N servers gets a boost of:
        min(boost_factor * (N - 1), max_boost)

    This encourages agreement across servers.

    Args:
        candidate_list:          Current ranked candidates.
        service_occurrence_count: Map of service_name → number of servers it appeared from.
        settings:                Pipeline settings with boost factor.
        trace:                   Optional trace.

    Returns:
        Updated CandidateList with boosted scores and re-ranking.
    """
    if trace is None:
        trace = ResolutionTrace()

    boost_factor = settings.resolver.cross_server_boost_factor
    max_boost = settings.resolver.max_cross_server_boost

    boosted = False

    with trace_timer(trace, TraceStepPhase.CROSS_SERVER_AGGREGATION, "apply_cross_server_boost") as t:
        for candidate in candidate_list.candidates:
            count = service_occurrence_count.get(candidate.service_name, 1)
            if count > 1:
                boost = min(boost_factor * (count - 1), max_boost)
                candidate.composite_score += boost
                candidate.score_breakdown.cross_server_boost = boost
                candidate.evidence.add(EvidenceItem(
                    source=SignalSource.CROSS_SERVER_BOOST,
                    key="cross_server_agreement",
                    value=candidate.service_name,
                    weight=boost,
                    confidence=0.95,
                    raw=f"appeared_in_{count}_servers",
                    explanation=(
                        f"Service '{candidate.service_name}' appeared in "
                        f"{count} servers — boost of {boost:.3f} applied"
                    ),
                ))
                candidate.notes.append(
                    f"Cross-server boost: +{boost:.3f} (from {count} servers)"
                )
                boosted = True

        # Re-rank if any boost was applied
        if boosted:
            candidate_list.assign_ranks()
            candidate_list.ambiguity = _assess_ambiguity(
                candidate_list, settings.resolver.ambiguity_threshold
            )

        t.result_summary = f"Boosted={boosted}, services boosted: " + ", ".join(
            f"{s}(x{c})" for s, c in service_occurrence_count.items() if c > 1
        )
        t.details = {
            "boosted": boosted,
            "occurrence_counts": service_occurrence_count,
        }

    return candidate_list