"""Advanced extractor registry with resolution diagnostics."""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, List, Optional, Sequence, Type

from .registry_models import (
    ExtractorMetadata,
    ExtractorResolutionCandidate,
    ExtractorResolutionResult,
    ExtractorSource,
    RegistryConflict,
    RegistryDecision,
    ResolutionReason,
    default_priority_for_source,
    source_trust_rank,
)

logger = logging.getLogger(__name__)


def _as_source(value: ExtractorSource | str | None) -> ExtractorSource:
    if isinstance(value, ExtractorSource):
        return value
    if isinstance(value, str):
        try:
            return ExtractorSource(value.lower().strip())
        except ValueError:
            pass
    return ExtractorSource.BUILTIN


class ExtractorRegistry:
    """Registry for builtin, upgraded, and plugin extractor classes."""

    def __init__(self) -> None:
        self._entries: dict[str, ExtractorMetadata] = {}
        self._conflicts: list[RegistryConflict] = []
        self._replace_index: dict[str, str] = {}
        self._registration_order: int = 0

    def _next_order(self) -> int:
        self._registration_order += 1
        return self._registration_order

    def _derive_name(self, cls: Type[Any]) -> str:
        name = getattr(cls, "EXTRACTOR_NAME", None) or getattr(cls, "__name__", "")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Extractor class {cls!r} has no valid EXTRACTOR_NAME")
        return name.lower().strip()

    def _build_metadata(
        self,
        cls: Type[Any],
        *,
        generic: bool = False,
        source: ExtractorSource | str | None = None,
        priority: int | None = None,
        replaces: Sequence[str] | None = None,
        enabled: bool | None = None,
        module_path: str | None = None,
        version: str | None = None,
    ) -> ExtractorMetadata:
        source_value = _as_source(source)
        name = self._derive_name(cls)
        class_replaces = list(getattr(cls, "REPLACES", []) or [])
        combined_replaces = list(replaces or class_replaces)
        normalized_replaces = [item.lower().strip() for item in combined_replaces if str(item).strip()]
        module_name = module_path or getattr(cls, "__module__", "") or ""
        class_priority = getattr(cls, "PRIORITY", priority or default_priority_for_source(source_value))

        return ExtractorMetadata(
            name=name,
            cls=cls,
            source=source_value,
            priority=int(class_priority),
            is_generic=bool(generic or getattr(cls, "IS_GENERIC", False)),
            replaces=normalized_replaces,
            enabled=bool(getattr(cls, "ENABLED", True) if enabled is None else enabled),
            module_path=str(module_name),
            version=version or getattr(cls, "EXTRACTOR_VERSION", None),
            registered_at=self._next_order(),
        )

    def register(
        self,
        cls: Type[Any],
        *,
        generic: bool = False,
        source: ExtractorSource | str | None = None,
        priority: int | None = None,
        replaces: Sequence[str] | None = None,
        enabled: bool | None = None,
        module_path: str | None = None,
        version: str | None = None,
    ) -> ExtractorMetadata:
        metadata = self._build_metadata(
            cls,
            generic=generic,
            source=source,
            priority=priority,
            replaces=replaces,
            enabled=enabled,
            module_path=module_path,
            version=version,
        )
        return self.register_metadata(metadata)

    def register_metadata(self, metadata: ExtractorMetadata) -> ExtractorMetadata:
        challenger = metadata
        incumbent = self._entries.get(challenger.name)

        if incumbent is None:
            self._entries[challenger.name] = challenger
            logger.debug("Registered extractor: %s (%s)", challenger.name, challenger.cls.__name__)
        else:
            winner = self._resolve_registration_conflict(incumbent, challenger)
            challenger = winner

        if challenger.replaces:
            self._apply_replacements(challenger)

        return challenger

    def _apply_replacements(self, metadata: ExtractorMetadata) -> None:
        for replaced_name in metadata.replaces:
            if replaced_name == metadata.name:
                self._replace_index[replaced_name] = metadata.name
                continue

            removed = self._entries.pop(replaced_name, None)
            self._replace_index[replaced_name] = metadata.name
            if removed is not None:
                logger.info(
                    "Extractor '%s' replaced by '%s' via explicit REPLACES declaration",
                    replaced_name,
                    metadata.name,
                )

    def _resolve_registration_conflict(
        self,
        incumbent: ExtractorMetadata,
        challenger: ExtractorMetadata,
    ) -> ExtractorMetadata:
        decision: RegistryDecision
        reason: str
        winner: ExtractorMetadata

        if incumbent.cls is challenger.cls:
            decision = RegistryDecision.REJECTED
            reason = "Same class already registered"
            winner = incumbent
        elif incumbent.name in challenger.replaces or challenger.name in challenger.replaces:
            decision = RegistryDecision.REPLACED
            reason = f"Challenger '{challenger.name}' declares it replaces '{incumbent.name}'"
            winner = challenger
        elif source_trust_rank(challenger.source) > source_trust_rank(incumbent.source):
            decision = RegistryDecision.OVERRIDDEN
            reason = (
                f"Challenger source '{challenger.source.value}' outranks "
                f"incumbent source '{incumbent.source.value}'"
            )
            winner = challenger
        elif source_trust_rank(challenger.source) < source_trust_rank(incumbent.source):
            decision = RegistryDecision.REJECTED
            reason = (
                f"Incumbent source '{incumbent.source.value}' outranks "
                f"challenger source '{challenger.source.value}'"
            )
            winner = incumbent
        elif challenger.priority > incumbent.priority:
            decision = RegistryDecision.OVERRIDDEN
            reason = f"Challenger priority {challenger.priority} > incumbent priority {incumbent.priority}"
            winner = challenger
        elif challenger.priority < incumbent.priority:
            decision = RegistryDecision.REJECTED
            reason = f"Incumbent priority {incumbent.priority} > challenger priority {challenger.priority}"
            winner = incumbent
        else:
            decision = RegistryDecision.REJECTED
            reason = "Same source rank and priority; incumbent registered first stays"
            winner = incumbent

        conflict = RegistryConflict(
            name=challenger.name,
            incumbent=incumbent,
            challenger=challenger,
            decision=decision,
            reason=reason,
        )
        self._conflicts.append(conflict)

        if winner is challenger:
            self._entries[challenger.name] = challenger

        logger.info("Registration conflict on '%s': %s - %s", challenger.name, decision.value, reason)
        return winner

    def unregister(self, name: str) -> ExtractorMetadata | None:
        key = name.lower().strip()
        removed = self._entries.pop(key, None)
        if removed is not None:
            self._replace_index = {k: v for k, v in self._replace_index.items() if v != key}
        return removed

    def replace(self, old_name: str, new_cls: Type[Any], **overrides: Any) -> ExtractorMetadata:
        old_key = old_name.lower().strip()
        replaces = list(overrides.pop("replaces", []) or [])
        if old_key not in [item.lower() for item in replaces]:
            replaces.append(old_key)
        self.unregister(old_key)
        return self.register(new_cls, replaces=replaces, **overrides)

    def get(self, name: str) -> ExtractorMetadata | None:
        return self._entries.get(name.lower().strip())

    def has(self, name: str) -> bool:
        return name.lower().strip() in self._entries

    def is_replaced(self, name: str) -> bool:
        return name.lower().strip() in self._replace_index

    def replaced_by(self, name: str) -> str | None:
        return self._replace_index.get(name.lower().strip())

    def list_all(
        self,
        *,
        include_disabled: bool = False,
        source: ExtractorSource | str | None = None,
    ) -> list[ExtractorMetadata]:
        entries = list(self._entries.values())
        if not include_disabled:
            entries = [entry for entry in entries if entry.enabled]

        source_value = _as_source(source) if source is not None else None
        if source_value is not None:
            entries = [entry for entry in entries if entry.source == source_value]

        entries.sort(key=lambda item: item.sort_key)
        return entries

    def get_all(self, include_disabled: bool = False) -> list[type]:
        return [entry.cls for entry in self.list_all(include_disabled=include_disabled)]

    @property
    def conflicts(self) -> list[RegistryConflict]:
        return list(self._conflicts)

    @property
    def size(self) -> int:
        return len(self._entries)

    @property
    def enabled_count(self) -> int:
        return sum(1 for entry in self._entries.values() if entry.enabled)

    def __len__(self) -> int:
        return self.size

    def reset(self) -> None:
        self._entries.clear()
        self._conflicts.clear()
        self._replace_index.clear()
        self._registration_order = 0

    def _call_matcher(self, func: Callable[..., Any], *args: Any) -> bool:
        try:
            return bool(func(*args))
        except TypeError:
            return False
        except Exception:
            return False

    def _compute_confidence(self, cls: Type[Any], url: str, matched: bool) -> float:
        if not matched:
            return 0.0
        scorer = getattr(cls, "match_confidence", None)
        if callable(scorer):
            try:
                raw_score = scorer(url)
                if isinstance(raw_score, str):
                    score = float(raw_score)
                elif isinstance(raw_score, (int, float)):
                    score = float(raw_score)
                else:
                    return 0.0
            except Exception:
                return 0.0
            return max(0.0, min(1.0, score))
        return 0.0

    def _evaluate_candidate(
        self,
        metadata: ExtractorMetadata,
        url: str,
        html: str | None,
    ) -> ExtractorResolutionCandidate:
        cls = metadata.cls
        matched = False
        can_handle = getattr(cls, "can_handle", None)
        suitable = getattr(cls, "suitable", None)

        if callable(suitable):
            matched = self._call_matcher(suitable, url)

        if not matched and html and callable(can_handle):
            matched = self._call_matcher(can_handle, html)

        if not matched and callable(can_handle):
            matched = self._call_matcher(can_handle, url)

        candidate = ExtractorResolutionCandidate(
            metadata=metadata,
            can_handle=matched,
            match_confidence=self._compute_confidence(cls, url, matched),
        )

        if not matched:
            candidate.eliminated = True
            candidate.elimination_reason = "did not match URL or HTML context"

        return candidate

    def _winner_reason(
        self,
        winner: ExtractorResolutionCandidate,
        runner_up: ExtractorResolutionCandidate | None,
        *,
        generic_fallback: bool = False,
    ) -> ResolutionReason:
        if generic_fallback:
            return ResolutionReason.GENERIC_FALLBACK
        if runner_up is None:
            return ResolutionReason.SOLE_CANDIDATE
        if winner.effective_confidence > runner_up.effective_confidence:
            return ResolutionReason.HIGHER_CONFIDENCE
        if winner.metadata.priority > runner_up.metadata.priority:
            return ResolutionReason.HIGHER_PRIORITY
        if source_trust_rank(winner.metadata.source) > source_trust_rank(runner_up.metadata.source):
            return ResolutionReason.SOURCE_RANK
        return ResolutionReason.DETERMINISTIC_TIEBREAK

    def _sort_candidates(self, candidates: Iterable[ExtractorResolutionCandidate]) -> list[ExtractorResolutionCandidate]:
        return sorted(
            candidates,
            key=lambda item: (
                -item.effective_confidence,
                -item.metadata.priority,
                -source_trust_rank(item.metadata.source),
                item.metadata.registered_at,
                item.metadata.name,
            ),
        )

    def resolve(self, url: str, html: str | None = None) -> ExtractorResolutionResult:
        all_candidates = [self._evaluate_candidate(entry, url, html) for entry in self.list_all()]
        matched_specific = [item for item in all_candidates if item.can_handle and not item.metadata.is_generic]
        matched_generic = [item for item in all_candidates if item.can_handle and item.metadata.is_generic]

        result = ExtractorResolutionResult(url=url, candidates=all_candidates)

        if matched_specific:
            ranked = self._sort_candidates(matched_specific)
            winner = ranked[0]
            runner_up = ranked[1] if len(ranked) > 1 else None
            result.winner = winner.metadata
            result.reason = self._winner_reason(winner, runner_up)
        elif matched_generic:
            ranked = self._sort_candidates(matched_generic)
            result.winner = ranked[0].metadata
            result.reason = ResolutionReason.GENERIC_FALLBACK
        else:
            result.reason = ResolutionReason.NO_MATCH

        result.explanation = self.debug_resolution(url, html=html, result=result)
        return result

    def find_extractor(self, url: str, html: str | None = None) -> type | None:
        return self.resolve(url, html=html).winner_cls

    def summary(self) -> str:
        lines = [
            "=== Extractor Registry Summary ===",
            f"Total registered : {self.size}",
            f"Enabled          : {self.enabled_count}",
            f"Conflicts logged : {len(self._conflicts)}",
            f"Replacements     : {len(self._replace_index)}",
            "",
            "--- Registered Extractors ---",
        ]
        for meta in self.list_all(include_disabled=True):
            marker = "[GENERIC] " if meta.is_generic else ""
            lines.append(
                f"  {marker}{meta.name}: source={meta.source.value} priority={meta.priority} cls={meta.cls.__name__}"
            )
        if self._replace_index:
            lines.append("")
            lines.append("--- Replace Index ---")
            for old_name, new_name in sorted(self._replace_index.items()):
                lines.append(f"  {old_name} -> {new_name}")
        if self._conflicts:
            lines.append("")
            lines.append("--- Conflicts ---")
            for conflict in self._conflicts:
                lines.append(f"  {conflict.name}: {conflict.decision.value} - {conflict.reason}")
        lines.append("=================================")
        return "\n".join(lines)

    def debug_resolution(
        self,
        url: str,
        *,
        html: str | None = None,
        result: ExtractorResolutionResult | None = None,
    ) -> str:
        if result is None:
            result = self.resolve(url, html=html)

        lines = [
            "=== EXTRACTOR RESOLUTION REPORT ===",
            f"URL: {url}",
            f"WINNER: {result.winner_name or 'none'}",
            f"REASON: {result.reason.value if result.reason else 'unknown'}",
            f"CANDIDATES EVALUATED: {len(result.candidates)}",
            "",
            "-- Candidate Details --",
        ]

        for candidate in self._sort_candidates(result.candidates):
            status = "MATCH" if candidate.can_handle else "SKIP"
            lines.append(
                f"[{status}] {candidate.metadata.name} "
                f"source={candidate.metadata.source.value} "
                f"priority={candidate.metadata.priority} "
                f"confidence={candidate.effective_confidence:.2f} "
                f"generic={candidate.metadata.is_generic}"
            )
            if candidate.elimination_reason:
                lines.append(f"      note: {candidate.elimination_reason}")

        if result.winner is not None:
            lines.append("")
            lines.append(
                f"Selected class: {result.winner.cls.__module__}.{result.winner.cls.__name__}"
            )

        lines.append("===================================")
        return "\n".join(lines)


registry = ExtractorRegistry()


def register_extractor(
    generic: bool = False,
    **overrides: Any,
) -> Callable[[Type[Any]], Type[Any]]:
    """Decorator used by built-in extractors for auto-registration."""

    def decorator(cls: Type[Any]) -> Type[Any]:
        registry.register(cls, generic=generic, **overrides)
        return cls

    return decorator
