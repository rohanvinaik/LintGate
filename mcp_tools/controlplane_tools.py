"""ControlPlane tools — controlplane_run, controlplane_get_details, controlplane_status,
controlplane_test_skeleton, controlplane_report_repair, controlplane_agent_feedback,
controlplane_apply_repairs."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from typing import Any, Literal

# ── Channel selection helpers ───────────────────────────────────────────

_ALL_CHANNEL_NAMES = "lint,tests,deps,git,behavior,structure"

_AVAILABLE_CHANNEL_DESCRIPTIONS = {
    "lint": "Code quality (ruff, mypy, complexity, structure)",
    "tests": "Test coverage and health (impacted test detection, skeleton generation)",
    "deps": "Dependency health (lockfile, venv, manifest)",
    "git": "Git hygiene (large changes, lockfile freshness, sensitive files)",
    "behavior": (
        "Behavioral drift signals "
        "(approach cycling, failure amnesia, brute force escalation)"
    ),
    "structure": (
        "Codebase structure lens "
        "(import cycles, module-size concentration, orphans, package cohesion)"
    ),
}


def _build_channel_registry():
    """Instantiate all available analysis channels."""
    from lintgate.channels.behavior_channel import BehaviorChannel
    from lintgate.channels.dependency_channel import DependencyChannel
    from lintgate.channels.git_channel import GitChannel
    from lintgate.channels.lint_channel import LintChannel
    from lintgate.channels.structure_channel import StructureChannel
    from lintgate.channels.test_channel import TestChannel

    return {
        "lint": LintChannel(),
        "tests": TestChannel(),
        "deps": DependencyChannel(),
        "git": GitChannel(),
        "behavior": BehaviorChannel(),
        "structure": StructureChannel(),
    }


def _select_channels(channels_str, channel_registry):
    """Parse channel string, return (active_channels, requested_names, unknown_names)."""
    requested = [c.strip() for c in (channels_str or _ALL_CHANNEL_NAMES).split(",")]
    active = []
    unknown = []
    for name in requested:
        if name in channel_registry:
            active.append(channel_registry[name])
        else:
            unknown.append(name)
    if not active:
        raise ValueError(f"No valid channels. Unknown: {unknown}")
    return active, requested, unknown


# ── controlplane_run helpers ────────────────────────────────────────────


def _collect_files_for_event(project_root, helpers):
    """Collect Python files for the supervision event, preferring git-changed files."""
    py_files = helpers["_collect_python_files"](project_root)
    files_for_event: list[str] = []
    with contextlib.suppress(Exception):
        from lintgate.symbol_gate_runner import collect_changed_python_files

        files_for_event = collect_changed_python_files(project_root)

    if not files_for_event:
        files_for_event = py_files
    return files_for_event[:50]


def _build_supervision_event(project_root, files_for_event, strictness, requested):
    """Build a SupervisionEvent for an MCP controlplane run."""
    from lintgate.controlplane.types import SupervisionEvent
    from lintgate.types import ChangeClassification

    change_classification = ChangeClassification(
        files_changed=files_for_event,
        files_by_language={"python": files_for_event} if files_for_event else {},
        change_kind="logic",
        risk_level="structural" if len(files_for_event) > 1 else "moderate",
        tool_name="controlplane_run",
    )
    return SupervisionEvent(
        surface="mcp",
        project_root=project_root,
        tool_name="controlplane_run",
        files_changed=files_for_event,
        change_classification=change_classification,
        raw_input={"strictness": strictness, "requested_channels": requested},
    )


def _setup_session(project_root, cp_config):
    """Load or create session memory if enabled. Returns session or None."""
    if not cp_config.session_memory:
        return None
    session = None
    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import get_or_create_session

        session = get_or_create_session(project_root, cp_config.session_max_age_hours)
    return session


def _inject_behavior_priors(event, session, cp_config):
    """Inject behavior compass and global priors into the event."""
    if session is not None and cp_config.channel_enabled("behavior"):
        event.raw_input["behavior_compass"] = session.behavior_compass

    if not (cp_config.global_memory_enabled and cp_config.channel_enabled("behavior")):
        return

    with contextlib.suppress(Exception):
        from lintgate.controlplane.global_behavior_profile import (
            MIN_SAMPLE_SIZE,
            load_global_profile,
        )

        gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
        if gp.session_count >= MIN_SAMPLE_SIZE:
            event.raw_input["behavior_global_priors"] = {
                "enabled": True,
                "alpha": cp_config.global_memory_alpha,
                "decay_horizon": cp_config.global_memory_decay_horizon,
                "computed_bias_adjustments": gp.computed_bias_adjustments,
            }


def _persist_behavior_compass_delta(cr, session, cp_config):
    """Persist behavior compass delta from a single channel result."""
    from lintgate.controlplane.session_memory import (
        load_behavior_compass,
        save_behavior_compass,
    )

    delta = cr.metrics.get("behavior_compass_delta")
    if not isinstance(delta, dict):
        return

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

    _persist_global_profile_delta(cr, session, cp_config)


def _persist_global_profile_delta(cr, session, cp_config):
    """Persist global behavior profile delta if enabled."""
    if not cp_config.global_memory_enabled:
        return

    gp_delta = cr.metrics.get("global_profile_delta")
    if not isinstance(gp_delta, dict):
        return

    with contextlib.suppress(Exception):
        from lintgate.controlplane.global_behavior_profile import (
            apply_session_delta,
            load_global_profile,
            save_global_profile,
        )

        gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
        sid = session.session_id if session else ""
        apply_session_delta(gp, gp_delta, session_id=sid)
        save_global_profile(gp)


def _persist_session_after_mesh(session, mesh_result, current_finding_index, cp_config):
    """Record mesh run snapshot, persist compass deltas, and save session."""
    if session is None:
        return

    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import record_mesh_run, save_session

        record_mesh_run(session, mesh_result, finding_index=current_finding_index)

        for cr in mesh_result.channel_results:
            if cr.channel != "behavior":
                continue
            _persist_behavior_compass_delta(cr, session, cp_config)
            break

        save_session(session)


def _persist_runtime_state(mesh_result, project_root, session):
    """Persist blocking/warning counts to RuntimeState for the quality gate."""
    with contextlib.suppress(Exception):
        from lintgate.runtime_state import build_runtime_state, save_runtime_state

        blocking = sum(
            1
            for cr in mesh_result.channel_results
            for f in cr.findings
            if f.severity == "blocking"
        )
        warnings = sum(
            1
            for cr in mesh_result.channel_results
            for f in cr.findings
            if f.severity == "warning"
        )
        symbol_blockers = sum(
            1
            for cr in mesh_result.channel_results
            if cr.channel == "tests"
            for f in cr.findings
            if f.severity == "blocking"
            and f.kind in {"symbol_uncovered", "unresolved_required_symbol"}
        )
        runtime = build_runtime_state(
            project_root,
            session=session,
            last_coherence_state=str(mesh_result.coherence.state or ""),
            last_blocking=blocking,
            last_warnings=warnings,
        )
        runtime.symbol_coverage_blockers = symbol_blockers
        save_runtime_state(project_root, runtime)


def _save_run_details_for_drilldown(mesh_result, current_finding_index, compact, helpers):
    """Save full details so controlplane_get_details can drill down."""
    with contextlib.suppress(Exception):
        from lintgate.state import save_controlplane_run

        full_details = helpers["_build_cp_full_details"](mesh_result, current_finding_index)
        save_controlplane_run(compact["run_id"], full_details)


def _impl_controlplane_run(path, channels, strictness, helpers):
    """Core implementation of controlplane_run."""
    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.reporter import build_finding_index, format_mesh_report_compact
    from lintgate.controlplane.runtime import run_mesh
    from lintgate.controlplane.types import ControlPlaneConfig

    project_root = helpers["_validate_project_root"](path)

    # Telemetry
    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("controlplane", project_root)

    # Config
    cp_config = load_controlplane_config(project_root)
    if not cp_config:
        cp_config = ControlPlaneConfig(enabled=True, latency_budget_ms=30000)

    # Channels
    channel_registry = _build_channel_registry()
    active_channels, requested, unknown = _select_channels(channels, channel_registry)

    # Event
    files_for_event = _collect_files_for_event(project_root, helpers)
    event = _build_supervision_event(project_root, files_for_event, strictness, requested)

    # Session + behavior injection
    session = _setup_session(project_root, cp_config)
    _inject_behavior_priors(event, session, cp_config)

    # Run mesh
    mesh_result = run_mesh(event, cp_config, active_channels, session=session)

    # Finding index and delta
    current_finding_index = build_finding_index(mesh_result)
    previous_finding_index = None
    if session is not None and session.snapshots:
        previous_finding_index = session.snapshots[-1].finding_index

    # Persist session
    _persist_session_after_mesh(session, mesh_result, current_finding_index, cp_config)

    # Compact output
    compact = format_mesh_report_compact(
        mesh_result, cp_config, previous_finding_index=previous_finding_index
    )

    # Persist runtime state and drill-down details
    _persist_runtime_state(mesh_result, project_root, session)
    _save_run_details_for_drilldown(mesh_result, current_finding_index, compact, helpers)

    # Final assembly
    if unknown:
        compact["unknown_channels"] = unknown
    compact.pop("finding_index", None)

    onboarding = helpers["_build_onboarding_status"](project_root)
    if onboarding.get("config_state") != "config_enabled":
        compact["onboarding"] = onboarding

    return helpers["_json_dumps"](compact, output_mode="compact")


# ── controlplane_get_details helpers ────────────────────────────────────


def _filter_channels(channels_dict, channel_filter):
    """Yield (ch_name, ch_data) pairs, filtered by channel name if specified."""
    for ch_name, ch_data in channels_dict.items():
        if channel_filter and ch_name != channel_filter:
            continue
        yield ch_name, ch_data


def _extract_findings(details, channel, severity, max_issues):
    """Extract and filter findings from run details."""
    all_findings = []
    for ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
        for f in ch_data.get("findings", []):
            if severity and f.get("severity") != severity:
                continue
            all_findings.append({**f, "channel": ch_name})

    result = {"total_matching": len(all_findings), "findings": all_findings[:max_issues]}
    if len(all_findings) > max_issues:
        result["truncated"] = len(all_findings) - max_issues
    return result


def _extract_channel_details(details, channel):
    """Extract channel summary info from run details."""
    ch_details: dict[str, Any] = {}
    for ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
        ch_details[ch_name] = {
            "status": ch_data.get("status"),
            "severity": ch_data.get("severity"),
            "finding_count": len(ch_data.get("findings", [])),
            "duration_ms": ch_data.get("duration_ms"),
            "error": ch_data.get("error"),
        }
    return ch_details


def _extract_repairs(details, channel):
    """Extract repair actions from run details."""
    all_repairs = []
    for _ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
        all_repairs.extend(ch_data.get("repairs", []))
    return all_repairs


def _extract_evidence(details, channel):
    """Extract metrics/evidence from run details."""
    evidence: dict[str, Any] = {}
    for ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
        metrics = ch_data.get("metrics", {})
        if metrics:
            evidence[ch_name] = metrics
    return evidence


def _impl_controlplane_get_details(run_id, channel, severity, max_issues, sections, helpers):
    """Core implementation of controlplane_get_details."""
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
        output.update(_extract_findings(details, channel, severity, max_issues))

    if "channel_details" in sections_set:
        output["channel_details"] = _extract_channel_details(details, channel)

    if "repairs" in sections_set:
        output["repairs"] = _extract_repairs(details, channel)

    if "evidence" in sections_set:
        evidence = _extract_evidence(details, channel)
        if evidence:
            output["evidence"] = evidence

    return helpers["_json_dumps"](output)


# ── controlplane_status helpers ─────────────────────────────────────────


def _build_config_status(cp_config, project_root, helpers):
    """Build status dict from an existing ControlPlane config."""
    status: dict[str, Any] = {
        "controlplane_enabled": cp_config.enabled,
        "latency_budget_ms": cp_config.latency_budget_ms,
        "advisory_default": cp_config.advisory_default,
        "session_memory": cp_config.session_memory,
        "session_max_age_hours": cp_config.session_max_age_hours,
        "constraint_proposal_threshold": cp_config.constraint_proposal_threshold,
        "token_policy": {
            "hook_max_tokens": cp_config.token_policy.hook_max_tokens,
            "include_pass_details": cp_config.token_policy.include_pass_details,
        },
        "channels": {
            name: {
                "enabled": ch.enabled,
                "blocking": ch.blocking,
                "timeout_ms": ch.timeout_ms,
            }
            for name, ch in cp_config.channels.items()
        },
    }

    if cp_config.session_memory:
        status["session"] = _get_session_status(project_root)

    if not cp_config.enabled:
        status["onboarding"] = helpers["_build_onboarding_status"](project_root)

    return status


def _get_session_status(project_root):
    """Load session and return a summary dict, or None."""
    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import load_session

        session = load_session(project_root)
        if session:
            return {
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
    return None


def _impl_controlplane_status(path, helpers):
    """Core implementation of controlplane_status."""
    from lintgate.config import load_controlplane_config

    project_root = helpers["_validate_project_root"](path) if path else os.getcwd()
    status: dict[str, Any] = {"project": project_root}

    cp_config = load_controlplane_config(project_root)
    if cp_config:
        status.update(_build_config_status(cp_config, project_root, helpers))
    else:
        status["controlplane_enabled"] = False
        status["note"] = (
            "Add 'controlplane: enabled: true' to .claude/lintgate.yaml to enable"
        )
        status["onboarding"] = helpers["_build_onboarding_status"](project_root)

    status["available_channels"] = _AVAILABLE_CHANNEL_DESCRIPTIONS
    return json.dumps(status, indent=2)


# ── controlplane_agent_feedback helpers ─────────────────────────────────


def _record_disagreement(session, run_id, disagreement, actions_taken):
    """Record a disagreement in the session."""
    session.agent_disagreements.append(
        {
            "run_id": run_id or "unknown",
            "disagreement": disagreement,
            "timestamp": time.time(),
        }
    )
    actions_taken.append(f"Recorded disagreement: {disagreement[:100]}")


def _process_accepted_constraints(session, accepted_constraints, actions_taken):
    """Accept constraints and collect their rule texts for patch generation."""
    from lintgate.controlplane.constraint_proposer import update_constraint_status

    accepted_rules: list[str] = []
    for key in accepted_constraints or []:
        if not update_constraint_status(session, key, "accepted"):
            actions_taken.append(f"Constraint not found: {key}")
            continue
        actions_taken.append(f"Accepted constraint: {key}")
        for p in session.proposed_constraints:
            if p.get("pattern_key") == key and p.get("status") == "accepted":
                rule_text = p.get("proposed_rule", "")
                if rule_text:
                    accepted_rules.append(rule_text)
                break
    return accepted_rules


def _process_rejected_constraints(session, rejected_constraints, actions_taken):
    """Reject constraints and record actions."""
    from lintgate.controlplane.constraint_proposer import update_constraint_status

    for key in rejected_constraints or []:
        if update_constraint_status(session, key, "rejected"):
            actions_taken.append(f"Rejected constraint: {key}")
        else:
            actions_taken.append(f"Constraint not found: {key}")


def _generate_living_context_patches(session, project_root, accepted_rules, actions_taken):
    """Generate context patches for accepted constraints if living context is enabled."""
    from lintgate.config import load_controlplane_config

    cp_config = load_controlplane_config(project_root)
    if not (cp_config and cp_config.inquiry.living_context and accepted_rules):
        return

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


def _impl_controlplane_agent_feedback(
    path, run_id, disagreement, accepted_constraints, rejected_constraints, helpers
):
    """Core implementation of controlplane_agent_feedback."""
    from lintgate.controlplane.session_memory import get_or_create_session, save_session

    project_root = helpers["_validate_project_root"](path)
    session = get_or_create_session(project_root)
    actions_taken: list[str] = []

    if disagreement:
        _record_disagreement(session, run_id, disagreement, actions_taken)

    accepted_rules = _process_accepted_constraints(session, accepted_constraints, actions_taken)
    _process_rejected_constraints(session, rejected_constraints, actions_taken)
    _generate_living_context_patches(session, project_root, accepted_rules, actions_taken)

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


# ── controlplane_apply_repairs helpers ──────────────────────────────────


def _collect_pending_repairs(session, action_ids, safe_only):
    """Collect pending repairs from the latest session snapshot."""
    if not session.snapshots:
        return []

    latest = session.snapshots[-1]
    all_repairs = _load_all_repairs(latest)
    proposed_ids = set(latest.repairs_proposed)

    pending: list[dict[str, Any]] = []
    for repair in all_repairs:
        repair_id = repair.get("action_id", "")
        if repair_id not in proposed_ids:
            continue
        if session.repair_outcomes.get(repair_id, "pending") != "pending":
            continue
        if action_ids and repair_id not in action_ids:
            continue
        if safe_only and not repair.get("safe", True):
            continue
        pending.append(repair)
    return pending


def _load_all_repairs(snapshot):
    """Load repair details from persisted run or fallback to snapshot catalog."""
    from lintgate.state import load_controlplane_run

    all_repairs: list[dict[str, Any]] = []
    run_details = load_controlplane_run(snapshot.run_id) if snapshot.run_id else None

    if run_details:
        for ch_data in run_details.get("channels", {}).values():
            all_repairs.extend(ch_data.get("repairs", []))
        return all_repairs

    # Fallback: reconstruct from snapshot's compact repair catalog
    for aid, meta in getattr(snapshot, "repair_catalog", {}).items():
        all_repairs.append(
            {
                "action_id": aid,
                "kind": meta.get("kind", "command"),
                "summary": meta.get("summary", ""),
                "safe": meta.get("safe", "true") == "true",
                "channel": meta.get("channel", ""),
                "payload": {},
            }
        )
    return all_repairs


def _execute_single_repair(repair, project_root, session):
    """Execute a single command repair. Returns a result dict."""
    from lintgate.controlplane.session_memory import report_repair_outcome

    action_id = repair.get("action_id")

    if repair.get("kind") != "command":
        return {"action_id": action_id, "status": "skipped", "reason": "not a command"}

    payload = repair.get("payload", {})
    command = payload.get("command", "")
    cwd = payload.get("cwd", project_root)

    if not command:
        return {"action_id": action_id, "status": "skipped", "reason": "empty command"}

    try:
        import shlex

        proc = subprocess.run(
            shlex.split(command),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
        )
        status = "ok" if proc.returncode == 0 else "error"
        report_repair_outcome(
            session, action_id or "", "applied" if status == "ok" else "ignored"
        )
        return {
            "action_id": action_id,
            "command": command,
            "status": status,
            "returncode": proc.returncode,
            "stderr": proc.stderr.strip()[-300:] if proc.stderr else None,
        }
    except subprocess.TimeoutExpired:
        return {"action_id": action_id, "command": command, "status": "timeout"}
    except OSError as e:
        return {"action_id": action_id, "command": command, "status": "error", "error": str(e)}


def _impl_controlplane_apply_repairs(path, action_ids, safe_only, helpers):
    """Core implementation of controlplane_apply_repairs."""
    from lintgate.controlplane.session_memory import get_or_create_session, save_session

    project_root = helpers["_validate_project_root"](path)
    session = get_or_create_session(project_root)

    pending_repairs = _collect_pending_repairs(session, action_ids, safe_only)

    results = [
        _execute_single_repair(repair, project_root, session) for repair in pending_repairs
    ]

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


# ── Registration ────────────────────────────────────────────────────────


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
        return _impl_controlplane_run(path, channels, strictness, helpers)

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
        return _impl_controlplane_get_details(
            run_id, channel, severity, max_issues, sections, helpers
        )

    @mcp.tool()
    def controlplane_status(path: str | None = None) -> str:
        """Show ControlPlane status for a project.

        Shows whether ControlPlane is enabled, which channels are configured,
        and the current config settings.
        """
        return _impl_controlplane_status(path, helpers)

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
        return _impl_controlplane_agent_feedback(
            path, run_id, disagreement, accepted_constraints, rejected_constraints, helpers
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
        return _impl_controlplane_apply_repairs(path, action_ids, safe_only, helpers)

    return {
        "controlplane_run": controlplane_run,
        "controlplane_get_details": controlplane_get_details,
        "controlplane_status": controlplane_status,
        "controlplane_test_skeleton": controlplane_test_skeleton,
        "controlplane_report_repair": controlplane_report_repair,
        "controlplane_agent_feedback": controlplane_agent_feedback,
        "controlplane_apply_repairs": controlplane_apply_repairs,
    }
