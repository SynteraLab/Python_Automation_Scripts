"""
intelligence.service_resolution.explain
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Human-readable and structured formatting of resolution traces.

This module provides formatters that turn ``ResolutionExplanation``,
``LabelResolutionResult``, and related objects into readable strings
or structured dicts suitable for logging, debugging, and JSON export.
"""

from __future__ import annotations

from typing import Any

from .models import (
    ConfidenceTier,
    LabelResolutionCandidate,
    LabelResolutionResult,
    ResolutionEvidence,
    ResolutionExplanation,
    ResolutionStage,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Single-item formatters
# ═══════════════════════════════════════════════════════════════════════════════


def format_evidence(evidence: ResolutionEvidence) -> str:
    """
    Format a single evidence object as a one-line string.

    Example output::

        [alias_base_score|NORMALIZED_ALIAS] +0.80 — Alias 'ST' has STRONG ...
    """
    sign = "+" if evidence.score_delta >= 0 else ""
    return (
        f"[{evidence.rule_name}|{evidence.stage.value}] "
        f"{sign}{evidence.score_delta:.2f} — {evidence.reason}"
    )


def format_candidate(candidate: LabelResolutionCandidate) -> str:
    """
    Format a single candidate as a compact one-line string.

    Example output::

        #1 streamtape (StreamTape) score=1.15 [4 evidence items]
    """
    alias_info = ""
    if candidate.matched_alias:
        alias_info = (
            f" via alias '{candidate.matched_alias.raw_label}'"
            f" ({candidate.matched_alias.strength.name})"
        )

    evidence_count = len(candidate.score_breakdown.adjustments)
    return (
        f"#{candidate.rank} {candidate.service_id} "
        f"({candidate.service.display_name}) "
        f"score={candidate.final_score:.2f} "
        f"[{evidence_count} evidence items]{alias_info}"
    )


def format_candidate_detail(candidate: LabelResolutionCandidate) -> str:
    """
    Format a candidate with full evidence breakdown (multi-line).

    Example output::

        #1 streamtape (StreamTape) — score=1.15
          Base: 0.80
          Adjustments:
            [alias_base_score|NORMALIZED_ALIAS] +0.80 — ...
            [site_override_boost|SITE_OVERRIDE] +0.35 — ...
    """
    lines: list[str] = []
    bd = candidate.score_breakdown

    lines.append(
        f"#{candidate.rank} {candidate.service_id} "
        f"({candidate.service.display_name}) — "
        f"score={candidate.final_score:.2f}"
    )

    if candidate.matched_alias:
        a = candidate.matched_alias
        lines.append(
            f"  Matched alias: '{a.raw_label}' → '{a.normalized_label}' "
            f"(strength={a.strength.name})"
        )
    else:
        lines.append("  Matched alias: (none — injected by domain/hint)")

    lines.append(f"  Base score: {bd.base_score:.2f}")

    if bd.adjustments:
        lines.append(f"  Evidence ({len(bd.adjustments)} items):")
        for ev in bd.adjustments:
            lines.append(f"    {format_evidence(ev)}")
    else:
        lines.append("  Evidence: (none)")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Result formatters
# ═══════════════════════════════════════════════════════════════════════════════


def format_result_summary(result: LabelResolutionResult) -> str:
    """
    Format a compact summary of the resolution result.

    Example output::

        'ST' → streamtape (StreamTape) [HIGH] score=1.15 (3 candidates)
    """
    if result.winner:
        return (
            f"'{result.input_label}' → {result.winner_id} "
            f"({result.winner.display_name}) "
            f"[{result.confidence.value.upper()}] "
            f"score={result.candidates[0].final_score:.2f} "
            f"({result.candidate_count} candidate(s))"
        )
    else:
        best = ""
        if result.candidates:
            top = result.candidates[0]
            best = (
                f" best={top.service_id} "
                f"score={top.final_score:.2f}"
            )
        return (
            f"'{result.input_label}' → UNRESOLVED "
            f"({result.candidate_count} candidate(s)){best}"
        )


def format_explanation(explanation: ResolutionExplanation) -> str:
    """
    Format the full resolution explanation as a multi-line string.

    Includes input/normalized labels, stages executed, all evidence,
    warnings, and summary.

    Example output::

        ═══ Resolution Trace ═══
        Input label:      'ST'
        Normalized label: 'st'
        Stages executed:  EXACT_ALIAS, NORMALIZED_ALIAS, SITE_OVERRIDE
        ...
    """
    lines: list[str] = []

    lines.append("═══ Resolution Trace ═══")
    lines.append(f"Input label:      '{explanation.input_label}'")
    lines.append(f"Normalized label: '{explanation.normalized_label}'")

    stages_str = ", ".join(s.value for s in explanation.stages_executed)
    lines.append(f"Stages executed:  {stages_str or '(none)'}")

    # Warnings
    if explanation.warnings:
        lines.append("")
        lines.append(f"Warnings ({len(explanation.warnings)}):")
        for w in explanation.warnings:
            lines.append(f"  ⚠ {w}")

    # Evidence by stage
    evidence_by_stage = explanation.evidence_by_stage
    if evidence_by_stage:
        lines.append("")
        lines.append(
            f"Evidence ({len(explanation.all_evidence)} total items):"
        )
        for stage in ResolutionStage:
            stage_evidence = evidence_by_stage.get(stage, [])
            if stage_evidence:
                lines.append(f"  [{stage.value}] ({len(stage_evidence)} items)")
                for ev in stage_evidence:
                    lines.append(f"    {format_evidence(ev)}")
    else:
        lines.append("")
        lines.append("Evidence: (none)")

    # Summary
    if explanation.summary:
        lines.append("")
        lines.append("Summary:")
        for summary_line in explanation.summary.split("\n"):
            lines.append(f"  {summary_line}")

    lines.append("═══ End Trace ═══")
    return "\n".join(lines)


def format_full_result(result: LabelResolutionResult) -> str:
    """
    Format the complete result including summary, all candidates with
    detail, and the full explanation trace.

    This is the most verbose output — intended for deep debugging.
    """
    lines: list[str] = []

    lines.append("╔══════════════════════════════════════════════╗")
    lines.append("║     LABEL RESOLUTION — FULL REPORT          ║")
    lines.append("╚══════════════════════════════════════════════╝")
    lines.append("")
    lines.append(f"Result: {format_result_summary(result)}")
    lines.append("")

    # Candidates
    if result.candidates:
        lines.append(f"Candidates ({result.candidate_count}):")
        lines.append("─" * 50)
        for candidate in result.candidates:
            lines.append(format_candidate_detail(candidate))
            lines.append("")
    else:
        lines.append("Candidates: (none)")
        lines.append("")

    # Explanation trace
    lines.append(format_explanation(result.explanation))

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# Structured dict builders (for JSON / logging)
# ═══════════════════════════════════════════════════════════════════════════════


def evidence_to_dict(evidence: ResolutionEvidence) -> dict[str, Any]:
    """Convert an evidence object to a plain dict."""
    return {
        "rule_name": evidence.rule_name,
        "stage": evidence.stage.value,
        "score_delta": round(evidence.score_delta, 4),
        "reason": evidence.reason,
        "source_detail": evidence.source_detail,
    }


def candidate_to_dict(candidate: LabelResolutionCandidate) -> dict[str, Any]:
    """Convert a candidate to a plain dict."""
    alias_dict = None
    if candidate.matched_alias:
        a = candidate.matched_alias
        alias_dict = {
            "raw_label": a.raw_label,
            "normalized_label": a.normalized_label,
            "strength": a.strength.name,
            "notes": a.notes,
        }

    return {
        "rank": candidate.rank,
        "service_id": candidate.service_id,
        "display_name": candidate.service.display_name,
        "final_score": round(candidate.final_score, 4),
        "base_score": round(candidate.score_breakdown.base_score, 4),
        "evidence_count": len(candidate.score_breakdown.adjustments),
        "evidence": [
            evidence_to_dict(e) for e in candidate.score_breakdown.adjustments
        ],
        "matched_alias": alias_dict,
    }


def explanation_to_dict(
    explanation: ResolutionExplanation,
) -> dict[str, Any]:
    """Convert an explanation to a plain dict."""
    return {
        "input_label": explanation.input_label,
        "normalized_label": explanation.normalized_label,
        "stages_executed": [s.value for s in explanation.stages_executed],
        "evidence_count": len(explanation.all_evidence),
        "evidence": [evidence_to_dict(e) for e in explanation.all_evidence],
        "warnings": list(explanation.warnings),
        "summary": explanation.summary,
    }


def build_debug_dict(result: LabelResolutionResult) -> dict[str, Any]:
    """
    Build a complete structured dict from a resolution result.

    Suitable for JSON serialization, structured logging, or API responses.

    Returns
    -------
    dict[str, Any]
        A fully-nested dict with all resolution details.
    """
    winner_dict = None
    if result.winner:
        winner_dict = {
            "service_id": result.winner.service_id,
            "display_name": result.winner.display_name,
            "domains": list(result.winner.domains),
            "family": result.winner.family,
        }

    return {
        "input_label": result.input_label,
        "normalized_label": result.normalized_label,
        "resolved": result.is_resolved,
        "confidence": result.confidence.value,
        "winner": winner_dict,
        "score_gap": (
            round(result.score_gap, 4)
            if result.score_gap != float("inf")
            else None
        ),
        "candidate_count": result.candidate_count,
        "candidates": [
            candidate_to_dict(c) for c in result.candidates
        ],
        "explanation": explanation_to_dict(result.explanation),
    }