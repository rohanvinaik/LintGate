"""ControlPlane mesh reporter — token-budgeted output formatting.

Converts MeshResult into systemMessage + hookSpecificOutput JSON
suitable for Claude Code's PostToolUse hook protocol.

Token budget strategy (OTP-inspired):
- Dynamic budget: scales proportionally to finding volume (worse code → bigger report)
- Static hook_max_tokens serves as floor for clean codebases
- Only inject non-zero channels (silent channels omitted — silence IS info)
- Strict priority order for sections
- Token counting + truncation of low-priority sections when budget is exhausted
- Truncation metadata always emitted

Section priority order (highest → lowest):
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
- reporter_delta: Finding fingerprint, index, and delta computation
- reporter_hook: PostToolUse context and telemetry counters
- reporter_compact: Compact MCP tool response formatting
"""

from __future__ import annotations

import os
from typing import Any

from .reporter_compact import (  # noqa: F401 — re-exports for backward compat
    _build_cp_next_actions,
    format_mesh_report_compact,
)

# ── Re-exports from sub-modules (backward compatibility) ─────────────────
# All public symbols that were previously defined here are imported and
# re-exported so that existing ``from lintgate.controlplane.reporter import X``
# statements continue to work without modification.
from .reporter_delta import (  # noqa: F401 — re-exports for backward compat
    _SEVERITY_ORDER,
    build_finding_index,
    compute_finding_delta,
    compute_finding_fingerprint,
)
from .reporter_hook import (  # noqa: F401 — re-exports for backward compat
    PostToolUseInputs,
    _build_posttooluse_context,
    _build_telemetry_counters,
)
from .types import ChannelResult, ControlPlaneConfig, MeshResult

# ── Dynamic token budget ────────────────────────────────────────────────

# Per-section token costs (empirical from typical output)
_BUDGET_BASE = 300  # header + coherence + channel summary + close tag
_BUDGET_PER_BLOCKING = 75  # each blocking finding with evidence
_BUDGET_PER_WARNING = 50  # each warning finding
_BUDGET_PER_INFO = 20  # each informational finding (channel summary line)
_BUDGET_PER_REPAIR = 40  # each repair suggestion
_BUDGET_HARD_CAP = 12000  # common-sense upper bound for dynamic growth


