"""
intelligence.service_resolution.canonical_services
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The canonical service database and alias registry.

This module defines all known media hosting services, their identities,
their domains, and every known label alias that maps to them.  It builds
and exposes the lookup indices used by the resolver.

Maintainers: to add a new service or alias, edit ``_DEFAULT_SERVICES_DATA``
and ``_DEFAULT_ALIASES_DATA`` at the bottom of this file.  The registry
builds itself from those declarations at first access.
"""

from __future__ import annotations

import threading
from typing import Any, Optional, Sequence

from .models import AliasStrength, CanonicalService, LabelAlias
from .normalization import quick_normalize


# ═══════════════════════════════════════════════════════════════════════════════
# ServiceRegistry
# ═══════════════════════════════════════════════════════════════════════════════


class ServiceRegistry:
    """
    In-memory registry of canonical services and their label aliases.

    Indices
    -------
    - **service index**: ``service_id → CanonicalService``
    - **normalized alias index**: ``normalized_label → list[LabelAlias]``
    - **exact alias index**: ``raw_label (stripped) → list[LabelAlias]``
    - **domain index**: ``domain → service_id``

    Thread-safety: registration methods are protected by a lock.
    Lookup methods are lock-free (read-only after build).
    """

    __slots__ = (
        "_services",
        "_alias_index_normalized",
        "_alias_index_exact",
        "_domain_index",
        "_lock",
    )

    def __init__(self) -> None:
        self._services: dict[str, CanonicalService] = {}
        self._alias_index_normalized: dict[str, list[LabelAlias]] = {}
        self._alias_index_exact: dict[str, list[LabelAlias]] = {}
        self._domain_index: dict[str, str] = {}
        self._lock = threading.Lock()

    # ── Registration ────────────────────────────────────────────────────────

    def register_service(self, service: CanonicalService) -> None:
        """
        Register a canonical service.

        Raises ``ValueError`` if a service with the same ``service_id``
        is already registered.
        """
        with self._lock:
            if service.service_id in self._services:
                raise ValueError(
                    f"Service '{service.service_id}' is already registered"
                )
            self._services[service.service_id] = service
            for domain in service.domains:
                self._domain_index[domain.lower()] = service.service_id

    def register_alias(self, alias: LabelAlias) -> None:
        """
        Register a single label alias.

        The alias's ``service_id`` must reference a previously-registered
        service.
        """
        with self._lock:
            if alias.service_id not in self._services:
                raise ValueError(
                    f"Cannot register alias '{alias.raw_label}': "
                    f"service '{alias.service_id}' is not registered"
                )
            # Normalized index
            bucket = self._alias_index_normalized.setdefault(
                alias.normalized_label, []
            )
            bucket.append(alias)

            # Exact index (stripped but case-preserved)
            exact_key = alias.raw_label.strip()
            exact_bucket = self._alias_index_exact.setdefault(exact_key, [])
            exact_bucket.append(alias)

    def register_aliases_bulk(
        self,
        service_id: str,
        aliases: Sequence[tuple[str, AliasStrength] | tuple[str, AliasStrength, str]],
    ) -> None:
        """
        Register multiple aliases for a single service in compact form.

        Each entry is either:
        - ``(raw_label, strength)``
        - ``(raw_label, strength, notes)``

        The ``normalized_label`` is computed automatically.
        """
        for entry in aliases:
            if len(entry) == 3:
                raw, strength, notes = entry  # type: ignore[misc]
            else:
                raw, strength = entry[0], entry[1]
                notes = None

            alias = LabelAlias(
                raw_label=raw,
                normalized_label=quick_normalize(raw),
                service_id=service_id,
                strength=strength,
                notes=notes,
            )
            self.register_alias(alias)

    def register_service_with_aliases(
        self,
        service: CanonicalService,
        aliases: Sequence[tuple[str, AliasStrength] | tuple[str, AliasStrength, str]],
    ) -> None:
        """Convenience: register a service and all its aliases in one call."""
        self.register_service(service)
        self.register_aliases_bulk(service.service_id, aliases)

    # ── Lookups ─────────────────────────────────────────────────────────────

    def get_service(self, service_id: str) -> Optional[CanonicalService]:
        """Look up a service by its canonical ID."""
        return self._services.get(service_id)

    def lookup_aliases_normalized(
        self, normalized_label: str
    ) -> list[LabelAlias]:
        """
        Look up aliases by normalized label.  Returns an empty list if
        no aliases match.
        """
        return list(self._alias_index_normalized.get(normalized_label, []))

    def lookup_aliases_exact(self, raw_label: str) -> list[LabelAlias]:
        """
        Look up aliases by exact (case-sensitive, stripped) raw label.
        Returns an empty list if no aliases match.
        """
        key = raw_label.strip()
        return list(self._alias_index_exact.get(key, []))

    def get_service_by_domain(self, domain: str) -> Optional[CanonicalService]:
        """
        Reverse-lookup: find the service that owns *domain*.

        Performs an exact match against registered domains.  For substring
        matching against full URLs, use ``find_service_by_url``.
        """
        service_id = self._domain_index.get(domain.lower())
        if service_id:
            return self._services.get(service_id)
        return None

    def find_service_by_url(self, url: str) -> Optional[CanonicalService]:
        """
        Find a service whose registered domain appears anywhere in *url*.

        Checks all registered domains as substrings.  Returns the first
        match (longest domain matched first for accuracy).
        """
        lowered = url.lower()
        # Sort by domain length descending to prefer more specific matches
        for domain in sorted(self._domain_index, key=len, reverse=True):
            if domain in lowered:
                sid = self._domain_index[domain]
                return self._services.get(sid)
        return None

    def is_ambiguous(self, normalized_label: str) -> bool:
        """
        Return ``True`` if *normalized_label* maps to two or more
        distinct services.
        """
        aliases = self._alias_index_normalized.get(normalized_label, [])
        service_ids = {a.service_id for a in aliases}
        return len(service_ids) > 1

    def list_services(self) -> list[CanonicalService]:
        """Return all registered services, sorted by service_id."""
        return sorted(self._services.values(), key=lambda s: s.service_id)

    def list_ambiguous_labels(self) -> list[str]:
        """Return all normalized labels that map to 2+ services."""
        return sorted(
            label
            for label, aliases in self._alias_index_normalized.items()
            if len({a.service_id for a in aliases}) > 1
        )

    @property
    def service_count(self) -> int:
        return len(self._services)

    @property
    def alias_count(self) -> int:
        return sum(
            len(bucket) for bucket in self._alias_index_normalized.values()
        )

    def __contains__(self, service_id: str) -> bool:
        return service_id in self._services

    def __repr__(self) -> str:
        return (
            f"<ServiceRegistry services={self.service_count} "
            f"aliases={self.alias_count}>"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Default service & alias data
# ═══════════════════════════════════════════════════════════════════════════════
#
# ADAPTATION POINT: This is the central place to add, remove, or modify
# services and their aliases.  Each entry in _DEFAULT_SERVICES_DATA is a
# dict consumed by _build_default_registry().
#
# Alias tuples: (raw_label, AliasStrength[, optional_notes])
#
# When a label is known to collide with another service, mark it as
# AMBIGUOUS on *both* services so the resolver knows disambiguation
# is required.
# ═══════════════════════════════════════════════════════════════════════════════

_S = AliasStrength  # shorthand for readability in data tables

_DEFAULT_SERVICES_DATA: list[dict[str, Any]] = [
    # ── StreamTape ──────────────────────────────────────────────────────
    {
        "service_id": "streamtape",
        "display_name": "StreamTape",
        "domains": ("streamtape.com", "stape.fun", "streamtape.to", "streamtape.xyz"),
        "family": "streamtape",
        "metadata": {"active": True},
        "aliases": [
            ("StreamTape", _S.EXACT),
            ("Streamtape", _S.EXACT),
            ("streamtape", _S.EXACT),
            ("ST", _S.STRONG),
            ("Stape", _S.STRONG),
            ("STP", _S.MODERATE),
            ("Tape", _S.WEAK),
        ],
    },
    # ── Voe ─────────────────────────────────────────────────────────────
    {
        "service_id": "voe",
        "display_name": "Voe",
        "domains": ("voe.sx", "voeunblock.com", "voe-unblock.com", "voeunblk.com"),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("Voe", _S.EXACT),
            ("VOE", _S.EXACT),
            ("voe", _S.EXACT),
            ("VoeVideo", _S.STRONG),
            ("VoeUnblock", _S.STRONG),
        ],
    },
    # ── DoodStream ──────────────────────────────────────────────────────
    {
        "service_id": "doodstream",
        "display_name": "DoodStream",
        "domains": (
            "doodstream.com", "dood.to", "dood.so", "dood.watch",
            "dood.la", "dood.ws", "dood.pm", "dood.re",
            "d0000d.com", "d000d.com", "ds2play.com",
        ),
        "family": "dood",
        "metadata": {"active": True, "aka": ["Dood", "D0000d"]},
        "aliases": [
            ("DoodStream", _S.EXACT),
            ("Doodstream", _S.EXACT),
            ("doodstream", _S.EXACT),
            ("Dood", _S.STRONG),
            ("DD", _S.STRONG),
            ("D0000d", _S.STRONG, "Leet-speak variant"),
            ("D000d", _S.STRONG, "Leet-speak variant"),
            ("DoodS", _S.MODERATE),
            ("DS", _S.AMBIGUOUS, "Collides with DropStream and others"),
            ("DStream", _S.MODERATE),
        ],
    },
    # ── Dropload ────────────────────────────────────────────────────────
    {
        "service_id": "dropload",
        "display_name": "Dropload",
        "domains": ("dropload.io",),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("Dropload", _S.EXACT),
            ("dropload", _S.EXACT),
            ("DL", _S.MODERATE, "Could collide with Download abbreviations"),
            ("Drop", _S.WEAK),
        ],
    },
    # ── FileMoon ────────────────────────────────────────────────────────
    {
        "service_id": "filemoon",
        "display_name": "FileMoon",
        "domains": ("filemoon.sx", "filemoon.to", "filemoon.in"),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("FileMoon", _S.EXACT),
            ("Filemoon", _S.EXACT),
            ("filemoon", _S.EXACT),
            ("FM", _S.STRONG),
            ("FMoon", _S.STRONG),
            ("Moon", _S.WEAK),
        ],
    },
    # ── MixDrop ─────────────────────────────────────────────────────────
    {
        "service_id": "mixdrop",
        "display_name": "MixDrop",
        "domains": ("mixdrop.co", "mixdrop.to", "mixdrop.club", "mixdrp.co"),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("MixDrop", _S.EXACT),
            ("Mixdrop", _S.EXACT),
            ("mixdrop", _S.EXACT),
            ("MD", _S.STRONG),
            ("MixDrp", _S.MODERATE),
            ("Mix", _S.WEAK),
        ],
    },
    # ── Upstream ────────────────────────────────────────────────────────
    {
        "service_id": "upstream",
        "display_name": "Upstream",
        "domains": ("upstream.to",),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("Upstream", _S.EXACT),
            ("upstream", _S.EXACT),
            ("UQ", _S.STRONG, "Common on German anime sites"),
            ("US", _S.AMBIGUOUS, "Collides with UserLoad and others"),
            ("UP", _S.MODERATE),
        ],
    },
    # ── StreamWish ──────────────────────────────────────────────────────
    {
        "service_id": "streamwish",
        "display_name": "StreamWish",
        "domains": (
            "streamwish.com", "streamwish.to",
            "wishembed.pro", "embedwish.com",
        ),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("StreamWish", _S.EXACT),
            ("Streamwish", _S.EXACT),
            ("streamwish", _S.EXACT),
            ("SW", _S.AMBIGUOUS, "Collides with SwiftPlayers"),
            ("SWish", _S.STRONG),
            ("Wish", _S.WEAK),
        ],
    },
    # ── SuperVideo ──────────────────────────────────────────────────────
    {
        "service_id": "supervideo",
        "display_name": "SuperVideo",
        "domains": ("supervideo.tv",),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("SuperVideo", _S.EXACT),
            ("Supervideo", _S.EXACT),
            ("supervideo", _S.EXACT),
            ("SV", _S.STRONG),
            ("SVideo", _S.MODERATE),
        ],
    },
    # ── StreamSB ────────────────────────────────────────────────────────
    {
        "service_id": "streamsb",
        "display_name": "StreamSB",
        "domains": (
            "streamsb.net", "sbembed.com", "sbplay.org",
            "sbvideo.net", "sbfull.com", "sblongvu.com",
        ),
        "family": "streamsb",
        "metadata": {"active": True, "aka": ["SBEmbed", "SBPlay"]},
        "aliases": [
            ("StreamSB", _S.EXACT),
            ("Streamsb", _S.EXACT),
            ("streamsb", _S.EXACT),
            ("SB", _S.STRONG),
            ("SBPlay", _S.STRONG),
            ("SBEmbed", _S.STRONG),
            ("SBVideo", _S.MODERATE),
        ],
    },
    # ── SwiftPlayers ────────────────────────────────────────────────────
    {
        "service_id": "swiftplayers",
        "display_name": "SwiftPlayers",
        "domains": ("swiftplayers.com",),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("SwiftPlayers", _S.EXACT),
            ("Swiftplayers", _S.EXACT),
            ("swiftplayers", _S.EXACT),
            ("Swift", _S.MODERATE),
            ("SW", _S.AMBIGUOUS, "Collides with StreamWish"),
            ("SWPlayers", _S.STRONG),
        ],
    },
    # ── TurboVideos ─────────────────────────────────────────────────────
    {
        "service_id": "turbovideos",
        "display_name": "TurboVideos",
        "domains": ("turbovideos.to",),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("TurboVideos", _S.EXACT),
            ("Turbovideos", _S.EXACT),
            ("turbovideos", _S.EXACT),
            ("TV", _S.AMBIGUOUS, "Collides with generic 'TV' usage"),
            ("TB", _S.AMBIGUOUS, "Collides with Tubely"),
            ("Turbo", _S.MODERATE),
            ("TVid", _S.MODERATE),
        ],
    },
    # ── Tubely ──────────────────────────────────────────────────────────
    {
        "service_id": "tubely",
        "display_name": "Tubely",
        "domains": ("tubely.com",),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("Tubely", _S.EXACT),
            ("tubely", _S.EXACT),
            ("TB", _S.AMBIGUOUS, "Collides with TurboVideos"),
            ("Tube", _S.WEAK, "Very generic"),
        ],
    },
    # ── MP4Upload ───────────────────────────────────────────────────────
    {
        "service_id": "mp4upload",
        "display_name": "MP4Upload",
        "domains": ("mp4upload.com",),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("MP4Upload", _S.EXACT),
            ("Mp4upload", _S.EXACT),
            ("mp4upload", _S.EXACT),
            ("MP4", _S.STRONG),
            ("MP4Up", _S.STRONG),
        ],
    },
    # ── VK Video ────────────────────────────────────────────────────────
    {
        "service_id": "vk_video",
        "display_name": "VK Video",
        "domains": ("vk.com", "vkvideo.ru"),
        "family": "vk",
        "metadata": {"active": True, "aka": ["VKontakte Video"]},
        "aliases": [
            ("VK Video", _S.EXACT),
            ("VKVideo", _S.EXACT),
            ("VKontakte", _S.STRONG),
            ("VK", _S.STRONG),
        ],
    },
    # ── OK.ru ───────────────────────────────────────────────────────────
    {
        "service_id": "ok_ru",
        "display_name": "OK.ru",
        "domains": ("ok.ru",),
        "family": None,
        "metadata": {"active": True, "aka": ["Odnoklassniki"]},
        "aliases": [
            ("OK.ru", _S.EXACT),
            ("OKru", _S.EXACT),
            ("OK", _S.STRONG),
            ("Odnoklassniki", _S.MODERATE),
        ],
    },
    # ── FileLions ───────────────────────────────────────────────────────
    {
        "service_id": "filelions",
        "display_name": "FileLions",
        "domains": ("filelions.to", "filelions.com", "filelions.live"),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("FileLions", _S.EXACT),
            ("Filelions", _S.EXACT),
            ("filelions", _S.EXACT),
            ("FL", _S.STRONG),
            ("FLI", _S.STRONG),
            ("Lions", _S.WEAK),
            ("DLions", _S.MODERATE, "Shortened variant"),
        ],
    },
    # ── FastStream ──────────────────────────────────────────────────────
    {
        "service_id": "faststream",
        "display_name": "FastStream",
        "domains": ("faststream.to",),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("FastStream", _S.EXACT),
            ("Faststream", _S.EXACT),
            ("faststream", _S.EXACT),
            ("FST", _S.STRONG),
            ("Fast", _S.WEAK),
        ],
    },
    # ── FlashX (deprecated) ────────────────────────────────────────────
    {
        "service_id": "flashx",
        "display_name": "FlashX",
        "domains": ("flashx.tv",),
        "family": None,
        "notes": "Service is offline / deprecated.  Kept for legacy label resolution.",
        "metadata": {"active": False},
        "aliases": [
            ("FlashX", _S.DEPRECATED),
            ("flashx", _S.DEPRECATED),
            ("FX", _S.DEPRECATED),
        ],
    },
    # ── MegaCloud ───────────────────────────────────────────────────────
    {
        "service_id": "megacloud",
        "display_name": "MegaCloud",
        "domains": ("megacloud.tv",),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("MegaCloud", _S.EXACT),
            ("Megacloud", _S.EXACT),
            ("megacloud", _S.EXACT),
            ("MC", _S.AMBIGUOUS, "Collides with MyCloud"),
            ("Mega", _S.WEAK, "Very generic"),
            ("MegaC", _S.MODERATE),
        ],
    },
    # ── MyCloud ─────────────────────────────────────────────────────────
    {
        "service_id": "mycloud",
        "display_name": "MyCloud",
        "domains": ("mycloud.to", "mcloud.to", "mcloud.bz"),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("MyCloud", _S.EXACT),
            ("Mycloud", _S.EXACT),
            ("mycloud", _S.EXACT),
            ("MC", _S.AMBIGUOUS, "Collides with MegaCloud"),
            ("MCloud", _S.STRONG),
        ],
    },
    # ── Vidoza ──────────────────────────────────────────────────────────
    {
        "service_id": "vidoza",
        "display_name": "Vidoza",
        "domains": ("vidoza.net", "vidoza.org"),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("Vidoza", _S.EXACT),
            ("vidoza", _S.EXACT),
            ("VZ", _S.MODERATE),
            ("Vdoza", _S.WEAK, "Observed typo"),
        ],
    },
    # ── VidMoly ─────────────────────────────────────────────────────────
    {
        "service_id": "vidmoly",
        "display_name": "VidMoly",
        "domains": ("vidmoly.to", "vidmoly.me"),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("VidMoly", _S.EXACT),
            ("Vidmoly", _S.EXACT),
            ("vidmoly", _S.EXACT),
            ("VM", _S.MODERATE),
        ],
    },
    # ── LuluStream ──────────────────────────────────────────────────────
    {
        "service_id": "lulustream",
        "display_name": "LuluStream",
        "domains": ("lulustream.com", "lulu-stream.com"),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("LuluStream", _S.EXACT),
            ("Lulustream", _S.EXACT),
            ("lulustream", _S.EXACT),
            ("Lulu", _S.MODERATE),
        ],
    },
    # ── WolfStream ──────────────────────────────────────────────────────
    {
        "service_id": "wolfstream",
        "display_name": "WolfStream",
        "domains": ("wolfstream.tv",),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("WolfStream", _S.EXACT),
            ("Wolfstream", _S.EXACT),
            ("wolfstream", _S.EXACT),
            ("Wolf", _S.MODERATE),
            ("WS", _S.WEAK, "Too generic for reliable resolution"),
        ],
    },
    # ── GogoStream ──────────────────────────────────────────────────────
    {
        "service_id": "gogostream",
        "display_name": "GogoStream",
        "domains": ("gogostream.com", "gogoanime.vc"),
        "family": None,
        "metadata": {"active": True},
        "aliases": [
            ("GogoStream", _S.EXACT),
            ("Gogostream", _S.EXACT),
            ("gogostream", _S.EXACT),
            ("Gogo", _S.MODERATE),
        ],
    },
]


