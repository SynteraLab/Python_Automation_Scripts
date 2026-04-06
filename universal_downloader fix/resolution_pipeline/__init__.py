"""
resolution_pipeline/__init__.py

Public API surface for the resolution pipeline package.

This package wires Phase 1 (extractor registry), Phase 2 (label/service
intelligence), and Phase 3 (player/framework intelligence) into a coherent
end-to-end resolution pipeline with:

    - structured evidence merging
    - ranked candidate generation
    - deterministic policy-driven decisions
    - configurable settings/thresholds
    - report-ready result objects
    - debug traceability
    - bootstrap wiring
    - backward-compatible legacy fallback

Usage:
    from resolution_pipeline import (
        bootstrap_detection_system,
        create_pipeline,
        RawInputContext,
        ServerEntry,
    )

    # Bootstrap
    result = bootstrap_detection_system(config={"enable_label_mapping_v2": True})
    pipeline = result.pipeline

    # Build input
    context = RawInputContext(
        page_html="<html>...</html>",
        server_buttons=[
            ServerEntry(label="Fembed", url="https://fembed.com/v/abc"),
            ServerEntry(label="Streamtape", url="https://streamtape.com/e/xyz"),
        ],
        site_domain="example.com",
    )

    # Resolve
    decision = pipeline.run(context)
    print(decision.resolved_service)   # e.g. "fembed"
    print(decision.confidence)         # e.g. 0.85
    print(decision.is_ambiguous)       # e.g. False

    # Report
    from resolution_pipeline import generate_analysis_report
    report = generate_analysis_report(decision, result.settings)
    print(report.summary)

Pipeline version: 4.0.0
"""

__version__ = "4.0.0"

# ──────────────────────────────────────────────
# Core Models
# ──────────────────────────────────────────────

from .models import (
    # Enums
    SignalSource,
    AmbiguityLevel,
    ResolutionPath,
    TraceStepPhase,
    # Evidence
    EvidenceItem,
    EvidenceBundle,
    # Input
    RawInputContext,
    ServerEntry,
    # Static Analysis
    DomainSignal,
    StaticAnalysisResult,
    # Player
    PlayerDetectionEvidence,
    # Candidates
    ScoreBreakdown,
    RankedServiceCandidate,
    CandidateList,
    # Decision
    ResolutionDecision,
    # Trace
    TraceStep,
    ResolutionTrace,
    trace_timer,
    # Report
    ReportSection,
    ReportModel,
)

# ──────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────

from .settings import (
    IntegrationSettings,
    LabelSettings,
    PlayerSettings,
    ResolverSettings,
    ReportSettings,
    DebugSettings,
    default_settings,
    legacy_compatible_settings,
    debug_settings,
)

# ──────────────────────────────────────────────
# Pipeline Functions
# ──────────────────────────────────────────────

from .static_analysis import analyze_static
from .candidate_merger import merge_service_evidence, apply_cross_server_boost
from .server_switcher_adapter import (
    resolve_server_candidates,
    resolve_server_legacy,
    LabelResolverProtocol,
)
from .multi_server_pipeline import (
    ServiceResolutionPipeline,
    build_resolution_trace,
)

# ──────────────────────────────────────────────
# Report
# ──────────────────────────────────────────────

from .report_builder import (
    generate_analysis_report,
    report_to_dict,
    report_to_text,
)

# ──────────────────────────────────────────────
# Bootstrap
# ──────────────────────────────────────────────

from .bootstrap import (
    bootstrap_detection_system,
    bootstrap_from_legacy_config,
    bootstrap_legacy_only,
    create_pipeline,
    BootstrapResult,
)

# ──────────────────────────────────────────────
# Public API Summary
# ──────────────────────────────────────────────

__all__ = [
    # Version
    "__version__",
    # Enums
    "SignalSource",
    "AmbiguityLevel",
    "ResolutionPath",
    "TraceStepPhase",
    # Evidence Models
    "EvidenceItem",
    "EvidenceBundle",
    # Input Models
    "RawInputContext",
    "ServerEntry",
    # Analysis Models
    "DomainSignal",
    "StaticAnalysisResult",
    "PlayerDetectionEvidence",
    # Candidate Models
    "ScoreBreakdown",
    "RankedServiceCandidate",
    "CandidateList",
    # Decision Models
    "ResolutionDecision",
    # Trace Models
    "TraceStep",
    "ResolutionTrace",
    "trace_timer",
    # Report Models
    "ReportSection",
    "ReportModel",
    # Settings
    "IntegrationSettings",
    "LabelSettings",
    "PlayerSettings",
    "ResolverSettings",
    "ReportSettings",
    "DebugSettings",
    "default_settings",
    "legacy_compatible_settings",
    "debug_settings",
    # Pipeline Functions
    "analyze_static",
    "merge_service_evidence",
    "apply_cross_server_boost",
    "resolve_server_candidates",
    "resolve_server_legacy",
    "build_resolution_trace",
    "generate_analysis_report",
    "report_to_dict",
    "report_to_text",
    # Pipeline Class
    "ServiceResolutionPipeline",
    # Protocols
    "LabelResolverProtocol",
    # Bootstrap
    "bootstrap_detection_system",
    "bootstrap_from_legacy_config",
    "bootstrap_legacy_only",
    "create_pipeline",
    "BootstrapResult",
]