def _compute_dynamic_budget(
    all_findings: list,
    mesh_result: MeshResult,
    config: ControlPlaneConfig,
) -> int:
    """Compute token budget proportional to finding volume.

    The budget scales with the actual content that needs reporting.
    Worse code produces more findings, which need more tokens — that's
    signal fidelity. The static hook_max_tokens serves as the floor
    for clean codebases.
    """
    blocking = sum(1 for f in all_findings if f.severity == "blocking")
    warnings = sum(1 for f in all_findings if f.severity == "warning")
    informational = sum(1 for f in all_findings if f.severity == "informational")
    repairs = sum(len(cr.repairs) for cr in mesh_result.channel_results)

    dynamic = (
        _BUDGET_BASE
        + blocking * _BUDGET_PER_BLOCKING
        + warnings * _BUDGET_PER_WARNING
        + informational * _BUDGET_PER_INFO
        + repairs * _BUDGET_PER_REPAIR
    )

    # Dynamic budget can grow with issue volume, but never unbounded.
    floor = max(1, int(config.token_policy.hook_max_tokens))
    effective_floor = min(floor, _BUDGET_HARD_CAP)
    dynamic_capped = min(dynamic, _BUDGET_HARD_CAP)
    return max(dynamic_capped, effective_floor)


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
            # Minimal heartbeat instead of full report
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

    # When delta is available, filter to new/escalated findings + resurfaced blockers
    display_findings = all_findings
    resurfaced_count = 0
    if delta is not None:
        # Build per-fingerprint display quota. This avoids over-reporting when
        # one fingerprint appears many times but only a subset is truly "new".
        quota_by_fp: dict[str, int] = {}
        for item in delta.get("new", []):
            fp = item.get("fingerprint", "")
            if fp:
                quota_by_fp[fp] = quota_by_fp.get(fp, 0) + max(1, int(item.get("count", 1)))
        for item in delta.get("escalated", []):
            fp = item.get("fingerprint", "")
            if fp:
                quota_by_fp[fp] = quota_by_fp.get(fp, 0) + max(1, int(item.get("count", 1)))

        # Resurfacing cadence: persistent blocking findings resurface every 10 runs
        if snapshot_count > 0 and snapshot_count % 10 == 0 and previous_finding_index:
            for fp, info in (current_index or {}).items():
                if (
                    info.get("severity") == "blocking"
                    and fp in previous_finding_index
                    and quota_by_fp.get(fp, 0) == 0
                ):
                    # Resurface one representative occurrence per fingerprint.
                    quota_by_fp[fp] = 1
                    resurfaced_count += 1

        # Filter findings by per-fingerprint quota.
        if quota_by_fp:
            display_findings = []
            used_by_fp: dict[str, int] = {}
            for cr in mesh_result.channel_results:
                for f in cr.findings:
                    fp = compute_finding_fingerprint(f, cr.channel)
                    allowed = quota_by_fp.get(fp, 0)
                    if allowed <= 0:
                        continue
                    used = used_by_fp.get(fp, 0)
                    if used < allowed:
                        display_findings.append(f)
                        used_by_fp[fp] = used + 1
        else:
            # No new/escalated findings — show nothing
            display_findings = []

    # Quick exit: nothing to report
    if not all_findings and not mesh_result.partial:
        # Check if there are error/timeout channels
        has_problems = any(cr.status in ("error", "timeout") for cr in mesh_result.channel_results)
        if not has_problems:
            return {}

    max_tokens = _compute_dynamic_budget(all_findings, mesh_result, config)
    parts: list[str] = []
    token_estimate = 0

    # Section 1: Header (always included but counted against budget)
    header = _format_header(mesh_result)
    header_tokens = _estimate_tokens(header)
    if header_tokens <= max_tokens:
        parts.append(header)
        token_estimate += header_tokens
    else:
        # Budget too small even for header — emit minimal header
        parts.append(f'<controlplane-report coherence="{mesh_result.coherence.state}">')
        token_estimate += 10

    # Section 1.5: Disposition Nudge (#155)
    if disposition:
        section = f"DISPOSITION: {disposition}"
        section_tokens = _estimate_tokens(section)
        if token_estimate + section_tokens <= max_tokens:
            parts.append(section)
            token_estimate += section_tokens

    # Delta summary line (when delta is available)
    if delta is not None:
        new_count = sum(f.get("count", 1) for f in delta.get("new", []))
        escalated_count = sum(f.get("count", 1) for f in delta.get("escalated", []))
        resolved = delta.get("resolved_count", 0)
        unchanged = delta.get("still_active_count", 0)
        delta_parts = []
        if new_count:
            delta_parts.append(f"{new_count} new")
        if escalated_count:
            delta_parts.append(f"{escalated_count} escalated")
        if resolved:
            delta_parts.append(f"{resolved} resolved")
        if unchanged:
            delta_parts.append(f"{unchanged} unchanged (suppressed)")
        if resurfaced_count:
            delta_parts.append(f"{resurfaced_count} resurfaced")
        if delta_parts:
            section = f"DELTA: {', '.join(delta_parts)}"
            section_tokens = _estimate_tokens(section)
            if token_estimate + section_tokens <= max_tokens:
                parts.append(section)
                token_estimate += section_tokens

    # Section 2: Blocking findings (with per-finding granularity)
    # Use display_findings (delta-filtered) for what to show
    blocking = [f for f in display_findings if f.severity == "blocking"]
    if blocking:
        section = _format_blocking(blocking)
        section_tokens = _estimate_tokens(section)
        if token_estimate + section_tokens <= max_tokens:
            parts.append(section)
            token_estimate += section_tokens
        else:
            # Budget tight: try with fewer findings (per-finding granularity)
            for cap in (3, 1):
                reduced = _format_blocking(blocking[:cap])
                reduced_tokens = _estimate_tokens(reduced)
                if token_estimate + reduced_tokens <= max_tokens:
                    overflow = len(blocking) - cap
                    if overflow > 0:
                        reduced += f"\n  ...and {overflow} more blocking issue{'s' if overflow != 1 else ''}"
                    parts.append(reduced)
                    token_estimate += _estimate_tokens(reduced)
                    break

    # Section 3: Coherence summary
    coherence = mesh_result.coherence
    if coherence.state != "stable" or coherence.summary:
        section = _format_coherence(coherence)
        section_tokens = _estimate_tokens(section)
        if token_estimate + section_tokens <= max_tokens:
            parts.append(section)
            token_estimate += section_tokens

    # Section 4: Channel warnings (non-blocking findings)
    warnings = [f for f in display_findings if f.severity == "warning"]
    if warnings:
        section = _format_warnings(warnings)
        section_tokens = _estimate_tokens(section)
        if token_estimate + section_tokens <= max_tokens:
            parts.append(section)
            token_estimate += section_tokens

    # Section 5: Incomplete channels notice
    if mesh_result.partial:
        section = _format_incomplete(mesh_result.incomplete_channels)
        section_tokens = _estimate_tokens(section)
        if token_estimate + section_tokens <= max_tokens:
            parts.append(section)
            token_estimate += section_tokens

    # Section 6: Channel status summary
    section = _format_channel_summary(active_channels)
    section_tokens = _estimate_tokens(section)
    if token_estimate + section_tokens <= max_tokens:
        parts.append(section)
        token_estimate += section_tokens

    # Section 7: Recurrence/pattern alerts (from lint channel metrics)
    lint_results = [cr for cr in mesh_result.channel_results if cr.channel == "lint"]
    if lint_results:
        pattern_alerts = lint_results[0].metrics.get("pattern_alerts", [])
        if pattern_alerts:
            section = _format_pattern_alerts(pattern_alerts)
            section_tokens = _estimate_tokens(section)
            if token_estimate + section_tokens <= max_tokens:
                parts.append(section)
                token_estimate += section_tokens

    # Section 8: Repair suggestions
    all_repairs = []
    for cr in mesh_result.channel_results:
        all_repairs.extend(cr.repairs)
    if all_repairs:
        section = _format_repairs(all_repairs)
        section_tokens = _estimate_tokens(section)
        if token_estimate + section_tokens <= max_tokens:
            parts.append(section)
            token_estimate += section_tokens

    # Informational count — show display_findings count when delta is active,
    # but note the total for context
    display_informational = [f for f in display_findings if f.severity == "informational"]
    total_informational = [f for f in all_findings if f.severity == "informational"]
    if display_informational or (total_informational and delta is None):
        shown = display_informational if delta is not None else total_informational
        section = f"INFO: {len(shown)} informational finding{'s' if len(shown) != 1 else ''}"
        section_tokens = _estimate_tokens(section)
        if token_estimate + section_tokens <= max_tokens:
            parts.append(section)
            token_estimate += section_tokens

    # Section 9: Proposed constraints (from session memory feedback loop)
    if proposed_constraints:
        active_proposals = [c for c in proposed_constraints if c.get("status") == "proposed"]
        if active_proposals:
            section = _format_proposed_constraints(active_proposals)
            section_tokens = _estimate_tokens(section)
            if token_estimate + section_tokens <= max_tokens:
                parts.append(section)
                token_estimate += section_tokens

    # Section 10: Cycle alerts
    if cycle_alerts:
        section = "CYCLE ALERTS (Repetitive behavior detected):\n  " + "\n  ".join(cycle_alerts)
        section_tokens = _estimate_tokens(section)
        if token_estimate + section_tokens <= max_tokens:
            parts.append(section)
            token_estimate += section_tokens

    # Close tag
    parts.append("</controlplane-report>")

    # Count findings not shown in the systemMessage text.
    # When delta is active, hidden = total - displayed (not just truncation).
    shown_blocking = min(len(blocking), 5)
    shown_warnings = min(len(warnings), 3)
    if delta is not None:
        # Delta mode: suppressed findings are the difference between total and displayed
        suppressed = len(all_findings) - len(display_findings)
        hidden_findings = (
            suppressed
            + max(0, len(blocking) - shown_blocking)
            + max(0, len(warnings) - shown_warnings)
        )
    else:
        hidden_findings = len(all_findings) - shown_blocking - shown_warnings

    if hidden_findings > 0:
        parts.insert(
            -1,
            f"[{hidden_findings} findings not shown inline]",
        )

    message = "\n".join(parts)
    output: dict[str, Any] = {"systemMessage": message}

    # Claude PostToolUse hook schema: hookEventName + optional additionalContext.
    informational_count = (
        len(display_informational) if delta is not None else len(total_informational)
    )
    additional_context = _build_posttooluse_context(
        mesh_result=mesh_result,
        blocking_count=len(blocking),
        warning_count=len(warnings),
        informational_count=informational_count,
        hidden_findings=hidden_findings,
        channels_run=len(active_channels),
        delta=delta,
        baseline_delta=baseline_delta,
        resurfaced_count=resurfaced_count,
        cycle_alerts=cycle_alerts,
    )
    output["hookSpecificOutput"] = {
        "hookEventName": "PostToolUse",
        "additionalContext": additional_context,
    }

    # Telemetry counters — lightweight increment-only for threshold tuning
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


