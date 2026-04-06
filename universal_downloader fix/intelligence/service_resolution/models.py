"""
intelligence.service_resolution.models
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

All core data types for the service resolution engine.

This module contains zero business logic — only dataclasses, enums,
and lightweight construction helpers. Every other module in the package
depends on this one; this module depends on nothing beyond stdlib.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence


# ═══════════════════════════════════════════════════════════════════════════════
# Enums
# ═══════════════════════════════════════════════════════════════════════════════


class AliasStrength(enum.Enum):
    """
    How strongly a label alias binds to a canonical service.

    The strength controls the *base score* a candidate receives when matched
    through this alias.  Higher strength = higher starting confidence.

    Values are ordered from strongest to weakest intentionally so that
    comparisons like ``strength.value >= AliasStrength.STRONG.value`` work
    via the numeric codes.
    """

    EXACT = 100
    """
    The label is the service's own canonical name or an unambiguous
    full-length identifier (e.g., 'StreamTape' → streamtape).
    """

    STRONG = 80
    """
    The label is a well-known, widely-used abbreviation with minimal
    collision risk (e.g., 'VOE' → voe, 'SB' → streamsb on most sites).
    """

    MODERATE = 60
    """
    The label is a reasonable abbreviation but has *some* collision
    potential or is used inconsistently across sites.
    """

    WEAK = 40
    """
    The label is a loose or uncommon abbreviation.  Should not resolve
    without supporting context.
    """

    AMBIGUOUS = 20
    """
    The label is known to map to two or more services.  It *must* be
    disambiguated via context (site override, iframe domain, etc.).
    """

    DEPRECATED = 5
    """
    The label was once valid but the service has rebranded or the alias
    is no longer in active use.  Kept for backward compatibility; heavily
    penalized in scoring.
    """


class ConfidenceTier(enum.Enum):
    """
    Qualitative confidence bracket for a resolution result.

    Derived from the numeric composite score, the number of competing
    candidates, and the score gap between #1 and #2.
    """

    HIGH = "high"
    """Score ≥ threshold, large gap to runner-up or single candidate."""

    MEDIUM = "medium"
    """Score above minimum but gap to runner-up is narrow."""

    LOW = "low"
    """Score is below the comfortable threshold; result is tentative."""

    UNRESOLVED = "unresolved"
    """No candidate reached the minimum viable score."""


class ResolutionStage(enum.Enum):
    """
    Named stages of the resolution fallback chain.

    Each stage can add candidates, inject evidence, or both.
    Stages execute in declared order (top to bottom).
    """

    EXACT_ALIAS = "exact_alias"
    """Raw input label matched an alias verbatim (case-sensitive)."""

    NORMALIZED_ALIAS = "normalized_alias"
    """Normalized form of the label matched one or more aliases."""

    SITE_OVERRIDE = "site_override"
    """A site-specific override mapping was applied."""

    DOMAIN_HINT = "domain_hint"
    """An iframe or page URL domain matched a service's known domains."""

    PLAYER_HINT = "player_hint"
    """A player/framework hint matched a service's metadata."""

    CONTEXT_HEURISTIC = "context_heuristic"
    """Button text, surrounding text, or other contextual signals matched."""

    UNRESOLVED = "unresolved"
    """No stage produced a viable candidate."""


