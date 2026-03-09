"""Section formatters for ControlPlane verbose XML-style reporter.

Extracted from reporter.py -- contains all _format_* helpers that build
individual sections of the PostToolUse hook report, plus the shared
utility functions _short_path and _estimate_tokens.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .types import ChannelResult, MeshResult


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


# ── Budget tracker ───────────────────────────────────────────────────────


class _BudgetTracker:
    """Tracks token budget and accumulates report parts."""

    def __init__(self, max_tokens: int) -> None:
        self.parts: list[str] = []
        self.token_estimate: int = 0
        self.max_tokens: int = max_tokens

    def try_append(self, section: str) -> bool:
        """Append section if it fits within the remaining budget."""
        section_tokens = _estimate_tokens(section)
        if self.token_estimate + section_tokens <= self.max_tokens:
            self.parts.append(section)
            self.token_estimate += section_tokens
            return True
        return False


# ── Section assembly ─────────────────────────────────────────────────────


def _assemble_report_sections(
    *,
    mesh_result: MeshResult,
    display_findings: list,
    all_findings: list,
    active_channels: list[ChannelResult],
    delta: dict[str, Any] | None,
    resurfaced_count: int,
    max_tokens: int,
    disposition: str | None,
    proposed_constraints: list[dict] | None,
    cycle_alerts: list[str] | None,
) -> tuple[list[str], int, int, int]:
    """Assemble all report sections against a token budget.

    Returns (parts, token_estimate, shown_blocking_count, shown_warnings_count)
    where parts is the list of formatted section strings.
    """
    bt = _BudgetTracker(max_tokens)

    # Section 1: Header
    header = _format_header(mesh_result)
    if not bt.try_append(header):
        bt.try_append(f'<controlplane-report coherence="{mesh_result.coherence.state}">')

    # Section 1.5: Disposition Nudge
    if disposition:
        bt.try_append(f"DISPOSITION: {disposition}")

    # Delta summary
    if delta is not None:
        bt.token_estimate = _append_delta_summary(
            bt.parts, delta, resurfaced_count, bt.token_estimate, max_tokens
        )

    # Section 2: Blocking findings
    blocking = [f for f in display_findings if f.severity == "blocking"]
    if blocking:
        bt.token_estimate = _append_blocking_section(
            bt.parts, blocking, bt.token_estimate, max_tokens
        )

    # Section 3: Coherence summary
    coherence = mesh_result.coherence
    if coherence.state != "stable" or coherence.summary:
        bt.try_append(_format_coherence(coherence))

    # Section 4: Warnings
    warnings = [f for f in display_findings if f.severity == "warning"]
    if warnings:
        bt.try_append(_format_warnings(warnings))

    # Section 5: Incomplete channels
    if mesh_result.partial:
        bt.try_append(_format_incomplete(mesh_result.incomplete_channels))

    # Section 6: Channel status
    bt.try_append(_format_channel_summary(active_channels))

    # Section 7: Pattern alerts
    lint_results = [cr for cr in mesh_result.channel_results if cr.channel == "lint"]
    if lint_results:
        pattern_alerts = lint_results[0].metrics.get("pattern_alerts", [])
        if pattern_alerts:
            bt.try_append(_format_pattern_alerts(pattern_alerts))

    # Section 8: Repairs
    all_repairs = [r for cr in mesh_result.channel_results for r in cr.repairs]
    if all_repairs:
        bt.try_append(_format_repairs(all_repairs))

    # Informational count
    display_informational = [f for f in display_findings if f.severity == "informational"]
    total_informational = [f for f in all_findings if f.severity == "informational"]
    if display_informational or (total_informational and delta is None):
        shown = display_informational if delta is not None else total_informational
        bt.try_append(f"INFO: {len(shown)} informational finding{'s' if len(shown) != 1 else ''}")

    # Section 9: Proposed constraints
    if proposed_constraints:
        active_proposals = [c for c in proposed_constraints if c.get("status") == "proposed"]
        if active_proposals:
            bt.try_append(_format_proposed_constraints(active_proposals))

    # Section 10: Cycle alerts
    if cycle_alerts:
        bt.try_append(
            "CYCLE ALERTS (Repetitive behavior detected):\n  " + "\n  ".join(cycle_alerts)
        )

    return bt.parts, bt.token_estimate, len(blocking), len(warnings)


def _append_delta_summary(
    parts: list[str],
    delta: dict[str, Any],
    resurfaced_count: int,
    token_estimate: int,
    max_tokens: int,
) -> int:
    """Append the delta summary line if it fits within the budget."""
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
    return token_estimate


def _append_blocking_section(
    parts: list[str],
    blocking: list,
    token_estimate: int,
    max_tokens: int,
) -> int:
    """Append blocking findings with per-finding granularity fallback."""
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
                    reduced += (
                        f"\n  ...and {overflow} more blocking issue{'s' if overflow != 1 else ''}"
                    )
                parts.append(reduced)
                token_estimate += _estimate_tokens(reduced)
                break
    return token_estimate
