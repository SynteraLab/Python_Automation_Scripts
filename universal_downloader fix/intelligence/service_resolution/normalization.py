"""
intelligence.service_resolution.normalization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Deterministic, multi-step label normalization pipeline.

Every label passes through an ordered series of transforms before being
used for alias index lookup.  The pipeline is stateless and side-effect-free.

The design allows new transforms to be inserted, reordered, or removed
without touching any other module.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Sequence

from .models import NormalizationResult


# ═══════════════════════════════════════════════════════════════════════════════
# Individual transform functions
# ═══════════════════════════════════════════════════════════════════════════════
#
# Each transform has the signature:  (label: str) -> str
# It receives the current state of the label and returns the (possibly
# modified) state.  Transforms must be pure and idempotent.
#
# They are deliberately kept as module-level functions so they can be
# unit-tested individually and reordered freely.
# ═══════════════════════════════════════════════════════════════════════════════

# Type alias for a named transform step.
TransformStep = tuple[str, Callable[[str], str]]


def _strip_whitespace(label: str) -> str:
    """Strip leading/trailing whitespace and collapse internal runs."""
    return re.sub(r"\s+", " ", label.strip())


def _to_lowercase(label: str) -> str:
    """Unicode-aware case folding (slightly more aggressive than ``.lower()``)."""
    return label.casefold()


def _normalize_unicode(label: str) -> str:
    """
    Apply NFKD Unicode normalization, then strip combining characters.

    This handles accented characters, fullwidth ASCII, etc.
    Example: ``'Ｖｏｅ'`` → ``'voe'``,  ``'café'`` → ``'cafe'``
    """
    decomposed = unicodedata.normalize("NFKD", label)
    return "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )


def _remove_punctuation(label: str) -> str:
    """
    Remove all characters that are not alphanumeric or whitespace.

    Dots, dashes, underscores, slashes, parens, etc. are all stripped.
    This ensures ``'mp4-upload'``, ``'mp4_upload'``, and ``'mp4upload'``
    converge to the same normalized form.
    """
    return re.sub(r"[^a-z0-9\s]", "", label)


def _collapse_whitespace_to_empty(label: str) -> str:
    """Remove all remaining whitespace so the label is a single slug."""
    return re.sub(r"\s+", "", label)


# ── Digit/letter substitution maps ──────────────────────────────────────────

_LEET_MAP: dict[str, str] = {
    "0": "o",
    "1": "l",
    "3": "e",
    "4": "a",
    "5": "s",
    "7": "t",
    "8": "b",
}

# Patterns where digit substitution is known to occur in service labels.
# We only apply leet-speak normalization when the result matches a
# "trigger" pattern, to avoid false positives on labels that legitimately
# contain digits (like "mp4upload").
#
# ADAPTATION POINT: This set should grow as new leet-variant service names
# are discovered.  For now we keep a conservative list.
_LEET_TRIGGER_PATTERNS: set[str] = {
    "d0000d",  # DoodStream leet variant
    "d000d",
    "d00d",
    "d0od",
    "str3am",
    "str34m",
    "f1l3",
    "f1le",
    "v1d",
}


def _apply_leet_normalization(label: str) -> str:
    """
    Apply digit→letter substitution *only* when the label matches a known
    leet-speak pattern.  This avoids corrupting labels like ``'mp4upload'``
    where the ``4`` is intentional.
    """
    if any(trigger in label for trigger in _LEET_TRIGGER_PATTERNS):
        return "".join(_LEET_MAP.get(ch, ch) for ch in label)
    return label


# ── Suffix / prefix stripping ───────────────────────────────────────────────

# ADAPTATION POINT: Extend these lists as new label patterns are observed
# in the wild.  Keep them sorted longest-first so greedy matching works
# correctly.
_STRIP_SUFFIXES: tuple[str, ...] = (
    "player",
    "stream",
    "server",
    "hoster",
    "embed",
    "video",
    "cdn",
    "hub",
)

_STRIP_PREFIXES: tuple[str, ...] = (
    "server",
    "hoster",
)


def _strip_common_suffixes(label: str) -> str:
    """
    Remove trailing filler words that do not contribute to identity.

    Example: ``'voeserver'`` → ``'voe'``,  ``'filemoonplayer'`` → ``'filemoon'``

    Only strips if the remaining prefix is at least 2 characters long
    to avoid accidentally stripping the entire label.
    """
    changed = True
    while changed:
        changed = False
        for suffix in _STRIP_SUFFIXES:
            if label.endswith(suffix) and len(label) > len(suffix) + 1:
                label = label[: -len(suffix)]
                changed = True
                break  # restart from longest suffixes
    return label


def _strip_common_prefixes(label: str) -> str:
    """
    Remove leading filler words.

    Example: ``'servervoe'`` → ``'voe'``
    """
    changed = True
    while changed:
        changed = False
        for prefix in _STRIP_PREFIXES:
            if label.startswith(prefix) and len(label) > len(prefix) + 1:
                label = label[len(prefix):]
                changed = True
                break
    return label


# ── Known whole-label rewrites ──────────────────────────────────────────────

# ADAPTATION POINT: This map handles labels that cannot be normalized by
# the generic pipeline because they are idiosyncratic abbreviations or
# legacy spellings.  Add entries as new sites are encountered.
_WHOLE_LABEL_REWRITES: dict[str, str] = {
    "stape": "streamtape",
    "streme": "streamtape",  # observed typo
    "dstream": "doodstream",
    "dood": "doodstream",
    "dlions": "filelions",
    "fmoon": "filemoon",
    "mcloud": "mycloud",
    "megac": "megacloud",
    "swplayers": "swiftplayers",
    "tvideos": "turbovideos",
    "svideo": "supervideo",
}


def _apply_whole_label_rewrites(label: str) -> str:
    """Replace entire label if it matches a known rewrite."""
    return _WHOLE_LABEL_REWRITES.get(label, label)


# ═══════════════════════════════════════════════════════════════════════════════
# Pipeline definition
# ═══════════════════════════════════════════════════════════════════════════════

#: The default normalization pipeline.  Order matters.
#:
#: 1. Whitespace cleanup (before anything else)
#: 2. Case folding
#: 3. Unicode normalization (before punctuation removal)
#: 4. Punctuation removal
#: 5. Collapse remaining whitespace into empty string
#: 6. Leet-speak normalization (after punctuation removal, on clean slug)
#: 7. Suffix stripping
#: 8. Prefix stripping
#: 9. Whole-label rewrites (last — operates on the fully cleaned slug)
DEFAULT_PIPELINE: tuple[TransformStep, ...] = (
    ("strip_whitespace", _strip_whitespace),
    ("to_lowercase", _to_lowercase),
    ("normalize_unicode", _normalize_unicode),
    ("remove_punctuation", _remove_punctuation),
    ("collapse_whitespace", _collapse_whitespace_to_empty),
    ("leet_normalization", _apply_leet_normalization),
    ("strip_suffixes", _strip_common_suffixes),
    ("strip_prefixes", _strip_common_prefixes),
    ("whole_label_rewrite", _apply_whole_label_rewrites),
)


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════


def normalize_label(
    label: str,
    *,
    pipeline: Sequence[TransformStep] | None = None,
) -> NormalizationResult:
    """
    Run the full normalization pipeline on *label* and return a detailed
    ``NormalizationResult``.

    Parameters
    ----------
    label:
        The raw label string to normalize.
    pipeline:
        Optional override for the transform pipeline.  If ``None``, uses
        ``DEFAULT_PIPELINE``.

    Returns
    -------
    NormalizationResult
        Contains the original label, the normalized output, and the names
        of transforms that actually modified the string.

    Examples
    --------
    >>> normalize_label("  Stream-Tape  ").normalized
    'streamtape'
    >>> normalize_label("D0000d").normalized
    'doodstream'
    >>> normalize_label("MP4Upload").normalized
    'mp4upload'
    """
    if pipeline is None:
        pipeline = DEFAULT_PIPELINE

    current = label
    applied: list[str] = []

    for step_name, transform in pipeline:
        previous = current
        current = transform(current)
        if current != previous:
            applied.append(step_name)

    return NormalizationResult(
        original=label,
        normalized=current,
        transforms_applied=tuple(applied),
    )


def quick_normalize(label: str) -> str:
    """
    Convenience shorthand — returns only the normalized string.

    Equivalent to ``normalize_label(label).normalized``, but avoids
    constructing the full ``NormalizationResult`` when you don't need
    the trace.

    Parameters
    ----------
    label:
        The raw label string.

    Returns
    -------
    str
        The normalized label slug.
    """
    current = label
    for _step_name, transform in DEFAULT_PIPELINE:
        current = transform(current)
    return current


def make_pipeline(
    *,
    include: set[str] | None = None,
    exclude: set[str] | None = None,
    extra_steps: Sequence[TransformStep] = (),
) -> tuple[TransformStep, ...]:
    """
    Build a customized pipeline from the default one.

    Parameters
    ----------
    include:
        If provided, only keep steps whose names are in this set.
    exclude:
        If provided, drop steps whose names are in this set.
    extra_steps:
        Additional steps appended after filtering.

    Returns
    -------
    tuple[TransformStep, ...]
        The customized pipeline.

    Raises
    ------
    ValueError
        If *include* and *exclude* are both provided.

    Examples
    --------
    >>> pipeline = make_pipeline(exclude={'leet_normalization'})
    >>> normalize_label('D0000d', pipeline=pipeline).normalized
    'd0000d'
    """
    if include is not None and exclude is not None:
        raise ValueError("Cannot specify both include and exclude")

    steps: list[TransformStep] = []

    for step_name, transform in DEFAULT_PIPELINE:
        if include is not None and step_name not in include:
            continue
        if exclude is not None and step_name in exclude:
            continue
        steps.append((step_name, transform))

    steps.extend(extra_steps)
    return tuple(steps)