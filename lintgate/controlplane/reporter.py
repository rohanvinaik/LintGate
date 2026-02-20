"""ControlPlane mesh reporter — token-budgeted output formatting.

Converts MeshResult into systemMessage + hookSpecificOutput JSON
suitable for Claude Code's PostToolUse hook protocol.

Token budget strategy (OTP-inspired):
- Only inject non-zero channels (silent channels omitted — silence IS info)
- Strict priority order for sections
- Token counting + truncation of low-priority sections
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

Backward compatibility:
- When only lint channel has findings → produce output compatible with
  legacy agent_reporter.format_report() format
- hookSpecificOutput.lint_blocking and blocking_count preserved
- New hookSpecificOutput.controlplane object added alongside
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from .types import ChannelResult, ControlPlaneConfig, MeshResult


def format_mesh_report(
    mesh_result: MeshResult,
    config: ControlPlaneConfig | None = None,
    proposed_constraints: list[dict] | None = None,
) -> dict[str, Any]:
    """Format MeshResult as JSON for Claude Code systemMessage.

    Args:
        mesh_result: Complete mesh execution result.
        config: ControlPlane config (for token budget policy).
        proposed_constraints: Optional constraint proposals from session memory.

    Returns:
        JSON dict with systemMessage key. Empty dict {} if no issues.
    """
    if config is None:
        config = ControlPlaneConfig()

    # Collect findings across all channels
    all_findings = []
    active_channels = []
    for cr in mesh_result.channel_results:
        if cr.status not in ("skip",):
            active_channels.append(cr)
        if cr.findings:
            all_findings.extend(cr.findings)

    # Quick exit: nothing to report
    if not all_findings and not mesh_result.partial:
        # Check if there are error/timeout channels
        has_problems = any(cr.status in ("error", "timeout") for cr in mesh_result.channel_results)
        if not has_problems:
            return {}

    max_tokens = config.token_policy.hook_max_tokens
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

    # Section 2: Blocking findings (with per-finding granularity)
    blocking = [f for f in all_findings if f.severity == "blocking"]
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
    warnings = [f for f in all_findings if f.severity == "warning"]
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

    # Informational count
    informational = [f for f in all_findings if f.severity == "informational"]
    if informational:
        section = f"INFO: {len(informational)} informational finding{'s' if len(informational) != 1 else ''}"
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

    # Close tag
    parts.append("</controlplane-report>")

    # Count truncated sections
    total_possible = 9  # Max sections we might have
    sections_included = len(parts) - 2  # Minus header and close tag
    truncated_count = max(0, total_possible - sections_included)
    hidden_findings = (
        len(all_findings) - len(blocking) - min(len(warnings), 3) - min(len(informational), 1)
    )

    if truncated_count > 0 and hidden_findings > 0:
        parts.insert(
            -1,
            f"[Truncated: {truncated_count} sections omitted, {hidden_findings} findings hidden]",
        )

    message = "\n".join(parts)
    output: dict[str, Any] = {"systemMessage": message}

    # Build hookSpecificOutput
    hook_output: dict[str, Any] = {}

    # Legacy compatibility: lint_blocking field
    if blocking:
        hook_output["lint_blocking"] = True
        hook_output["blocking_count"] = len(blocking)
        hook_output["issues_json"] = [f.to_dict() for f in blocking[:10]]

    # ControlPlane-specific output
    cp_output: dict[str, Any] = {
        "coherence_state": mesh_result.coherence.state,
        "channels_run": len(active_channels),
        "partial": mesh_result.partial,
        "duration_ms": mesh_result.duration_ms,
        "channel_statuses": {
            cr.channel: cr.status for cr in mesh_result.channel_results if cr.status != "skip"
        },
    }
    if mesh_result.coherence.confidence < 1.0:
        cp_output["coherence_confidence"] = mesh_result.coherence.confidence
    hook_output["controlplane"] = cp_output

    if hook_output:
        output["hookSpecificOutput"] = hook_output

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
        icon = {"pass": "✓", "fail": "✗", "error": "⚠", "timeout": "⏱"}.get(cr.status, "?")
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
        parts.append(f"  • {r.summary}{safe_tag}")
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


# ── Finding Fingerprint & Delta ──────────────────────────────────────────

_SEVERITY_ORDER = {"informational": 0, "warning": 1, "blocking": 2}


def compute_finding_fingerprint(finding: Any, channel: str) -> str:
    """Stable fingerprint: channel|kind|normalized_file_path|msg_hash.

    Excludes line number — survives edits that shift lines.
    """
    file_path = (getattr(finding, "file", None) or "").replace("\\", "/")
    msg = getattr(finding, "message", "") or ""
    # Hash the full message to avoid prefix collisions when messages share a start.
    msg_hash = hashlib.sha256(msg.encode()).hexdigest()[:8]
    raw = f"{channel}|{getattr(finding, 'kind', '')}|{file_path}|{msg_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def build_finding_index(mesh_result: MeshResult) -> dict[str, dict[str, Any]]:
    """Build fingerprint → finding-summary dict from a MeshResult."""
    index: dict[str, dict[str, Any]] = {}
    for cr in mesh_result.channel_results:
        for f in cr.findings:
            fp = compute_finding_fingerprint(f, cr.channel)
            existing = index.get(fp)
            if existing is None:
                index[fp] = {
                    "channel": cr.channel,
                    "kind": getattr(f, "kind", ""),
                    "severity": getattr(f, "severity", ""),
                    "confidence": getattr(f, "confidence", None),
                    "file": f.short_location() if hasattr(f, "short_location") else "",
                    "message": (getattr(f, "message", "") or "")[:80],
                    "line": getattr(f, "line", None),
                    "count": 1,
                }
            else:
                existing["count"] = int(existing.get("count", 1)) + 1
                # Keep the highest-severity label for this fingerprint bucket.
                cur_rank = _SEVERITY_ORDER.get(str(existing.get("severity", "")), 0)
                new_sev = str(getattr(f, "severity", ""))
                new_rank = _SEVERITY_ORDER.get(new_sev, 0)
                if new_rank > cur_rank:
                    existing["severity"] = new_sev
    return index


def compute_finding_delta(
    current_index: dict[str, dict[str, Any]],
    previous_index: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Compute new/escalated/resolved/still_active from two finding indexes."""
    current_fps = set(current_index)
    previous_fps = set(previous_index)

    new_fps = current_fps - previous_fps
    resolved_fps = previous_fps - current_fps
    common_fps = current_fps & previous_fps

    new_findings = []
    escalated = []
    still_active_count = 0
    resolved_count = 0

    for fp in common_fps:
        current = current_index[fp]
        previous = previous_index[fp]
        cur_count = int(current.get("count", 1))
        prev_count = int(previous.get("count", 1))
        shared = min(cur_count, prev_count)

        cur_sev = _SEVERITY_ORDER.get(str(current.get("severity", "")), 0)
        prev_sev = _SEVERITY_ORDER.get(str(previous.get("severity", "")), 0)
        if cur_sev > prev_sev and shared > 0:
            escalated.append(
                {
                    **current,
                    "fingerprint": fp,
                    "previous_severity": previous.get("severity", ""),
                    "count": shared,
                }
            )
        else:
            still_active_count += shared

        if cur_count > prev_count:
            extra = cur_count - prev_count
            new_findings.append({**current, "fingerprint": fp, "count": extra})
        elif prev_count > cur_count:
            resolved_count += prev_count - cur_count

    for fp in sorted(new_fps):
        current = current_index[fp]
        new_findings.append({**current, "fingerprint": fp, "count": int(current.get("count", 1))})

    for fp in resolved_fps:
        previous = previous_index[fp]
        resolved_count += int(previous.get("count", 1))

    return {
        "new": new_findings,
        "escalated": escalated,
        "resolved_count": resolved_count,
        "still_active_count": still_active_count,
    }


