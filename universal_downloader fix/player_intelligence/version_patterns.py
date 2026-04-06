# player_intelligence/version_patterns.py
"""
Reusable version detection engine.

Given HTML/JS content and a list of PlayerVersionPattern objects,
this module finds the best version string, normalizes it, and
returns a structured VersionDetectionResult.

Also provides shared cross-player version regex fragments
that profiles can compose into their own patterns.
"""

from __future__ import annotations

import re
from typing import Sequence

from .models import (
    PlayerVersionPattern,
    VersionDetectionResult,
)


# ═══════════════════════════════════════════════════════════════
# SHARED VERSION REGEX FRAGMENTS
# ═══════════════════════════════════════════════════════════════

# These fragments are building blocks that profiles.py can use
# when constructing PlayerVersionPattern regex strings.

VERSION_CORE = r'(\d+\.\d+(?:\.\d+)?)'
"""Matches 'X.Y' or 'X.Y.Z' — the most common version format."""

VERSION_CORE_EXTENDED = r'(\d+\.\d+(?:\.\d+)?(?:[-+][a-zA-Z0-9._]+)?)'
"""Matches 'X.Y.Z-beta.1' or 'X.Y.Z+build.42' — semver extended."""

VERSION_PREFIX = r'v?'
"""Optional 'v' prefix before version number."""

VERSION_FULL = VERSION_PREFIX + VERSION_CORE
"""Common pattern: optional 'v' then X.Y or X.Y.Z."""

VERSION_FULL_EXTENDED = VERSION_PREFIX + VERSION_CORE_EXTENDED
"""Common pattern: optional 'v' then semver extended."""


# ═══════════════════════════════════════════════════════════════
# VERSION NORMALIZATION
# ═══════════════════════════════════════════════════════════════

_NORMALIZE_RE = re.compile(r'[^\d.+\-a-zA-Z]')
_STRIP_PREFIX_RE = re.compile(r'^[vV]')


def normalize_version_string(raw: str) -> str:
    """
    Normalize a raw version string into a canonical form.

    - Strips leading 'v'/'V'
    - Removes surrounding whitespace
    - Removes characters that are clearly not version components
    - Preserves semver pre-release/build suffixes

    Examples:
        'v8.21.0'      → '8.21.0'
        '  7.12 '      → '7.12'
        'v3.6.0-beta'  → '3.6.0-beta'
        '2.9.7+build1' → '2.9.7+build1'
    """
    cleaned = raw.strip()
    cleaned = _STRIP_PREFIX_RE.sub('', cleaned)
    cleaned = _NORMALIZE_RE.sub('', cleaned)
    return cleaned


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    """
    Parse a version string into a numeric tuple for comparison.

    Only the numeric prefix is parsed; pre-release/build suffixes
    are ignored for ordering purposes.

    '8.21.0'      → (8, 21, 0)
    '3.6.0-beta'  → (3, 6, 0)
    '7.12'        → (7, 12)
    """
    # Strip anything after a hyphen or plus (pre-release/build)
    numeric_part = re.split(r'[-+]', version, maxsplit=1)[0]
    parts: list[int] = []
    for segment in numeric_part.split('.'):
        try:
            parts.append(int(segment))
        except ValueError:
            break
    return tuple(parts) if parts else (0,)


# ═══════════════════════════════════════════════════════════════
# VERSION DETECTION ENGINE
# ═══════════════════════════════════════════════════════════════

