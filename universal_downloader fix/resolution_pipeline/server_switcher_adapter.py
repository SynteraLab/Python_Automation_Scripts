"""
resolution_pipeline/server_switcher_adapter.py

Bridges legacy server-switching logic with the v2 label resolution
and candidate ranking pipeline.

Gaps closed:
    G1 — server switching wired to label mapping v2
    G7 — backward-compatible fallback behavior preserved

External dependencies (ADAPTATION POINTs):
    - Phase 2 label resolver interface
    - Legacy server switch function
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from .models import (
    AmbiguityLevel,
    CandidateList,
    EvidenceBundle,
    EvidenceItem,
    PlayerDetectionEvidence,
    RankedServiceCandidate,
    RawInputContext,
    ResolutionDecision,
    ResolutionPath,
    ResolutionTrace,
    ScoreBreakdown,
    SignalSource,
    StaticAnalysisResult,
    TraceStepPhase,
    trace_timer,
)
from .settings import IntegrationSettings
from .candidate_merger import merge_service_evidence
from .static_analysis import analyze_static


# ──────────────────────────────────────────────
# Phase 2 Label Resolver Protocol
# ──────────────────────────────────────────────

@runtime_checkable
class LabelResolverProtocol(Protocol):
    """
    ADAPTATION POINT: Protocol that the Phase 2 label resolver must satisfy.

    The real repo's label resolver should be adapted to match this interface,
    or a thin wrapper should be provided during bootstrap.
    """

    def resolve(
        self,
        label: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Resolve a server label into candidate services.

        Args:
            label:   The raw server label string.
            context: Optional context dict (site domain, page url, etc.)

        Returns:
            List of dicts, each with at minimum:
                - 'service_name': str
                - 'confidence': float (0.0–1.0)
                - 'explanation': str (optional)
                - 'aliases_used': List[str] (optional)
        """
        ...


# ──────────────────────────────────────────────
# Legacy Switch Function Type
# ──────────────────────────────────────────────

# ADAPTATION POINT: The legacy switch function signature.
# Expected: Callable[[str], Optional[str]]
# Takes a label, returns a service name or None.
LegacySwitchFn = Callable[[str], Optional[str]]


# ──────────────────────────────────────────────
# Label Resolution → Candidate Conversion
# ──────────────────────────────────────────────

def _convert_label_results_to_candidates(
    label_results: List[Dict[str, Any]],
    original_label: str,
) -> List[RankedServiceCandidate]:
    """
    Convert Phase 2 label resolver output into RankedServiceCandidate objects.
    """
    candidates: List[RankedServiceCandidate] = []

    for i, result in enumerate(label_results):
        service_name = result.get("service_name", "")
        if not service_name:
            continue

        confidence = float(result.get("confidence", 0.5))
        explanation = result.get("explanation", f"Label '{original_label}' resolved to '{service_name}'")
        aliases = result.get("aliases_used", [])

        evidence = EvidenceBundle()
        evidence.add(EvidenceItem(
            source=SignalSource.LABEL,
            key="label_resolution",
            value=service_name,
            weight=0.30,
            confidence=confidence,
            raw=original_label,
            explanation=explanation,
        ))

        if aliases:
            for alias in aliases:
                evidence.add(EvidenceItem(
                    source=SignalSource.LABEL,
                    key="label_alias",
                    value=service_name,
                    weight=0.10,
                    confidence=confidence * 0.9,
                    raw=alias,
                    explanation=f"Alias '{alias}' contributed to resolving '{original_label}'",
                ))

        candidate = RankedServiceCandidate(
            service_name=service_name,
            composite_score=confidence,
            evidence=evidence,
            score_breakdown=ScoreBreakdown(label_score=confidence),
            source_path=ResolutionPath.V2_FULL,
        )
        candidates.append(candidate)

    return candidates


# ──────────────────────────────────────────────
# Wrap Legacy Result
# ──────────────────────────────────────────────