# ── Compact ControlPlane Reporter ────────────────────────────────────────


def format_mesh_report_compact(
    mesh_result: MeshResult,
    config: ControlPlaneConfig | None = None,
    previous_finding_index: dict[str, dict[str, Any]] | None = None,
    proposed_constraints: list[dict] | None = None,
) -> dict[str, Any]:
    """Format MeshResult as compact JSON for MCP tool responses.

    If previous_finding_index is provided, emits delta-first output.
    Otherwise emits full compact output with inline blocking issues.

    Returns a dict suitable for json.dumps().
    """
    if config is None:
        config = ControlPlaneConfig()

    from lintgate.state import generate_run_id

    run_id = generate_run_id()

    # Build current finding index
    current_index = build_finding_index(mesh_result)

    # Count findings by severity (account for aggregated duplicate counts).
    blocking_count = 0
    warning_count = 0
    informational_count = 0
    for _fp, info in current_index.items():
        sev = info.get("severity", "")
        count = int(info.get("count", 1))
        if sev == "blocking":
            blocking_count += count
        elif sev == "warning":
            warning_count += count
        elif sev == "informational":
            informational_count += count

    # Count repairs
    repairs_available = sum(len(cr.repairs) for cr in mesh_result.channel_results)

    # Coherence
    coherence = mesh_result.coherence
    coherence_dict: dict[str, Any] = {
        "state": coherence.state,
        "summary": coherence.summary,
    }
    if coherence.recommended_action:
        coherence_dict["action"] = coherence.recommended_action
    if coherence.confidence < 1.0:
        coherence_dict["confidence"] = coherence.confidence
    if coherence.classification_notes:
        coherence_dict["classification_notes"] = coherence.classification_notes

    # Counts
    channels_run = sum(1 for cr in mesh_result.channel_results if cr.status != "skip")
    counts = {
        "blocking": blocking_count,
        "warning": warning_count,
        "informational": informational_count,
        "channels_run": channels_run,
        "repairs_available": repairs_available,
    }

    # Channel summary
    channel_summary: dict[str, str] = {}
    for cr in mesh_result.channel_results:
        if cr.status == "skip":
            continue
        if cr.status == "pass":
            channel_summary[cr.channel] = "pass"
        elif cr.status == "fail":
            parts = []
            b = sum(1 for f in cr.findings if f.severity == "blocking")
            w = sum(1 for f in cr.findings if f.severity == "warning")
            i = sum(1 for f in cr.findings if f.severity == "informational")
            if b:
                parts.append(f"{b} blocking")
            if w:
                parts.append(f"{w} warning")
            if i:
                parts.append(f"{i} info")
            channel_summary[cr.channel] = f"fail({', '.join(parts)})" if parts else "fail"
        else:
            channel_summary[cr.channel] = cr.status

    # Build compact output
    compact: dict[str, Any] = {
        "run_id": run_id,
        "duration_ms": round(mesh_result.duration_ms, 1),
        "coherence": coherence_dict,
        "counts": counts,
    }

    if previous_finding_index is not None:
        # Delta mode
        delta = compute_finding_delta(current_index, previous_finding_index)
        compact["delta"] = delta
    else:
        # First-run mode: inline blocking issues
        blocking_issues = [
            {**info, "fingerprint": fp}
            for fp, info in sorted(current_index.items())
            if info.get("severity") == "blocking"
        ]
        if blocking_issues:
            compact["blocking_issues"] = blocking_issues

    compact["channels"] = channel_summary

    # Next actions
    compact["next_actions"] = _build_cp_next_actions(run_id, counts)

    # Finding index for storage (used by session memory)
    compact["finding_index"] = current_index

    return compact


def _build_cp_next_actions(run_id: str, counts: dict[str, int]) -> list[dict[str, Any]]:
    """Build next_actions for ControlPlane compact output."""
    actions: list[dict[str, Any]] = []
    if counts.get("blocking", 0) > 0:
        actions.append(
            {
                "tool": "controlplane_get_details",
                "args": {"run_id": run_id, "severity": "blocking"},
                "reason": f"View {counts['blocking']} blocking finding{'s' if counts['blocking'] != 1 else ''}",
                "priority": 1,
            }
        )
    if counts.get("repairs_available", 0) > 0:
        actions.append(
            {
                "tool": "controlplane_apply_repairs",
                "args": {"path": ".", "safe_only": True},
                "reason": f"{counts['repairs_available']} safe repair{'s' if counts['repairs_available'] != 1 else ''} available",
                "priority": 2,
            }
        )
    if counts.get("warning", 0) > 0 and run_id:
        actions.append(
            {
                "tool": "controlplane_get_details",
                "args": {"run_id": run_id, "severity": "warning"},
                "reason": f"View {counts['warning']} warning{'s' if counts['warning'] != 1 else ''}",
                "priority": 3,
            }
        )
    return actions


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
