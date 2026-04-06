"""
resolution_pipeline/multi_server_pipeline.py

Full orchestration pipeline for multi-server resolution scenarios.
Runs static analysis, label resolution, player detection, evidence
merging, cross-server aggregation, and decision finalization for
all server entries in a RawInputContext.

Gaps closed:
    G3 — multi-server aggregation with candidate/evidence models
    G7 — legacy fallback when all v2 systems are disabled
    G8 — end-to-end trace from input to decision

External dependencies (ADAPTATION POINTs):
    - Phase 2 label resolver (injected via constructor)
    - Phase 3 player detector (injected via constructor)
    - Legacy server switch function (injected via constructor)
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
    RawInputContext,
    ResolutionDecision,
    ResolutionPath,
    ResolutionTrace,
    ScoreBreakdown,
    ServerEntry,
    SignalSource,
    StaticAnalysisResult,
    TraceStepPhase,
    trace_timer,
)
from .settings import IntegrationSettings
from .static_analysis import analyze_static
from .candidate_merger import (
    apply_cross_server_boost,
    merge_service_evidence,
)
from .server_switcher_adapter import (
    LabelResolverProtocol,
    LegacySwitchFn,
    resolve_server_candidates,
    _convert_label_results_to_candidates,
    _wrap_legacy_result,
)


# ──────────────────────────────────────────────
# Per-Server Result Container
# ──────────────────────────────────────────────

class _PerServerResult:
    """
    Intermediate result for a single server entry's analysis.
    Internal to the pipeline — not exported.
    """

    __slots__ = (
        "server_entry",
        "server_index",
        "decision",
        "static_result",
        "player_evidence",
        "label_candidates",
        "candidate_list",
    )

    def __init__(
        self,
        server_entry: ServerEntry,
        server_index: int,
    ) -> None:
        self.server_entry = server_entry
        self.server_index = server_index
        self.decision: Optional[ResolutionDecision] = None
        self.static_result: Optional[StaticAnalysisResult] = None
        self.player_evidence: Optional[PlayerDetectionEvidence] = None
        self.label_candidates: List[RankedServiceCandidate] = []
        self.candidate_list: Optional[CandidateList] = None


# ──────────────────────────────────────────────
# ServiceResolutionPipeline
# ──────────────────────────────────────────────

class ServiceResolutionPipeline:
    """
    Full multi-server resolution pipeline.

    Wired with:
        - settings (IntegrationSettings)
        - label resolver (Phase 2, optional)
        - player detector (Phase 3, optional)
        - legacy switch function (optional)

    Usage:
        pipeline = ServiceResolutionPipeline(settings, resolver, detector, legacy_fn)
        decision = pipeline.run(context)
        decision = pipeline.run_single_server("Fembed", context)
    """

    def __init__(
        self,
        settings: IntegrationSettings,
        label_resolver: Optional[LabelResolverProtocol] = None,
        player_detector: Optional[Callable] = None,
        legacy_switch_fn: Optional[LegacySwitchFn] = None,
    ) -> None:
        self._settings = settings
        self._label_resolver = label_resolver
        self._player_detector = player_detector
        self._legacy_switch_fn = legacy_switch_fn

    # ── Properties ──

    @property
    def settings(self) -> IntegrationSettings:
        return self._settings

    @property
    def has_label_resolver(self) -> bool:
        return self._label_resolver is not None

    @property
    def has_player_detector(self) -> bool:
        return self._player_detector is not None

    @property
    def has_legacy_fallback(self) -> bool:
        return self._legacy_switch_fn is not None

    @property
    def capabilities(self) -> Dict[str, bool]:
        return {
            "label_v2": self._settings.effective_label_v2() and self.has_label_resolver,
            "player_v2": self._settings.effective_player_v2() and self.has_player_detector,
            "legacy_fallback": self._settings.legacy_fallback_enabled and self.has_legacy_fallback,
            "multi_server": True,
            "cross_server_boost": True,
            "debug_trace": self._settings.effective_debug(),
        }

    # ── Single Server Resolution ──

    def run_single_server(
        self,
        label: str,
        context: Optional[RawInputContext] = None,
        trace: Optional[ResolutionTrace] = None,
    ) -> ResolutionDecision:
        """
        Resolve a single server label with optional context.

        Delegates to the server_switcher_adapter which handles
        v2/legacy routing internally.
        """
        if trace is None:
            trace = ResolutionTrace()

        trace.step(
            phase=TraceStepPhase.INPUT_NORMALIZATION,
            action="single_server_start",
            result_summary=f"Resolving single server label: '{label}'",
            details={
                "label": label,
                "has_context": context is not None,
                "capabilities": self.capabilities,
            },
        )

        decision = resolve_server_candidates(
            server_label=label,
            context=context,
            settings=self._settings,
            label_resolver=self._label_resolver,
            player_detector=self._player_detector,
            legacy_switch_fn=self._legacy_switch_fn,
            trace=trace,
        )

        if context:
            decision.request_id = context.request_id

        trace.mark_complete()
        decision.trace = trace

        return decision

    # ── Multi-Server Resolution ──

    def run(
        self,
        context: RawInputContext,
    ) -> ResolutionDecision:
        """
        Run the full multi-server resolution pipeline.

        Steps:
            1. Normalize inputs
            2. For each server entry: resolve individually
            3. Aggregate candidates across servers
            4. Apply cross-server boosting
            5. Finalize decision
            6. Attach trace

        Args:
            context: RawInputContext containing all page/server inputs.

        Returns:
            ResolutionDecision with best candidate, alternatives, and trace.
        """
        trace = ResolutionTrace()

        # ── Step 0: Input normalization ──
        with trace_timer(trace, TraceStepPhase.INPUT_NORMALIZATION, "normalize_inputs") as t:
            server_count = len(context.server_buttons)
            t.result_summary = (
                f"Inputs: {server_count} servers, "
                f"html={'yes' if context.has_html else 'no'}, "
                f"iframes={len(context.iframe_urls)}"
            )
            t.details = {
                "request_id": context.request_id,
                "server_count": server_count,
                "server_labels": context.server_labels,
                "has_html": context.has_html,
                "iframe_count": len(context.iframe_urls),
                "embed_count": len(context.embed_codes),
                "site_domain": context.site_domain,
            }

        # ── Handle: No servers ──
        if not context.has_servers:
            return self._resolve_no_servers(context, trace)

        # ── Handle: Fully legacy mode ──
        if self._settings.is_fully_legacy():
            return self._resolve_all_legacy(context, trace)

        # ── Step 1: Process each server ──
        per_server_results: List[_PerServerResult] = []

        for idx, server_entry in enumerate(context.server_buttons):
            with trace_timer(
                trace,
                TraceStepPhase.LABEL_RESOLUTION,
                f"process_server_{idx}",
            ) as t:
                psr = self._process_single_server(
                    server_entry=server_entry,
                    server_index=idx,
                    context=context,
                    trace=trace,
                )
                per_server_results.append(psr)
                t.result_summary = (
                    f"Server[{idx}] '{server_entry.label}': "
                    f"resolved={psr.decision.is_resolved if psr.decision else False}"
                )
                t.details = {
                    "label": server_entry.label,
                    "resolved_service": (
                        psr.decision.resolved_service
                        if psr.decision else None
                    ),
                }

        # ── Step 2: Aggregate across servers ──
        with trace_timer(
            trace,
            TraceStepPhase.CROSS_SERVER_AGGREGATION,
            "aggregate_cross_server",
        ) as t:
            aggregated = self._aggregate_cross_server(per_server_results, trace)
            t.result_summary = f"Aggregated: {aggregated.count} candidates"

        # ── Step 3: Finalize decision ──
        decision = self._finalize_decision(
            candidate_list=aggregated,
            per_server_results=per_server_results,
            context=context,
            trace=trace,
        )

        trace.mark_complete()
        decision.trace = trace
        decision.request_id = context.request_id

        return decision

    # ──────────────────────────────────────────
    # Internal: Process Single Server
    # ──────────────────────────────────────────

    def _process_single_server(
        self,
        server_entry: ServerEntry,
        server_index: int,
        context: RawInputContext,
        trace: ResolutionTrace,
    ) -> _PerServerResult:
        """
        Process a single server entry through the resolution pipeline.
        """
        psr = _PerServerResult(server_entry=server_entry, server_index=server_index)

        # Build a per-server context if the server has its own URL
        server_context = context
        if server_entry.url:
            # Augment the context with the server's URL in iframe_urls
            # so static analysis can extract its domain
            augmented_iframes = list(context.iframe_urls)
            if server_entry.url not in augmented_iframes:
                augmented_iframes.append(server_entry.url)

            server_context = RawInputContext(
                page_html=context.page_html,
                page_url=context.page_url,
                site_domain=context.site_domain,
                server_buttons=context.server_buttons,
                iframe_urls=augmented_iframes,
                embed_codes=context.embed_codes,
                script_urls=context.script_urls,
                user_hints=context.user_hints,
                extra_metadata=context.extra_metadata,
                request_id=context.request_id,
            )

        decision = resolve_server_candidates(
            server_label=server_entry.label,
            context=server_context,
            settings=self._settings,
            label_resolver=self._label_resolver,
            player_detector=self._player_detector,
            legacy_switch_fn=self._legacy_switch_fn,
            trace=trace,
        )

        psr.decision = decision
        psr.player_evidence = decision.player_evidence
        psr.candidate_list = decision.candidate_list

        return psr

    # ──────────────────────────────────────────
    # Internal: No Servers Path
    # ──────────────────────────────────────────

    def _resolve_no_servers(
        self,
        context: RawInputContext,
        trace: ResolutionTrace,
    ) -> ResolutionDecision:
        """
        Handle the case where no server buttons are present.
        Run static analysis only and attempt resolution from page content.
        """
        trace.step(
            phase=TraceStepPhase.INPUT_NORMALIZATION,
            action="no_servers_detected",
            result_summary="No server buttons — running static analysis only",
        )

        static_result: Optional[StaticAnalysisResult] = None
        player_evidence: Optional[PlayerDetectionEvidence] = None

        if context.has_html or context.has_iframes:
            static_result, player_evidence = analyze_static(
                context=context,
                settings=self._settings,
                trace=trace,
                player_detector=self._player_detector,
            )

        # Merge whatever evidence we have
        candidate_list = merge_service_evidence(
            static_result=static_result,
            label_candidates=[],
            player_evidence=player_evidence,
            site_hints=[],
            settings=self._settings,
            trace=trace,
        )

        decision = self._finalize_decision(
            candidate_list=candidate_list,
            per_server_results=[],
            context=context,
            trace=trace,
        )

        decision.player_evidence = player_evidence
        trace.mark_complete()
        decision.trace = trace
        decision.request_id = context.request_id

        return decision

    # ──────────────────────────────────────────
    # Internal: All-Legacy Path
    # ──────────────────────────────────────────

    def _resolve_all_legacy(
        self,
        context: RawInputContext,
        trace: ResolutionTrace,
    ) -> ResolutionDecision:
        """
        Handle the case where all v2 systems are disabled.
        Use legacy switch for each server and pick the first success.
        """
        trace.step(
            phase=TraceStepPhase.FALLBACK,
            action="all_legacy_mode",
            result_summary="All v2 systems disabled — using legacy path for all servers",
        )

        if self._legacy_switch_fn is None:
            trace.step(
                phase=TraceStepPhase.FALLBACK,
                action="no_legacy_fn",
                result_summary="No legacy switch function available — cannot resolve",
            )
            trace.mark_complete()
            return ResolutionDecision(
                best=None,
                alternatives=[],
                fallback_used=True,
                ambiguity=AmbiguityLevel.NONE,
                path=ResolutionPath.LEGACY_FALLBACK,
                trace=trace,
                request_id=context.request_id,
                explanation="All v2 disabled and no legacy switch function available",
            )

        all_candidates: List[RankedServiceCandidate] = []
        seen_services: set = set()

        for idx, server_entry in enumerate(context.server_buttons):
            with trace_timer(trace, TraceStepPhase.FALLBACK, f"legacy_server_{idx}") as t:
                try:
                    service_name = self._legacy_switch_fn(server_entry.label)
                except Exception as e:
                    t.result_summary = f"Legacy switch error for '{server_entry.label}': {e}"
                    continue

                if service_name and service_name not in seen_services:
                    seen_services.add(service_name)

                    evidence = EvidenceBundle()
                    evidence.add(EvidenceItem(
                        source=SignalSource.LEGACY_FALLBACK,
                        key="legacy_switch",
                        value=service_name,
                        weight=0.50,
                        confidence=0.70,
                        raw=server_entry.label,
                        explanation=(
                            f"Legacy switch: '{server_entry.label}' → '{service_name}'"
                        ),
                    ))

                    candidate = RankedServiceCandidate(
                        service_name=service_name,
                        composite_score=0.70,
                        evidence=evidence,
                        score_breakdown=ScoreBreakdown(label_score=0.70),
                        source_path=ResolutionPath.LEGACY_FALLBACK,
                        server_indices=[idx],
                        notes=[f"Legacy resolution from server[{idx}] '{server_entry.label}'"],
                    )
                    all_candidates.append(candidate)

                t.result_summary = (
                    f"Legacy: '{server_entry.label}' → '{service_name or 'none'}'"
                )

        candidate_list = CandidateList(candidates=all_candidates)
        candidate_list.assign_ranks()

        best = candidate_list.best
        alternatives = candidate_list.candidates[1:] if candidate_list.count > 1 else []

        trace.mark_complete()

        return ResolutionDecision(
            best=best,
            alternatives=alternatives,
            candidate_list=candidate_list,
            fallback_used=True,
            ambiguity=AmbiguityLevel.NONE,
            path=ResolutionPath.LEGACY_FALLBACK,
            trace=trace,
            request_id=context.request_id,
            explanation=(
                f"Legacy resolution: {candidate_list.count} services resolved "
                f"from {len(context.server_buttons)} servers"
            ),
        )

    # ──────────────────────────────────────────
    # Internal: Cross-Server Aggregation
    # ──────────────────────────────────────────

    def _aggregate_cross_server(
        self,
        per_server_results: List[_PerServerResult],
        trace: ResolutionTrace,
    ) -> CandidateList:
        """
        Aggregate candidates from all per-server results into a
        unified, deduplicated, boosted CandidateList.
        """
        # Collect all candidates across servers
        all_candidates: Dict[str, RankedServiceCandidate] = {}
        service_occurrence: Dict[str, int] = {}
        service_server_indices: Dict[str, List[int]] = {}

        for psr in per_server_results:
            if psr.candidate_list is None:
                continue

            for candidate in psr.candidate_list.candidates:
                svc = candidate.service_name
                service_occurrence[svc] = service_occurrence.get(svc, 0) + 1

                if svc not in service_server_indices:
                    service_server_indices[svc] = []
                service_server_indices[svc].append(psr.server_index)

                if svc in all_candidates:
                    # Merge evidence and take the higher score
                    existing = all_candidates[svc]
                    merged_evidence = existing.evidence.merge(candidate.evidence)
                    if candidate.composite_score > existing.composite_score:
                        # Use the better-scoring candidate as the base
                        candidate.evidence = merged_evidence
                        candidate.server_indices = list(service_server_indices[svc])
                        all_candidates[svc] = candidate
                    else:
                        existing.evidence = merged_evidence
                        existing.server_indices = list(service_server_indices[svc])
                else:
                    candidate.server_indices = list(service_server_indices[svc])
                    all_candidates[svc] = candidate

        # Build unified list
        unified = CandidateList(candidates=list(all_candidates.values()))
        unified.assign_ranks()

        # Apply cross-server boost
        if any(count > 1 for count in service_occurrence.values()):
            unified = apply_cross_server_boost(
                candidate_list=unified,
                service_occurrence_count=service_occurrence,
                settings=self._settings,
                trace=trace,
            )

        trace.step(
            phase=TraceStepPhase.CROSS_SERVER_AGGREGATION,
            action="aggregation_complete",
            result_summary=(
                f"Unified {len(all_candidates)} services from "
                f"{len(per_server_results)} servers"
            ),
            details={
                "service_occurrence": service_occurrence,
                "server_indices": service_server_indices,
                "final_count": unified.count,
            },
        )

        return unified

    # ──────────────────────────────────────────
    # Internal: Finalize Decision
    # ──────────────────────────────────────────

    def _finalize_decision(
        self,
        candidate_list: CandidateList,
        per_server_results: List[_PerServerResult],
        context: RawInputContext,
        trace: ResolutionTrace,
    ) -> ResolutionDecision:
        """
        Finalize the resolution decision from the aggregated candidate list.
        """
        with trace_timer(trace, TraceStepPhase.DECISION, "finalize_decision") as t:
            best = candidate_list.best
            alternatives = (
                candidate_list.candidates[1: self._settings.report.max_alternatives + 1]
                if candidate_list.count > 1
                else []
            )

            # Determine resolution path
            if best is None:
                path = ResolutionPath.LEGACY_FALLBACK if self._settings.legacy_fallback_enabled else ResolutionPath.V2_FULL
            else:
                path = best.source_path

            # Collect player evidence from per-server results
            player_evidence = self._best_player_evidence(per_server_results)

            # Build explanation
            explanation = self._build_explanation(
                best=best,
                candidate_list=candidate_list,
                context=context,
                path=path,
            )

            decision = ResolutionDecision(
                best=best,
                alternatives=alternatives,
                candidate_list=candidate_list,
                fallback_used=(path == ResolutionPath.LEGACY_FALLBACK),
                ambiguity=candidate_list.ambiguity,
                path=path,
                trace=trace,
                player_evidence=player_evidence,
                request_id=context.request_id,
                explanation=explanation,
            )

            t.result_summary = explanation
            t.details = {
                "resolved_service": decision.resolved_service,
                "confidence": round(decision.confidence, 4),
                "ambiguity": decision.ambiguity.value,
                "alternative_count": decision.alternative_count,
                "path": decision.path.value,
            }

        return decision

    # ──────────────────────────────────────────
    # Internal: Helpers
    # ──────────────────────────────────────────

    def _best_player_evidence(
        self,
        per_server_results: List[_PerServerResult],
    ) -> Optional[PlayerDetectionEvidence]:
        """
        Pick the highest-confidence player evidence from all server results.
        """
        best_pe: Optional[PlayerDetectionEvidence] = None
        best_conf: float = 0.0

        for psr in per_server_results:
            if psr.player_evidence and psr.player_evidence.is_detected:
                if psr.player_evidence.confidence > best_conf:
                    best_pe = psr.player_evidence
                    best_conf = psr.player_evidence.confidence

        return best_pe

    def _build_explanation(
        self,
        best: Optional[RankedServiceCandidate],
        candidate_list: CandidateList,
        context: RawInputContext,
        path: ResolutionPath,
    ) -> str:
        """Build a human-readable explanation of the decision."""
        parts: List[str] = []

        if best:
            parts.append(
                f"Resolved to '{best.service_name}' "
                f"(score={best.composite_score:.3f}, rank={best.rank})"
            )
        else:
            parts.append("No service could be resolved")

        parts.append(f"path={path.value}")
        parts.append(f"candidates={candidate_list.count}")
        parts.append(f"servers={len(context.server_buttons)}")

        if candidate_list.is_ambiguous:
            parts.append(f"AMBIGUOUS (level={candidate_list.ambiguity.value})")

        if best and best.sources_used:
            parts.append(
                f"sources=[{', '.join(s.value for s in best.sources_used)}]"
            )

        return " | ".join(parts)


# ──────────────────────────────────────────────
# Standalone Trace Builder
# ──────────────────────────────────────────────

def build_resolution_trace(
    decision: ResolutionDecision,
) -> ResolutionTrace:
    """
    Return the trace attached to a decision.

    If the decision has no trace, construct a minimal one.
    This is a convenience function for callers who need
    trace access outside the pipeline.
    """
    if decision.trace and decision.trace.step_count > 0:
        return decision.trace

    # Build a minimal trace from the decision
    trace = ResolutionTrace()
    trace.step(
        phase=TraceStepPhase.DECISION,
        action="trace_reconstructed",
        result_summary=(
            f"Trace reconstructed from decision: "
            f"service={decision.resolved_service}, "
            f"path={decision.path.value}"
        ),
        details={"from_decision": True},
    )
    trace.mark_complete()
    return trace