"""ControlPlane mesh reporter — token-budgeted output formatting.

Converts MeshResult into systemMessage + hookSpecificOutput JSON
suitable for Claude Code's PostToolUse hook protocol.

Token budget strategy (OTP-inspired):
- Dynamic budget: scales proportionally to finding volume (worse code -> bigger report)
- Static hook_max_tokens serves as floor for clean codebases
- Only inject non-zero channels (silent channels omitted -- silence IS info)
- Strict priority order for sections
- Token counting + truncation of low-priority sections when budget is exhausted
- Truncation metadata always emitted

Section priority order (highest -> lowest):
1. Header (coherence state, duration)
2. Blocking findings (from any channel)
3. Coherence summary + recommended action
4. Channel-specific warnings (non-blocking)
5. Incomplete channels notice (if partial)
6. Channel status summary
7. Recurrence/pattern alerts
8. Repair action suggestions
9. Proposed constraints (from session memory feedback loop)

Protocol note:
- PostToolUse hookSpecificOutput must match Claude's schema:
  {"hookEventName": "PostToolUse", "additionalContext": "..."}

Sub-modules:
- budget: Dynamic token budget constants and computation
- sections: Section formatters (_format_*, _short_path, _estimate_tokens,
  _assemble_report_sections)
- delta: Finding fingerprint, index, delta computation, and display filtering
- hook: PostToolUse context and telemetry counters
- compact: Compact MCP tool response formatting
"""

from __future__ import annotations

from typing import Any

from ..types import ControlPlaneConfig, MeshResult

# ── Re-exports from sub-modules (backward compatibility) ─────────────────
# All public symbols that were previously defined in the flat reporter_* modules
# are imported and re-exported so that existing
# ``from lintgate.controlplane.reporter import X`` statements continue to work.
from .budget import (  # noqa: F401
    _BUDGET_BASE,
    _BUDGET_HARD_CAP,
    _BUDGET_PER_BLOCKING,
    _BUDGET_PER_INFO,
    _BUDGET_PER_REPAIR,
    _BUDGET_PER_WARNING,
    _compute_dynamic_budget,
)
from .compact import (  # noqa: F401
    _build_cp_next_actions,
    format_mesh_report_compact,
)
from .delta import (  # noqa: F401
    _SEVERITY_ORDER,
    DisplayFilterResult,
    build_finding_index,
    compute_finding_delta,
    compute_finding_fingerprint,
    filter_display_findings,
)
from .hook import (  # noqa: F401
    PostToolUseInputs,
    _build_posttooluse_context,
    _build_telemetry_counters,
)
from .sections import (  # noqa: F401
    _assemble_report_sections,
    _estimate_tokens,
    _format_blocking,
    _format_channel_summary,
    _format_coherence,
    _format_header,
    _format_incomplete,
    _format_pattern_alerts,
    _format_proposed_constraints,
    _format_repairs,
    _format_warnings,
    _short_path,
)


