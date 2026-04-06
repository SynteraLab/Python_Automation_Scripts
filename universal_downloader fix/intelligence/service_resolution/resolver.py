"""
intelligence.service_resolution.resolver
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The main resolution orchestrator.

``LabelResolver`` implements the full fallback chain, builds candidates,
invokes scoring rules, ranks results, and produces a complete
``LabelResolutionResult`` with explanation trace.

Resolution stages (in order)
-----------------------------
1. **EXACT_ALIAS** — raw label matches an alias verbatim (case-sensitive).
2. **NORMALIZED_ALIAS** — normalized label matches the alias index.
3. **SITE_OVERRIDE** — site-specific override injects/boosts a candidate.
4. **DOMAIN_HINT** — iframe/page URL domain matches a service's known domains.
5. **PLAYER_HINT** — player framework hint matches service metadata.
6. **CONTEXT_HEURISTIC** — button text and extra hints are evaluated.
7. **UNRESOLVED** — no stage produced a viable candidate.

Stages 1–6 can each add new candidates and/or inject evidence into
existing candidates.  After all stages, the scoring engine runs over
all candidates, ranks them, and derives the confidence tier.
"""

from __future__ import annotations

import threading
from typing import Any, Optional, Sequence

from .canonical_services import ServiceRegistry, get_default_registry
from .models import (
    CanonicalService,
    ConfidenceTier,
    LabelAlias,
    LabelResolutionCandidate,
    LabelResolutionContext,
    LabelResolutionResult,
    ResolutionEvidence,
    ResolutionExplanation,
    ResolutionStage,
    ScoreBreakdown,
    SiteOverrideEntry,
)
from .normalization import normalize_label as _normalize_label, quick_normalize
from .scoring import (
    DEFAULT_SCORING_RULES,
    compute_confidence_from_candidates,
    score_all_candidates,
)
from .site_overrides import SiteOverrideManager, get_default_override_manager


# ═══════════════════════════════════════════════════════════════════════════════
# LabelResolver
# ═══════════════════════════════════════════════════════════════════════════════