# ── Section formatters ───────────────────────────────────────────────────


def _format_header(mesh_result: MeshResult) -> str:
    """Format the report header with coherence state."""
    coherence = mesh_result.coherence
    channel_count = sum(1 for cr in mesh_result.channel_results if cr.status != "skip")
    duration = mesh_result.duration_ms

    conf_attr = ""
    if coherence.confidence < 1.0:
        conf_attr = f' confidence="{coherence.confidence:.2f}"'

    return (
        f'<controlplane-report coherence="{coherence.state}"{conf_attr} '
        f'channels="{channel_count}" '
        f'duration="{duration:.0f}ms">'
    )


def _format_blocking(findings: list) -> str:
    """Format blocking findings section."""
    parts = [f"BLOCKING ({len(findings)} issue{'s' if len(findings) != 1 else ''} - must fix):"]
    for f in findings[:5]:
        location = _short_path(f.file) if f.file else ""
        if f.line:
            location += f":{f.line}"
        channel_prefix = f"[{f.linter}/{f.kind}]" if f.linter else f"[{f.kind}]"
        parts.append(f"  {channel_prefix} {location}: {f.message}")
        if f.fix_description:
            parts.append(f"    Fix: {f.fix_description}")
    if len(findings) > 5:
        parts.append(f"  ... and {len(findings) - 5} more blocking issues")
    return "\n".join(parts)


