"""
intelligence.service_resolution
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Label-to-service resolution engine.

This package resolves ambiguous, abbreviated, or inconsistent media
hosting service labels (e.g., ``'ST'``, ``'VOE'``, ``'DD'``) into
canonical service identities with confidence scoring, evidence tracing,
and site-specific override support.

Quick start
-----------
::

    from intelligence.service_resolution import resolve_label, LabelResolutionContext

    result = resolve_label("ST", site_id="aniworld.to")
    print(result.winner_id)    # 'streamtape'
    print(result.confidence)   # ConfidenceTier.HIGH

Public API
----------
- ``resolve_label(label, ...)`` — resolve a label to a service
- ``resolve_candidates(label, ...)`` — get ranked candidate list
- ``explain_resolution(label, ...)`` — get full explanation trace
- ``normalize_label(label)`` — normalize a raw label string
- ``register_site_overrides(site_id, mapping)`` — add site overrides
- ``get_canonical_service(service_id)`` — look up a service by ID
- ``list_canonical_services()`` — list all known services
- ``get_resolver()`` — access the default resolver instance

ADAPTATION POINT: This __init__.py provides convenience wrappers around
the default singleton instances.  The host project can bypass these and
construct custom ``LabelResolver`` instances directly for full control.
"""

from __future__ import annotations

from typing import Any, Optional

# ── Re-export core types ────────────────────────────────────────────────────
from .models import (
    AliasStrength,
    CanonicalService,
    ConfidenceTier,
    LabelAlias,
    LabelResolutionCandidate,
    LabelResolutionContext,
    LabelResolutionResult,
    NormalizationResult,
    ResolutionEvidence,
    ResolutionExplanation,
    ResolutionStage,
    ScoreBreakdown,
    SiteOverrideEntry,
)

# ── Re-export key classes ───────────────────────────────────────────────────
from .canonical_services import ServiceRegistry
from .normalization import normalize_label, quick_normalize, make_pipeline
from .resolver import LabelResolver
from .scoring import ScoringRule
from .site_overrides import SiteOverrideManager

