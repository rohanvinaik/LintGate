"""ControlPlane tools — controlplane_run, controlplane_get_details, controlplane_status,
controlplane_test_skeleton, controlplane_report_repair, controlplane_agent_feedback,
controlplane_apply_repairs."""

from __future__ import annotations

import contextlib
import json
import os
import time
from typing import Any, Literal


def register(mcp, helpers):
    """Register ControlPlane tools on the shared MCP instance."""

    @mcp.tool()
    def controlplane_run(
        path: str,
        channels: str | None = None,
        strictness: Literal["relaxed", "normal", "strict"] = "normal",
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
        """
        from lintgate.channels.dependency_channel import DependencyChannel
        from lintgate.channels.git_channel import GitChannel
        from lintgate.channels.lint_channel import LintChannel
        from lintgate.channels.structure_channel import StructureChannel
        from lintgate.channels.test_channel import TestChannel
        from lintgate.config import load_controlplane_config
        from lintgate.controlplane.runtime import run_mesh
        from lintgate.controlplane.types import ControlPlaneConfig, SupervisionEvent
        from lintgate.types import ChangeClassification

        project_root = helpers["_validate_project_root"](path)

        # Telemetry: track controlplane usage
        import contextlib

        with contextlib.suppress(Exception):
            from lintgate.state import log_feature_usage

            log_feature_usage("controlplane", project_root)

        # Load or build config
        cp_config = load_controlplane_config(project_root)
        if not cp_config:
            cp_config = ControlPlaneConfig(
                enabled=True,
                latency_budget_ms=30000,  # MCP gets more time
            )

        # Build channel registry
        from lintgate.channels.behavior_channel import BehaviorChannel

        channel_registry = {
            "lint": LintChannel(),
            "tests": TestChannel(),
            "deps": DependencyChannel(),
            "git": GitChannel(),
            "behavior": BehaviorChannel(),
            "structure": StructureChannel(),
        }

        # Select requested channels
        requested = [
            c.strip() for c in (channels or "lint,tests,deps,git,behavior,structure").split(",")
        ]
        active_channels = []
        unknown = []
        for name in requested:
            if name in channel_registry:
                active_channels.append(channel_registry[name])
            else:
                unknown.append(name)

        if not active_channels:
            raise ValueError(f"No valid channels. Unknown: {unknown}")

        # Discover Python files for the event
        py_files = helpers["_collect_python_files"](project_root)

        # Build an explicit synthetic classification for full-project MCP audits.
        # Channels use this to decide relevance; MCP runs should exercise the
        # requested channels even without a concrete hook event.
        files_for_event = py_files[:50]
        change_classification = ChangeClassification(
            files_changed=files_for_event,
            files_by_language={"python": files_for_event} if files_for_event else {},
            change_kind="logic",
            risk_level="structural" if len(files_for_event) > 1 else "moderate",
            tool_name="controlplane_run",
        )

        # Build event
        event = SupervisionEvent(
            surface="mcp",
            project_root=project_root,
            tool_name="controlplane_run",
            files_changed=files_for_event,
            change_classification=change_classification,
            raw_input={"strictness": strictness, "requested_channels": requested},
        )

        # Session memory: wire into MCP path for behavior channel
        session = None
        if cp_config.session_memory:
            with contextlib.suppress(Exception):
                from lintgate.controlplane.session_memory import get_or_create_session

                session = get_or_create_session(project_root, cp_config.session_max_age_hours)

        # Inject behavior compass into event for BehaviorChannel
        if session is not None and cp_config.channel_enabled("behavior"):
            event.raw_input["behavior_compass"] = session.behavior_compass

        # Inject global behavior priors if enabled
        if cp_config.global_memory_enabled and cp_config.channel_enabled("behavior"):
            with contextlib.suppress(Exception):
                from lintgate.controlplane.global_behavior_profile import (
                    MIN_SAMPLE_SIZE,
                    load_global_profile,
                )

                _gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
                if _gp.session_count >= MIN_SAMPLE_SIZE:
                    event.raw_input["behavior_global_priors"] = {
                        "enabled": True,
                        "alpha": cp_config.global_memory_alpha,
                        "decay_horizon": cp_config.global_memory_decay_horizon,
                        "computed_bias_adjustments": _gp.computed_bias_adjustments,
                    }

        # Run mesh
        mesh_result = run_mesh(event, cp_config, active_channels, session=session)

        # Build finding index for delta computation
        from lintgate.controlplane.reporter import build_finding_index, format_mesh_report_compact

        current_finding_index = build_finding_index(mesh_result)

        # Get previous finding index from session (if available)
        previous_finding_index = None
        if session is not None and session.snapshots:
            previous_finding_index = session.snapshots[-1].finding_index

        # Session memory: record snapshot after mesh
        if session is not None:
            with contextlib.suppress(Exception):
                from lintgate.controlplane.session_memory import (
                    load_behavior_compass,
                    record_mesh_run,
                    save_behavior_compass,
                    save_session,
                )

                record_mesh_run(session, mesh_result, finding_index=current_finding_index)

                # Persist behavior channel state deltas (cooldowns/escalation flags).
                for cr in mesh_result.channel_results:
                    if cr.channel != "behavior":
                        continue
                    delta = cr.metrics.get("behavior_compass_delta")
                    if not isinstance(delta, dict):
                        continue
                    compass = load_behavior_compass(session)
                    compass.last_fired = delta.get("last_fired", compass.last_fired)
                    compass.signal_fire_counts = delta.get(
                        "signal_fire_counts", compass.signal_fire_counts
                    )
                    compass.early_nudge_emitted = delta.get(
                        "early_nudge_emitted", compass.early_nudge_emitted
                    )
                    compass.pending_nudge_signals = delta.get(
                        "pending_nudge_signals", compass.pending_nudge_signals
                    )
                    compass.pending_nudge_constraint_check_count = delta.get(
                        "pending_nudge_constraint_check_count",
                        compass.pending_nudge_constraint_check_count,
                    )
                    compass.nudge_outcomes = delta.get("nudge_outcomes", compass.nudge_outcomes)
                    save_behavior_compass(session, compass)

                    # Persist global profile delta
                    if cp_config.global_memory_enabled:
                        gp_delta = cr.metrics.get("global_profile_delta")
                        if isinstance(gp_delta, dict):
                            with contextlib.suppress(Exception):
                                from lintgate.controlplane.global_behavior_profile import (
                                    apply_session_delta,
                                    load_global_profile,
                                    save_global_profile,
                                )

                                _gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
                                _sid = session.session_id if session else ""
                                apply_session_delta(_gp, gp_delta, session_id=_sid)
                                save_global_profile(_gp)

                    break

                save_session(session)

        # Generate compact output
        compact = format_mesh_report_compact(
            mesh_result,
            cp_config,
            previous_finding_index=previous_finding_index,
        )

        # Save full details for drill-down
        with contextlib.suppress(Exception):
            from lintgate.state import save_controlplane_run

            full_details = helpers["_build_cp_full_details"](mesh_result, current_finding_index)
            save_controlplane_run(compact["run_id"], full_details)

        if unknown:
            compact["unknown_channels"] = unknown

        # Remove finding_index from returned output (it's stored, not sent to agent)
        compact.pop("finding_index", None)

        # Include onboarding when not fully configured
        _onboarding = helpers["_build_onboarding_status"](project_root)
        if _onboarding.get("config_state") != "config_enabled":
            compact["onboarding"] = _onboarding

        return helpers["_json_dumps"](compact, output_mode="compact")

    @mcp.tool()
    def controlplane_get_details(
        run_id: str,
        channel: str | None = None,
        severity: str | None = None,
        max_issues: int = 10,
        sections: list[str] | None = None,
    ) -> str:
        """Drill into a previous ControlPlane run by run_id.

        WHEN TO USE: After controlplane_run returns findings. The compact output
        shows counts and summaries — use this to see full issue details, evidence,
        and suggested repairs.

        Example: controlplane_get_details(run_id="cp_abc123")

        Args:
            run_id: The run_id from a controlplane_run response.
            channel: Filter findings by channel (lint, tests, deps, git, behavior, structure).
            severity: Filter by severity (blocking, warning, informational).
            max_issues: Maximum findings to return (default 10).
            sections: Which sections to include. Default: all.
                Options: "findings", "channel_details", "evidence", "repairs", "coherence"
        """
        from lintgate.state import load_controlplane_run

        details = load_controlplane_run(run_id)
        if details is None:
            raise ValueError(f"No ControlPlane run found with run_id: {run_id}")

        sections_set = set(
            sections or ["findings", "channel_details", "evidence", "repairs", "coherence"]
        )
        output: dict[str, Any] = {"run_id": run_id, "duration_ms": details.get("duration_ms", 0)}

        if "coherence" in sections_set:
            output["coherence"] = details.get("coherence", {})

        if "findings" in sections_set:
            all_findings = []
            for ch_name, ch_data in details.get("channels", {}).items():
                if channel and ch_name != channel:
                    continue
                for f in ch_data.get("findings", []):
                    if severity and f.get("severity") != severity:
                        continue
                    f_copy = {**f, "channel": ch_name}
                    all_findings.append(f_copy)
            output["total_matching"] = len(all_findings)
            output["findings"] = all_findings[:max_issues]
            if len(all_findings) > max_issues:
                output["truncated"] = len(all_findings) - max_issues

        if "channel_details" in sections_set:
            ch_details: dict[str, Any] = {}
            for ch_name, ch_data in details.get("channels", {}).items():
                if channel and ch_name != channel:
                    continue
                ch_details[ch_name] = {
                    "status": ch_data.get("status"),
                    "severity": ch_data.get("severity"),
                    "finding_count": len(ch_data.get("findings", [])),
                    "duration_ms": ch_data.get("duration_ms"),
                    "error": ch_data.get("error"),
                }
            output["channel_details"] = ch_details

        if "repairs" in sections_set:
            all_repairs = []
            for ch_name, ch_data in details.get("channels", {}).items():
                if channel and ch_name != channel:
                    continue
                all_repairs.extend(ch_data.get("repairs", []))
            output["repairs"] = all_repairs

        if "evidence" in sections_set:
            evidence: dict[str, Any] = {}
            for ch_name, ch_data in details.get("channels", {}).items():
                if channel and ch_name != channel:
                    continue
                metrics = ch_data.get("metrics", {})
                if metrics:
                    evidence[ch_name] = metrics
            if evidence:
                output["evidence"] = evidence

        return helpers["_json_dumps"](output)

    @mcp.tool()
    def controlplane_status(path: str | None = None) -> str:
        """Show ControlPlane status for a project.

        Shows whether ControlPlane is enabled, which channels are configured,
        and the current config settings.
        """
        from lintgate.config import load_controlplane_config

        project_root = helpers["_validate_project_root"](path) if path else os.getcwd()

        status: dict[str, Any] = {
            "project": project_root,
        }

        cp_config = load_controlplane_config(project_root)
        if cp_config:
            status["controlplane_enabled"] = cp_config.enabled
            status["latency_budget_ms"] = cp_config.latency_budget_ms
            status["advisory_default"] = cp_config.advisory_default
            status["session_memory"] = cp_config.session_memory
            status["session_max_age_hours"] = cp_config.session_max_age_hours
            status["constraint_proposal_threshold"] = cp_config.constraint_proposal_threshold
            status["token_policy"] = {
                "hook_max_tokens": cp_config.token_policy.hook_max_tokens,
                "include_pass_details": cp_config.token_policy.include_pass_details,
            }
            status["channels"] = {
                name: {
                    "enabled": ch.enabled,
                    "blocking": ch.blocking,
                    "timeout_ms": ch.timeout_ms,
                }
                for name, ch in cp_config.channels.items()
            }

            # Session status
            if cp_config.session_memory:
                with contextlib.suppress(Exception):
                    from lintgate.controlplane.session_memory import load_session

                    session = load_session(project_root)
                    if session:
                        status["session"] = {
                            "session_id": session.session_id,
                            "runs": len(session.snapshots),
                            "coherence_trajectory": session.coherence_trajectory[-5:],
                            "pending_repairs": sum(
                                1 for v in session.repair_outcomes.values() if v == "pending"
                            ),
                            "proposed_constraints": len(session.proposed_constraints),
                            "active_proposals": sum(
                                1
                                for c in session.proposed_constraints
                                if c.get("status") == "proposed"
                            ),
                        }
                    else:
                        status["session"] = None

            # Config exists but CP disabled — surface onboarding
            if not cp_config.enabled:
                status["onboarding"] = helpers["_build_onboarding_status"](project_root)
        else:
            status["controlplane_enabled"] = False  # backward compat
            status["note"] = (
                "Add 'controlplane: enabled: true' to .claude/lintgate.yaml to enable"  # backward compat
            )
            status["onboarding"] = helpers["_build_onboarding_status"](project_root)

        # Available channels
        status["available_channels"] = {
            "lint": "Code quality (ruff, mypy, complexity, structure)",
            "tests": "Test coverage and health (impacted test detection, skeleton generation)",
            "deps": "Dependency health (lockfile, venv, manifest)",
            "git": "Git hygiene (large changes, lockfile freshness, sensitive files)",
            "behavior": "Behavioral drift signals (approach cycling, failure amnesia, brute force escalation)",
            "structure": "Codebase structure lens (import cycles, module-size concentration, orphans, package cohesion)",
        }

        return json.dumps(status, indent=2)

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

        # Resolve target file
        if not os.path.isabs(target_file):
            target_file = os.path.normpath(os.path.join(project_root, target_file))

        if not os.path.exists(target_file):
            raise ValueError(f"Source file not found: {target_file}")

        skeleton = generate_test_skeleton(target_file, project_root=project_root)
        test_path = generate_test_path(target_file, project_root)

        return json.dumps(
            {
                "source_file": target_file,
                "test_path": test_path,
                "skeleton": skeleton,
                "note": "Review and customize before saving. Use Write tool to create the file.",
            },
            indent=2,
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

        return json.dumps(
            {
                "action_id": action_id,
                "outcome": outcome,
                "session_id": session.session_id,
                "pending_repairs": sum(
                    1 for v in session.repair_outcomes.values() if v == "pending"
                ),
                "total_repairs_tracked": len(session.repair_outcomes),
            },
            indent=2,
        )

    @mcp.tool()
    def controlplane_agent_feedback(
        path: str,
        run_id: str | None = None,
        disagreement: str | None = None,
        accepted_constraints: list[str] | None = None,
        rejected_constraints: list[str] | None = None,
    ) -> str:
        """Provide agent feedback on ControlPlane findings or constraint proposals.

        Use this to:
        - Record disagreements with specific findings
        - Accept proposed constraints (they'll be tracked as accepted)
        - Reject proposed constraints (they won't be re-proposed)

        Args:
            path: Project root path.
            run_id: Optional run ID this feedback relates to.
            disagreement: Optional description of what the agent disagrees with.
            accepted_constraints: Pattern keys to accept (e.g. ["ruff|F821"]).
            rejected_constraints: Pattern keys to reject.
        """
        from lintgate.controlplane.constraint_proposer import update_constraint_status
        from lintgate.controlplane.session_memory import get_or_create_session, save_session

        project_root = helpers["_validate_project_root"](path)
        session = get_or_create_session(project_root)

        actions_taken = []

        # Record disagreement
        if disagreement:
            session.agent_disagreements.append(
                {
                    "run_id": run_id or "unknown",
                    "disagreement": disagreement,
                    "timestamp": time.time(),
                }
            )
            actions_taken.append(f"Recorded disagreement: {disagreement[:100]}")

        # Accept constraints
        accepted_rules: list[str] = []
        for key in accepted_constraints or []:
            if update_constraint_status(session, key, "accepted"):
                actions_taken.append(f"Accepted constraint: {key}")
                # Find the accepted rule text for patch generation
                for p in session.proposed_constraints:
                    if p.get("pattern_key") == key and p.get("status") == "accepted":
                        rule_text = p.get("proposed_rule", "")
                        if rule_text:
                            accepted_rules.append(rule_text)
                        break
            else:
                actions_taken.append(f"Constraint not found: {key}")

        # Reject constraints
        for key in rejected_constraints or []:
            if update_constraint_status(session, key, "rejected"):
                actions_taken.append(f"Rejected constraint: {key}")
            else:
                actions_taken.append(f"Constraint not found: {key}")

        # Generate context patches for accepted constraints (living context)
        from lintgate.config import load_controlplane_config

        cp_config = load_controlplane_config(project_root)
        if cp_config and cp_config.inquiry.living_context and accepted_rules:
            from lintgate.context_bootstrap import generate_context_patch

            for rule_text in accepted_rules:
                patch = generate_context_patch(
                    project_root,
                    trigger="constraint_accepted",
                    evidence={"rule": rule_text, "rationale": "Accepted via agent feedback"},
                )
                if patch is not None:
                    session.pending_patches.append(patch.to_dict())
                    actions_taken.append(f"Generated context patch: {patch.patch_id}")

        save_session(session)

        return json.dumps(
            {
                "session_id": session.session_id,
                "actions_taken": actions_taken,
                "total_disagreements": len(session.agent_disagreements),
                "proposed_constraints": len(session.proposed_constraints),
                "active_proposals": sum(
                    1 for c in session.proposed_constraints if c.get("status") == "proposed"
                ),
            },
            indent=2,
        )

    @mcp.tool()
    def controlplane_apply_repairs(
        path: str,
        action_ids: list[str] | None = None,
        safe_only: bool = True,
    ) -> str:
        """Execute proposed repair actions from a ControlPlane run.

        Only executes command-type repairs. Requires explicit invocation.

        Args:
            path: Project root path.
            action_ids: Specific action IDs to execute. If None, executes all safe pending repairs.
            safe_only: Only execute repairs marked as safe (default True).
        """
        import subprocess

        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            report_repair_outcome,
            save_session,
        )

        project_root = helpers["_validate_project_root"](path)
        session = get_or_create_session(project_root)

        # Collect pending repairs from the latest snapshot
        pending_repairs: list[dict[str, Any]] = []
        if session.snapshots:
            latest = session.snapshots[-1]
            for repair in latest.get("repairs", []):
                repair_id = repair.get("action_id", "")
                outcome = session.repair_outcomes.get(repair_id, "pending")
                if outcome != "pending":
                    continue
                if action_ids and repair_id not in action_ids:
                    continue
                if safe_only and not repair.get("safe", True):
                    continue
                pending_repairs.append(repair)

        results: list[dict[str, Any]] = []
        for repair in pending_repairs:
            if repair.get("kind") != "command":
                results.append(
                    {
                        "action_id": repair.get("action_id"),
                        "status": "skipped",
                        "reason": "not a command",
                    }
                )
                continue

            payload = repair.get("payload", {})
            command = payload.get("command", "")
            cwd = payload.get("cwd", project_root)

            if not command:
                results.append(
                    {
                        "action_id": repair.get("action_id"),
                        "status": "skipped",
                        "reason": "empty command",
                    }
                )
                continue

            try:
                proc = subprocess.run(
                    command.split(),
                    capture_output=True,
                    text=True,
                    timeout=60,
                    cwd=cwd,
                )
                status = "ok" if proc.returncode == 0 else "error"
                results.append(
                    {
                        "action_id": repair.get("action_id"),
                        "command": command,
                        "status": status,
                        "returncode": proc.returncode,
                        "stderr": proc.stderr.strip()[-300:] if proc.stderr else None,
                    }
                )
                report_repair_outcome(
                    session, repair.get("action_id", ""), "applied" if status == "ok" else "ignored"
                )
            except subprocess.TimeoutExpired:
                results.append(
                    {"action_id": repair.get("action_id"), "command": command, "status": "timeout"}
                )
            except OSError as e:
                results.append(
                    {
                        "action_id": repair.get("action_id"),
                        "command": command,
                        "status": "error",
                        "error": str(e),
                    }
                )

        save_session(session)

        return json.dumps(
            {
                "repairs_executed": len(results),
                "results": results,
                "pending_remaining": sum(
                    1 for v in session.repair_outcomes.values() if v == "pending"
                ),
            },
            indent=2,
        )

    return {
        "controlplane_run": controlplane_run,
        "controlplane_get_details": controlplane_get_details,
        "controlplane_status": controlplane_status,
        "controlplane_test_skeleton": controlplane_test_skeleton,
        "controlplane_report_repair": controlplane_report_repair,
        "controlplane_agent_feedback": controlplane_agent_feedback,
        "controlplane_apply_repairs": controlplane_apply_repairs,
    }
