"""Compact ControlPlane reporter for MCP tool responses.

Extracted from reporter.py — formats MeshResult as compact JSON
suitable for MCP tool responses (as opposed to the verbose XML-style
systemMessage format used by the PostToolUse hook).
"""

from __future__ import annotations

from typing import Any

from .reporter_delta import build_finding_index, compute_finding_delta
from .types import ControlPlaneConfig, MeshResult


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
