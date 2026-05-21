"""ControlPlane tools — thin subprocess wrappers around scripts/controlplane_run.py.

All computation lives in scripts/controlplane_run.py, which delegates to the
_impl_* functions in mcp_tools/_controlplane_impl_*.py. Those impl modules stay
in place — they house the orchestration logic shared with scripts and with
existing tests. This module re-exports the same symbols the old module did so
downstream tests keep importing them here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from ._controlplane_impl_details import (  # noqa: F401
    _DEFAULT_SECTIONS,
    _SECTION_POPULATORS,
    _build_config_status,
    _build_details_next_actions,
    _extract_channel_details,
    _extract_evidence,
    _extract_findings,
    _extract_proven_resolutions_from_details,
    _extract_repairs,
    _filter_channels,
    _get_session_status,
    _impl_controlplane_get_details,
    _impl_controlplane_status,
    _populate_findings_section,
)
from ._controlplane_impl_feedback import (  # noqa: F401
    _build_feedback_result,
    _collect_pending_repairs,
    _execute_safe_delete,
    _execute_single_repair,
    _generate_living_context_patches,
    _impl_controlplane_agent_feedback,
    _impl_controlplane_apply_repairs,
    _load_all_repairs,
    _process_accepted_constraints,
    _process_rejected_constraints,
    _process_test_failure_classifications,
    _process_tuned_findings,
    _record_disagreement,
)
from ._controlplane_impl_run import (  # noqa: F401
    _ALL_CHANNEL_NAMES,
    _AVAILABLE_CHANNEL_DESCRIPTIONS,
    _CYCLE_REASON_TEMPLATES,
    _KNOWN_SCOPES,
    _accumulate_delivery_metrics,
    _append_schema_findings,
    _apply_exit_gate_to_compact,
    _build_channel_registry,
    _build_run_result,
    _build_supervision_event,
    _check_exit_gate,
    _check_ship_gate_parity,
    _check_theory_staleness_for_compact,
    _collect_files_for_event,
    _compute_dynamic_budget_ms,
    _dedup_files,
    _detect_edit_cycles,
    _execute_channels,
    _extract_proven_resolutions,
    _impl_controlplane_run,
    _inject_behavior_priors,
    _persist_behavior_compass_delta,
    _persist_global_profile_delta,
    _persist_runtime_state,
    _persist_session_after_mesh,
    _record_tool_event_for_behavior,
    _resolve_explicit_files,
    _resolve_git_changed_files,
    _resolve_scope_files,
    _RunContext,
    _save_run_details_for_drilldown,
    _select_channels,
    _setup_run,
    _setup_session,
    _update_refactor_state,
    _validate_channel_wiring,
)

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts",
    "controlplane_run.py",
)

# Error prefixes that should become ValueError in the MCP layer
_VALUE_ERROR_PREFIXES = (
    "Invalid outcome",
    "Source file not found",
    "Invalid severity",
)


def _run_script(*args: str, timeout: float = 600.0) -> str:
    """Invoke scripts/controlplane_run.py as a subprocess and relay stdout.

    Raises ValueError for known validation error prefixes to preserve the
    pre-subprocess MCP contract (backward compat for pytest.raises callers).
    """
    try:
        proc = subprocess.run(
            [sys.executable, _SCRIPT, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"error": "controlplane_run subprocess timed out"})
    except OSError as exc:
        return json.dumps({"error": f"controlplane_run subprocess failed: {exc}"})

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return json.dumps({
            "error": f"controlplane_run exit {proc.returncode}",
            "stderr": (proc.stderr or "").strip()[-500:],
        })

    last = stdout.splitlines()[-1]
    try:
        parsed = json.loads(last)
    except json.JSONDecodeError:
        return stdout
    if isinstance(parsed, dict) and "error" in parsed and "analysis_id" not in parsed:
        msg = str(parsed["error"])
        for prefix in _VALUE_ERROR_PREFIXES:
            if msg.startswith(prefix):
                raise ValueError(msg)
    return stdout


def register(mcp, helpers):
    """Register ControlPlane tools on the shared MCP instance."""
    del helpers  # unused — the script provides its own helpers dict

    @mcp.tool()
    def controlplane_run(
        path: str,
        channels: str | None = None,
        strictness: str = "normal",
        scope: str | None = None,
        files: list[str] | None = None,
    ) -> str:
        """Run a comprehensive project health check across multiple dimensions."""
        args = ["run", path, "--strictness", strictness]
        if channels:
            args.extend(["--channels", channels])
        if scope:
            args.extend(["--scope", scope])
        for f in files or []:
            args.extend(["--file", f])
        return _run_script(*args)

    @mcp.tool()
    def controlplane_get_details(
        run_id: str,
        channel: str | None = None,
        severity: str | None = None,
        max_issues: int = 10,
        sections: list[str] | None = None,
        top_n: int | None = None,
        time_budget_minutes: float | None = None,
        finding_domain: str | None = None,
    ) -> str:
        """Drill into a previous ControlPlane run by run_id."""
        args = ["get-details", run_id, "--max-issues", str(max_issues)]
        if channel:
            args.extend(["--channel", channel])
        if severity:
            args.extend(["--severity", severity])
        for s in sections or []:
            args.extend(["--section", s])
        if top_n is not None:
            args.extend(["--top-n", str(top_n)])
        if time_budget_minutes is not None:
            args.extend(["--time-budget-minutes", str(time_budget_minutes)])
        if finding_domain:
            args.extend(["--finding-domain", finding_domain])
        return _run_script(*args)

    @mcp.tool()
    def controlplane_status(path: str | None = None) -> str:
        """Show ControlPlane status for a project."""
        args = ["status"]
        if path:
            args.extend(["--path", path])
        return _run_script(*args)

    @mcp.tool()
    def controlplane_test_skeleton(path: str, target_file: str) -> str:
        """Generate a test skeleton for a source file."""
        return _run_script("test-skeleton", path, "--target-file", target_file)

    @mcp.tool()
    def controlplane_report_repair(
        path: str,
        action_id: str,
        outcome: str = "applied",
    ) -> str:
        """Report the outcome of a proposed repair action."""
        return _run_script(
            "report-repair", path, "--action-id", action_id, "--outcome", outcome
        )

    @mcp.tool()
    def controlplane_agent_feedback(
        path: str,
        run_id: str | None = None,
        disagreement: str | None = None,
        accepted_constraints: list[str] | None = None,
        rejected_constraints: list[str] | None = None,
        tuned_findings: list[dict] | None = None,
        test_failure_classifications: list[dict] | None = None,
    ) -> str:
        """Provide agent feedback on ControlPlane findings or constraint proposals."""
        args = ["agent-feedback", path]
        if run_id:
            args.extend(["--run-id", run_id])
        if disagreement:
            args.extend(["--disagreement", disagreement])
        for c in accepted_constraints or []:
            args.extend(["--accept", c])
        for c in rejected_constraints or []:
            args.extend(["--reject", c])
        if tuned_findings is not None:
            args.extend(["--tuned-json", json.dumps(tuned_findings)])
        if test_failure_classifications is not None:
            args.extend(["--classifications-json", json.dumps(test_failure_classifications)])
        return _run_script(*args)

    @mcp.tool()
    def controlplane_apply_repairs(
        path: str,
        action_ids: list[str] | None = None,
        safe_only: bool = True,
        run_id: str | None = None,
    ) -> str:
        """Execute proposed repair actions from a ControlPlane run."""
        args = ["apply-repairs", path]
        for aid in action_ids or []:
            args.extend(["--action-id", aid])
        if not safe_only:
            args.append("--unsafe")
        if run_id:
            args.extend(["--run-id", run_id])
        return _run_script(*args)

    @mcp.tool()
    def controlplane_get_work_queue(run_id: str, max_items: int = 25) -> str:
        """Get the dependency-ordered work queue from a cached ControlPlane run."""
        return _run_script("get-work-queue", run_id, "--max-items", str(max_items))

    @mcp.tool()
    def controlplane_execute(
        path: str,
        budget_s: float = 300.0,
        max_files: int = 10,
        safe_only: bool = True,
        exclusion_set: list[str] | None = None,
    ) -> str:
        """Single-command project improvement: analyze, repair, generate tests, validate."""
        args = [
            "execute", path,
            "--budget-s", str(budget_s),
            "--max-files", str(max_files),
        ]
        if not safe_only:
            args.append("--unsafe")
        for e in exclusion_set or []:
            args.extend(["--exclude", e])
        return _run_script(*args)

    return {
        "controlplane_run": controlplane_run,
        "controlplane_get_details": controlplane_get_details,
        "controlplane_status": controlplane_status,
        "controlplane_test_skeleton": controlplane_test_skeleton,
        "controlplane_report_repair": controlplane_report_repair,
        "controlplane_agent_feedback": controlplane_agent_feedback,
        "controlplane_apply_repairs": controlplane_apply_repairs,
        "controlplane_get_work_queue": controlplane_get_work_queue,
        "controlplane_execute": controlplane_execute,
    }
