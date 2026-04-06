"""
intelligence.service_resolution.scoring
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The scoring rule engine for label-to-service resolution.

Each scoring rule is a callable that inspects a candidate, the resolution
context, and the backing data stores, then produces zero or more
``ResolutionEvidence`` objects describing score adjustments.

Rules are applied to every candidate in order.  After all rules have fired,
the candidate's ``ScoreBreakdown`` reflects the full composite score with
a transparent audit trail.

Design principles
-----------------
- Every score adjustment is an explicit ``ResolutionEvidence`` with a reason.
- Rules are stateless callables — no hidden side effects.
- Rules follow a ``Protocol`` interface so new rules can be added without
  modifying core logic.
- Confidence tier derivation is deterministic and based on published thresholds.
"""

from __future__ import annotations

import re
from typing import (
    Any,
    Optional,
    Protocol,
    Sequence,
    runtime_checkable,
)

from .models import (
    AliasStrength,
    CanonicalService,
    ConfidenceTier,
    LabelAlias,
    LabelResolutionCandidate,
    LabelResolutionContext,
    ResolutionEvidence,
    ResolutionStage,
    ScoreBreakdown,
    SiteOverrideEntry,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Constants & score tables
# ═══════════════════════════════════════════════════════════════════════════════

#: Base score assigned to a candidate based on the alias strength that
#: introduced it.  These values are the starting point before any
#: contextual adjustments.
#:
#: ADAPTATION POINT: Tune these weights if the resolver is producing
#: unexpected confidence tiers in practice.
ALIAS_STRENGTH_BASE_SCORES: dict[AliasStrength, float] = {
    AliasStrength.EXACT: 1.00,
    AliasStrength.STRONG: 0.80,
    AliasStrength.MODERATE: 0.55,
    AliasStrength.WEAK: 0.35,
    AliasStrength.AMBIGUOUS: 0.30,
    AliasStrength.DEPRECATED: 0.10,
}

#: Score boost applied when a site-specific override matches the candidate.
#: Keyed by the override entry's strength.
SITE_OVERRIDE_BOOST: dict[AliasStrength, float] = {
    AliasStrength.EXACT: 0.35,
    AliasStrength.STRONG: 0.30,
    AliasStrength.MODERATE: 0.20,
    AliasStrength.WEAK: 0.10,
    AliasStrength.AMBIGUOUS: 0.05,
    AliasStrength.DEPRECATED: 0.00,
}

#: Score boost when iframe or page URL domain matches the candidate's
#: service domains.
DOMAIN_MATCH_BOOST: float = 0.25

#: Additional boost when the domain match is on the iframe URL specifically
#: (stronger signal than page URL).
IFRAME_DOMAIN_EXTRA_BOOST: float = 0.05

#: Score boost when the button/UI text contains the service's display name
#: or a known alias.
BUTTON_TEXT_MATCH_BOOST: float = 0.10

#: Extra boost when button text is a near-exact match (entire text is
#: essentially the service name).
BUTTON_TEXT_STRONG_BOOST: float = 0.15

#: Score boost when a player hint matches known player frameworks for
#: this service.
PLAYER_HINT_MATCH_BOOST: float = 0.15

#: Penalty applied when a candidate was introduced through an AMBIGUOUS
#: alias and no contextual disambiguator has boosted it.
AMBIGUITY_PENALTY: float = -0.15

#: Additional penalty when multiple candidates are tied from ambiguous
#: aliases (crowded field).
AMBIGUITY_CROWDED_PENALTY: float = -0.05

#: Penalty applied to candidates from DEPRECATED aliases.
DEPRECATION_PENALTY: float = -0.30

#: Penalty note appended when a deprecated service is flagged as inactive.
INACTIVE_SERVICE_PENALTY: float = -0.10

# ── Confidence tier thresholds ──────────────────────────────────────────────

#: Minimum final score for a candidate to be considered resolved at all.
CONFIDENCE_MIN_SCORE: float = 0.30

#: Minimum final score for HIGH confidence.
CONFIDENCE_HIGH_SCORE: float = 0.75

#: Minimum score gap between #1 and #2 for HIGH confidence.
CONFIDENCE_HIGH_GAP: float = 0.20

#: Minimum final score for MEDIUM confidence.
CONFIDENCE_MEDIUM_SCORE: float = 0.45

#: Minimum score gap between #1 and #2 for MEDIUM confidence.
CONFIDENCE_MEDIUM_GAP: float = 0.10


# ═══════════════════════════════════════════════════════════════════════════════
# Scoring rule protocol
# ═══════════════════════════════════════════════════════════════════════════════


@runtime_checkable
class ScoringRule(Protocol):
    """
    Interface for a scoring rule.

    A rule inspects the candidate, context, and data stores, then returns
    a list of ``ResolutionEvidence`` objects.  An empty list means the
    rule did not fire for this candidate.
    """

    @property
    def rule_name(self) -> str:
        """Unique identifier for this rule (used in evidence records)."""
        ...

    def evaluate(
        self,
        candidate: LabelResolutionCandidate,
        context: LabelResolutionContext,
        *,
        all_candidates: Sequence[LabelResolutionCandidate],
        site_override: Optional[SiteOverrideEntry],
        normalized_label: str,
    ) -> list[ResolutionEvidence]:
        """
        Evaluate this rule against a single candidate.

        Parameters
        ----------
        candidate:
            The candidate being scored.
        context:
            Resolution context (may have None fields).
        all_candidates:
            All candidates in the current resolution (for relative scoring).
        site_override:
            The site override entry for this candidate's service, or None.
        normalized_label:
            The normalized input label.

        Returns
        -------
        list[ResolutionEvidence]
            Zero or more evidence objects.  Each will be appended to the
            candidate's ``ScoreBreakdown``.
        """
        ...


# ═══════════════════════════════════════════════════════════════════════════════
# Concrete scoring rules
# ═══════════════════════════════════════════════════════════════════════════════


class AliasBaseScoreRule:
    """
    Assigns the base score from the alias strength that introduced
    this candidate.

    This is always the first rule applied.  It establishes the starting
    score that subsequent rules adjust.
    """

    @property
    def rule_name(self) -> str:
        return "alias_base_score"

    def evaluate(
        self,
        candidate: LabelResolutionCandidate,
        context: LabelResolutionContext,
        *,
        all_candidates: Sequence[LabelResolutionCandidate],
        site_override: Optional[SiteOverrideEntry],
        normalized_label: str,
    ) -> list[ResolutionEvidence]:
        alias = candidate.matched_alias
        if alias is None:
            # Candidate was injected by domain/player hint, not alias lookup.
            # Give it a minimal base score.
            return [
                ResolutionEvidence(
                    rule_name=self.rule_name,
                    stage=ResolutionStage.NORMALIZED_ALIAS,
                    score_delta=0.15,
                    reason=(
                        f"Candidate '{candidate.service_id}' was not introduced "
                        f"by alias match; assigned minimal base score 0.15"
                    ),
                    source_detail=None,
                )
            ]

        base = ALIAS_STRENGTH_BASE_SCORES.get(alias.strength, 0.30)

        # Determine the stage based on whether this was an exact or
        # normalized match.  We encode this in the alias notes or
        # infer from label comparison.
        if alias.raw_label.strip() == normalized_label:
            stage = ResolutionStage.EXACT_ALIAS
            stage_desc = "exact raw match"
        elif alias.normalized_label == normalized_label:
            stage = ResolutionStage.NORMALIZED_ALIAS
            stage_desc = "normalized match"
        else:
            stage = ResolutionStage.NORMALIZED_ALIAS
            stage_desc = "normalized match (post-transform)"

        return [
            ResolutionEvidence(
                rule_name=self.rule_name,
                stage=stage,
                score_delta=base,
                reason=(
                    f"Alias '{alias.raw_label}' has {alias.strength.name} "
                    f"binding to '{candidate.service_id}' "
                    f"({stage_desc}, base={base:.2f})"
                ),
                source_detail=f"alias_raw='{alias.raw_label}' "
                f"strength={alias.strength.name}",
            )
        ]


class SiteOverrideBoostRule:
    """
    Applies a score boost when a site-specific override maps the input
    label to this candidate's service.
    """

    @property
    def rule_name(self) -> str:
        return "site_override_boost"

    def evaluate(
        self,
        candidate: LabelResolutionCandidate,
        context: LabelResolutionContext,
        *,
        all_candidates: Sequence[LabelResolutionCandidate],
        site_override: Optional[SiteOverrideEntry],
        normalized_label: str,
    ) -> list[ResolutionEvidence]:
        if site_override is None:
            return []

        if site_override.service_id != candidate.service_id:
            return []

        boost = SITE_OVERRIDE_BOOST.get(site_override.strength, 0.20)

        notes_detail = ""
        if site_override.notes:
            notes_detail = f" — {site_override.notes}"

        return [
            ResolutionEvidence(
                rule_name=self.rule_name,
                stage=ResolutionStage.SITE_OVERRIDE,
                score_delta=boost,
                reason=(
                    f"Site override for '{site_override.site_id}' maps "
                    f"'{normalized_label}' → '{site_override.service_id}' "
                    f"(strength={site_override.strength.name}, "
                    f"boost=+{boost:.2f}){notes_detail}"
                ),
                source_detail=f"site_id='{site_override.site_id}' "
                f"override_strength={site_override.strength.name}",
            )
        ]


class SiteOverridePenaltyRule:
    """
    Applies a penalty to candidates that are NOT the target of a site
    override, when a site override exists for this label.

    This creates separation between the overridden service and competitors.
    """

    @property
    def rule_name(self) -> str:
        return "site_override_penalty"

    def evaluate(
        self,
        candidate: LabelResolutionCandidate,
        context: LabelResolutionContext,
        *,
        all_candidates: Sequence[LabelResolutionCandidate],
        site_override: Optional[SiteOverrideEntry],
        normalized_label: str,
    ) -> list[ResolutionEvidence]:
        if site_override is None:
            return []

        if site_override.service_id == candidate.service_id:
            # This candidate IS the override target; no penalty.
            return []

        penalty = -0.15

        return [
            ResolutionEvidence(
                rule_name=self.rule_name,
                stage=ResolutionStage.SITE_OVERRIDE,
                score_delta=penalty,
                reason=(
                    f"Site override for '{site_override.site_id}' targets "
                    f"'{site_override.service_id}', not "
                    f"'{candidate.service_id}' (penalty={penalty:.2f})"
                ),
                source_detail=f"override_target='{site_override.service_id}'",
            )
        ]


class DomainMatchRule:
    """
    Boosts a candidate when the iframe URL or page URL contains one of
    the service's known domains.

    iframe URL matches receive a stronger boost than page URL matches
    because iframe src is a more direct signal of the embed source.
    """

    @property
    def rule_name(self) -> str:
        return "domain_match"

    def evaluate(
        self,
        candidate: LabelResolutionCandidate,
        context: LabelResolutionContext,
        *,
        all_candidates: Sequence[LabelResolutionCandidate],
        site_override: Optional[SiteOverrideEntry],
        normalized_label: str,
    ) -> list[ResolutionEvidence]:
        evidence: list[ResolutionEvidence] = []
        service = candidate.service

        if not service.domains:
            return evidence

        # Check iframe URL
        if context.iframe_url and service.domain_matches(context.iframe_url):
            matched_domain = self._find_matched_domain(
                service, context.iframe_url
            )
            boost = DOMAIN_MATCH_BOOST + IFRAME_DOMAIN_EXTRA_BOOST
            evidence.append(
                ResolutionEvidence(
                    rule_name=self.rule_name,
                    stage=ResolutionStage.DOMAIN_HINT,
                    score_delta=boost,
                    reason=(
                        f"iframe URL contains domain '{matched_domain}' "
                        f"matching service '{service.service_id}' "
                        f"(boost=+{boost:.2f})"
                    ),
                    source_detail=f"iframe_url='{context.iframe_url}' "
                    f"matched_domain='{matched_domain}'",
                )
            )

        # Check page URL (weaker signal, and skip if iframe already matched)
        if (
            context.page_url
            and not evidence  # avoid double-counting
            and service.domain_matches(context.page_url)
        ):
            matched_domain = self._find_matched_domain(
                service, context.page_url
            )
            boost = DOMAIN_MATCH_BOOST
            evidence.append(
                ResolutionEvidence(
                    rule_name=self.rule_name,
                    stage=ResolutionStage.DOMAIN_HINT,
                    score_delta=boost,
                    reason=(
                        f"Page URL contains domain '{matched_domain}' "
                        f"matching service '{service.service_id}' "
                        f"(boost=+{boost:.2f})"
                    ),
                    source_detail=f"page_url='{context.page_url}' "
                    f"matched_domain='{matched_domain}'",
                )
            )

        return evidence

    @staticmethod
    def _find_matched_domain(
        service: CanonicalService, url: str
    ) -> str:
        """Find which domain from the service actually matched the URL."""
        lowered = url.lower()
        # Prefer longest domain match for accuracy
        for domain in sorted(service.domains, key=len, reverse=True):
            if domain in lowered:
                return domain
        return "(unknown)"


class ButtonTextRule:
    """
    Boosts a candidate when the button/UI text contains the service's
    display name, service_id, or a known strong alias.

    A near-exact match (button text is essentially just the service name)
    gets a stronger boost than a substring match.
    """

    @property
    def rule_name(self) -> str:
        return "button_text_match"

    def evaluate(
        self,
        candidate: LabelResolutionCandidate,
        context: LabelResolutionContext,
        *,
        all_candidates: Sequence[LabelResolutionCandidate],
        site_override: Optional[SiteOverrideEntry],
        normalized_label: str,
    ) -> list[ResolutionEvidence]:
        if not context.button_text:
            return []

        service = candidate.service
        btn_lower = context.button_text.lower().strip()
        btn_clean = re.sub(r"[^a-z0-9\s]", "", btn_lower).strip()

        if not btn_clean:
            return []

        # Check for near-exact match first
        match_targets_exact = {
            service.display_name.lower(),
            service.service_id,
        }

        for target in match_targets_exact:
            if btn_clean == target or btn_clean == target.replace(" ", ""):
                return [
                    ResolutionEvidence(
                        rule_name=self.rule_name,
                        stage=ResolutionStage.CONTEXT_HEURISTIC,
                        score_delta=BUTTON_TEXT_STRONG_BOOST,
                        reason=(
                            f"Button text '{context.button_text}' is a "
                            f"near-exact match for service "
                            f"'{service.display_name}' "
                            f"(boost=+{BUTTON_TEXT_STRONG_BOOST:.2f})"
                        ),
                        source_detail=f"button_text='{context.button_text}' "
                        f"matched_target='{target}'",
                    )
                ]

        # Check for substring match
        match_targets_sub = {
            service.display_name.lower(),
            service.service_id,
        }
        # Add known aliases with EXACT or STRONG strength
        if candidate.matched_alias and candidate.matched_alias.strength in (
            AliasStrength.EXACT,
            AliasStrength.STRONG,
        ):
            match_targets_sub.add(candidate.matched_alias.raw_label.lower())

        for target in match_targets_sub:
            if len(target) >= 3 and target in btn_clean:
                return [
                    ResolutionEvidence(
                        rule_name=self.rule_name,
                        stage=ResolutionStage.CONTEXT_HEURISTIC,
                        score_delta=BUTTON_TEXT_MATCH_BOOST,
                        reason=(
                            f"Button text '{context.button_text}' contains "
                            f"'{target}' matching service "
                            f"'{service.display_name}' "
                            f"(boost=+{BUTTON_TEXT_MATCH_BOOST:.2f})"
                        ),
                        source_detail=f"button_text='{context.button_text}' "
                        f"substring_match='{target}'",
                    )
                ]

        return []


class PlayerHintRule:
    """
    Boosts a candidate when the detected player/framework hint matches
    known player metadata for this service.

    ADAPTATION POINT: The player_frameworks metadata on CanonicalService
    is populated by Phase 3.  This rule is the hook interface — it reads
    from ``service.metadata.get('player_frameworks', [])`` and matches
    against ``context.player_hint``.
    """

    @property
    def rule_name(self) -> str:
        return "player_hint_match"

    def evaluate(
        self,
        candidate: LabelResolutionCandidate,
        context: LabelResolutionContext,
        *,
        all_candidates: Sequence[LabelResolutionCandidate],
        site_override: Optional[SiteOverrideEntry],
        normalized_label: str,
    ) -> list[ResolutionEvidence]:
        if not context.player_hint:
            return []

        service = candidate.service
        known_players: list[str] = service.metadata.get(
            "player_frameworks", []
        )

        if not known_players:
            return []

        hint_lower = context.player_hint.lower().strip()

        for player in known_players:
            if player.lower() == hint_lower:
                return [
                    ResolutionEvidence(
                        rule_name=self.rule_name,
                        stage=ResolutionStage.PLAYER_HINT,
                        score_delta=PLAYER_HINT_MATCH_BOOST,
                        reason=(
                            f"Player hint '{context.player_hint}' matches "
                            f"known framework '{player}' for service "
                            f"'{service.display_name}' "
                            f"(boost=+{PLAYER_HINT_MATCH_BOOST:.2f})"
                        ),
                        source_detail=f"player_hint='{context.player_hint}' "
                        f"matched_framework='{player}'",
                    )
                ]

        return []


class AmbiguityPenaltyRule:
    """
    Penalizes candidates introduced through an AMBIGUOUS alias when no
    strong disambiguator (site override, domain match) has already fired
    for them.

    Also applies an additional penalty if the candidate field is crowded
    (3+ candidates from ambiguous aliases).
    """

    @property
    def rule_name(self) -> str:
        return "ambiguity_penalty"

    def evaluate(
        self,
        candidate: LabelResolutionCandidate,
        context: LabelResolutionContext,
        *,
        all_candidates: Sequence[LabelResolutionCandidate],
        site_override: Optional[SiteOverrideEntry],
        normalized_label: str,
    ) -> list[ResolutionEvidence]:
        alias = candidate.matched_alias
        if alias is None or alias.strength != AliasStrength.AMBIGUOUS:
            return []

        evidence: list[ResolutionEvidence] = []

        # Check if a strong disambiguator has already fired.
        # A disambiguator is any evidence from SITE_OVERRIDE or DOMAIN_HINT
        # stage with a positive score delta.
        existing_evidence = candidate.score_breakdown.adjustments
        has_disambiguator = any(
            e.score_delta > 0
            and e.stage
            in (ResolutionStage.SITE_OVERRIDE, ResolutionStage.DOMAIN_HINT)
            for e in existing_evidence
        )

        if not has_disambiguator:
            evidence.append(
                ResolutionEvidence(
                    rule_name=self.rule_name,
                    stage=ResolutionStage.CONTEXT_HEURISTIC,
                    score_delta=AMBIGUITY_PENALTY,
                    reason=(
                        f"Alias '{alias.raw_label}' is AMBIGUOUS for "
                        f"'{candidate.service_id}' and no strong "
                        f"disambiguator has fired "
                        f"(penalty={AMBIGUITY_PENALTY:.2f})"
                    ),
                    source_detail=f"alias_strength=AMBIGUOUS "
                    f"disambiguated=False",
                )
            )

        # Crowded-field penalty
        ambiguous_candidate_count = sum(
            1
            for c in all_candidates
            if c.matched_alias is not None
            and c.matched_alias.strength == AliasStrength.AMBIGUOUS
        )
        if ambiguous_candidate_count >= 3:
            evidence.append(
                ResolutionEvidence(
                    rule_name=self.rule_name,
                    stage=ResolutionStage.CONTEXT_HEURISTIC,
                    score_delta=AMBIGUITY_CROWDED_PENALTY,
                    reason=(
                        f"Crowded ambiguous field: {ambiguous_candidate_count} "
                        f"candidates from AMBIGUOUS aliases "
                        f"(penalty={AMBIGUITY_CROWDED_PENALTY:.2f})"
                    ),
                    source_detail=f"ambiguous_candidates="
                    f"{ambiguous_candidate_count}",
                )
            )

        return evidence


class DeprecationPenaltyRule:
    """
    Penalizes candidates introduced through DEPRECATED aliases and/or
    candidates whose service is flagged as inactive.
    """

    @property
    def rule_name(self) -> str:
        return "deprecation_penalty"

    def evaluate(
        self,
        candidate: LabelResolutionCandidate,
        context: LabelResolutionContext,
        *,
        all_candidates: Sequence[LabelResolutionCandidate],
        site_override: Optional[SiteOverrideEntry],
        normalized_label: str,
    ) -> list[ResolutionEvidence]:
        evidence: list[ResolutionEvidence] = []

        alias = candidate.matched_alias
        if alias is not None and alias.strength == AliasStrength.DEPRECATED:
            evidence.append(
                ResolutionEvidence(
                    rule_name=self.rule_name,
                    stage=ResolutionStage.NORMALIZED_ALIAS,
                    score_delta=DEPRECATION_PENALTY,
                    reason=(
                        f"Alias '{alias.raw_label}' is DEPRECATED for "
                        f"'{candidate.service_id}' "
                        f"(penalty={DEPRECATION_PENALTY:.2f})"
                    ),
                    source_detail=f"alias_strength=DEPRECATED",
                )
            )

        # Check if service itself is inactive
        is_active = candidate.service.metadata.get("active", True)
        if not is_active:
            evidence.append(
                ResolutionEvidence(
                    rule_name=self.rule_name,
                    stage=ResolutionStage.NORMALIZED_ALIAS,
                    score_delta=INACTIVE_SERVICE_PENALTY,
                    reason=(
                        f"Service '{candidate.service_id}' is flagged as "
                        f"inactive (penalty={INACTIVE_SERVICE_PENALTY:.2f})"
                    ),
                    source_detail=f"service_active=False",
                )
            )

        return evidence


class ExtraHintsRule:
    """
    Extensible rule that inspects ``context.extra_hints`` for additional
    scoring signals.

    Currently supports:
    - ``'prefer_service'``: if set to a service_id, boosts that candidate
    - ``'avoid_service'``: if set to a service_id, penalizes that candidate

    ADAPTATION POINT: Add more hint keys here as new contextual signals
    are defined by the host project.
    """

    PREFER_BOOST: float = 0.15
    AVOID_PENALTY: float = -0.15

    @property
    def rule_name(self) -> str:
        return "extra_hints"

    def evaluate(
        self,
        candidate: LabelResolutionCandidate,
        context: LabelResolutionContext,
        *,
        all_candidates: Sequence[LabelResolutionCandidate],
        site_override: Optional[SiteOverrideEntry],
        normalized_label: str,
    ) -> list[ResolutionEvidence]:
        if not context.extra_hints:
            return []

        evidence: list[ResolutionEvidence] = []

        prefer = context.extra_hints.get("prefer_service")
        if prefer and prefer == candidate.service_id:
            evidence.append(
                ResolutionEvidence(
                    rule_name=self.rule_name,
                    stage=ResolutionStage.CONTEXT_HEURISTIC,
                    score_delta=self.PREFER_BOOST,
                    reason=(
                        f"Extra hint 'prefer_service' matches "
                        f"'{candidate.service_id}' "
                        f"(boost=+{self.PREFER_BOOST:.2f})"
                    ),
                    source_detail=f"prefer_service='{prefer}'",
                )
            )

        avoid = context.extra_hints.get("avoid_service")
        if avoid and avoid == candidate.service_id:
            evidence.append(
                ResolutionEvidence(
                    rule_name=self.rule_name,
                    stage=ResolutionStage.CONTEXT_HEURISTIC,
                    score_delta=self.AVOID_PENALTY,
                    reason=(
                        f"Extra hint 'avoid_service' matches "
                        f"'{candidate.service_id}' "
                        f"(penalty={self.AVOID_PENALTY:.2f})"
                    ),
                    source_detail=f"avoid_service='{avoid}'",
                )
            )

        return evidence


# ═══════════════════════════════════════════════════════════════════════════════
# Default rule set
# ═══════════════════════════════════════════════════════════════════════════════

#: The default ordered list of scoring rules.
#:
#: Order matters:
#: 1. AliasBaseScoreRule — establishes base score from alias strength.
#: 2. SiteOverrideBoostRule — boosts the override target.
#: 3. SiteOverridePenaltyRule — penalizes non-targets when override exists.
#: 4. DomainMatchRule — boosts on domain match.
#: 5. ButtonTextRule — boosts on button text match.
#: 6. PlayerHintRule — boosts on player framework match.
#: 7. AmbiguityPenaltyRule — penalizes unresolved ambiguity (must run AFTER
#:    disambiguators like override/domain so it can detect them).
#: 8. DeprecationPenaltyRule — penalizes deprecated/inactive.
#: 9. ExtraHintsRule — applies any ad-hoc hint adjustments.
#:
#: ADAPTATION POINT: To add custom rules, instantiate them and insert
#: them into this list at the appropriate position, or construct a new
#: list for the resolver.
DEFAULT_SCORING_RULES: tuple[Any, ...] = (
    AliasBaseScoreRule(),
    SiteOverrideBoostRule(),
    SiteOverridePenaltyRule(),
    DomainMatchRule(),
    ButtonTextRule(),
    PlayerHintRule(),
    AmbiguityPenaltyRule(),
    DeprecationPenaltyRule(),
    ExtraHintsRule(),
)


# ═══════════════════════════════════════════════════════════════════════════════
# Score computation helpers
# ═══════════════════════════════════════════════════════════════════════════════


def score_candidate(
    candidate: LabelResolutionCandidate,
    context: LabelResolutionContext,
    *,
    all_candidates: Sequence[LabelResolutionCandidate],
    site_override: Optional[SiteOverrideEntry],
    normalized_label: str,
    rules: Sequence[Any] | None = None,
) -> ScoreBreakdown:
    """
    Apply all scoring rules to a single candidate and return the
    resulting ``ScoreBreakdown``.

    This function mutates ``candidate.score_breakdown`` in place AND
    returns it for convenience.

    Parameters
    ----------
    candidate:
        The candidate to score.
    context:
        Resolution context.
    all_candidates:
        All candidates in the current resolution (for relative rules).
    site_override:
        The site override entry for the input label (or None).
    normalized_label:
        The normalized input label.
    rules:
        Scoring rules to apply.  Defaults to ``DEFAULT_SCORING_RULES``.

    Returns
    -------
    ScoreBreakdown
        The candidate's updated score breakdown.
    """
    if rules is None:
        rules = DEFAULT_SCORING_RULES

    breakdown = candidate.score_breakdown
    all_evidence: list[ResolutionEvidence] = []

    for rule in rules:
        evidence = rule.evaluate(
            candidate,
            context,
            all_candidates=all_candidates,
            site_override=site_override,
            normalized_label=normalized_label,
        )
        all_evidence.extend(evidence)

    # Separate base score from adjustments.
    # The first rule (AliasBaseScoreRule) sets the base.
    # All subsequent evidence is adjustments.
    if all_evidence:
        # First evidence from alias_base_score rule becomes the base_score
        base_evidence = [
            e for e in all_evidence if e.rule_name == "alias_base_score"
        ]
        adjustment_evidence = [
            e for e in all_evidence if e.rule_name != "alias_base_score"
        ]

        if base_evidence:
            breakdown.base_score = sum(e.score_delta for e in base_evidence)
            # Record base evidence in adjustments too for full trace
            breakdown.adjustments.extend(base_evidence)
        breakdown.add_evidence_batch(adjustment_evidence)
    else:
        breakdown.recompute()

    # Floor the final score at 0.0 (negative scores are meaningless)
    if breakdown.final_score < 0.0:
        floor_delta = -breakdown.final_score
        floor_evidence = ResolutionEvidence(
            rule_name="score_floor",
            stage=ResolutionStage.UNRESOLVED,
            score_delta=floor_delta,
            reason=(
                f"Score floored from {breakdown.final_score - floor_delta:.2f} "
                f"to 0.00"
            ),
            source_detail=None,
        )
        breakdown.add_evidence(floor_evidence)

    return breakdown


def score_all_candidates(
    candidates: list[LabelResolutionCandidate],
    context: LabelResolutionContext,
    *,
    site_override: Optional[SiteOverrideEntry],
    normalized_label: str,
    rules: Sequence[Any] | None = None,
) -> list[LabelResolutionCandidate]:
    """
    Score all candidates, sort by final score descending, and assign ranks.

    Returns the same list, sorted and ranked in place.

    Parameters
    ----------
    candidates:
        All candidates to score.
    context:
        Resolution context.
    site_override:
        The site override for the current label (if any).
    normalized_label:
        The normalized input label.
    rules:
        Scoring rules.  Defaults to ``DEFAULT_SCORING_RULES``.

    Returns
    -------
    list[LabelResolutionCandidate]
        The input list, sorted descending by score and with ``rank`` assigned.
    """
    for candidate in candidates:
        score_candidate(
            candidate,
            context,
            all_candidates=candidates,
            site_override=site_override,
            normalized_label=normalized_label,
            rules=rules,
        )

    # Sort descending by final score, then by service_id for determinism
    candidates.sort(
        key=lambda c: (-c.final_score, c.service_id)
    )

    # Assign 1-based ranks
    for idx, candidate in enumerate(candidates):
        candidate.rank = idx + 1

    return candidates


# ═══════════════════════════════════════════════════════════════════════════════
# Confidence tier computation
# ═══════════════════════════════════════════════════════════════════════════════


def compute_confidence_tier(
    top_score: float,
    candidate_count: int,
    score_gap: float,
) -> ConfidenceTier:
    """
    Determine the qualitative confidence tier from numeric indicators.

    Parameters
    ----------
    top_score:
        The final score of the top-ranked candidate.
    candidate_count:
        Number of candidates with score > 0.
    score_gap:
        Score difference between #1 and #2 (``float('inf')`` if only
        one candidate).

    Returns
    -------
    ConfidenceTier
        The qualitative confidence bracket.

    Logic
    -----
    - **UNRESOLVED**: top_score < CONFIDENCE_MIN_SCORE, or no candidates.
    - **HIGH**: top_score ≥ CONFIDENCE_HIGH_SCORE AND
      (score_gap ≥ CONFIDENCE_HIGH_GAP OR only one candidate).
    - **MEDIUM**: top_score ≥ CONFIDENCE_MEDIUM_SCORE AND
      score_gap ≥ CONFIDENCE_MEDIUM_GAP.
    - **LOW**: everything else above the minimum.
    """
    if candidate_count == 0 or top_score < CONFIDENCE_MIN_SCORE:
        return ConfidenceTier.UNRESOLVED

    is_sole_candidate = candidate_count == 1 or score_gap == float("inf")

    # HIGH
    if top_score >= CONFIDENCE_HIGH_SCORE:
        if is_sole_candidate or score_gap >= CONFIDENCE_HIGH_GAP:
            return ConfidenceTier.HIGH

    # MEDIUM
    if top_score >= CONFIDENCE_MEDIUM_SCORE:
        if is_sole_candidate or score_gap >= CONFIDENCE_MEDIUM_GAP:
            return ConfidenceTier.MEDIUM

    # LOW — above minimum but doesn't meet higher thresholds
    return ConfidenceTier.LOW


def compute_confidence_from_candidates(
    candidates: list[LabelResolutionCandidate],
) -> ConfidenceTier:
    """
    Convenience wrapper: compute confidence tier directly from a ranked
    candidate list (must already be sorted descending by score).

    Parameters
    ----------
    candidates:
        Ranked candidate list (index 0 = top).

    Returns
    -------
    ConfidenceTier
    """
    if not candidates:
        return ConfidenceTier.UNRESOLVED

    top_score = candidates[0].final_score
    candidate_count = len([c for c in candidates if c.final_score > 0])

    if len(candidates) >= 2:
        score_gap = candidates[0].final_score - candidates[1].final_score
    else:
        score_gap = float("inf")

    return compute_confidence_tier(top_score, candidate_count, score_gap)