def _format_coherence(coherence) -> str:
    """Format coherence summary section."""
    conf_suffix = ""
    if coherence.confidence < 1.0:
        conf_suffix = f" (confidence: {coherence.confidence:.0%})"
    parts = [f"COHERENCE [{coherence.state}]{conf_suffix}: {coherence.summary}"]
    if coherence.recommended_action:
        parts.append(f"  Action: {coherence.recommended_action}")
    if getattr(coherence, "classification_notes", None):
        for note in coherence.classification_notes:
            parts.append(f"  Note: {note}")
    return "\n".join(parts)


def _format_warnings(findings: list) -> str:
    """Format warning findings section."""
    parts = [f"WARNINGS ({len(findings)}):"]
    for f in findings[:3]:
        location = _short_path(f.file) if f.file else ""
        if f.line:
            location += f":{f.line}"
        channel_prefix = f"[{f.linter}/{f.kind}]" if f.linter else f"[{f.kind}]"
        parts.append(f"  {channel_prefix} {location}: {f.message}")
    if len(findings) > 3:
        parts.append(f"  ... and {len(findings) - 3} more warnings")
    return "\n".join(parts)


def _format_incomplete(incomplete_channels: list[str]) -> str:
    """Format incomplete channels notice."""
    names = ", ".join(incomplete_channels)
    return f"PARTIAL: Channels timed out: {names}. Results may be incomplete."