# ── Re-export explanation formatters ────────────────────────────────────────
from .explain import (
    build_debug_dict,
    format_explanation,
    format_full_result,
    format_result_summary,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience API (delegates to default singletons)
# ═══════════════════════════════════════════════════════════════════════════════


def get_resolver() -> LabelResolver:
    """
    Return the default ``LabelResolver`` instance.

    The resolver is lazily constructed on first access using the default
    service registry, site overrides, and scoring rules.
    """
    from .resolver import get_default_resolver

    return get_default_resolver()


def resolve_label(
    label: str,
    context: Optional[LabelResolutionContext] = None,
    *,
    site_id: Optional[str] = None,
    iframe_url: Optional[str] = None,
    page_url: Optional[str] = None,
    button_text: Optional[str] = None,
    player_hint: Optional[str] = None,
    **extra_hints: Any,
) -> LabelResolutionResult:
    """
    Resolve a label to a canonical service.

    This is the primary convenience entry point.  It accepts context
    either as a ``LabelResolutionContext`` object or as keyword arguments
    (which are assembled into a context automatically).

    Parameters
    ----------
    label:
        The raw label string (e.g., ``'ST'``, ``'VOE'``).
    context:
        Optional pre-built context object.  If provided, keyword
        arguments are ignored.
    site_id:
        Site identifier (e.g., ``'aniworld.to'``).
    iframe_url:
        Embed iframe URL.
    page_url:
        Page URL being scraped.
    button_text:
        Button/link text associated with the label.
    player_hint:
        Detected player/framework identifier.
    **extra_hints:
        Additional hint key-value pairs.

    Returns
    -------
    LabelResolutionResult
        Complete resolution result.

    Examples
    --------
    >>> result = resolve_label("ST", site_id="aniworld.to")
    >>> result.winner_id
    'streamtape'

    >>> result = resolve_label("MC", site_id="aniworld.to")
    >>> result.winner_id
    'mycloud'

    >>> result = resolve_label("MC", site_id="megakino.co")
    >>> result.winner_id
    'megacloud'
    """
    if context is None:
        context = LabelResolutionContext.build(
            site_id=site_id,
            iframe_url=iframe_url,
            page_url=page_url,
            button_text=button_text,
            player_hint=player_hint,
            **extra_hints,
        )

    return get_resolver().resolve(label, context)


def resolve_candidates(
    label: str,
    context: Optional[LabelResolutionContext] = None,
    *,
    site_id: Optional[str] = None,
    iframe_url: Optional[str] = None,
    **extra_hints: Any,
) -> list[LabelResolutionCandidate]:
    """
    Resolve and return the ranked candidate list.

    Accepts the same keyword arguments as ``resolve_label()`` for
    context construction.

    Parameters
    ----------
    label:
        Raw label string.
    context:
        Optional pre-built context.
    site_id:
        Site identifier.
    iframe_url:
        Embed iframe URL.
    **extra_hints:
        Additional hints.

    Returns
    -------
    list[LabelResolutionCandidate]
        Candidates ranked by score descending.
    """
    if context is None:
        context = LabelResolutionContext.build(
            site_id=site_id,
            iframe_url=iframe_url,
            **extra_hints,
        )

    return get_resolver().resolve_candidates(label, context)


def explain_resolution(
    label: str,
    context: Optional[LabelResolutionContext] = None,
    *,
    site_id: Optional[str] = None,
    iframe_url: Optional[str] = None,
    **extra_hints: Any,
) -> ResolutionExplanation:
    """
    Resolve and return the full explanation trace.

    Parameters
    ----------
    label:
        Raw label string.
    context:
        Optional pre-built context.
    site_id:
        Site identifier.
    iframe_url:
        Embed iframe URL.
    **extra_hints:
        Additional hints.

    Returns
    -------
    ResolutionExplanation
        Full trace of the resolution attempt.
    """
    if context is None:
        context = LabelResolutionContext.build(
            site_id=site_id,
            iframe_url=iframe_url,
            **extra_hints,
        )

    return get_resolver().explain(label, context)


def register_site_overrides(
    site_id: str,
    mapping: dict[str, str],
    *,
    strength: AliasStrength = AliasStrength.EXACT,
) -> None:
    """
    Register site-specific label overrides on the default override manager.

    This also resets the default resolver so it picks up the new overrides
    on next use.

    Parameters
    ----------
    site_id:
        Site identifier (e.g., ``'newsite.com'``).
    mapping:
        ``{raw_label: target_service_id}`` pairs.
    strength:
        Override strength applied to all entries.

    Examples
    --------
    >>> register_site_overrides("newsite.com", {
    ...     "ST": "streamtape",
    ...     "SW": "swiftplayers",
    ... })
    """
    from .site_overrides import get_default_override_manager
    from .resolver import reset_default_resolver

    manager = get_default_override_manager()
    manager.register_overrides_bulk(site_id, mapping, strength=strength)

    # Reset resolver so it picks up updated overrides
    reset_default_resolver()


def get_canonical_service(service_id: str) -> Optional[CanonicalService]:
    """
    Look up a canonical service by its ID.

    Parameters
    ----------
    service_id:
        Service slug (e.g., ``'streamtape'``, ``'voe'``).

    Returns
    -------
    CanonicalService or None
    """
    from .canonical_services import get_default_registry

    return get_default_registry().get_service(service_id)


def list_canonical_services() -> list[CanonicalService]:
    """
    Return all registered canonical services, sorted by service_id.

    Returns
    -------
    list[CanonicalService]
    """
    from .canonical_services import get_default_registry

    return get_default_registry().list_services()


# ═══════════════════════════════════════════════════════════════════════════════
# __all__ — explicit public API surface
# ═══════════════════════════════════════════════════════════════════════════════

__all__ = [
    # Core types / enums
    "AliasStrength",
    "CanonicalService",
    "ConfidenceTier",
    "LabelAlias",
    "LabelResolutionCandidate",
    "LabelResolutionContext",
    "LabelResolutionResult",
    "NormalizationResult",
    "ResolutionEvidence",
    "ResolutionExplanation",
    "ResolutionStage",
    "ScoreBreakdown",
    "SiteOverrideEntry",
    # Classes
    "LabelResolver",
    "ServiceRegistry",
    "SiteOverrideManager",
    "ScoringRule",
    # Normalization
    "normalize_label",
    "quick_normalize",
    "make_pipeline",
    # Convenience API
    "resolve_label",
    "resolve_candidates",
    "explain_resolution",
    "register_site_overrides",
    "get_canonical_service",
    "list_canonical_services",
    "get_resolver",
    # Formatters
    "build_debug_dict",
    "format_explanation",
    "format_full_result",
    "format_result_summary",
]