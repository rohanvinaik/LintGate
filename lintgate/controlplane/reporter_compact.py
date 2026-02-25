"""Compact ControlPlane reporter for MCP tool responses.

Extracted from reporter.py — formats MeshResult as compact JSON
suitable for MCP tool responses (as opposed to the verbose XML-style
systemMessage format used by the PostToolUse hook).
"""

from __future__ import annotations

from typing import Any

from .reporter_delta import build_finding_index, compute_finding_delta
from .types import ChannelResult, CoherenceResult, ControlPlaneConfig, MeshResult


def format_mesh_report_compact(
    mesh_result: MeshResult,
    config: ControlPlaneConfig | None = None,
    previous_finding_index: dict[str, dict[str, Any]] | None = None,
    proposed_constraints: list[dict] | None = None,
    ship_gate_parity: dict[str, Any] | None = None,
    max_findings: int | None = None,
    scope: str = "project",
    files_analyzed: list[str] | None = None,
    output_budget: str = "standard",
) -> dict[str, Any]:
    """Format MeshResult as compact JSON for MCP tool responses.

    If previous_finding_index is provided, emits delta-first output.
    Otherwise emits full compact output with inline blocking issues.

    Returns a dict suitable for json.dumps().
    """
    if config is None:
        config = ControlPlaneConfig()

    from lintgate.state import generate_run_id

    # Use the event's stable ID so the compact report, saved run file,
    # and session snapshot all share the same run_id.
    run_id = mesh_result.event.event_id if mesh_result.event else generate_run_id()

    current_index = build_finding_index(mesh_result)
    severity_counts = _count_findings_by_severity(current_index)
    symbol_blockers = _collect_symbol_coverage_blockers(mesh_result)

    counts = _build_counts(mesh_result, severity_counts, symbol_blockers)
    compact: dict[str, Any] = {
        "run_id": run_id,
        "duration_ms": round(mesh_result.duration_ms, 1),
        "coherence": _build_coherence_dict(mesh_result.coherence),
        "counts": counts,
    }

    if max_findings is not None:
        truncated_items = _truncate_finding_index(current_index, mesh_result, max_findings)
        current_index = dict(truncated_items)
        findings_truncated = len(current_index) < len(severity_counts)  # Rough check but accurate
    else:
        findings_truncated = False

    _attach_delta_or_blocking(compact, current_index, previous_finding_index, output_budget)

    if output_budget != "minimal":
        compact["channels"] = _build_channel_summary(mesh_result)

    if ship_gate_parity is not None:
        compact["ship_gate_parity"] = ship_gate_parity

    compact["next_actions"] = _build_cp_next_actions(
        run_id, counts, symbol_blockers, ship_gate_parity
    )

    if symbol_blockers and output_budget != "minimal":
        compact["remediation_loop"] = _build_remediation_loop(symbol_blockers)

    if files_analyzed is None:
        files_analyzed = []

    if output_budget != "minimal":
        compact["finding_index"] = current_index

    compact["scope"] = scope
    compact["files_analyzed"] = files_analyzed if output_budget != "minimal" else []
    compact["max_findings"] = max_findings if max_findings is not None else len(current_index)

    # Calculate truthy findings truncated
    total_findings_count = sum(severity_counts.values())
    if max_findings is not None and total_findings_count > max_findings:
        findings_truncated = True
    else:
        findings_truncated = False

    compact["findings_truncated"] = findings_truncated

    return compact


# ── Helpers ──────────────────────────────────────────────────────────────