def _format_channel_summary(active_channels: list[ChannelResult]) -> str:
    """Format channel status summary."""
    parts = ["Channels:"]
    for cr in active_channels:
        icon = {
            "pass": "\u2713",
            "fail": "\u2717",
            "error": "\u26a0",
            "timeout": "\u23f1",
        }.get(cr.status, "?")
        detail = ""
        if cr.findings:
            detail = f" ({len(cr.findings)} findings)"
        elif cr.status == "error":
            detail = f" ({cr.error_message or 'unknown error'})"
        parts.append(f"  {icon} {cr.channel}: {cr.status}{detail}")
    return "\n".join(parts)


def _format_pattern_alerts(alerts: list[dict]) -> str:
    """Format pattern alerts from lint channel."""
    parts = []
    for alert in alerts[:3]:
        linter = alert.get("linter", "?")
        kind = alert.get("kind", "?")
        reason = alert.get("alert_reason", "")
        if reason == "recurring_across_runs":
            recent = alert.get("recent_run_count", 0)
            parts.append(f"PATTERN ALERT: [{linter}/{kind}] recurring across {recent} recent runs")
        elif reason == "single_run_volume":
            count = alert.get("count_this_run", 0)
            parts.append(f"PATTERN NOTE: [{linter}/{kind}] appeared {count} times this run")
    return "\n".join(parts)


def _format_repairs(repairs: list) -> str:
    """Format repair action suggestions."""
    parts = [f"SUGGESTED REPAIRS ({len(repairs)}):"]
    for r in repairs[:5]:
        safe_tag = " [safe]" if r.safe else " [review]"
        parts.append(f"  \u2022 {r.summary}{safe_tag}")
    if len(repairs) > 5:
        parts.append(f"  ... and {len(repairs) - 5} more repair actions")
    return "\n".join(parts)


def _format_proposed_constraints(proposals: list[dict]) -> str:
    """Format proposed constraints section (Section 9)."""
    parts = [f"PROPOSED CONSTRAINTS ({len(proposals)}):"]
    for p in proposals[:3]:
        rule_type = p.get("rule_type", "note")
        confidence = p.get("confidence", 0.0)
        rationale = p.get("rationale", "")
        rule = p.get("proposed_rule", "")
        conf_pct = f"{confidence * 100:.0f}%"
        parts.append(f"  [{rule_type}] ({conf_pct} confidence): {rule}")
        if rationale:
            parts.append(f"    Reason: {rationale}")
        # Surface drift warning if present
        if p.get("drift_warning"):
            tc = p.get("theory_coherence", {})
            contradicting = tc.get("contradicting_claims", []) if tc else []
            if contradicting:
                parts.append(
                    f"    DRIFT WARNING: contradicts theory claim: '{contradicting[0][:80]}'"
                )
            else:
                parts.append("    DRIFT WARNING: potential conflict with project theory")
    if len(proposals) > 3:
        parts.append(f"  ... and {len(proposals) - 3} more proposals")
    parts.append("  Use controlplane_agent_feedback to accept or reject.")
    return "\n".join(parts)


# ── Helpers ──────────────────────────────────────────────────────────────


def _short_path(filepath: str | None) -> str:
    """Shorten a file path for display."""
    if not filepath:
        return ""
    return os.path.basename(filepath)


def _estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.

    Uses a simple heuristic: ~4 characters per token.
    This avoids the tiktoken dependency for v1. If more precision
    is needed, tiktoken can be swapped in later.

    For v1, the 4-char heuristic is within 20% of tiktoken on
    typical lint output text.
    """
    return len(text) // 4