# ═══════════════════════════════════════════════════════════════════════════════
# Core identity models
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class CanonicalService:
    """
    The immutable identity of a media hosting service.

    Every resolution ultimately maps a label to one of these.
    ``service_id`` is the unique slug used as the primary key
    (e.g., ``'streamtape'``, ``'voe'``, ``'doodstream'``).
    """

    service_id: str
    """Unique lowercase slug.  Must be [a-z0-9_]+."""

    display_name: str
    """Human-friendly display name (e.g., 'StreamTape')."""

    domains: tuple[str, ...] = ()
    """
    Known domains for this service, lowest-level first.
    Example: ``('streamtape.com', 'stape.fun', 'streamtape.to')``
    """

    family: Optional[str] = None
    """
    Optional grouping slug for related services
    (e.g., ``'dood'`` for DoodStream + DoodS + D0000d).
    """

    notes: Optional[str] = None
    """Free-text notes for maintainers."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """
    Arbitrary key-value metadata.  Suggested keys:
    - ``'player_frameworks'``: list of known player techs
    - ``'cdn_patterns'``:      list of CDN URL patterns
    - ``'active'``:            bool, whether service is still online
    - ``'aka'``:               list of former names
    """

    def __post_init__(self) -> None:
        if not self.service_id or not self.service_id.replace("_", "").isalnum():
            raise ValueError(
                f"service_id must be a non-empty [a-z0-9_]+ slug, "
                f"got {self.service_id!r}"
            )
        if self.service_id != self.service_id.lower():
            raise ValueError(
                f"service_id must be lowercase, got {self.service_id!r}"
            )

    def domain_matches(self, url_or_domain: str) -> bool:
        """
        Return ``True`` if *url_or_domain* contains any of this service's
        known domains as a substring.  Lightweight check — not a full URL
        parser, but sufficient for evidence scoring.
        """
        lowered = url_or_domain.lower()
        return any(d in lowered for d in self.domains)


@dataclass(frozen=True, slots=True)
class LabelAlias:
    """
    A mapping from a single label string to a canonical service.

    Multiple ``LabelAlias`` objects can share the same ``normalized_label``
    if that label is ambiguous — each points to a different service.
    """

    raw_label: str
    """The original alias string as authored (before normalization)."""

    normalized_label: str
    """The fully-normalized form used for index lookup."""

    service_id: str
    """The ``CanonicalService.service_id`` this alias points to."""

    strength: AliasStrength = AliasStrength.STRONG
    """How strongly this alias binds to the service."""

    notes: Optional[str] = None
    """Optional maintainer note (e.g., 'only on German anime sites')."""


@dataclass(frozen=True, slots=True)
class SiteOverrideEntry:
    """
    A per-site override that forces a label to resolve to a specific service.

    Overrides inject strong evidence into the scoring pipeline; they do not
    bypass candidate ranking entirely, but they dominate it.
    """

    site_id: str
    """Identifier for the site (e.g., ``'aniworld.to'``)."""

    normalized_label: str
    """The normalized label this override applies to."""

    service_id: str
    """Target ``CanonicalService.service_id``."""

    strength: AliasStrength = AliasStrength.EXACT
    """Override strength — typically EXACT or STRONG."""

    notes: Optional[str] = None


# ═══════════════════════════════════════════════════════════════════════════════
# Resolution context
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class LabelResolutionContext:
    """
    Optional contextual signals available at resolution time.

    Any field may be ``None``.  The resolver uses whatever is available
    and records which signals contributed evidence.
    """

    site_id: Optional[str] = None
    """
    Identifier of the originating site (e.g., ``'aniworld.to'``).
    Used for site-override lookups.
    """

    iframe_url: Optional[str] = None
    """
    The ``src`` attribute of a detected iframe embed.
    Used for domain-hint matching.
    """

    page_url: Optional[str] = None
    """
    The URL of the page being scraped.
    Secondary domain-hint source.
    """

    button_text: Optional[str] = None
    """
    The visible text on the button/link that led to this label
    (e.g., ``'Watch on StreamTape'``).
    """

    player_hint: Optional[str] = None
    """
    A player or framework identifier detected in the embed
    (e.g., ``'plyr'``, ``'jwplayer'``, ``'vidstack'``).
    Populated by a later detection phase; this field is the hook.
    """

    extra_hints: dict[str, Any] = field(default_factory=dict)
    """
    Arbitrary additional hints for future rule extensions.

    Suggested keys:
    - ``'language_tag'``: e.g. ``'de'``, ``'en'``
    - ``'server_index'``: integer position in server list
    - ``'embed_type'``:   ``'iframe'``, ``'direct'``, ``'api'``
    """

    @classmethod
    def empty(cls) -> LabelResolutionContext:
        """Return a fully-empty context instance."""
        return cls()

    @classmethod
    def from_site(cls, site_id: str) -> LabelResolutionContext:
        """Convenience: context with only a site_id populated."""
        return cls(site_id=site_id)

    @classmethod
    def build(
        cls,
        *,
        site_id: Optional[str] = None,
        iframe_url: Optional[str] = None,
        page_url: Optional[str] = None,
        button_text: Optional[str] = None,
        player_hint: Optional[str] = None,
        **extra: Any,
    ) -> LabelResolutionContext:
        """Keyword-only builder with extras folded into ``extra_hints``."""
        return cls(
            site_id=site_id,
            iframe_url=iframe_url,
            page_url=page_url,
            button_text=button_text,
            player_hint=player_hint,
            extra_hints=extra,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Evidence & scoring models
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class ResolutionEvidence:
    """
    One atomic piece of evidence contributing to a candidate's score.

    Every score adjustment is recorded as an ``Evidence`` object so the
    resolution trace is fully transparent.
    """

    rule_name: str
    """
    Identifier of the scoring rule that produced this evidence
    (e.g., ``'alias_base_score'``, ``'domain_match'``).
    """

    stage: ResolutionStage
    """Which fallback-chain stage this evidence originated from."""

    score_delta: float
    """
    Signed score adjustment.  Positive = boost, negative = penalty.
    Typical range: -0.30 to +1.00.
    """

    reason: str
    """
    Human-readable explanation.
    Example: ``"Alias 'ST' has STRONG binding to streamtape (base=0.80)"``
    """

    source_detail: Optional[str] = None
    """
    Optional raw data backing this evidence.
    Example: the iframe URL that triggered a domain match.
    """


@dataclass(slots=True)
class ScoreBreakdown:
    """
    Itemized breakdown of how a candidate's final score was computed.
    """

    base_score: float = 0.0
    """Starting score (typically from alias strength)."""

    adjustments: list[ResolutionEvidence] = field(default_factory=list)
    """All evidence objects applied after the base score."""

    final_score: float = 0.0
    """Computed as ``base_score + sum(adj.score_delta for adj in adjustments)``."""

    def recompute(self) -> None:
        """Recalculate ``final_score`` from base + adjustments."""
        self.final_score = self.base_score + sum(
            e.score_delta for e in self.adjustments
        )

    def add_evidence(self, evidence: ResolutionEvidence) -> None:
        """Append evidence and recompute the final score."""
        self.adjustments.append(evidence)
        self.recompute()

    def add_evidence_batch(self, evidence_list: Sequence[ResolutionEvidence]) -> None:
        """Append multiple evidence objects and recompute once."""
        self.adjustments.extend(evidence_list)
        self.recompute()

    @property
    def total_adjustment(self) -> float:
        return sum(e.score_delta for e in self.adjustments)

    @property
    def positive_contributions(self) -> list[ResolutionEvidence]:
        return [e for e in self.adjustments if e.score_delta > 0]

    @property
    def negative_contributions(self) -> list[ResolutionEvidence]:
        return [e for e in self.adjustments if e.score_delta < 0]


@dataclass(slots=True)
class LabelResolutionCandidate:
    """
    A single candidate service produced during label resolution.

    Candidates are ranked by ``score_breakdown.final_score`` descending.
    """

    service: CanonicalService
    """The candidate service."""

    score_breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    """Full score decomposition."""

    matched_alias: Optional[LabelAlias] = None
    """The alias that initially introduced this candidate (if any)."""

    rank: int = 0
    """
    Position in the ranked output (1-based).
    Assigned after scoring is complete.
    """

    @property
    def final_score(self) -> float:
        return self.score_breakdown.final_score

    @property
    def service_id(self) -> str:
        return self.service.service_id


# ═══════════════════════════════════════════════════════════════════════════════
# Explanation & result models
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(slots=True)
class ResolutionExplanation:
    """
    Full trace of a resolution attempt — every stage executed, every
    piece of evidence collected, plus a human-readable summary.
    """

    input_label: str = ""
    """The original raw label before any processing."""

    normalized_label: str = ""
    """The label after normalization."""

    stages_executed: list[ResolutionStage] = field(default_factory=list)
    """Which stages of the fallback chain actually ran."""

    all_evidence: list[ResolutionEvidence] = field(default_factory=list)
    """Every evidence object produced across all candidates and stages."""

    summary: str = ""
    """One-paragraph human-readable summary of the outcome."""

    warnings: list[str] = field(default_factory=list)
    """
    Advisory warnings (e.g., 'Label is ambiguous with no disambiguator',
    'Site override not found for this site').
    """

    def record_stage(self, stage: ResolutionStage) -> None:
        if stage not in self.stages_executed:
            self.stages_executed.append(stage)

    def record_evidence(self, evidence: ResolutionEvidence) -> None:
        self.all_evidence.append(evidence)

    def record_evidence_batch(
        self, evidence_list: Sequence[ResolutionEvidence]
    ) -> None:
        self.all_evidence.extend(evidence_list)

    def add_warning(self, warning: str) -> None:
        self.warnings.append(warning)

    @property
    def evidence_by_stage(self) -> dict[ResolutionStage, list[ResolutionEvidence]]:
        result: dict[ResolutionStage, list[ResolutionEvidence]] = {}
        for ev in self.all_evidence:
            result.setdefault(ev.stage, []).append(ev)
        return result

    @property
    def evidence_by_rule(self) -> dict[str, list[ResolutionEvidence]]:
        result: dict[str, list[ResolutionEvidence]] = {}
        for ev in self.all_evidence:
            result.setdefault(ev.rule_name, []).append(ev)
        return result


@dataclass(slots=True)
class LabelResolutionResult:
    """
    The complete outcome of resolving a label to a canonical service.

    Contains the winner (if any), confidence assessment, full candidate
    ranking, and the resolution explanation trace.
    """

    input_label: str = ""
    """Original raw label."""

    normalized_label: str = ""
    """Normalized label."""

    winner: Optional[CanonicalService] = None
    """
    The top-ranked service, or ``None`` if resolution is UNRESOLVED.
    """

    confidence: ConfidenceTier = ConfidenceTier.UNRESOLVED
    """Qualitative confidence of the winner."""

    candidates: list[LabelResolutionCandidate] = field(default_factory=list)
    """All candidates, ranked by score descending."""

    explanation: ResolutionExplanation = field(
        default_factory=ResolutionExplanation
    )
    """Full resolution trace."""

    @property
    def is_resolved(self) -> bool:
        return self.winner is not None and self.confidence != ConfidenceTier.UNRESOLVED

    @property
    def winner_id(self) -> Optional[str]:
        return self.winner.service_id if self.winner else None

    @property
    def runner_up(self) -> Optional[LabelResolutionCandidate]:
        return self.candidates[1] if len(self.candidates) > 1 else None

    @property
    def score_gap(self) -> float:
        """Score difference between #1 and #2.  Returns inf if only one candidate."""
        if len(self.candidates) < 2:
            return float("inf")
        return self.candidates[0].final_score - self.candidates[1].final_score

    @property
    def candidate_count(self) -> int:
        return len(self.candidates)


# ═══════════════════════════════════════════════════════════════════════════════
# Normalization result (used by normalization.py, defined here to avoid cycles)
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    """
    Output of the label normalization pipeline.

    Captures the original input, the final normalized string, and which
    transforms were applied (for debug traceability).
    """

    original: str
    """The raw input label."""

    normalized: str
    """The fully-normalized output."""

    transforms_applied: tuple[str, ...] = ()
    """
    Names of the transform steps that actually modified the string.
    Steps that were no-ops are omitted.
    """