def _wrap_legacy_result(
    service_name: Optional[str],
    original_label: str,
) -> ResolutionDecision:
    """
    Wrap a simple legacy service name string into a full ResolutionDecision.
    """
    if service_name is None:
        return ResolutionDecision(
            best=None,
            alternatives=[],
            fallback_used=True,
            ambiguity=AmbiguityLevel.NONE,
            path=ResolutionPath.LEGACY_FALLBACK,
            trace=ResolutionTrace(),
            explanation=f"Legacy switch returned no result for label '{original_label}'",
        )

    evidence = EvidenceBundle()
    evidence.add(EvidenceItem(
        source=SignalSource.LEGACY_FALLBACK,
        key="legacy_switch",
        value=service_name,
        weight=0.50,
        confidence=0.70,
        raw=original_label,
        explanation=f"Legacy switch resolved '{original_label}' → '{service_name}'",
    ))

    best = RankedServiceCandidate(
        service_name=service_name,
        composite_score=0.70,
        rank=1,
        evidence=evidence,
        score_breakdown=ScoreBreakdown(label_score=0.70),
        source_path=ResolutionPath.LEGACY_FALLBACK,
        notes=["Resolved via legacy fallback path"],
    )

    return ResolutionDecision(
        best=best,
        alternatives=[],
        candidate_list=CandidateList(
            candidates=[best],
            ambiguity=AmbiguityLevel.NONE,
            score_spread=best.composite_score,
        ),
        fallback_used=True,
        ambiguity=AmbiguityLevel.NONE,
        path=ResolutionPath.LEGACY_FALLBACK,
        trace=ResolutionTrace(),
        explanation=f"Legacy switch: '{original_label}' → '{service_name}'",
    )


# ──────────────────────────────────────────────
# V2 Resolution Path
# ──────────────────────────────────────────────