def detect_version(
    text: str,
    patterns: Sequence[PlayerVersionPattern],
) -> VersionDetectionResult:
    """
    Detect the best version string from text using provided patterns.

    Strategy:
    1. Run all patterns against the text.
    2. Collect all matches with their source metadata.
    3. Select the best match using priority rules:
       a. Prefer 3-part versions (X.Y.Z) over 2-part (X.Y).
       b. Prefer higher version numbers (more likely to be current).
       c. Prefer matches from higher-priority sources
          (script_src > inline_js > comment).
    4. Normalize the selected version.
    5. Return a VersionDetectionResult.

    Parameters
    ----------
    text : str
        HTML/JS content to scan for version strings.
    patterns : Sequence[PlayerVersionPattern]
        Ordered list of version extraction patterns to try.

    Returns
    -------
    VersionDetectionResult
        Structured result; check `.detected` to see if a version was found.
    """
    if not patterns or not text:
        return VersionDetectionResult()

    candidates: list[_VersionCandidate] = []

    for pat in patterns:
        raw_version = pat.extract(text)
        if raw_version:
            normalized = normalize_version_string(raw_version)
            if _is_plausible_version(normalized):
                candidates.append(_VersionCandidate(
                    raw=raw_version,
                    normalized=normalized,
                    source=pat.source,
                    description=pat.description,
                    parsed=_parse_version_tuple(normalized),
                    source_priority=_SOURCE_PRIORITY.get(pat.source, 50),
                ))

    if not candidates:
        return VersionDetectionResult()

    # Sort: higher source priority first, then 3-part over 2-part,
    # then higher version number
    candidates.sort(key=_version_sort_key, reverse=True)
    best = candidates[0]

    return VersionDetectionResult(
        version=best.normalized,
        raw_match=best.raw,
        source=best.source,
        pattern_description=best.description,
        normalized=best.normalized,
    )


def detect_all_versions(
    text: str,
    patterns: Sequence[PlayerVersionPattern],
) -> list[VersionDetectionResult]:
    """
    Detect all version strings found by all patterns.

    Returns a list of VersionDetectionResult objects sorted by
    priority (best first). Useful for debugging or when multiple
    version references exist in the same page.
    """
    if not patterns or not text:
        return []

    results: list[VersionDetectionResult] = []
    seen: set[str] = set()

    for pat in patterns:
        raw_version = pat.extract(text)
        if raw_version:
            normalized = normalize_version_string(raw_version)
            if _is_plausible_version(normalized) and normalized not in seen:
                seen.add(normalized)
                results.append(VersionDetectionResult(
                    version=normalized,
                    raw_match=raw_version,
                    source=pat.source,
                    pattern_description=pat.description,
                    normalized=normalized,
                ))

    # Sort by source priority then version magnitude
    results.sort(
        key=lambda r: (
            _SOURCE_PRIORITY.get(r.source, 50),
            len(r.normalized.split('.')) if r.normalized else 0,
            _parse_version_tuple(r.normalized) if r.normalized else (0,),
        ),
        reverse=True,
    )
    return results


# ═══════════════════════════════════════════════════════════════
# INTERNAL HELPERS
# ═══════════════════════════════════════════════════════════════

class _VersionCandidate:
    """Internal intermediate representation for version sorting."""
    __slots__ = ('raw', 'normalized', 'source', 'description',
                 'parsed', 'source_priority')

    def __init__(
        self,
        raw: str,
        normalized: str,
        source: str,
        description: str,
        parsed: tuple[int, ...],
        source_priority: int,
    ) -> None:
        self.raw = raw
        self.normalized = normalized
        self.source = source
        self.description = description
        self.parsed = parsed
        self.source_priority = source_priority


# Source priority ranking (higher = more trustworthy)
_SOURCE_PRIORITY: dict[str, int] = {
    "script_src": 90,
    "inline_js": 70,
    "comment": 50,
    "css": 30,
    "unknown": 10,
}


def _version_sort_key(c: _VersionCandidate) -> tuple[int, int, tuple[int, ...]]:
    """Sort key: (source_priority, part_count, version_tuple)."""
    return (
        c.source_priority,
        len(c.parsed),
        c.parsed,
    )


def _is_plausible_version(version: str) -> bool:
    """
    Filter out obviously invalid version strings.

    Rejects:
    - Empty strings
    - Versions with a major number > 999 (probably not a version)
    - Versions with only zeroes
    - Single-segment strings (just '3' is not a version)
    """
    if not version:
        return False
    parts = version.split('.')
    if len(parts) < 2:
        return False
    try:
        major = int(re.split(r'[-+]', parts[0], maxsplit=1)[0])
        if major > 999:
            return False
        # All zeroes check
        numeric_parts = [
            int(re.split(r'[-+]', p, maxsplit=1)[0])
            for p in parts
            if re.split(r'[-+]', p, maxsplit=1)[0].isdigit()
        ]
        if all(n == 0 for n in numeric_parts):
            return False
    except (ValueError, IndexError):
        return False
    return True