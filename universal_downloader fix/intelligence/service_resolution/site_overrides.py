"""
intelligence.service_resolution.site_overrides
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Per-site label → service override management.

Site overrides allow specific sites to declare that a label always resolves
to a particular service on their pages, regardless of the label's global
ambiguity.  This is the primary mechanism for handling sites that use
non-standard or conflicting label conventions.

Overrides integrate into the scoring pipeline as strong positive evidence,
rather than bypassing candidate ranking entirely.  This keeps the system
transparent and debuggable.
"""

from __future__ import annotations

import threading
from typing import Any, Optional, Sequence

from .models import AliasStrength, SiteOverrideEntry
from .normalization import quick_normalize


# ═══════════════════════════════════════════════════════════════════════════════
# SiteOverrideManager
# ═══════════════════════════════════════════════════════════════════════════════


class SiteOverrideManager:
    """
    Registry for per-site label override mappings.

    Internal structure
    ------------------
    ``_overrides[site_id][normalized_label] → SiteOverrideEntry``

    A site can have overrides for any number of labels.  Each
    ``(site_id, normalized_label)`` pair maps to exactly one override.
    Registering a second override for the same pair replaces the first.
    """

    __slots__ = ("_overrides", "_lock")

    def __init__(self) -> None:
        self._overrides: dict[str, dict[str, SiteOverrideEntry]] = {}
        self._lock = threading.Lock()

    # ── Registration ────────────────────────────────────────────────────────

    def register_override(self, entry: SiteOverrideEntry) -> None:
        """
        Register a single override entry.

        If an override for the same ``(site_id, normalized_label)`` already
        exists, it is silently replaced (last-write-wins).
        """
        with self._lock:
            site_map = self._overrides.setdefault(entry.site_id, {})
            site_map[entry.normalized_label] = entry

    def register_overrides_bulk(
        self,
        site_id: str,
        mapping: dict[str, str],
        *,
        strength: AliasStrength = AliasStrength.EXACT,
        notes: Optional[str] = None,
    ) -> None:
        """
        Register multiple overrides for a single site in compact form.

        Parameters
        ----------
        site_id:
            The site identifier (e.g., ``'aniworld.to'``).
        mapping:
            ``{raw_label: target_service_id}`` pairs.
            Labels are normalized automatically.
        strength:
            Override strength applied to all entries.
        notes:
            Optional notes applied to all entries.
        """
        for raw_label, service_id in mapping.items():
            entry = SiteOverrideEntry(
                site_id=site_id,
                normalized_label=quick_normalize(raw_label),
                service_id=service_id,
                strength=strength,
                notes=notes,
            )
            self.register_override(entry)

    def register_site_profile(
        self,
        site_id: str,
        overrides: Sequence[
            tuple[str, str]
            | tuple[str, str, AliasStrength]
            | tuple[str, str, AliasStrength, str]
        ],
    ) -> None:
        """
        Register a complete site profile with per-entry strength control.

        Each entry is:
        - ``(raw_label, service_id)``
        - ``(raw_label, service_id, strength)``
        - ``(raw_label, service_id, strength, notes)``

        Entries with 2 elements default to ``AliasStrength.EXACT``.
        """
        for entry_tuple in overrides:
            raw_label = entry_tuple[0]
            service_id = entry_tuple[1]
            strength = entry_tuple[2] if len(entry_tuple) > 2 else AliasStrength.EXACT  # type: ignore[misc]
            entry_notes = entry_tuple[3] if len(entry_tuple) > 3 else None  # type: ignore[misc]

            entry = SiteOverrideEntry(
                site_id=site_id,
                normalized_label=quick_normalize(raw_label),
                service_id=service_id,
                strength=strength,
                notes=entry_notes,
            )
            self.register_override(entry)

    # ── Lookups ─────────────────────────────────────────────────────────────

    def lookup(
        self, site_id: str, normalized_label: str
    ) -> Optional[SiteOverrideEntry]:
        """
        Look up an override for a specific site and normalized label.

        Returns ``None`` if no override exists.
        """
        site_map = self._overrides.get(site_id)
        if site_map is None:
            return None
        return site_map.get(normalized_label)

    def has_overrides(self, site_id: str) -> bool:
        """Return ``True`` if *site_id* has any registered overrides."""
        return site_id in self._overrides and len(self._overrides[site_id]) > 0

    def list_sites(self) -> list[str]:
        """Return all site IDs that have registered overrides, sorted."""
        return sorted(self._overrides.keys())

    def list_overrides_for_site(
        self, site_id: str
    ) -> list[SiteOverrideEntry]:
        """Return all override entries for a site, sorted by label."""
        site_map = self._overrides.get(site_id, {})
        return sorted(site_map.values(), key=lambda e: e.normalized_label)

    def get_all_overrides(self) -> dict[str, list[SiteOverrideEntry]]:
        """Return all overrides, keyed by site_id."""
        return {
            site_id: sorted(entries.values(), key=lambda e: e.normalized_label)
            for site_id, entries in self._overrides.items()
        }

    @property
    def site_count(self) -> int:
        return len(self._overrides)

    @property
    def total_override_count(self) -> int:
        return sum(len(m) for m in self._overrides.values())

    def __repr__(self) -> str:
        return (
            f"<SiteOverrideManager sites={self.site_count} "
            f"overrides={self.total_override_count}>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Default site override data
# ═══════════════════════════════════════════════════════════════════════════════
#
# ADAPTATION POINT: Add new site profiles here as new scraping targets are
# onboarded.  Each profile maps the labels used on that specific site to
# the correct canonical service IDs.
#
# Format: list of (site_id, overrides_list)
# Override entry: (raw_label, service_id[, strength[, notes]])
# ═══════════════════════════════════════════════════════════════════════════════

_S = AliasStrength  # shorthand

_DEFAULT_SITE_PROFILES: list[tuple[str, list[Any]]] = [
    # ── aniworld.to (German anime streaming) ────────────────────────────
    (
        "aniworld.to",
        [
            ("ST", "streamtape"),
            ("VOE", "voe"),
            ("DD", "doodstream"),
            ("US", "upstream", _S.EXACT, "On aniworld, US always means Upstream"),
            ("UQ", "upstream"),
            ("SB", "streamsb"),
            ("FM", "filemoon"),
            ("MD", "mixdrop"),
            ("SW", "streamwish", _S.EXACT, "On aniworld, SW always means StreamWish"),
            ("MC", "mycloud", _S.EXACT, "On aniworld, MC always means MyCloud"),
            ("TB", "turbovideos", _S.EXACT, "On aniworld, TB always means TurboVideos"),
            ("TV", "turbovideos"),
            ("FL", "filelions"),
            ("FLI", "filelions"),
            ("VK", "vk_video"),
            ("OK", "ok_ru"),
            ("MP4", "mp4upload"),
            ("FST", "faststream"),
        ],
    ),
    # ── serienstream.to (German series streaming) ───────────────────────
    (
        "serienstream.to",
        [
            ("ST", "streamtape"),
            ("VOE", "voe"),
            ("DD", "doodstream"),
            ("US", "upstream", _S.EXACT, "On serienstream, US = Upstream"),
            ("SB", "streamsb"),
            ("FM", "filemoon"),
            ("MD", "mixdrop"),
            ("MC", "megacloud", _S.EXACT, "On serienstream, MC = MegaCloud"),
            ("VK", "vk_video"),
        ],
    ),
    # ── movie4k.st (multi-language movie site) ──────────────────────────
    (
        "movie4k.st",
        [
            ("Streamtape", "streamtape"),
            ("Voe", "voe"),
            ("DoodStream", "doodstream"),
            ("MixDrop", "mixdrop"),
            ("Upstream", "upstream"),
            ("FileMoon", "filemoon"),
            ("StreamWish", "streamwish"),
            ("SuperVideo", "supervideo"),
            ("Vidoza", "vidoza"),
        ],
    ),
    # ── megakino.co (German movie streaming) ────────────────────────────
    (
        "megakino.co",
        [
            ("ST", "streamtape"),
            ("VOE", "voe"),
            ("SW", "swiftplayers", _S.EXACT, "On megakino, SW = SwiftPlayers"),
            ("MC", "megacloud", _S.EXACT, "On megakino, MC = MegaCloud"),
        ],
    ),
]


# ═══════════════════════════════════════════════════════════════════════════════
# Default override manager builder
# ═══════════════════════════════════════════════════════════════════════════════


def _build_default_override_manager() -> SiteOverrideManager:
    """
    Construct a fully-populated ``SiteOverrideManager`` from built-in
    site profiles.
    """
    manager = SiteOverrideManager()
    for site_id, overrides in _DEFAULT_SITE_PROFILES:
        manager.register_site_profile(site_id, overrides)
    return manager


# ── Module-level singleton ──────────────────────────────────────────────────

_default_manager: Optional[SiteOverrideManager] = None
_manager_lock = threading.Lock()


def get_default_override_manager() -> SiteOverrideManager:
    """
    Return the module-level default ``SiteOverrideManager``.

    Lazily built on first call.  Thread-safe.

    ADAPTATION POINT: If the host project wants to inject a custom manager
    (e.g., loaded from YAML config or a database), replace this function
    or call ``set_default_override_manager()`` before first access.
    """
    global _default_manager
    if _default_manager is None:
        with _manager_lock:
            if _default_manager is None:
                _default_manager = _build_default_override_manager()
    return _default_manager


def set_default_override_manager(manager: SiteOverrideManager) -> None:
    """
    Replace the module-level default override manager.

    Intended for testing or host-project customization.
    """
    global _default_manager
    with _manager_lock:
        _default_manager = manager


def reset_default_override_manager() -> None:
    """
    Clear the cached default manager so it will be rebuilt on next access.

    Primarily for testing.
    """
    global _default_manager
    with _manager_lock:
        _default_manager = None