def _resolve_via_v2(
    label: str,
    context: Optional[RawInputContext],
    settings: IntegrationSettings,
    label_resolver: Optional[LabelResolverProtocol],
    player_detector: Optional[Callable] = None,
    trace: Optional[ResolutionTrace] = None,
) -> ResolutionDecision:
    """
    Full v2 resolution path for a single server label.

    Steps:
        1. Label resolution via Phase 2
        2. Optional static analysis if context is available
        3. Evidence merge into ranked candidates
        4. Decision
    """
    if trace is None:
        trace = ResolutionTrace()

    label_candidates: List[RankedServiceCandidate] = []
    static_result: Optional[StaticAnalysisResult] = None
    player_evidence: Optional[PlayerDetectionEvidence] = None
    site_hints: List[EvidenceItem] = []

    # ── Step 1: Label resolution ──
    with trace_timer(trace, TraceStepPhase.LABEL_RESOLUTION, "resolve_label_v2") as t:
        if label_resolver is not None:
            try:
                resolver_context: Optional[Dict[str, Any]] = None
                if context:
                    resolver_context = {
                        "site_domain": context.site_domain,
                        "page_url": context.page_url,
                    }

                # ADAPTATION POINT: Phase 2 label resolver call
                raw_results = label_resolver.resolve(label, resolver_context)
                label_candidates = _convert_label_results_to_candidates(raw_results, label)

                t.result_summary = f"Label resolver returned {len(label_candidates)} candidates"
                t.details = {
                    "candidates": [c.service_name for c in label_candidates],
                    "raw_result_count": len(raw_results),
                }

            except Exception as e:
                t.result_summary = f"Label resolver error: {e}"
                t.details = {"error": str(e)}
        else:
            t.result_summary = "No label resolver available"

    # ── Step 2: Static analysis (if context is available) ──
    if context and context.has_html:
        with trace_timer(trace, TraceStepPhase.STATIC_ANALYSIS, "static_from_switcher") as t:
            static_result, player_evidence = analyze_static(
                context=context,
                settings=settings,
                trace=trace,
                player_detector=player_detector,
            )
            t.result_summary = f"Static analysis: {static_result.total_signals} signals"

    # ── Step 3: Merge evidence ──
    with trace_timer(trace, TraceStepPhase.EVIDENCE_MERGE, "merge_from_switcher") as t:
        candidate_list = merge_service_evidence(
            static_result=static_result,
            label_candidates=label_candidates,
            player_evidence=player_evidence,
            site_hints=site_hints,
            settings=settings,
            trace=trace,
        )
        t.result_summary = f"Merged into {candidate_list.count} candidates"

    # ── Step 4: Build decision ──
    best = candidate_list.best
    alternatives = candidate_list.candidates[1:] if candidate_list.count > 1 else []

    # Apply ambiguous label policy
    if candidate_list.is_ambiguous and settings.label.ambiguous_policy == "reject":
        trace.step(
            phase=TraceStepPhase.DECISION,
            action="ambiguous_reject",
            result_summary=f"Ambiguity={candidate_list.ambiguity.value} — policy='reject' — no result",
        )
        return ResolutionDecision(
            best=None,
            alternatives=list(candidate_list.candidates),
            candidate_list=candidate_list,
            fallback_used=False,
            ambiguity=candidate_list.ambiguity,
            path=ResolutionPath.V2_FULL,
            trace=trace,
            explanation=(
                f"Label '{label}' is ambiguous (level={candidate_list.ambiguity.value}) "
                f"and policy is 'reject'"
            ),
        )

    # Check confidence threshold
    if best is not None and best.composite_score < settings.label.confidence_threshold:
        trace.step(
            phase=TraceStepPhase.DECISION,
            action="below_confidence_threshold",
            result_summary=(
                f"Best candidate '{best.service_name}' "
                f"score={best.composite_score:.3f} < "
                f"threshold={settings.label.confidence_threshold}"
            ),
        )
        # Don't reject — record a note but still return it
        best.notes.append(
            f"Score {best.composite_score:.3f} is below confidence "
            f"threshold {settings.label.confidence_threshold}"
        )

    decision = ResolutionDecision(
        best=best,
        alternatives=alternatives,
        candidate_list=candidate_list,
        fallback_used=False,
        ambiguity=candidate_list.ambiguity,
        path=ResolutionPath.V2_FULL,
        trace=trace,
        player_evidence=player_evidence,
        explanation=(
            f"V2 resolution for label '{label}': "
            f"best='{best.service_name if best else 'none'}', "
            f"score={best.composite_score:.3f if best else 0.0}, "
            f"alternatives={len(alternatives)}"
        ),
    )

    trace.step(
        phase=TraceStepPhase.DECISION,
        action="v2_decision_complete",
        result_summary=decision.explanation,
        details={"decision": decision.to_dict()},
    )

    return decision


# ──────────────────────────────────────────────
# Legacy Resolution Path
# ──────────────────────────────────────────────

def _resolve_via_legacy(
    label: str,
    legacy_fn: Optional[LegacySwitchFn],
    trace: Optional[ResolutionTrace] = None,
) -> ResolutionDecision:
    """
    Legacy resolution path — simple label → service_name lookup.
    """
    if trace is None:
        trace = ResolutionTrace()

    with trace_timer(trace, TraceStepPhase.FALLBACK, "legacy_switch") as t:
        service_name: Optional[str] = None

        if legacy_fn is not None:
            try:
                # ADAPTATION POINT: legacy server switch function
                service_name = legacy_fn(label)
                t.result_summary = f"Legacy switch: '{label}' → '{service_name}'"
            except Exception as e:
                t.result_summary = f"Legacy switch error: {e}"
                t.details = {"error": str(e)}
        else:
            t.result_summary = "No legacy switch function available"

    decision = _wrap_legacy_result(service_name, label)
    decision.trace = trace
    return decision