def format_mesh_report(
    mesh_result: MeshResult,
    config: ControlPlaneConfig | None = None,
    proposed_constraints: list[dict] | None = None,
    previous_finding_index: dict[str, dict[str, Any]] | None = None,
    baseline_finding_index: dict[str, dict[str, Any]] | None = None,
    snapshot_count: int = 0,
    cycle_alerts: list[str] | None = None,
    disposition: str | None = None,
) -> dict[str, Any]:
    """Format MeshResult as JSON for Claude Code systemMessage.

    Args:
        mesh_result: Complete mesh execution result.
        config: ControlPlane config (for token budget policy).
        proposed_constraints: Optional constraint proposals from session memory.
        previous_finding_index: Finding index from previous run (for delta).
        baseline_finding_index: Finding index from first run in session (debt baseline).
        snapshot_count: Number of snapshots in session (for resurfacing cadence).
        cycle_alerts: Optional list of cycle alerts.
        disposition: Optional behavioral disposition nudge.

    Returns:
        JSON dict with systemMessage key. Empty dict {} if no issues.
    """
    if config is None:
        config = ControlPlaneConfig()

    # Budget Mode: Hook Verbosity
    if config.hook_verbosity == "silent":
        return {}
    if config.hook_verbosity == "pulse":
        # Only pulse every N events (floor 1)
        interval = max(1, config.hook_pulse_interval)
        if snapshot_count > 0 and snapshot_count % interval != 0:
            return {
                "systemMessage": f"CONTROLPLANE PULSE: Session active. Coherence: {mesh_result.coherence.state}.",
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": f"Pulse suppression active (interval={interval}).",
                },
            }

    # Collect findings across all channels
    all_findings = []
    active_channels = []
    for cr in mesh_result.channel_results:
        if cr.status not in ("skip",):
            active_channels.append(cr)
        if cr.findings:
            all_findings.extend(cr.findings)

    # Compute delta if previous finding index is available
    delta: dict[str, Any] | None = None
    baseline_delta: dict[str, Any] | None = None
    current_index: dict[str, dict[str, Any]] | None = None
    if previous_finding_index is not None:
        current_index = build_finding_index(mesh_result)
        delta = compute_finding_delta(current_index, previous_finding_index)
    if baseline_finding_index is not None:
        if current_index is None:
            current_index = build_finding_index(mesh_result)
        baseline_delta = compute_finding_delta(current_index, baseline_finding_index)

    # Delta-based display filtering
    filter_result = filter_display_findings(
        all_findings=all_findings,
        delta=delta,
        mesh_result=mesh_result,
        current_index=current_index,
        previous_finding_index=previous_finding_index,
        snapshot_count=snapshot_count,
    )
    display_findings = filter_result.display_findings
    resurfaced_count = filter_result.resurfaced_count

    # Quick exit: nothing to report
    if not all_findings and not mesh_result.partial:
        has_problems = any(cr.status in ("error", "timeout") for cr in mesh_result.channel_results)
        if not has_problems:
            return {}

    # Count findings by severity
    blocking_findings = [f for f in all_findings if f.severity == "blocking"]
    warning_findings = [f for f in all_findings if f.severity == "warning"]
    blocking_count = len(blocking_findings)
    warning_count = len(warning_findings)
    display_informational = [f for f in display_findings if f.severity == "informational"]
    total_informational = [f for f in all_findings if f.severity == "informational"]
    informational_count = (
        len(display_informational) if delta is not None else len(total_informational)
    )
    hidden_findings = len(all_findings) - len(display_findings)

    # Build compact additional context (max 300 chars, budget-controlled)
    additional_context = _build_posttooluse_context(
        PostToolUseInputs(
            mesh_result=mesh_result,
            blocking_count=blocking_count,
            warning_count=warning_count,
            informational_count=informational_count,
            hidden_findings=hidden_findings,
            channels_run=len(active_channels),
            delta=delta,
            baseline_delta=baseline_delta,
            resurfaced_count=resurfaced_count,
            cycle_alerts=cycle_alerts or [],
        )
    )

    # Save verbose report to disk (not to context window)
    import json as _json
    import os as _os

    max_tokens = _compute_dynamic_budget(all_findings, mesh_result, config)
    parts, _token_est, _bc, _wc = _assemble_report_sections(
        mesh_result=mesh_result,
        display_findings=display_findings,
        all_findings=all_findings,
        active_channels=active_channels,
        delta=delta,
        resurfaced_count=resurfaced_count,
        max_tokens=max_tokens,
        disposition=disposition,
        proposed_constraints=proposed_constraints,
        cycle_alerts=cycle_alerts,
    )
    parts.append("</controlplane-report>")
    verbose_report = "\n".join(parts)

    # Persist verbose report to disk for optional drill-down
    project_root = _os.environ.get("LINTGATE_PROJECT_ROOT", _os.getcwd())
    report_dir = _os.path.join(project_root, ".lintgate", "analysis", "posttooluse_report")
    _os.makedirs(report_dir, exist_ok=True)
    import hashlib as _hashlib

    report_id = _hashlib.sha256(verbose_report.encode()).hexdigest()[:10]
    report_path = _os.path.join(report_dir, f"{report_id}.json")
    try:
        with open(report_path, "w", encoding="utf-8") as _f:
            _json.dump(
                {"report": verbose_report, "additional_context": additional_context},
                _f,
                separators=(",", ":"),
            )
    except OSError:
        report_path = ""

    # systemMessage: compact NL summary only — verbose report is on disk
    output: dict[str, Any] = {
        "systemMessage": f"PostToolUse: {additional_context}",
    }
    output["hookSpecificOutput"] = {
        "hookEventName": "PostToolUse",
        "additionalContext": additional_context,
    }
    # Private pointer to the verbose on-disk report. Claude Code ignores
    # unknown keys; internal consumers (tests, drill-down tools) can load
    # the full XML-style report from this path.
    if report_path:
        output["_report_path"] = report_path

    # Telemetry counters
    telemetry = _build_telemetry_counters(
        mesh_result=mesh_result,
        delta=delta,
        baseline_delta=baseline_delta,
        display_findings=display_findings,
        all_findings=all_findings,
        resurfaced_count=resurfaced_count,
    )
    if telemetry:
        output["_telemetry"] = telemetry

    return output
