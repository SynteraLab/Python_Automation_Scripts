# extractors/registry_models.py
"""
Extractor registry data models.

Pure data structures used by the registry, resolution engine, and upgraded loader.
No business logic. No project imports. Stdlib only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, IntEnum, unique
from typing import Any, Dict, List, Optional, Type


# ============================================================
# Enums
# ============================================================

@unique
class ExtractorSource(str, Enum):
    """
    Classifies where an extractor originated.

    Trust rank (implicit): PLUGIN > UPGRADED > BUILTIN.
    Used as a tiebreaker when priority is equal.
    """
    BUILTIN = "builtin"
    UPGRADED = "upgraded"
    PLUGIN = "plugin"


# Explicit numeric trust rank mapping.
# Higher value = higher trust.
_SOURCE_TRUST_RANK: Dict[ExtractorSource, int] = {
    ExtractorSource.BUILTIN: 10,
    ExtractorSource.UPGRADED: 20,
    ExtractorSource.PLUGIN: 30,
}


def source_trust_rank(source: ExtractorSource) -> int:
    """
    Return the numeric trust rank for a given ExtractorSource.

    Higher value = more trusted / wins tiebreaks.
    Unknown sources default to 0.
    """
    return _SOURCE_TRUST_RANK.get(source, 0)


@unique
class RegistryDecision(str, Enum):
    """
    Outcome when a registration conflict is detected
    (two extractors sharing the same canonical name).
    """
    REPLACED = "replaced"        # Challenger explicitly replaces incumbent
    OVERRIDDEN = "overridden"    # Challenger wins by source rank or priority
    REJECTED = "rejected"        # Challenger loses; incumbent kept
    COEXIST = "coexist"          # Both kept (only if names differ but overlap on URLs)


@unique
class ResolutionReason(str, Enum):
    """
    Explains why a particular extractor won URL resolution.
    """
    EXPLICIT_REPLACE = "explicit_replace"
    HIGHER_PRIORITY = "higher_priority"
    SOURCE_RANK = "source_rank"
    HIGHER_CONFIDENCE = "higher_confidence"
    DETERMINISTIC_TIEBREAK = "deterministic_tiebreak"
    SOLE_CANDIDATE = "sole_candidate"
    GENERIC_FALLBACK = "generic_fallback"
    NO_MATCH = "no_match"


# ============================================================
# Core Metadata
# ============================================================

@dataclass
class ExtractorMetadata:
    """
    Rich metadata record for a single registered extractor.

    One instance per registered extractor in the registry.
    Immutable after creation by convention (not enforced via frozen
    to allow the `enabled` toggle).
    """

    # --- Identity ---
    name: str
    """Canonical extractor identity (e.g. 'youtube', 'generic')."""

    cls: Type[Any]
    """Reference to the extractor class itself."""

    # --- Classification ---
    source: ExtractorSource = ExtractorSource.BUILTIN
    """Where this extractor came from."""

    priority: int = 100
    """
    Numeric priority. Higher value wins.
    Defaults: BUILTIN=100, UPGRADED=200, PLUGIN=300.
    """

    is_generic: bool = False
    """
    If True, this extractor is a catch-all fallback.
    Generic extractors only participate in resolution after all
    specific extractors have failed.
    """

    # --- Override / Replace ---
    replaces: List[str] = field(default_factory=list)
    """
    List of canonical extractor names that this extractor formally replaces.
    When registered, any incumbent with a name in this list is displaced.
    During URL resolution, replaced extractors are eliminated.
    """

    # --- State ---
    enabled: bool = True
    """
    If False, the extractor is registered but excluded from resolution.
    Useful for disabling without full removal.
    """

    # --- Traceability ---
    module_path: str = ""
    """Dotted module path for debug traceability (e.g. 'extractors.upgraded.youtube')."""

    version: Optional[str] = None
    """Optional version string for the extractor."""

    registered_at: float = field(default_factory=time.monotonic)
    """
    Monotonic timestamp of registration.
    Used as the final deterministic tiebreaker when all other
    factors are equal. Earlier registration wins.
    """

    # --- Derived helpers ---

    @property
    def trust_rank(self) -> int:
        """Numeric trust rank derived from source."""
        return source_trust_rank(self.source)

    @property
    def sort_key(self) -> tuple:
        """
        Sort key for deterministic ordering.

        Sorted DESCENDING by:
          1. priority (higher wins)
          2. trust_rank (higher wins)
        Then ASCENDING by:
          3. registered_at (earlier wins — tiebreaker)

        Usage: sorted(candidates, key=lambda m: m.sort_key, reverse=True)
        But we encode direction into the tuple so a single
        ascending sort works:
          - Negate priority and trust_rank (so ascending = highest first)
          - Keep registered_at positive (so ascending = earliest first)
        """
        return (-self.priority, -self.trust_rank, self.registered_at)

    def __repr__(self) -> str:
        status = "enabled" if self.enabled else "DISABLED"
        generic_tag = " [generic]" if self.is_generic else ""
        replaces_tag = f" replaces={self.replaces}" if self.replaces else ""
        return (
            f"<ExtractorMetadata "
            f"name={self.name!r} "
            f"source={self.source.value} "
            f"priority={self.priority} "
            f"{status}{generic_tag}{replaces_tag} "
            f"cls={self.cls.__name__}>"
        )

    def summary(self) -> str:
        """One-line human-readable summary."""
        parts = [
            f"{self.name}",
            f"source={self.source.value}",
            f"priority={self.priority}",
        ]
        if self.is_generic:
            parts.append("generic=True")
        if self.replaces:
            parts.append(f"replaces={self.replaces}")
        if not self.enabled:
            parts.append("DISABLED")
        if self.version:
            parts.append(f"v={self.version}")
        return " | ".join(parts)


# ============================================================
# Conflict Record
# ============================================================

@dataclass
class RegistryConflict:
    """
    Recorded when two extractors contest the same canonical name
    during registration.
    """
    name: str
    """The contested canonical name."""

    incumbent: ExtractorMetadata
    """The previously registered extractor."""

    challenger: ExtractorMetadata
    """The newly registering extractor."""

    decision: RegistryDecision
    """What the registry decided."""

    reason: str
    """Human-readable explanation of the decision."""

    timestamp: float = field(default_factory=time.monotonic)
    """When this conflict was recorded."""

    def __repr__(self) -> str:
        return (
            f"<RegistryConflict "
            f"name={self.name!r} "
            f"decision={self.decision.value} "
            f"incumbent={self.incumbent.cls.__name__} "
            f"challenger={self.challenger.cls.__name__}>"
        )

    def explain(self) -> str:
        """Multi-line explanation of the conflict and its resolution."""
        lines = [
            f"Conflict on name '{self.name}':",
            f"  Incumbent : {self.incumbent.summary()}",
            f"  Challenger: {self.challenger.summary()}",
            f"  Decision  : {self.decision.value}",
            f"  Reason    : {self.reason}",
        ]
        return "\n".join(lines)


# ============================================================
# Resolution Models
# ============================================================

@dataclass
class ExtractorResolutionCandidate:
    """
    Represents one extractor evaluated during URL resolution.
    """
    metadata: ExtractorMetadata
    """The extractor being evaluated."""

    can_handle: bool = False
    """Whether the extractor reported it can handle the URL."""

    match_confidence: float = 0.0
    """
    0.0–1.0 confidence score.
    Extractors may optionally provide this. Default 0.0 means
    the extractor does not report granular confidence.
    If can_handle is True and confidence is 0.0, a default
    of 1.0 is assumed during resolution.
    """

    eliminated: bool = False
    """Whether this candidate was eliminated by resolution rules."""

    elimination_reason: Optional[str] = None
    """Why this candidate was eliminated (if applicable)."""

    @property
    def effective_confidence(self) -> float:
        """
        Confidence used for sorting.
        If the extractor can handle the URL but reported 0.0 confidence,
        we treat it as 1.0 (full confidence by assertion).
        """
        if self.can_handle and self.match_confidence <= 0.0:
            return 1.0
        return self.match_confidence

    def __repr__(self) -> str:
        status = "ELIMINATED" if self.eliminated else ("match" if self.can_handle else "no-match")
        return (
            f"<Candidate "
            f"{self.metadata.name} "
            f"{status} "
            f"confidence={self.effective_confidence:.2f}>"
        )


@dataclass
class ExtractorResolutionResult:
    """
    Full result of resolving a URL to an extractor.

    Contains the winner, the reason, all evaluated candidates,
    and a human-readable explanation.
    """
    url: str
    """The URL that was resolved."""

    winner: Optional[ExtractorMetadata] = None
    """The selected extractor, or None if no match at all."""

    reason: Optional[ResolutionReason] = None
    """Why the winner was selected."""

    candidates: List[ExtractorResolutionCandidate] = field(default_factory=list)
    """All extractors that were evaluated, with full details."""

    explanation: str = ""
    """Multi-line human-readable explanation of the entire resolution."""

    @property
    def success(self) -> bool:
        """Whether resolution found a usable extractor."""
        return self.winner is not None

    @property
    def is_generic_fallback(self) -> bool:
        """Whether the winner is the generic fallback."""
        return (
            self.winner is not None
            and self.reason == ResolutionReason.GENERIC_FALLBACK
        )

    @property
    def winner_cls(self) -> Optional[Type[Any]]:
        """Convenience: the winning class, or None."""
        return self.winner.cls if self.winner else None

    @property
    def winner_name(self) -> Optional[str]:
        """Convenience: the winning extractor name, or None."""
        return self.winner.name if self.winner else None

    def __repr__(self) -> str:
        if self.winner:
            return (
                f"<ResolutionResult "
                f"url={self.url!r} "
                f"winner={self.winner.name} "
                f"reason={self.reason.value if self.reason else '?'} "
                f"candidates={len(self.candidates)}>"
            )
        return (
            f"<ResolutionResult "
            f"url={self.url!r} "
            f"winner=None "
            f"candidates={len(self.candidates)}>"
        )

    def short_explanation(self) -> str:
        """One-line summary of the resolution."""
        if self.winner:
            return (
                f"URL {self.url!r} → {self.winner.name} "
                f"({self.reason.value if self.reason else 'unknown'})"
            )
        return f"URL {self.url!r} → no match"


# ============================================================
# Default priority constants
# ============================================================

DEFAULT_PRIORITY_BUILTIN: int = 100
DEFAULT_PRIORITY_UPGRADED: int = 200
DEFAULT_PRIORITY_PLUGIN: int = 300

# Map source → default priority for convenience.
DEFAULT_PRIORITY_BY_SOURCE: Dict[ExtractorSource, int] = {
    ExtractorSource.BUILTIN: DEFAULT_PRIORITY_BUILTIN,
    ExtractorSource.UPGRADED: DEFAULT_PRIORITY_UPGRADED,
    ExtractorSource.PLUGIN: DEFAULT_PRIORITY_PLUGIN,
}


def default_priority_for_source(source: ExtractorSource) -> int:
    """Return the default priority for a given source type."""
    return DEFAULT_PRIORITY_BY_SOURCE.get(source, DEFAULT_PRIORITY_BUILTIN)