# ──────────────────────────────────────────────
# Main Public API
# ──────────────────────────────────────────────

def resolve_server_candidates(
    server_label: str,
    context: Optional[RawInputContext],
    settings: IntegrationSettings,
    label_resolver: Optional[LabelResolverProtocol] = None,
    player_detector: Optional[Callable] = None,
    legacy_switch_fn: Optional[LegacySwitchFn] = None,
    trace: Optional[ResolutionTrace] = None,
) -> ResolutionDecision:
    """
    Resolve a server label into a ResolutionDecision.

    Routes to v2 or legacy path based on settings.
    Both paths produce the same output type — callers don't need
    to know which path ran.

    Args:
        server_label:     The raw server label string.
        context:          Optional input context for richer analysis.
        settings:         Pipeline settings.
        label_resolver:   Phase 2 label resolver. ADAPTATION POINT.
        player_detector:  Phase 3 player detector callable. ADAPTATION POINT.
        legacy_switch_fn: Legacy switch function. ADAPTATION POINT.
        trace:            Optional trace to record steps into.

    Returns:
        ResolutionDecision containing best candidate, alternatives,
        trace, and explanation.
    """
    if trace is None:
        trace = ResolutionTrace()

    # Route decision
    use_v2 = settings.effective_label_v2() and label_resolver is not None
    has_legacy = legacy_switch_fn is not None

    trace.step(
        phase=TraceStepPhase.DECISION,
        action="route_decision",
        result_summary=f"use_v2={use_v2}, has_legacy={has_legacy}",
        details={
            "label": server_label,
            "v2_enabled": settings.effective_label_v2(),
            "resolver_available": label_resolver is not None,
            "legacy_available": has_legacy,
            "fallback_enabled": settings.legacy_fallback_enabled,
        },
    )

    if use_v2:
        decision = _resolve_via_v2(
            label=server_label,
            context=context,
            settings=settings,
            label_resolver=label_resolver,
            player_detector=player_detector,
            trace=trace,
        )

        # If v2 produced no result and legacy fallback is enabled, try legacy
        if not decision.is_resolved and settings.legacy_fallback_enabled and has_legacy:
            trace.step(
                phase=TraceStepPhase.FALLBACK,
                action="v2_to_legacy_fallback",
                result_summary="V2 produced no result — falling back to legacy",
            )
            legacy_decision = _resolve_via_legacy(server_label, legacy_switch_fn, trace)
            if legacy_decision.is_resolved:
                legacy_decision.path = ResolutionPath.HYBRID
                legacy_decision.explanation = (
                    f"V2 produced no result for '{server_label}' — "
                    f"legacy fallback resolved to '{legacy_decision.resolved_service}'"
                )
                return legacy_decision

        return decision

    elif has_legacy:
        return _resolve_via_legacy(server_label, legacy_switch_fn, trace)

    else:
        # Neither v2 nor legacy available
        trace.step(
            phase=TraceStepPhase.FALLBACK,
            action="no_resolver_available",
            result_summary="No label resolver and no legacy switch function available",
        )
        return ResolutionDecision(
            best=None,
            alternatives=[],
            fallback_used=False,
            ambiguity=AmbiguityLevel.NONE,
            path=ResolutionPath.LEGACY_FALLBACK,
            trace=trace,
            explanation=f"No resolver available for label '{server_label}'",
        )


def resolve_server_legacy(
    server_label: str,
    legacy_switch_fn: Optional[LegacySwitchFn] = None,
) -> Optional[str]:
    """
    Backward-compatible thin wrapper.

    Takes a label, returns a service name string or None.
    Matches the signature of the original legacy server switch function.

    ADAPTATION POINT: The real repo should call this as a drop-in replacement
    for the old server switch function during migration.
    """
    if legacy_switch_fn is None:
        return None

    try:
        return legacy_switch_fn(server_label)
    except Exception:
        return None