# ═══════════════════════════════════════════════════════════════════════════════
# Registry builder
# ═══════════════════════════════════════════════════════════════════════════════


def _build_default_registry() -> ServiceRegistry:
    """
    Construct a fully-populated ``ServiceRegistry`` from the built-in
    service and alias data.

    This function is called once at first access via ``get_default_registry()``.
    """
    registry = ServiceRegistry()

    for entry in _DEFAULT_SERVICES_DATA:
        service = CanonicalService(
            service_id=entry["service_id"],
            display_name=entry["display_name"],
            domains=tuple(entry.get("domains", ())),
            family=entry.get("family"),
            notes=entry.get("notes"),
            metadata=dict(entry.get("metadata", {})),
        )
        aliases = entry.get("aliases", [])
        registry.register_service_with_aliases(service, aliases)

    return registry


# ── Module-level singleton ──────────────────────────────────────────────────

_default_registry: Optional[ServiceRegistry] = None
_registry_lock = threading.Lock()


def get_default_registry() -> ServiceRegistry:
    """
    Return the module-level default ``ServiceRegistry``.

    Lazily built on first call.  Thread-safe.

    ADAPTATION POINT: If the host project wants to inject a custom registry
    (e.g., loaded from config or extended with plugin services), replace
    this function or call ``set_default_registry()`` before first access.
    """
    global _default_registry
    if _default_registry is None:
        with _registry_lock:
            if _default_registry is None:
                _default_registry = _build_default_registry()
    return _default_registry


def set_default_registry(registry: ServiceRegistry) -> None:
    """
    Replace the module-level default registry.

    Intended for testing or host-project customization.
    """
    global _default_registry
    with _registry_lock:
        _default_registry = registry


def reset_default_registry() -> None:
    """
    Clear the cached default registry so it will be rebuilt on next access.

    Primarily for testing.
    """
    global _default_registry
    with _registry_lock:
        _default_registry = None