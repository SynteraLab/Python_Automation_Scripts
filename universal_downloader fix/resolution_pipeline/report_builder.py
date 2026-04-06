"""
resolution_pipeline/report_builder.py

Generates structured ReportModel from ResolutionDecision objects.
Reports contain ordered sections covering candidates, evidence,
confidence, ambiguity, player detection, and resolution trace.

Gaps closed:
    G4 — reports include confidence, ambiguity, evidence, player data, trace
    G8 — trace section in report provides full end-to-end debug output

Design:
    - Reports are structured data (ReportModel / ReportSection)
    - Formatting (JSON, text, HTML) is left to downstream consumers
    - Sections are conditionally included based on ReportSettings
    - Every section has an order field for deterministic rendering
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .models import (
    AmbiguityLevel,
    CandidateList,
    PlayerDetectionEvidence,
    RankedServiceCandidate,
    ReportModel,
    ReportSection,
    ResolutionDecision,
    ResolutionTrace,
    SignalSource,
)
from .settings import IntegrationSettings, ReportSettings


# ──────────────────────────────────────────────
# Section Builders
# ──────────────────────────────────────────────

def _build_summary_section(
    decision: ResolutionDecision,
) -> ReportSection:
    """
    Build the summary section — always included.
    """
    notes: List[str] = []

    if decision.fallback_used:
        notes.append("Resolution used legacy fallback path")
    if decision.is_ambiguous:
        notes.append(
            f"Resolution is ambiguous (level={decision.ambiguity.value})"
        )
    if not decision.is_resolved:
        notes.append("No service was resolved")

    return ReportSection(
        name="summary",
        title="Resolution Summary",
        content={
            "resolved_service": decision.resolved_service,
            "confidence": round(decision.confidence, 4),
            "is_resolved": decision.is_resolved,
            "resolution_path": decision.path.value,
            "fallback_used": decision.fallback_used,
            "ambiguity_level": decision.ambiguity.value,
            "alternative_count": decision.alternative_count,
            "request_id": decision.request_id,
            "explanation": decision.explanation,
        },
        notes=notes,
        order=0,
    )


def _build_candidates_section(
    decision: ResolutionDecision,
    report_settings: ReportSettings,
) -> ReportSection:
    """
    Build the candidates section — ranked list with scores.
    """
    candidates_data: List[Dict[str, Any]] = []

    if decision.candidate_list:
        limit = report_settings.max_alternatives + 1  # +1 for the best
        for candidate in decision.candidate_list.candidates[:limit]:
            entry: Dict[str, Any] = {
                "rank": candidate.rank,
                "service_name": candidate.service_name,
                "composite_score": round(candidate.composite_score, 4),
                "source_path": candidate.source_path.value,
                "evidence_count": candidate.evidence_count,
                "server_indices": candidate.server_indices,
            }

            if report_settings.include_score_breakdown:
                entry["score_breakdown"] = candidate.score_breakdown.to_dict()

            if candidate.notes:
                entry["notes"] = candidate.notes

            candidates_data.append(entry)

    notes: List[str] = []
    if decision.candidate_list and decision.candidate_list.count > len(candidates_data):
        omitted = decision.candidate_list.count - len(candidates_data)
        notes.append(f"{omitted} lower-ranked candidates omitted from report")

    return ReportSection(
        name="candidates",
        title="Ranked Service Candidates",
        content={
            "total_candidates": (
                decision.candidate_list.count
                if decision.candidate_list else 0
            ),
            "displayed": len(candidates_data),
            "candidates": candidates_data,
        },
        notes=notes,
        order=1,
    )


def _build_confidence_section(
    decision: ResolutionDecision,
) -> ReportSection:
    """
    Build the confidence section — details about the best candidate's score.
    """
    content: Dict[str, Any] = {
        "best_score": round(decision.confidence, 4),
        "is_above_threshold": True,  # will be overridden below
        "threshold": 0.0,
    }

    notes: List[str] = []

    if decision.best:
        breakdown = decision.best.score_breakdown.to_dict()
        content["score_breakdown"] = breakdown
        content["sources_used"] = [s.value for s in decision.best.sources_used]
        content["evidence_count"] = decision.best.evidence_count

        # Check for low-confidence notes
        for note in decision.best.notes:
            if "threshold" in note.lower() or "confidence" in note.lower():
                notes.append(note)
                content["is_above_threshold"] = False

    if decision.candidate_list:
        content["score_spread"] = round(decision.candidate_list.score_spread, 4)

    return ReportSection(
        name="confidence",
        title="Confidence Analysis",
        content=content,
        notes=notes,
        order=2,
    )


def _build_ambiguity_section(
    decision: ResolutionDecision,
) -> ReportSection:
    """
    Build the ambiguity section — what made it ambiguous and what alternatives exist.
    """
    content: Dict[str, Any] = {
        "ambiguity_level": decision.ambiguity.value,
        "is_ambiguous": decision.is_ambiguous,
    }

    notes: List[str] = []

    if decision.is_ambiguous and decision.candidate_list:
        cl = decision.candidate_list

        content["score_spread"] = round(cl.score_spread, 4)

        if cl.best and cl.runner_up:
            content["top_two"] = {
                "best": {
                    "service": cl.best.service_name,
                    "score": round(cl.best.composite_score, 4),
                },
                "runner_up": {
                    "service": cl.runner_up.service_name,
                    "score": round(cl.runner_up.composite_score, 4),
                },
                "gap": round(
                    cl.best.composite_score - cl.runner_up.composite_score, 4
                ),
            }

            notes.append(
                f"Top two candidates are close: "
                f"'{cl.best.service_name}' ({cl.best.composite_score:.3f}) vs "
                f"'{cl.runner_up.service_name}' ({cl.runner_up.composite_score:.3f})"
            )

        # List all alternatives that are within ambiguity range
        if cl.best:
            close_alternatives = [
                c for c in cl.candidates[1:]
                if (cl.best.composite_score - c.composite_score) < 0.20
            ]
            if close_alternatives:
                content["close_alternatives"] = [
                    {
                        "service": c.service_name,
                        "score": round(c.composite_score, 4),
                        "gap": round(cl.best.composite_score - c.composite_score, 4),
                    }
                    for c in close_alternatives
                ]
    else:
        notes.append("Resolution is unambiguous")

    return ReportSection(
        name="ambiguity",
        title="Ambiguity Analysis",
        content=content,
        notes=notes,
        order=3,
    )


def _build_player_section(
    decision: ResolutionDecision,
) -> ReportSection:
    """
    Build the player/framework section.
    """
    content: Dict[str, Any] = {
        "detected": False,
    }

    notes: List[str] = []

    pe = decision.player_evidence
    if pe and pe.is_detected:
        content["detected"] = True
        content["player_name"] = pe.player_name
        content["player_version"] = pe.player_version
        content["framework"] = pe.framework
        content["confidence"] = round(pe.confidence, 4)

        if pe.has_wrapper:
            content["wrapper_detected"] = pe.wrapper_detected
            notes.append(f"Player is wrapped by '{pe.wrapper_detected}'")

        if pe.has_config:
            content["config_keys"] = list(pe.config_extracted.keys())
            content["config_count"] = len(pe.config_extracted)
            notes.append(
                f"Extracted {len(pe.config_extracted)} config keys from player setup"
            )

        content["evidence_count"] = len(pe.evidence_items)
    else:
        notes.append("No player/framework was detected")

    return ReportSection(
        name="player",
        title="Player / Framework Detection",
        content=content,
        notes=notes,
        order=4,
    )


def _build_evidence_section(
    decision: ResolutionDecision,
    report_settings: ReportSettings,
) -> ReportSection:
    """
    Build the evidence section — flattened evidence from all candidates.
    """
    all_evidence: List[Dict[str, Any]] = []
    source_counts: Dict[str, int] = {}

    if decision.candidate_list:
        for candidate in decision.candidate_list.candidates:
            for item in candidate.evidence.items:
                source_key = item.source.value
                source_counts[source_key] = source_counts.get(source_key, 0) + 1

                all_evidence.append({
                    "source": source_key,
                    "key": item.key,
                    "value": (
                        item.value
                        if isinstance(item.value, (str, int, float, bool))
                        else str(item.value)
                    ),
                    "weight": round(item.weight, 4),
                    "confidence": round(item.confidence, 4),
                    "effective_weight": round(item.effective_weight, 4),
                    "explanation": item.explanation,
                    "for_service": candidate.service_name,
                })

    # Deduplicate by (source, key, value, for_service)
    seen_keys: set = set()
    deduped: List[Dict[str, Any]] = []
    for ev in all_evidence:
        dedup_key = (ev["source"], ev["key"], str(ev["value"]), ev["for_service"])
        if dedup_key not in seen_keys:
            seen_keys.add(dedup_key)
            deduped.append(ev)

    notes: List[str] = []
    notes.append(
        f"Total evidence items: {len(all_evidence)}, unique: {len(deduped)}"
    )

    return ReportSection(
        name="evidence",
        title="Evidence Trail",
        content={
            "total_items": len(all_evidence),
            "unique_items": len(deduped),
            "source_distribution": source_counts,
            "items": deduped,
        },
        notes=notes,
        order=5,
    )


def _build_trace_section(
    decision: ResolutionDecision,
) -> ReportSection:
    """
    Build the trace section — full step-by-step trace of pipeline execution.
    """
    trace = decision.trace
    content: Dict[str, Any] = {
        "step_count": trace.step_count if trace else 0,
        "total_duration_ms": round(trace.total_duration_ms, 2) if trace else 0.0,
    }

    notes: List[str] = []

    if trace and trace.step_count > 0:
        content["phases_visited"] = [p.value for p in trace.phases_visited()]
        content["steps"] = [step.to_dict() for step in trace.steps]

        # Compute per-phase duration summary
        phase_durations: Dict[str, float] = {}
        for step in trace.steps:
            phase_key = step.phase.value
            phase_durations[phase_key] = (
                phase_durations.get(phase_key, 0.0) + step.duration_ms
            )
        content["phase_durations_ms"] = {
            k: round(v, 2) for k, v in phase_durations.items()
        }

        # Find slowest phase
        if phase_durations:
            slowest = max(phase_durations, key=phase_durations.get)  # type: ignore[arg-type]
            notes.append(
                f"Slowest phase: {slowest} ({phase_durations[slowest]:.1f}ms)"
            )

        notes.append(trace.summary())
    else:
        notes.append("No trace data available")

    return ReportSection(
        name="trace",
        title="Resolution Trace",
        content=content,
        notes=notes,
        order=6,
    )


def _build_explanation_section(
    decision: ResolutionDecision,
) -> ReportSection:
    """
    Build the explanation section — human-readable narrative of what happened.
    """
    paragraphs: List[str] = []

    # Opening
    if decision.is_resolved:
        paragraphs.append(
            f"The pipeline resolved the input to service "
            f"'{decision.resolved_service}' with a confidence of "
            f"{decision.confidence:.1%}."
        )
    else:
        paragraphs.append(
            "The pipeline was unable to resolve the input to any known service."
        )

    # Path
    path_descriptions = {
        "v2_full": "Full v2 resolution was used, combining label, domain, and player evidence.",
        "v2_partial": "Partial v2 resolution was used — not all signal sources were available.",
        "legacy_fallback": "Legacy fallback was used — v2 systems were disabled or unavailable.",
        "hybrid": "A hybrid path was used — v2 produced no result and legacy fallback succeeded.",
    }
    paragraphs.append(
        path_descriptions.get(
            decision.path.value,
            f"Resolution path: {decision.path.value}."
        )
    )

    # Ambiguity
    if decision.is_ambiguous:
        paragraphs.append(
            f"The resolution is AMBIGUOUS (level={decision.ambiguity.value}). "
            f"Multiple candidates have similar scores. "
            f"The decision may change if additional evidence becomes available."
        )

    # Alternatives
    if decision.alternatives:
        alt_names = [a.service_name for a in decision.alternatives[:3]]
        paragraphs.append(
            f"Alternative candidates: {', '.join(alt_names)}"
            + (f" (+{len(decision.alternatives) - 3} more)" if len(decision.alternatives) > 3 else "")
            + "."
        )

    # Player
    if decision.player_evidence and decision.player_evidence.is_detected:
        pe = decision.player_evidence
        player_desc = pe.player_name or "unknown"
        if pe.player_version:
            player_desc += f" v{pe.player_version}"
        paragraphs.append(
            f"Player/framework detected: {player_desc} "
            f"(confidence={pe.confidence:.1%})."
        )
        if pe.has_wrapper:
            paragraphs.append(f"The player appears to be wrapped by '{pe.wrapper_detected}'.")

    # Fallback
    if decision.fallback_used:
        paragraphs.append(
            "NOTE: This decision was produced by the legacy fallback path. "
            "Enable v2 systems for richer resolution."
        )

    return ReportSection(
        name="explanation",
        title="Resolution Explanation",
        content={
            "narrative": "\n\n".join(paragraphs),
            "paragraphs": paragraphs,
        },
        notes=[],
        order=7,
    )


# ──────────────────────────────────────────────
# Report Summary Builder
# ──────────────────────────────────────────────

def _format_report_summary(decision: ResolutionDecision) -> str:
    """Build a one-line summary for the report."""
    parts = []

    if decision.is_resolved:
        parts.append(f"→ {decision.resolved_service}")
        parts.append(f"confidence={decision.confidence:.1%}")
    else:
        parts.append("→ UNRESOLVED")

    parts.append(f"path={decision.path.value}")

    if decision.is_ambiguous:
        parts.append(f"AMBIGUOUS({decision.ambiguity.value})")

    if decision.alternative_count > 0:
        parts.append(f"alternatives={decision.alternative_count}")

    return " | ".join(parts)


# ──────────────────────────────────────────────
# Main Report Entry Point
# ──────────────────────────────────────────────

def generate_analysis_report(
    decision: ResolutionDecision,
    settings: IntegrationSettings,
) -> ReportModel:
    """
    Generate a structured ReportModel from a ResolutionDecision.

    Which sections are included depends on ReportSettings.
    All sections produce structured data — formatting is handled downstream.

    Args:
        decision: The resolution decision to report on.
        settings: Pipeline settings controlling report content.

    Returns:
        ReportModel with ordered sections.
    """
    report_settings = settings.report
    verbosity = report_settings.verbosity

    report = ReportModel(
        summary=_format_report_summary(decision),
        pipeline_version=settings.pipeline_version,
        request_id=decision.request_id,
    )

    # ── Summary — always included ──
    report.add_section(_build_summary_section(decision))

    # ── Explanation — always included ──
    report.add_section(_build_explanation_section(decision))

    if verbosity == "minimal":
        return report

    # ── Candidates — included for summary and full ──
    if report_settings.include_alternatives or verbosity in ("summary", "full"):
        report.add_section(
            _build_candidates_section(decision, report_settings)
        )

    # ── Confidence — included for summary and full ──
    report.add_section(_build_confidence_section(decision))

    if verbosity == "summary":
        return report

    # ── Full verbosity sections ──

    # Ambiguity
    report.add_section(_build_ambiguity_section(decision))

    # Player
    if report_settings.include_player:
        report.add_section(_build_player_section(decision))

    # Evidence
    if report_settings.include_evidence:
        report.add_section(
            _build_evidence_section(decision, report_settings)
        )

    # Trace
    if report_settings.include_trace and settings.effective_debug():
        report.add_section(_build_trace_section(decision))

    return report


# ──────────────────────────────────────────────
# Report Serialization Helpers
# ──────────────────────────────────────────────
# ADAPTATION POINT: The real repo may use its own serialization.
# These are convenience functions for standalone use.

def report_to_dict(report: ReportModel) -> Dict[str, Any]:
    """Convert a ReportModel to a plain dict."""
    return report.to_dict()


def report_to_text(report: ReportModel) -> str:
    """
    Convert a ReportModel to a human-readable text string.

    This is a basic formatter — the real repo should implement
    its own formatting if needed.
    """
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append(f"RESOLUTION REPORT — {report.summary}")
    lines.append(f"Pipeline Version: {report.pipeline_version}")
    lines.append(f"Request ID: {report.request_id}")
    lines.append("=" * 60)

    for section in sorted(report.sections, key=lambda s: s.order):
        lines.append("")
        lines.append(f"── {section.title} ──")

        if section.name == "explanation":
            narrative = section.content.get("narrative", "")
            if narrative:
                lines.append(narrative)
        else:
            for key, value in section.content.items():
                if isinstance(value, list) and len(value) > 5:
                    lines.append(f"  {key}: [{len(value)} items]")
                elif isinstance(value, dict):
                    lines.append(f"  {key}:")
                    for sk, sv in value.items():
                        lines.append(f"    {sk}: {sv}")
                else:
                    lines.append(f"  {key}: {value}")

        if section.notes:
            for note in section.notes:
                lines.append(f"  * {note}")

    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)