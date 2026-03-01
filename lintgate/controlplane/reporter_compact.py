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
    cycle_alerts: list[str] | None = None,
    proven_resolutions: list[dict[str, Any]] | None = None,
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

    _attach_delta_or_blocking(compact, current_index, previous_finding_index, config)
    compact["channels"] = _build_channel_summary(mesh_result)

    # Git-aware scope signaling (#179): surface working tree context
    git_ctx = getattr(mesh_result, "git_context", None)
    if git_ctx and (git_ctx.get("modified_count") or git_ctx.get("untracked_count")):
        compact["git_context"] = {
            "branch": git_ctx.get("branch", ""),
            "modified_count": git_ctx.get("modified_count", 0),
            "untracked_count": git_ctx.get("untracked_count", 0),
            "uncommitted_loc_delta": git_ctx.get("uncommitted_loc_delta", 0),
        }
        if git_ctx.get("large_uncommitted_diff"):
            compact["git_context"]["advisory"] = (
                f"Large uncommitted diff: {git_ctx['modified_count']} modified, "
                f"{git_ctx['untracked_count']} untracked files. "
                "Working tree appears to be the active design surface."
            )

    # Bootstrap progress — insert when test channel signals bootstrap needed/running
    bootstrap_progress = _build_bootstrap_progress(mesh_result)
    if bootstrap_progress:
        compact["bootstrap"] = bootstrap_progress

    if ship_gate_parity is not None:
        compact["ship_gate_parity"] = ship_gate_parity

    if cycle_alerts:
        compact["cycle_alerts"] = cycle_alerts

    if proven_resolutions:
        compact["proven_resolutions"] = proven_resolutions

    compact["next_actions"] = _build_cp_next_actions(
        run_id,
        counts,
        symbol_blockers,
        ship_gate_parity,
        bootstrap_progress=bootstrap_progress,
    )

    if symbol_blockers:
        compact["remediation_loop"] = _build_remediation_loop(symbol_blockers)

    compact["finding_index"] = current_index

    # Work queue: dependency-ordered finding execution (#192)
    try:
        from lintgate.controlplane.work_queue import build_work_queue

        import_graph, file_map = _extract_import_graph(mesh_result)
        finding_list = list(current_index.values())
        if finding_list:
            wq = build_work_queue(finding_list, import_graph, file_map)
            if wq.items:
                compact["work_queue"] = wq.to_dict()
    except Exception:
        pass  # Non-fatal: work queue is advisory

    return compact


# ── Helpers ──────────────────────────────────────────────────────────────


def _extract_import_graph(
    mesh_result: MeshResult,
) -> tuple[dict[str, list[str] | set[str]], dict[str, str]]:
    """Extract import graph data from the structure channel result.

    Returns (import_graph, file_map). Both empty if the structure channel
    didn't run or didn't expose graph data.
    """
    for cr in mesh_result.channel_results:
        if cr.channel == "structure" and isinstance(cr.metrics, dict):
            ig = cr.metrics.get("_import_graph", {})
            fm = cr.metrics.get("_file_map", {})
            if isinstance(ig, dict) and isinstance(fm, dict):
                return ig, fm
    return {}, {}


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
    config: ControlPlaneConfig | None = None,
) -> None:
    """Add either a delta section or inline blocking issues to the report."""
    if previous_finding_index is not None:
        compact["delta"] = compute_finding_delta(current_index, previous_finding_index)
    else:
        # Provide a safe default of 5 if no config is available, but try to use config
        # We take a max over all channel configs for simplicity if flattening
        max_findings = 5
        if config and config.channels:
            channel_limits = [
                c.max_findings_shown
                for c in config.channels.values()
                if hasattr(c, "max_findings_shown")
            ]
            if channel_limits:
                max_findings = max(channel_limits)

        blocking_issues = [
            {**info, "fingerprint": fp}
            for fp, info in sorted(current_index.items())
            if info.get("severity") == "blocking"
        ]
        if blocking_issues:
            if len(blocking_issues) > max_findings:
                compact["blocking_truncated"] = len(blocking_issues) - max_findings
                blocking_issues = blocking_issues[:max_findings]
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
    bootstrap_progress: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build next_actions for ControlPlane compact output."""
    actions: list[dict[str, Any]] = []
    symbol_count = len(symbol_blockers or [])

    # Bootstrap action — when test files are missing, suggest bootstrap_tests
    if bootstrap_progress and bootstrap_progress.get("needed"):
        bs_status = bootstrap_progress.get("status")
        if bs_status in (None, "idle", "failed"):
            actions.append(
                {
                    "tool": "bootstrap_tests",
                    "args": {"path": "."},
                    "reason": "No test files detected. Run bootstrap to generate test scaffolding.",
                    "priority": 1,
                }
            )
        elif bs_status == "running":
            actions.append(
                {
                    "tool": "bootstrap_status",
                    "args": {"path": "."},
                    "reason": (
                        f"Bootstrap pipeline running "
                        f"(phase: {bootstrap_progress.get('phase', 'unknown')}). "
                        f"Check progress."
                    ),
                    "priority": 1,
                }
            )

    parity_status = ship_gate_parity.get("status") if ship_gate_parity else None
    parity_failing = parity_status in ("fail", "error")
    parity_missing = parity_status in ("unknown", "skipped", "stale")

    # Only emit parity actions when parity data is explicitly present.
    if ship_gate_parity and (
        parity_failing or (parity_missing and counts.get("blocking", 0) > 0)
    ):
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


# ── Bootstrap Progress ───────────────────────────────────────────────────


def _build_bootstrap_progress(mesh_result: MeshResult) -> dict[str, Any] | None:
    """Build bootstrap progress section if test channel signals bootstrap needed.

    Checks both the test channel metrics (for the trigger signal) and the
    persistent bootstrap state (for phase progress).
    """
    for cr in mesh_result.channel_results:
        if cr.channel != "tests":
            continue
        metrics = cr.metrics if isinstance(cr.metrics, dict) else {}
        if not metrics.get("bootstrap_needed"):
            return None

        # Bootstrap is needed — check persistent state for progress
        progress: dict[str, Any] = {
            "needed": True,
            "reason": metrics.get("bootstrap_reason", "zero_test_files"),
        }

        try:
            from lintgate.orchestration.bootstrap_state import PHASES, BootstrapState

            project_root = metrics.get("project_root", "")
            if project_root:
                state = BootstrapState.load(project_root)
                if state.status != "idle":
                    phase_idx = (
                        PHASES.index(state.phase) if state.phase in PHASES else 0
                    )
                    total_phases = len(PHASES) - 1  # exclude "not_started"
                    progress["status"] = state.status
                    progress["phase"] = state.phase
                    progress["phase_progress"] = f"{phase_idx}/{total_phases}"
                    progress["files_processed"] = len(state.files_processed)
                    progress["tests_generated"] = state.tests_generated
                    if state.error:
                        progress["error"] = state.error
        except Exception:
            pass  # Graceful degradation — bootstrap state is optional

        return progress

    return None


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
            symbol_key = str(
                evidence.get("symbol_key") or evidence.get("symbol") or ""
            ).strip()
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