class LabelResolver:
    """
    The primary resolution engine.

    Holds references to the service registry, site override manager, and
    scoring rules.  All resolution methods are thread-safe (the resolver
    is stateless per-call; shared state is read-only after initialization).

    Usage
    -----
    ::

        resolver = LabelResolver()  # uses defaults
        result = resolver.resolve("ST", LabelResolutionContext.from_site("aniworld.to"))
        print(result.winner_id)        # 'streamtape'
        print(result.confidence)       # ConfidenceTier.HIGH
    """

    __slots__ = ("_registry", "_overrides", "_rules")

    def __init__(
        self,
        *,
        registry: Optional[ServiceRegistry] = None,
        override_manager: Optional[SiteOverrideManager] = None,
        scoring_rules: Optional[Sequence[Any]] = None,
    ) -> None:
        """
        Parameters
        ----------
        registry:
            Service registry.  Defaults to the module-level singleton.
        override_manager:
            Site override manager.  Defaults to the module-level singleton.
        scoring_rules:
            Scoring rules to apply.  Defaults to ``DEFAULT_SCORING_RULES``.

        ADAPTATION POINT: The host project can inject custom instances
        to extend or replace the built-in data and rules.
        """
        self._registry = registry or get_default_registry()
        self._overrides = override_manager or get_default_override_manager()
        self._rules = scoring_rules or DEFAULT_SCORING_RULES

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def registry(self) -> ServiceRegistry:
        return self._registry

    @property
    def override_manager(self) -> SiteOverrideManager:
        return self._overrides

    # ── Public API ──────────────────────────────────────────────────────────

    def resolve(
        self,
        label: str,
        context: Optional[LabelResolutionContext] = None,
    ) -> LabelResolutionResult:
        """
        Resolve a label to a canonical service.

        This is the primary entry point.  It runs the full fallback chain,
        scores all candidates, and returns a complete result with
        explanation.

        Parameters
        ----------
        label:
            The raw label string (e.g., ``'ST'``, ``'VOE'``, ``'DD'``).
        context:
            Optional resolution context.  If ``None``, an empty context
            is used.

        Returns
        -------
        LabelResolutionResult
            Complete result including winner, confidence, candidates,
            and explanation.
        """
        if context is None:
            context = LabelResolutionContext.empty()

        # Initialize explanation trace
        explanation = ResolutionExplanation(
            input_label=label,
        )

        # Step 0: Normalize
        norm_result = _normalize_label(label)
        normalized = norm_result.normalized
        explanation.normalized_label = normalized

        # Step 1–2: Gather candidates from alias lookup
        candidates = self._gather_alias_candidates(
            label, normalized, explanation
        )

        # Step 3: Apply site overrides
        site_override = self._lookup_site_override(
            normalized, context, explanation
        )
        self._inject_override_candidate(
            site_override, candidates, normalized, explanation
        )

        # Step 4: Apply domain hints
        self._apply_domain_hints(context, candidates, explanation)

        # Step 5: Player hint (handled by scoring rules, but record stage)
        if context.player_hint:
            explanation.record_stage(ResolutionStage.PLAYER_HINT)

        # Step 6: Context heuristic (handled by scoring rules, but record stage)
        if context.button_text or context.extra_hints:
            explanation.record_stage(ResolutionStage.CONTEXT_HEURISTIC)

        # Scoring pass: apply all rules to all candidates
        if candidates:
            score_all_candidates(
                candidates,
                context,
                site_override=site_override,
                normalized_label=normalized,
                rules=self._rules,
            )

            # Collect all evidence from candidates into explanation
            for candidate in candidates:
                explanation.record_evidence_batch(
                    candidate.score_breakdown.adjustments
                )

        # Build final result
        result = self._build_result(
            label, normalized, candidates, explanation
        )

        return result

    def resolve_candidates(
        self,
        label: str,
        context: Optional[LabelResolutionContext] = None,
    ) -> list[LabelResolutionCandidate]:
        """
        Resolve and return only the ranked candidate list.

        Convenience method — calls ``resolve()`` internally.

        Parameters
        ----------
        label:
            Raw label string.
        context:
            Optional resolution context.

        Returns
        -------
        list[LabelResolutionCandidate]
            Candidates ranked by score descending.
        """
        result = self.resolve(label, context)
        return result.candidates

    def explain(
        self,
        label: str,
        context: Optional[LabelResolutionContext] = None,
    ) -> ResolutionExplanation:
        """
        Resolve and return only the explanation trace.

        Convenience method — calls ``resolve()`` internally.

        Parameters
        ----------
        label:
            Raw label string.
        context:
            Optional resolution context.

        Returns
        -------
        ResolutionExplanation
            Full trace of the resolution attempt.
        """
        result = self.resolve(label, context)
        return result.explanation

    # ── Stage implementations ───────────────────────────────────────────────

    def _gather_alias_candidates(
        self,
        raw_label: str,
        normalized: str,
        explanation: ResolutionExplanation,
    ) -> list[LabelResolutionCandidate]:
        """
        Stages 1 & 2: Look up the label in the alias index and create
        initial candidates.

        Stage 1 (EXACT_ALIAS): tries the raw label against the exact
        alias index (case-sensitive, stripped).

        Stage 2 (NORMALIZED_ALIAS): tries the normalized label against
        the normalized alias index.

        Deduplication: if the same service is matched by both exact and
        normalized lookup, only one candidate is created, keeping the
        stronger alias.
        """
        candidates: list[LabelResolutionCandidate] = []
        seen_services: dict[str, LabelResolutionCandidate] = {}

        # Stage 1: Exact alias lookup
        exact_aliases = self._registry.lookup_aliases_exact(raw_label)
        if exact_aliases:
            explanation.record_stage(ResolutionStage.EXACT_ALIAS)
            for alias in exact_aliases:
                service = self._registry.get_service(alias.service_id)
                if service is None:
                    explanation.add_warning(
                        f"Alias '{alias.raw_label}' references unknown "
                        f"service '{alias.service_id}'"
                    )
                    continue

                candidate = LabelResolutionCandidate(
                    service=service,
                    score_breakdown=ScoreBreakdown(),
                    matched_alias=alias,
                )
                candidates.append(candidate)
                seen_services[alias.service_id] = candidate

        # Stage 2: Normalized alias lookup
        norm_aliases = self._registry.lookup_aliases_normalized(normalized)
        if norm_aliases:
            explanation.record_stage(ResolutionStage.NORMALIZED_ALIAS)
            for alias in norm_aliases:
                if alias.service_id in seen_services:
                    # Already have a candidate from exact match.
                    # Keep the stronger alias.
                    existing = seen_services[alias.service_id]
                    if (
                        existing.matched_alias is not None
                        and alias.strength.value
                        > existing.matched_alias.strength.value
                    ):
                        existing.matched_alias = alias  # type: ignore[misc]
                    continue

                service = self._registry.get_service(alias.service_id)
                if service is None:
                    explanation.add_warning(
                        f"Alias '{alias.raw_label}' references unknown "
                        f"service '{alias.service_id}'"
                    )
                    continue

                candidate = LabelResolutionCandidate(
                    service=service,
                    score_breakdown=ScoreBreakdown(),
                    matched_alias=alias,
                )
                candidates.append(candidate)
                seen_services[alias.service_id] = candidate

        # Warn if label is ambiguous
        if len(seen_services) > 1:
            service_ids = sorted(seen_services.keys())
            explanation.add_warning(
                f"Label '{raw_label}' (normalized: '{normalized}') is "
                f"ambiguous — maps to {len(service_ids)} services: "
                f"{', '.join(service_ids)}"
            )

        return candidates

    def _lookup_site_override(
        self,
        normalized: str,
        context: LabelResolutionContext,
        explanation: ResolutionExplanation,
    ) -> Optional[SiteOverrideEntry]:
        """
        Stage 3 (partial): Look up the site override for the current
        label and site.

        Returns the override entry if found, else ``None``.
        """
        if not context.site_id:
            return None

        override = self._overrides.lookup(context.site_id, normalized)
        if override is not None:
            explanation.record_stage(ResolutionStage.SITE_OVERRIDE)
        else:
            # Check if site has overrides at all (useful diagnostic)
            if self._overrides.has_overrides(context.site_id):
                explanation.add_warning(
                    f"Site '{context.site_id}' has overrides but none for "
                    f"label '{normalized}'"
                )

        return override

    def _inject_override_candidate(
        self,
        override: Optional[SiteOverrideEntry],
        candidates: list[LabelResolutionCandidate],
        normalized: str,
        explanation: ResolutionExplanation,
    ) -> None:
        """
        Stage 3 (continued): If a site override points to a service that
        is NOT already in the candidate list, inject it as a new candidate.

        This ensures the override target is always present for scoring,
        even if the label didn't match any alias for that service.
        """
        if override is None:
            return

        # Check if override target is already a candidate
        existing_ids = {c.service_id for c in candidates}
        if override.service_id in existing_ids:
            return  # Will be boosted by SiteOverrideBoostRule during scoring

        # Inject the override target as a new candidate
        service = self._registry.get_service(override.service_id)
        if service is None:
            explanation.add_warning(
                f"Site override for '{override.site_id}' references "
                f"unknown service '{override.service_id}'"
            )
            return

        # Create a synthetic alias for the injected candidate
        synthetic_alias = LabelAlias(
            raw_label=f"[override:{override.site_id}]",
            normalized_label=normalized,
            service_id=override.service_id,
            strength=override.strength,
            notes=f"Injected by site override from '{override.site_id}'",
        )

        candidate = LabelResolutionCandidate(
            service=service,
            score_breakdown=ScoreBreakdown(),
            matched_alias=synthetic_alias,
        )
        candidates.append(candidate)

    def _apply_domain_hints(
        self,
        context: LabelResolutionContext,
        candidates: list[LabelResolutionCandidate],
        explanation: ResolutionExplanation,
    ) -> None:
        """
        Stage 4: Check iframe URL and page URL for domain matches against
        known services.

        If a domain match identifies a service NOT already in the candidate
        list, inject it as a new candidate.  The ``DomainMatchRule`` in
        the scoring engine will apply the actual score boost to all
        candidates with matching domains.
        """
        urls_to_check: list[tuple[str, str]] = []
        if context.iframe_url:
            urls_to_check.append(("iframe_url", context.iframe_url))
        if context.page_url:
            urls_to_check.append(("page_url", context.page_url))

        if not urls_to_check:
            return

        existing_ids = {c.service_id for c in candidates}
        injected: set[str] = set()

        for url_type, url in urls_to_check:
            service = self._registry.find_service_by_url(url)
            if service is None:
                continue

            explanation.record_stage(ResolutionStage.DOMAIN_HINT)

            if service.service_id not in existing_ids and service.service_id not in injected:
                # Inject as a new candidate (no alias match — domain only)
                candidate = LabelResolutionCandidate(
                    service=service,
                    score_breakdown=ScoreBreakdown(),
                    matched_alias=None,  # No alias — introduced by domain
                )
                candidates.append(candidate)
                injected.add(service.service_id)

    # ── Result construction ─────────────────────────────────────────────────

    def _build_result(
        self,
        raw_label: str,
        normalized: str,
        candidates: list[LabelResolutionCandidate],
        explanation: ResolutionExplanation,
    ) -> LabelResolutionResult:
        """
        Construct the final ``LabelResolutionResult`` from scored and
        ranked candidates.
        """
        # Determine winner and confidence
        if candidates:
            winner_candidate = candidates[0]
            confidence = compute_confidence_from_candidates(candidates)

            # If confidence is UNRESOLVED, there's no winner
            if confidence == ConfidenceTier.UNRESOLVED:
                winner = None
            else:
                winner = winner_candidate.service
        else:
            winner = None
            confidence = ConfidenceTier.UNRESOLVED
            explanation.record_stage(ResolutionStage.UNRESOLVED)

        # Build summary
        explanation.summary = self._build_summary(
            raw_label, normalized, winner, confidence, candidates
        )

        result = LabelResolutionResult(
            input_label=raw_label,
            normalized_label=normalized,
            winner=winner,
            confidence=confidence,
            candidates=candidates,
            explanation=explanation,
        )

        return result

    @staticmethod
    def _build_summary(
        raw_label: str,
        normalized: str,
        winner: Optional[CanonicalService],
        confidence: ConfidenceTier,
        candidates: list[LabelResolutionCandidate],
    ) -> str:
        """Generate a human-readable summary paragraph."""
        parts: list[str] = []

        parts.append(
            f"Resolution of label '{raw_label}' "
            f"(normalized: '{normalized}'):"
        )

        if winner is not None:
            top = candidates[0]
            parts.append(
                f"  Winner: {winner.display_name} ({winner.service_id}) "
                f"with score {top.final_score:.2f}, "
                f"confidence={confidence.value}."
            )

            if len(candidates) > 1:
                runner = candidates[1]
                gap = top.final_score - runner.final_score
                parts.append(
                    f"  Runner-up: {runner.service.display_name} "
                    f"({runner.service_id}) with score "
                    f"{runner.final_score:.2f} (gap={gap:.2f})."
                )

            evidence_count = len(top.score_breakdown.adjustments)
            parts.append(
                f"  Decision based on {evidence_count} evidence items "
                f"across {len(candidates)} candidate(s)."
            )
        else:
            parts.append(
                f"  UNRESOLVED — no candidate reached the minimum "
                f"confidence threshold."
            )
            if candidates:
                top = candidates[0]
                parts.append(
                    f"  Best candidate: {top.service.display_name} "
                    f"({top.service_id}) with score "
                    f"{top.final_score:.2f}, but confidence is too low."
                )
            else:
                parts.append(
                    f"  No candidates found for this label."
                )

        return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level singleton
# ═══════════════════════════════════════════════════════════════════════════════

_default_resolver: Optional[LabelResolver] = None
_resolver_lock = threading.Lock()


def get_default_resolver() -> LabelResolver:
    """
    Return the module-level default ``LabelResolver``.

    Lazily built on first call.  Thread-safe.

    ADAPTATION POINT: Call ``set_default_resolver()`` before first access
    to inject a resolver with custom registry, overrides, or rules.
    """
    global _default_resolver
    if _default_resolver is None:
        with _resolver_lock:
            if _default_resolver is None:
                _default_resolver = LabelResolver()
    return _default_resolver


def set_default_resolver(resolver: LabelResolver) -> None:
    """Replace the module-level default resolver."""
    global _default_resolver
    with _resolver_lock:
        _default_resolver = resolver


def reset_default_resolver() -> None:
    """Clear the cached default resolver.  Primarily for testing."""
    global _default_resolver
    with _resolver_lock:
        _default_resolver = None