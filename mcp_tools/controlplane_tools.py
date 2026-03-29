"""ControlPlane tools — controlplane_run, controlplane_get_details, controlplane_status,
controlplane_test_skeleton, controlplane_report_repair, controlplane_agent_feedback,
controlplane_apply_repairs.

Implementation functions live in:
- _controlplane_impl_run.py      (run, channel selection, file resolution, persistence)
- _controlplane_impl_details.py  (details drill-down, status)
- _controlplane_impl_feedback.py (agent feedback, repairs)
"""

from __future__ import annotations

import json
import os
from typing import Literal

from mcp_tools._disk_helpers import _safe_json, tool_response

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

# ── Registration ────────────────────────────────────────────────────────


def register(mcp, helpers):
    """Register ControlPlane tools on the shared MCP instance."""

    @mcp.tool()
    def controlplane_run(
        path: str,
        channels: str | None = None,
        strictness: Literal["relaxed", "normal", "strict"] = "normal",
        scope: Literal["project", "changed", "staged", "files", "full_sweep"] | None = None,
        files: list[str] | None = None,
    ) -> str:
        """Run a comprehensive project health check across multiple dimensions.

        WHEN TO USE: At the start of a session to understand project state, or after
        significant changes. This is the most thorough single analysis available.
        Works without any configuration file.

        Example: controlplane_run(path="/my/project")

        Runs 6 independent analysis channels in parallel: lint (code quality),
        tests (coverage and health), deps (dependency issues), git (hygiene),
        behavior (patterns across sessions), structure (codebase architecture).
        Returns compact findings with a run_id.
        Use controlplane_get_details(run_id) to drill into specific findings.

        Args:
            path: Project root path.
            channels: Comma-separated channel list (default: all). Options: lint,tests,deps,git,behavior,structure
            strictness: Strictness level for analysis.
            scope: The scope of files to analyze. Defaults to "changed".
                Use "full_sweep" for project-wide refactoring (no 50-file cap).
            files: Explicit list of files to analyze when scope="files".
        """
        # impl now returns a slim tool_response() string directly (data saved to disk)
        return str(_impl_controlplane_run(path, channels, strictness, scope, files, helpers))

    @mcp.tool()
    def controlplane_get_details(
        run_id: str,
        channel: str | None = None,
        severity: str | None = None,
        max_issues: int = 10,
        sections: list[str] | None = None,
        top_n: int | None = None,
        time_budget_minutes: float | None = None,
        finding_domain: Literal["all", "code", "environment"] | None = None,
    ) -> str:
        """Drill into a previous ControlPlane run by run_id.

        WHEN TO USE: After controlplane_run returns findings. The compact output
        shows counts and summaries — use this to see full issue details, evidence,
        and suggested repairs. Includes a code-vs-environment summary so dependency
        CVEs do not drown out code findings.

        Example: controlplane_get_details(run_id="cp_abc123")
        ROI example: controlplane_get_details(run_id="cp_abc123", time_budget_minutes=30)

        Args:
            run_id: The run_id from a controlplane_run response.
            channel: Filter findings by channel (lint, tests, deps, git, behavior, structure).
            severity: Filter by severity (blocking, warning, informational).
            max_issues: Maximum findings to return (default 10).
            sections: Which sections to include. Default: all.
                Options: "findings", "channel_details", "evidence", "repairs", "coherence", "next_actions", "proven_resolutions"
            top_n: Return the N highest-ROI findings (sorted by value-per-effort).
            time_budget_minutes: Return findings that fit within this time budget,
                sorted by ROI. E.g., 30 = "best fixes in 30 minutes."
            finding_domain: Optional bucket filter. Use "code" to exclude environment
                findings such as dependency CVEs; use "environment" for the inverse.
        """
        try:
            # impl now returns a slim tool_response() string directly (data saved to disk)
            return str(
                _impl_controlplane_get_details(
                    run_id,
                    channel,
                    severity,
                    max_issues,
                    sections,
                    helpers,
                    finding_domain=finding_domain,
                    top_n=top_n,
                    time_budget_minutes=time_budget_minutes,
                )
            )
        except (ValueError, FileNotFoundError) as exc:
            # Run not found — return error with pointer to disk file if it exists
            disk_file = os.path.join(
                os.getcwd(), ".lintgate", "analysis", "controlplane_run", f"{run_id}.json"
            )
            if os.path.isfile(disk_file):
                return tool_response(
                    {"error": str(exc), "note": "Run not in session memory but available on disk."},
                    "controlplane_get_details",
                    os.getcwd(),
                    f"Run {run_id} not in session memory. Full data at: {disk_file}",
                    run_id=run_id,
                )
            raise

    @mcp.tool()
    def controlplane_status(path: str | None = None) -> str:
        """Show ControlPlane status for a project.

        Shows whether ControlPlane is enabled, which channels are configured,
        and the current config settings.
        """
        # impl now returns a slim tool_response() string directly (data saved to disk)
        return str(_impl_controlplane_status(path, helpers))

    @mcp.tool()
    def controlplane_test_skeleton(
        path: str,
        target_file: str,
    ) -> str:
        """Generate a test skeleton for a source file.

        Uses AST analysis and test archetype matching to produce a pytest
        skeleton with appropriate test stubs, fixtures, and imports.

        Args:
            path: Project root path.
            target_file: Source file to generate tests for.
        """
        from lintgate.controlplane.skeleton_generator import (
            generate_test_path,
            generate_test_skeleton,
        )

        project_root = helpers["_validate_project_root"](path)

        if not os.path.isabs(target_file):
            target_file = os.path.normpath(os.path.join(project_root, target_file))

        if not os.path.exists(target_file):
            raise ValueError(f"Source file not found: {target_file}")

        from lintgate.next_action import NextAction, serialize_next_actions

        skeleton = generate_test_skeleton(target_file, project_root=project_root)
        test_path = generate_test_path(target_file, project_root)
        rel_file = os.path.relpath(target_file, project_root)

        next_actions = serialize_next_actions(
            [
                NextAction(
                    tool="mutation_run_sampling",
                    args={"path": path, "file": rel_file},
                    reason="Run mutation sampling to validate generated skeleton",
                ),
                NextAction(
                    tool="spec_file_analyze",
                    args={"path": path, "file": rel_file},
                    reason="View specification analysis for test prioritization",
                ),
            ]
        )

        result = {
            "source_file": target_file,
            "test_path": test_path,
            "skeleton": skeleton,
            "status": "skeleton_needs_edit",
            "action_required": (
                "This is a SKELETON — it will NOT pass if run directly. "
                "Replace all placeholder values (pass stubs, ..., EXPECTED) "
                "with real values, then write with the Write tool."
            ),
            "next_actions": next_actions,
        }
        summary = f"Test skeleton for {rel_file}."
        return tool_response(
            result, "controlplane_test_skeleton", project_root, summary, next_actions=next_actions
        )

    @mcp.tool()
    def controlplane_report_repair(
        path: str,
        action_id: str,
        outcome: str = "applied",
    ) -> str:
        """Report the outcome of a proposed repair action.

        Call this after applying (or deciding to skip) a repair suggested
        by ControlPlane. Tracks outcomes in session memory for future
        improvement of repair proposals.

        Args:
            path: Project root path.
            action_id: The repair action ID from the controlplane report.
            outcome: One of 'applied', 'ignored', 'rejected'.
        """
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            report_repair_outcome,
            save_session,
        )

        project_root = helpers["_validate_project_root"](path)
        valid_outcomes = {"applied", "ignored", "rejected"}
        if outcome not in valid_outcomes:
            raise ValueError(
                f"Invalid outcome '{outcome}'; expected one of: {sorted(valid_outcomes)}"
            )

        session = get_or_create_session(project_root)
        report_repair_outcome(session, action_id, outcome)
        save_session(session)

        result = {
            "action_id": action_id,
            "outcome": outcome,
            "session_id": session.session_id,
            "pending_repairs": sum(1 for v in session.repair_outcomes.values() if v == "pending"),
            "total_repairs_tracked": len(session.repair_outcomes),
        }
        summary = f"Repair reported for {action_id}."
        return tool_response(result, "controlplane_report_repair", project_root, summary)

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
        """Provide agent feedback on ControlPlane findings or constraint proposals.

        Use this to:
        - Record disagreements with specific findings
        - Accept proposed constraints (they'll be tracked as accepted)
        - Reject proposed constraints (they won't be re-proposed)
        - Tune persistent advisory findings (suppress or downgrade)
        - Classify persistent test failures (stale_test/known_regression/flaky/out_of_scope)

        Args:
            path: Project root path.
            run_id: Optional run ID this feedback relates to.
            disagreement: Optional description of what the agent disagrees with.
            accepted_constraints: Pattern keys to accept (e.g. ["ruff|F821"]).
            rejected_constraints: Pattern keys to reject.
            tuned_findings: Findings to tune. Each dict has:
                ``signature`` (e.g. "structure_channel|STRUCT003|log_event.py"),
                ``action`` ("suppress", "downgrade", or "reset"),
                ``rationale`` (why this finding is non-actionable).
            test_failure_classifications: Classify persistent test failures. Each dict has:
                ``fingerprint`` (finding fingerprint from persistent_test_failures),
                ``classification`` ("stale_test", "known_regression", "flaky", "out_of_scope"),
                ``rationale`` (why this classification applies).
        """
        return str(
            _impl_controlplane_agent_feedback(
                path,
                run_id,
                disagreement,
                accepted_constraints,
                rejected_constraints,
                helpers,
                tuned_findings=tuned_findings,
                test_failure_classifications=test_failure_classifications,
            )
        )

    @mcp.tool()
    def controlplane_apply_repairs(
        path: str,
        action_ids: list[str] | None = None,
        safe_only: bool = True,
        run_id: str | None = None,
    ) -> str:
        """Execute proposed repair actions from a ControlPlane run.

        Only executes command-type repairs. Requires explicit invocation.
        Pass ``run_id`` from ``controlplane_run`` or ``controlplane_get_details``
        to replay persisted repairs even if the session snapshot has rolled forward.

        Args:
            path: Project root path.
            action_ids: Specific action IDs to execute. If None, executes all safe pending repairs.
            safe_only: Only execute repairs marked as safe (default True).
            run_id: Optional originating ControlPlane run ID.
        """
        return str(
            _impl_controlplane_apply_repairs(
                path,
                action_ids,
                safe_only,
                helpers,
                run_id=run_id,
            )
        )

    @mcp.tool()
    def controlplane_get_work_queue(
        run_id: str,
        max_items: int = 25,
    ) -> str:
        """Get the dependency-ordered work queue from a cached ControlPlane run.

        WHEN TO USE: When you need the prioritized fix order without re-running
        the full health check. Returns the same work queue format as
        controlplane_run but from a cached result.

        Args:
            run_id: The run_id from a previous controlplane_run response.
            max_items: Maximum work queue items to return (default 25).
        """
        from lintgate.state import load_controlplane_run

        run_data = load_controlplane_run(run_id)
        if run_data is None:
            return json.dumps({"error": f"Run {run_id} not found"})

        # Reconstruct finding index from persisted data
        finding_index = run_data.get("finding_index", {})
        if not finding_index:
            return _safe_json({"run_id": run_id, "note": "No findings in this run."})

        # Extract import graph from structure channel if available
        import_graph: dict = {}
        file_map: dict = {}
        for ch_data in run_data.get("channels", {}).values():
            metrics = ch_data.get("metrics", {})
            if "_import_graph" in metrics:
                import_graph = metrics["_import_graph"]
                file_map = metrics.get("_file_map", {})
                break

        try:
            from lintgate.controlplane.work_queue import build_work_queue

            finding_list = list(finding_index.values())
            wq = build_work_queue(finding_list, import_graph, file_map)
            wq_dict = wq.to_dict()
            total_items = len(wq_dict.get("items", []))
            if total_items > max_items:
                wq_dict["items"] = wq_dict["items"][:max_items]
                wq_dict["truncated"] = True
                wq_dict["total_items"] = total_items
            wq_result = {"run_id": run_id, "work_queue": wq_dict}
            n_files = wq_dict.get("total_files", total_items)
            wq_summary = f"Work queue: {n_files} files."
            return tool_response(wq_result, "controlplane_get_work_queue", os.getcwd(), wq_summary)
        except Exception as e:
            return _safe_json({"run_id": run_id, "error": f"Failed to build work queue: {e}"})

    return {
        "controlplane_run": controlplane_run,
        "controlplane_get_details": controlplane_get_details,
        "controlplane_status": controlplane_status,
        "controlplane_test_skeleton": controlplane_test_skeleton,
        "controlplane_report_repair": controlplane_report_repair,
        "controlplane_agent_feedback": controlplane_agent_feedback,
        "controlplane_apply_repairs": controlplane_apply_repairs,
        "controlplane_get_work_queue": controlplane_get_work_queue,
    }