def _truncate_finding_index(
    finding_index: dict[str, dict[str, Any]],
    mesh_result: MeshResult,
    max_findings: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Deterministically truncate findings index.

    Sort order:
    1. Severity (blocking > warning > informational > other)
    2. Channel stability (failed > passed > other)
    3. Fingerprint (lexicographic)
    """
    if len(finding_index) <= max_findings:
        return list(finding_index.items())

    # Map channel to its status to penalize stable channels less
    channel_status = {cr.channel: cr.status for cr in mesh_result.channel_results}

    def _sort_key(item: tuple[str, dict[str, Any]]) -> tuple[int, int, str]:
        fp, info = item

        # 1. Severity rank
        sev = info.get("severity", "")
        sev_rank = {"blocking": 0, "warning": 1, "informational": 2}.get(sev, 3)

        # 2. Channel stability rank
        # We only really care if the channel failed
        status = channel_status.get(info.get("channel", ""), "unknown")
        status_rank = 0 if status == "fail" else (1 if status == "pass" else 2)

        # 3. Fingerprint
        return (sev_rank, status_rank, fp)

    sorted_items = sorted(finding_index.items(), key=_sort_key)
    return sorted_items[:max_findings]


def _count_findings_by_severity(
    finding_index: dict[str, dict[str, Any]],
) -> dict[str, int]:
    """Tally finding counts from the index, grouped by severity level."""
    totals: dict[str, int] = {"blocking": 0, "warning": 0, "informational": 0}
    for info in finding_index.values():
        sev = info.get("severity", "")
        count = int(info.get("count", 1))
        if sev in totals:
            totals[sev] += count
    return totals


def _build_coherence_dict(coherence: CoherenceResult) -> dict[str, Any]:
    """Build a compact coherence section from CoherenceResult."""
    result: dict[str, Any] = {
        "state": coherence.state,
        "summary": coherence.summary,
    }
    if coherence.recommended_action:
        result["action"] = coherence.recommended_action
    if coherence.confidence < 1.0:
        result["confidence"] = coherence.confidence
    if coherence.classification_notes:
        result["classification_notes"] = coherence.classification_notes
    return result


def _build_counts(
    mesh_result: MeshResult,
    severity_counts: dict[str, int],
    symbol_blockers: list[dict[str, Any]],
) -> dict[str, int]:
    """Assemble the top-level counts dict."""
    repairs_available = sum(len(cr.repairs) for cr in mesh_result.channel_results)
    channels_run = sum(1 for cr in mesh_result.channel_results if cr.status != "skip")
    return {
        "blocking": severity_counts["blocking"],
        "warning": severity_counts["warning"],
        "informational": severity_counts["informational"],
        "channels_run": channels_run,
        "repairs_available": repairs_available,
        "symbol_blocking": len(symbol_blockers),
    }


def _build_channel_summary(mesh_result: MeshResult) -> dict[str, str]:
    """Summarize each channel's status with finding severity breakdowns."""
    summary: dict[str, str] = {}
    for cr in mesh_result.channel_results:
        if cr.status == "skip":
            continue
        if cr.status == "fail":
            summary[cr.channel] = _format_fail_status(cr)
        elif cr.status == "pass":
            summary[cr.channel] = "pass"
        else:
            summary[cr.channel] = cr.status
    return summary


def _format_fail_status(cr: ChannelResult) -> str:
    """Format a failing channel's status string with severity breakdown."""
    labels = [
        (sum(1 for f in cr.findings if f.severity == "blocking"), "blocking"),
        (sum(1 for f in cr.findings if f.severity == "warning"), "warning"),
        (sum(1 for f in cr.findings if f.severity == "informational"), "info"),
    ]
    parts = [f"{n} {label}" for n, label in labels if n]
    return f"fail({', '.join(parts)})" if parts else "fail"


def _attach_delta_or_blocking(
    compact: dict[str, Any],
    current_index: dict[str, dict[str, Any]],
    previous_finding_index: dict[str, dict[str, Any]] | None,
    output_budget: str = "standard",
) -> None:
    """Add either a delta section or inline blocking issues to the report."""
    if previous_finding_index is not None:
        compact["delta"] = compute_finding_delta(current_index, previous_finding_index)
    else:
        blocking_issues = []
        for fp, info in sorted(current_index.items()):
            if info.get("severity") == "blocking":
                issue = {**info, "fingerprint": fp}
                # Minimal mode: strip heavy detail (Issue #152)
                if output_budget == "minimal" and "message" in issue:
                    issue["message"] = str(issue["message"]).split("\n")[0][:120]
                    for key in ["hint", "context", "evidence", "repair_proposals"]:
                        issue.pop(key, None)
                blocking_issues.append(issue)

        if blocking_issues:
            compact["blocking_issues"] = blocking_issues


def _build_remediation_loop(
    symbol_blockers: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the remediation_loop section for symbol coverage blockers."""
    return {
        "required": True,
        "type": "symbol_coverage",
        "blocking_symbols": symbol_blockers[:25],
        "exit_condition": "counts.symbol_blocking == 0 AND counts.blocking == 0",
        "policy": (
            "Add tests for uncovered symbols, rerun controlplane_run, and repeat "
            "until no symbol coverage blockers remain."
        ),
    }


# ── Next Actions ─────────────────────────────────────────────────────────


def _build_cp_next_actions(
    run_id: str,
    counts: dict[str, int],
    symbol_blockers: list[dict[str, Any]] | None = None,
    ship_gate_parity: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build next_actions for ControlPlane compact output."""
    actions: list[dict[str, Any]] = []
    symbol_count = len(symbol_blockers or [])

    parity_status = ship_gate_parity.get("status") if ship_gate_parity else None
    parity_failing = parity_status in ("fail", "error")
    parity_missing = parity_status in ("unknown", "skipped", "stale")

    # Only emit parity actions when parity data is explicitly present.
    if ship_gate_parity and (parity_failing or (parity_missing and counts.get("blocking", 0) > 0)):
        actions.append(
            {
                "tool": "controlplane_run" if parity_missing else "terminal",
                "args": (
                    {"path": ".", "strictness": "strict"}
                    if parity_missing
                    else {"command": "python scripts/ship_main.py --preflight"}
                ),
                "reason": "Ship gate parity is failing or missing. Evaluate strict preflight output.",
                "priority": 1,
            }
        )

    if symbol_count > 0:
        actions.append(
            {
                "tool": "controlplane_get_details",
                "args": {
                    "run_id": run_id,
                    "channel": "tests",
                    "severity": "blocking",
                    "max_issues": 50,
                },
                "reason": (
                    f"Inspect {symbol_count} symbol coverage blocker"
                    f"{'s' if symbol_count != 1 else ''}"
                ),
                "priority": 1,
            }
        )
        actions.append(
            {
                "tool": "controlplane_run",
                "args": {"path": "."},
                "reason": "After adding tests, rerun to verify blockers are cleared.",
                "priority": 2,
            }
        )

    if counts.get("blocking", 0) > 0:
        actions.append(
            {
                "tool": "controlplane_get_details",
                "args": {"run_id": run_id, "severity": "blocking"},
                "reason": f"View {counts['blocking']} blocking finding{'s' if counts['blocking'] != 1 else ''}",
                "priority": 3 if symbol_count > 0 else 1,
            }
        )
    if counts.get("repairs_available", 0) > 0:
        actions.append(
            {
                "tool": "controlplane_apply_repairs",
                "args": {"path": ".", "safe_only": True},
                "reason": f"{counts['repairs_available']} safe repair{'s' if counts['repairs_available'] != 1 else ''} available",
                "priority": 4 if symbol_count > 0 else 2,
            }
        )
    if counts.get("warning", 0) > 0 and run_id:
        actions.append(
            {
                "tool": "controlplane_get_details",
                "args": {"run_id": run_id, "severity": "warning"},
                "reason": f"View {counts['warning']} warning{'s' if counts['warning'] != 1 else ''}",
                "priority": 5 if symbol_count > 0 else 3,
            }
        )
    return actions


# ── Symbol Coverage Blockers ─────────────────────────────────────────────


def _collect_symbol_coverage_blockers(mesh_result: MeshResult) -> list[dict[str, Any]]:
    """Extract blocking symbol-coverage findings from tests channel."""
    blockers: list[dict[str, Any]] = []
    for channel_result in mesh_result.channel_results:
        if channel_result.channel != "tests":
            continue
        for finding in channel_result.findings:
            if str(getattr(finding, "severity", "")).lower() != "blocking":
                continue
            kind = str(getattr(finding, "kind", "") or "")
            if kind not in {"symbol_uncovered", "unresolved_required_symbol"}:
                continue

            evidence = finding.evidence if isinstance(finding.evidence, dict) else {}
            symbol_key = str(evidence.get("symbol_key") or evidence.get("symbol") or "").strip()
            if not symbol_key:
                symbol_key = str(finding.message or "").strip()[:200]

            blocker: dict[str, Any] = {
                "kind": kind,
                "symbol": symbol_key,
            }
            if finding.file:
                blocker["file"] = finding.file
            missing_lines = evidence.get("missing_lines")
            if isinstance(missing_lines, list) and missing_lines:
                blocker["missing_lines"] = missing_lines[:12]
            blockers.append(blocker)
    return blockers
