"""
resolution_pipeline/models.py

All data models for the integration pipeline.
These are pure data structures with no external dependencies.
Every downstream module in the package imports from here.

Gaps closed:
    G8 — trace/evidence model foundations
    G4 — report model foundations
    G3 — candidate/evidence model foundations
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum, unique
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ──────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────

@unique
class SignalSource(Enum):
    """Identifies the origin of a single piece of evidence."""
    DOMAIN = "domain"
    LABEL = "label"
    PLAYER = "player"
    IFRAME = "iframe"
    SCRIPT = "script"
    EMBED = "embed"
    SITE_HINT = "site_hint"
    USER_OVERRIDE = "user_override"
    LEGACY_FALLBACK = "legacy_fallback"
    CROSS_SERVER_BOOST = "cross_server_boost"
    CONFIG_PATTERN = "config_pattern"
    WRAPPER_DETECTION = "wrapper_detection"


@unique
class AmbiguityLevel(Enum):
    """Degree of ambiguity in the final resolution."""
    NONE = "none"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def from_spread(cls, spread: float, threshold: float) -> "AmbiguityLevel":
        """
        Derive ambiguity level from the score spread between
        rank-1 and rank-2 candidates relative to a threshold.

        spread = score_rank1 - score_rank2
        Lower spread → higher ambiguity.
        """
        if spread >= threshold * 3:
            return cls.NONE
        if spread >= threshold * 2:
            return cls.LOW
        if spread >= threshold:
            return cls.MODERATE
        if spread >= threshold * 0.5:
            return cls.HIGH
        return cls.CRITICAL


@unique
class ResolutionPath(Enum):
    """Which resolution path produced the decision."""
    V2_FULL = "v2_full"
    V2_PARTIAL = "v2_partial"
    LEGACY_FALLBACK = "legacy_fallback"
    HYBRID = "hybrid"


@unique
class TraceStepPhase(Enum):
    """Identifies which pipeline phase a trace step belongs to."""
    INPUT_NORMALIZATION = "input_normalization"
    STATIC_ANALYSIS = "static_analysis"
    LABEL_RESOLUTION = "label_resolution"
    PLAYER_DETECTION = "player_detection"
    EVIDENCE_MERGE = "evidence_merge"
    CROSS_SERVER_AGGREGATION = "cross_server_aggregation"
    DECISION = "decision"
    REPORT = "report"
    BOOTSTRAP = "bootstrap"
    FALLBACK = "fallback"


# ──────────────────────────────────────────────
# Evidence Models
# ──────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceItem:
    """
    Single piece of evidence contributing to service resolution.

    Attributes:
        source:      Which signal source produced this evidence.
        key:         A short machine-readable key (e.g. 'domain_match', 'label_alias').
        value:       The resolved or detected value (e.g. service name, domain).
        weight:      Relative weight of this evidence (0.0–1.0).
        confidence:  How confident the producing system is (0.0–1.0).
        raw:         The raw input that produced this evidence (for traceability).
        explanation: Human-readable explanation of why this evidence exists.
    """
    source: SignalSource
    key: str
    value: Any
    weight: float = 0.5
    confidence: float = 1.0
    raw: Optional[str] = None
    explanation: str = ""

    @property
    def effective_weight(self) -> float:
        """Weight adjusted by confidence."""
        return self.weight * self.confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source.value,
            "key": self.key,
            "value": self.value,
            "weight": self.weight,
            "confidence": self.confidence,
            "effective_weight": self.effective_weight,
            "raw": self.raw,
            "explanation": self.explanation,
        }


@dataclass
class EvidenceBundle:
    """
    Collection of EvidenceItem instances with grouping and filtering helpers.
    """
    items: List[EvidenceItem] = field(default_factory=list)

    def add(self, item: EvidenceItem) -> None:
        self.items.append(item)

    def add_all(self, items: Sequence[EvidenceItem]) -> None:
        self.items.extend(items)

    def merge(self, other: "EvidenceBundle") -> "EvidenceBundle":
        """Return a new bundle combining this and another bundle."""
        combined = EvidenceBundle(items=list(self.items) + list(other.items))
        return combined

    def by_source(self, source: SignalSource) -> List[EvidenceItem]:
        return [i for i in self.items if i.source == source]

    def by_key(self, key: str) -> List[EvidenceItem]:
        return [i for i in self.items if i.key == key]

    def by_service(self, service_name: str) -> "EvidenceBundle":
        """Return a sub-bundle containing only items whose value matches service_name."""
        return EvidenceBundle(
            items=[i for i in self.items if i.value == service_name]
        )

    def unique_services(self) -> List[str]:
        """Return deduplicated list of service names referenced in evidence values."""
        seen = []
        for item in self.items:
            if isinstance(item.value, str) and item.value not in seen:
                seen.append(item.value)
        return seen

    def total_effective_weight(self) -> float:
        return sum(i.effective_weight for i in self.items)

    def max_confidence(self) -> float:
        if not self.items:
            return 0.0
        return max(i.confidence for i in self.items)

    def sources_present(self) -> List[SignalSource]:
        """Return deduplicated list of signal sources present in this bundle."""
        seen = []
        for item in self.items:
            if item.source not in seen:
                seen.append(item.source)
        return seen

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def is_empty(self) -> bool:
        return len(self.items) == 0

    def to_dict_list(self) -> List[Dict[str, Any]]:
        return [i.to_dict() for i in self.items]

    def __repr__(self) -> str:
        return f"EvidenceBundle(count={self.count}, sources={[s.value for s in self.sources_present()]})"


# ──────────────────────────────────────────────
# Input Context
# ──────────────────────────────────────────────

@dataclass
class ServerEntry:
    """
    Represents a single server button/tab/option from the page.
    """
    label: str
    url: Optional[str] = None
    data_id: Optional[str] = None
    index: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RawInputContext:
    """
    Normalized container for all raw inputs to the resolution pipeline.

    This is assembled by the caller before invoking the pipeline.
    The pipeline never modifies this object.
    """
    page_html: str = ""
    page_url: str = ""
    site_domain: str = ""
    server_buttons: List[ServerEntry] = field(default_factory=list)
    iframe_urls: List[str] = field(default_factory=list)
    embed_codes: List[str] = field(default_factory=list)
    script_urls: List[str] = field(default_factory=list)
    user_hints: Dict[str, Any] = field(default_factory=dict)
    extra_metadata: Dict[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def has_servers(self) -> bool:
        return len(self.server_buttons) > 0

    @property
    def has_html(self) -> bool:
        return bool(self.page_html.strip())

    @property
    def has_iframes(self) -> bool:
        return len(self.iframe_urls) > 0

    @property
    def server_labels(self) -> List[str]:
        return [s.label for s in self.server_buttons]


# ──────────────────────────────────────────────
# Static Analysis Result
# ──────────────────────────────────────────────

@dataclass
class DomainSignal:
    """A domain extracted from some URL or embed with its context."""
    domain: str
    full_url: str
    source_type: str  # 'page', 'iframe', 'embed', 'script'
    inferred_service: Optional[str] = None
    confidence: float = 0.0


@dataclass
class StaticAnalysisResult:
    """
    Structured output from static analysis of raw page content.
    Replaces simplistic single-domain checks with multi-signal evidence.
    """
    domain_signals: List[DomainSignal] = field(default_factory=list)
    embed_signals: List[EvidenceItem] = field(default_factory=list)
    iframe_signals: List[EvidenceItem] = field(default_factory=list)
    script_signals: List[EvidenceItem] = field(default_factory=list)
    player_hints: List[EvidenceItem] = field(default_factory=list)
    evidence: EvidenceBundle = field(default_factory=EvidenceBundle)

    @property
    def has_domain_evidence(self) -> bool:
        return len(self.domain_signals) > 0

    @property
    def has_player_hints(self) -> bool:
        return len(self.player_hints) > 0

    @property
    def total_signals(self) -> int:
        return (
            len(self.domain_signals)
            + len(self.embed_signals)
            + len(self.iframe_signals)
            + len(self.script_signals)
            + len(self.player_hints)
        )

    def inferred_services(self) -> List[str]:
        """Collect all unique inferred service names from domain signals."""
        services = []
        for ds in self.domain_signals:
            if ds.inferred_service and ds.inferred_service not in services:
                services.append(ds.inferred_service)
        return services

    def to_evidence_bundle(self) -> EvidenceBundle:
        """Flatten all signals into a single EvidenceBundle."""
        bundle = EvidenceBundle()
        for ds in self.domain_signals:
            if ds.inferred_service:
                bundle.add(EvidenceItem(
                    source=SignalSource.DOMAIN,
                    key="domain_match",
                    value=ds.inferred_service,
                    weight=0.35,
                    confidence=ds.confidence,
                    raw=ds.full_url,
                    explanation=f"Domain '{ds.domain}' maps to service '{ds.inferred_service}'",
                ))
        bundle.add_all(self.embed_signals)
        bundle.add_all(self.iframe_signals)
        bundle.add_all(self.script_signals)
        bundle.add_all(self.player_hints)
        return bundle


# ──────────────────────────────────────────────
# Player Detection Evidence
# ──────────────────────────────────────────────

@dataclass
class PlayerDetectionEvidence:
    """
    Structured output from player/framework detection (Phase 3).
    Adapted into integration models here.
    """
    player_name: Optional[str] = None
    player_version: Optional[str] = None
    framework: Optional[str] = None
    config_extracted: Dict[str, Any] = field(default_factory=dict)
    wrapper_detected: Optional[str] = None
    confidence: float = 0.0
    evidence_items: List[EvidenceItem] = field(default_factory=list)

    @property
    def is_detected(self) -> bool:
        return self.player_name is not None

    @property
    def has_config(self) -> bool:
        return len(self.config_extracted) > 0

    @property
    def has_wrapper(self) -> bool:
        return self.wrapper_detected is not None

    def to_evidence_bundle(self) -> EvidenceBundle:
        bundle = EvidenceBundle(items=list(self.evidence_items))
        if self.player_name:
            bundle.add(EvidenceItem(
                source=SignalSource.PLAYER,
                key="player_detected",
                value=self.player_name,
                weight=0.20,
                confidence=self.confidence,
                raw=None,
                explanation=f"Player '{self.player_name}' detected"
                            + (f" v{self.player_version}" if self.player_version else ""),
            ))
        if self.wrapper_detected:
            bundle.add(EvidenceItem(
                source=SignalSource.WRAPPER_DETECTION,
                key="wrapper_detected",
                value=self.wrapper_detected,
                weight=0.15,
                confidence=self.confidence * 0.9,
                raw=None,
                explanation=f"Wrapper '{self.wrapper_detected}' detected around player",
            ))
        if self.config_extracted:
            for config_key, config_val in self.config_extracted.items():
                if isinstance(config_val, str) and config_val:
                    bundle.add(EvidenceItem(
                        source=SignalSource.CONFIG_PATTERN,
                        key=f"config_{config_key}",
                        value=config_val,
                        weight=0.10,
                        confidence=self.confidence * 0.8,
                        raw=str(config_val),
                        explanation=f"Config key '{config_key}' extracted from player setup",
                    ))
        return bundle

    def to_dict(self) -> Dict[str, Any]:
        return {
            "player_name": self.player_name,
            "player_version": self.player_version,
            "framework": self.framework,
            "config_extracted": self.config_extracted,
            "wrapper_detected": self.wrapper_detected,
            "confidence": self.confidence,
            "evidence_count": len(self.evidence_items),
        }


# ──────────────────────────────────────────────
# Ranked Candidates
# ─────���────────────────────────────────────────

@dataclass
class ScoreBreakdown:
    """
    Detailed breakdown of how a candidate's composite score was computed.
    """
    domain_score: float = 0.0
    label_score: float = 0.0
    player_score: float = 0.0
    iframe_score: float = 0.0
    site_hint_score: float = 0.0
    cross_server_boost: float = 0.0
    penalty: float = 0.0

    @property
    def raw_total(self) -> float:
        return (
            self.domain_score
            + self.label_score
            + self.player_score
            + self.iframe_score
            + self.site_hint_score
            + self.cross_server_boost
            - self.penalty
        )

    def to_dict(self) -> Dict[str, float]:
        return {
            "domain": self.domain_score,
            "label": self.label_score,
            "player": self.player_score,
            "iframe": self.iframe_score,
            "site_hint": self.site_hint_score,
            "cross_server_boost": self.cross_server_boost,
            "penalty": self.penalty,
            "raw_total": self.raw_total,
        }


@dataclass
class RankedServiceCandidate:
    """
    A single service candidate with composite score, rank, and evidence trail.
    """
    service_name: str
    composite_score: float = 0.0
    rank: int = 0
    evidence: EvidenceBundle = field(default_factory=EvidenceBundle)
    score_breakdown: ScoreBreakdown = field(default_factory=ScoreBreakdown)
    source_path: ResolutionPath = ResolutionPath.V2_FULL
    server_indices: List[int] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def evidence_count(self) -> int:
        return self.evidence.count

    @property
    def sources_used(self) -> List[SignalSource]:
        return self.evidence.sources_present()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_name": self.service_name,
            "composite_score": round(self.composite_score, 4),
            "rank": self.rank,
            "evidence_count": self.evidence_count,
            "sources_used": [s.value for s in self.sources_used],
            "score_breakdown": self.score_breakdown.to_dict(),
            "source_path": self.source_path.value,
            "server_indices": self.server_indices,
            "notes": self.notes,
        }


@dataclass
class CandidateList:
    """
    Ordered list of ranked service candidates with aggregation metadata.
    """
    candidates: List[RankedServiceCandidate] = field(default_factory=list)
    ambiguity: AmbiguityLevel = AmbiguityLevel.NONE
    score_spread: float = 0.0

    @property
    def count(self) -> int:
        return len(self.candidates)

    @property
    def is_empty(self) -> bool:
        return len(self.candidates) == 0

    @property
    def best(self) -> Optional[RankedServiceCandidate]:
        if not self.candidates:
            return None
        return self.candidates[0]

    @property
    def runner_up(self) -> Optional[RankedServiceCandidate]:
        if len(self.candidates) < 2:
            return None
        return self.candidates[1]

    @property
    def is_ambiguous(self) -> bool:
        return self.ambiguity not in (AmbiguityLevel.NONE, AmbiguityLevel.LOW)

    def top_n(self, n: int) -> List[RankedServiceCandidate]:
        return self.candidates[:n]

    def by_service(self, service_name: str) -> Optional[RankedServiceCandidate]:
        for c in self.candidates:
            if c.service_name == service_name:
                return c
        return None

    def service_names(self) -> List[str]:
        return [c.service_name for c in self.candidates]

    def assign_ranks(self) -> None:
        """Sort candidates by composite_score descending and assign rank numbers."""
        self.candidates.sort(key=lambda c: c.composite_score, reverse=True)
        for i, candidate in enumerate(self.candidates):
            candidate.rank = i + 1
        if len(self.candidates) >= 2:
            self.score_spread = (
                self.candidates[0].composite_score
                - self.candidates[1].composite_score
            )
        elif len(self.candidates) == 1:
            self.score_spread = self.candidates[0].composite_score
        else:
            self.score_spread = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "ambiguity": self.ambiguity.value,
            "score_spread": round(self.score_spread, 4),
            "candidates": [c.to_dict() for c in self.candidates],
        }


# ──────────────────────────────────────────────
# Resolution Trace
# ──────────────────────────────────────────────

@dataclass
class TraceStep:
    """
    A single step recorded during pipeline execution.
    """
    phase: TraceStepPhase
    action: str
    result_summary: str = ""
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase": self.phase.value,
            "action": self.action,
            "result_summary": self.result_summary,
            "duration_ms": round(self.duration_ms, 2),
            "details": self.details,
            "timestamp": self.timestamp,
        }


class ResolutionTrace:
    """
    Ordered collection of trace steps recorded during pipeline execution.
    Lightweight — always constructed, only serialized when debug is enabled.
    """

    def __init__(self) -> None:
        self._steps: List[TraceStep] = []
        self._started_at: float = time.time()
        self._completed_at: Optional[float] = None

    def step(
        self,
        phase: TraceStepPhase,
        action: str,
        result_summary: str = "",
        duration_ms: float = 0.0,
        details: Optional[Dict[str, Any]] = None,
    ) -> TraceStep:
        """Record a trace step and return it."""
        ts = TraceStep(
            phase=phase,
            action=action,
            result_summary=result_summary,
            duration_ms=duration_ms,
            details=details or {},
        )
        self._steps.append(ts)
        return ts

    def mark_complete(self) -> None:
        self._completed_at = time.time()

    @property
    def steps(self) -> List[TraceStep]:
        return list(self._steps)

    @property
    def step_count(self) -> int:
        return len(self._steps)

    @property
    def started_at(self) -> float:
        return self._started_at

    @property
    def completed_at(self) -> Optional[float]:
        return self._completed_at

    @property
    def total_duration_ms(self) -> float:
        if self._completed_at is None:
            return (time.time() - self._started_at) * 1000
        return (self._completed_at - self._started_at) * 1000

    def phases_visited(self) -> List[TraceStepPhase]:
        seen: List[TraceStepPhase] = []
        for s in self._steps:
            if s.phase not in seen:
                seen.append(s.phase)
        return seen

    def steps_for_phase(self, phase: TraceStepPhase) -> List[TraceStep]:
        return [s for s in self._steps if s.phase == phase]

    def summary(self) -> str:
        """One-line summary of trace execution."""
        phases = [p.value for p in self.phases_visited()]
        return (
            f"Trace: {self.step_count} steps across {len(phases)} phases "
            f"in {self.total_duration_ms:.1f}ms — [{', '.join(phases)}]"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_count": self.step_count,
            "started_at": self._started_at,
            "completed_at": self._completed_at,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "phases_visited": [p.value for p in self.phases_visited()],
            "steps": [s.to_dict() for s in self._steps],
        }

    def __repr__(self) -> str:
        return self.summary()


class _TraceTimer:
    """
    Context manager for timing a pipeline phase and recording it in a trace.

    Usage:
        with trace_timer(trace, TraceStepPhase.STATIC_ANALYSIS, "analyze domains") as t:
            # ... do work ...
            t.result_summary = "found 3 domains"
            t.details["domains"] = [...]
    """

    def __init__(
        self, trace: ResolutionTrace, phase: TraceStepPhase, action: str
    ) -> None:
        self._trace = trace
        self._phase = phase
        self._action = action
        self._start: float = 0.0
        self.result_summary: str = ""
        self.details: Dict[str, Any] = {}

    def __enter__(self) -> "_TraceTimer":
        self._start = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore[override]
        duration_ms = (time.time() - self._start) * 1000
        if exc_type is not None:
            self.result_summary = f"ERROR: {exc_type.__name__}: {exc_val}"
            self.details["error"] = True
        self._trace.step(
            phase=self._phase,
            action=self._action,
            result_summary=self.result_summary,
            duration_ms=duration_ms,
            details=self.details,
        )
        return None  # do not suppress exceptions


def trace_timer(
    trace: ResolutionTrace, phase: TraceStepPhase, action: str
) -> _TraceTimer:
    """Factory for trace timing context manager."""
    return _TraceTimer(trace, phase, action)


# ──────────────────────────────────────────────
# Resolution Decision
# ──────────────────────────────────────────────

@dataclass
class ResolutionDecision:
    """
    Final output of the resolution pipeline.

    Contains the best candidate, alternatives, the path taken,
    fallback flags, ambiguity assessment, and the full trace.
    """
    best: Optional[RankedServiceCandidate] = None
    alternatives: List[RankedServiceCandidate] = field(default_factory=list)
    candidate_list: Optional[CandidateList] = None
    fallback_used: bool = False
    ambiguity: AmbiguityLevel = AmbiguityLevel.NONE
    path: ResolutionPath = ResolutionPath.V2_FULL
    trace: ResolutionTrace = field(default_factory=ResolutionTrace)
    player_evidence: Optional[PlayerDetectionEvidence] = None
    request_id: str = ""
    explanation: str = ""

    @property
    def resolved_service(self) -> Optional[str]:
        if self.best is not None:
            return self.best.service_name
        return None

    @property
    def confidence(self) -> float:
        if self.best is not None:
            return self.best.composite_score
        return 0.0

    @property
    def is_resolved(self) -> bool:
        return self.best is not None

    @property
    def is_ambiguous(self) -> bool:
        return self.ambiguity not in (AmbiguityLevel.NONE, AmbiguityLevel.LOW)

    @property
    def alternative_count(self) -> int:
        return len(self.alternatives)

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "resolved_service": self.resolved_service,
            "confidence": round(self.confidence, 4),
            "is_resolved": self.is_resolved,
            "is_ambiguous": self.is_ambiguous,
            "ambiguity": self.ambiguity.value,
            "path": self.path.value,
            "fallback_used": self.fallback_used,
            "alternative_count": self.alternative_count,
            "request_id": self.request_id,
            "explanation": self.explanation,
        }
        if self.best:
            result["best"] = self.best.to_dict()
        if self.alternatives:
            result["alternatives"] = [a.to_dict() for a in self.alternatives]
        if self.player_evidence and self.player_evidence.is_detected:
            result["player"] = self.player_evidence.to_dict()
        return result


# ──────────────────────────────────────────────
# Report Models
# ──────────────────────────────────────────────

@dataclass
class ReportSection:
    """
    A named section of a resolution report.

    Content is structured data (dict), not formatted text.
    Formatting is handled downstream by the consumer.
    """
    name: str
    title: str = ""
    content: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title or self.name,
            "content": self.content,
            "notes": self.notes,
            "order": self.order,
        }


@dataclass
class ReportModel:
    """
    Complete resolution report, suitable for serialization.

    Contains ordered sections covering candidates, evidence,
    confidence, ambiguity, player detection, and resolution trace.
    """
    sections: List[ReportSection] = field(default_factory=list)
    summary: str = ""
    generated_at: float = field(default_factory=time.time)
    pipeline_version: str = "4.0.0"
    request_id: str = ""

    def add_section(self, section: ReportSection) -> None:
        self.sections.append(section)

    def get_section(self, name: str) -> Optional[ReportSection]:
        for s in self.sections:
            if s.name == name:
                return s
        return None

    def section_names(self) -> List[str]:
        return [s.name for s in self.sections]

    @property
    def section_count(self) -> int:
        return len(self.sections)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "summary": self.summary,
            "generated_at": self.generated_at,
            "pipeline_version": self.pipeline_version,
            "request_id": self.request_id,
            "section_count": self.section_count,
            "sections": [s.to_dict() for s in sorted(self.sections, key=lambda s: s.order)],
        }

    def __repr__(self) -> str:
        return (
            f"ReportModel(sections={self.section_count}, "
            f"summary='{self.summary[:60]}...')"
        )