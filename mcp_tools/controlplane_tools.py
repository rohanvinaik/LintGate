"""ControlPlane tools — controlplane_run, controlplane_get_details, controlplane_status,
controlplane_test_skeleton, controlplane_report_repair, controlplane_agent_feedback,
controlplane_apply_repairs."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Literal

# ── Channel selection helpers ───────────────────────────────────────────

_ALL_CHANNEL_NAMES = (
    "lint,tests,deps,git,behavior,structure,performance,test_effectiveness,specification"
)

_AVAILABLE_CHANNEL_DESCRIPTIONS = {
    "lint": "Code quality (ruff, mypy, complexity, structure)",
    "tests": "Test coverage and health (impacted test detection, skeleton generation)",
    "deps": "Dependency health (lockfile, venv, manifest)",
    "git": "Git hygiene (large changes, lockfile freshness, sensitive files)",
    "behavior": (
        "Behavioral drift signals (approach cycling, failure amnesia, brute force escalation)"
    ),
    "structure": (
        "Codebase structure lens "
        "(import cycles, module-size concentration, orphans, package cohesion)"
    ),
    "performance": (
        "Algebraic performance analysis "
        "(purity detection, algebraic properties, optimization hints)"
    ),
    "test_effectiveness": "Test assertion quality and vulnerability analysis",
    "specification": (
        "Specification complexity analysis "
        "(sigma estimation, regime classification, risk model, optimization gate)"
    ),
}


def _build_channel_registry():
    """Instantiate all available analysis channels."""
    from lintgate.channels.behavior_channel import BehaviorChannel
    from lintgate.channels.dependency_channel import DependencyChannel
    from lintgate.channels.git_channel import GitChannel
    from lintgate.channels.lint_channel import LintChannel
    from lintgate.channels.performance_channel import PerformanceChannel
    from lintgate.channels.specification_channel import SpecificationChannel
    from lintgate.channels.structure_channel import StructureChannel
    from lintgate.channels.test_channel import TestChannel
    from lintgate.channels.test_effectiveness_channel import TestEffectivenessChannel

    return {
        "lint": LintChannel(),
        "tests": TestChannel(),
        "deps": DependencyChannel(),
        "git": GitChannel(),
        "behavior": BehaviorChannel(),
        "structure": StructureChannel(),
        "performance": PerformanceChannel(),
        "test_effectiveness": TestEffectivenessChannel(),
        "specification": SpecificationChannel(),
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


def _validate_channel_wiring(active_channel_names: list[str]) -> list:
    """Run schema wiring validation. Returns list of LintIssue for WIRE001/WIRE002."""
    findings = []
    try:
        from lintgate.controlplane.metric_schema import (
            register_all_schemas,
            validate_wiring,
        )

        register_all_schemas()
        issues = validate_wiring(active_channel_names)
        if issues:
            from lintgate.linters.lint_types import LintIssue

            for issue in issues:
                code = "WIRE001" if issue.issue_type == "missing_publisher" else "WIRE002"
                findings.append(
                    LintIssue(
                        file="<schema>",
                        line=0,
                        col=0,
                        linter="metric_schema",
                        kind=code,
                        message=(
                            f"Channel '{issue.consumer}' consumes '{issue.key}' "
                            f"but {issue.missing_publisher}"
                        ),
                        severity="warning",
                    )
                )
    except Exception:
        pass  # Graceful degradation — schema validation is advisory
    return findings


def _append_schema_findings(mesh_result, wiring_findings: list) -> None:
    """Append WIRE findings + per-channel WIRE002 to mesh result."""
    if not wiring_findings and not mesh_result.channel_results:
        return

    try:
        from lintgate.controlplane.metric_schema import validate_result
        from lintgate.linters.lint_types import LintIssue

        # Per-channel output validation (skip-aware)
        for cr in mesh_result.channel_results:
            missing = validate_result(cr.channel, cr.metrics, status=cr.status)
            for key in missing:
                wiring_findings.append(
                    LintIssue(
                        file="<schema>",
                        line=0,
                        col=0,
                        linter="metric_schema",
                        kind="WIRE002",
                        message=(
                            f"Channel '{cr.channel}' declared key '{key}' "
                            f"but did not publish it"
                        ),
                        severity="informational",
                    )
                )

        # Attach wiring findings to the first channel result (or create a synthetic one)
        if wiring_findings:
            if mesh_result.channel_results:
                # Append to the last channel result's findings
                cr = mesh_result.channel_results[-1]
                cr.findings = list(cr.findings or []) + wiring_findings
            else:
                from lintgate.controlplane.types import ChannelResult

                mesh_result.channel_results.append(
                    ChannelResult(
                        channel="schema_validation",
                        status="fail",
                        severity="warning",
                        findings=wiring_findings,
                        metrics={},
                    )
                )
    except Exception:
        pass  # Graceful degradation


# ── controlplane_run helpers ────────────────────────────────────────────


def _resolve_scope_files(project_root, scope, files, helpers):
    """Resolve files based on requested scope."""
    if scope == "files":
        if not files:
            raise ValueError("scope='files' requires a non-empty files list")
        resolved = []
        for f in files:
            p = Path(project_root) / f if not Path(f).is_absolute() else Path(f)
            resolved.append(str(p.resolve()))
        return resolved

    if scope and scope not in ("project", "changed", "staged", "full_sweep"):
        raise ValueError(f"Unknown scope: {scope}")

    py_files = helpers["_collect_python_files"](project_root)

    # Deduplicate matching the old logic limiting to 50
    def dedup(fs, limit=50):
        seen = set()
        res = []
        for f in fs[:limit]:
            r = str(Path(f).resolve())
            if r not in seen:
                seen.add(r)
                res.append(f)
        return res

    if scope == "full_sweep":
        # No file cap — include every Python file in the project
        return dedup(py_files, limit=len(py_files))

    if scope == "project":
        return dedup(py_files)

    try:
        if scope == "staged":
            cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
        else:
            cmd = ["git", "diff", "--name-only", "HEAD", "--diff-filter=ACMR"]

        proc = subprocess.run(
            cmd, cwd=project_root, capture_output=True, text=True, check=True
        )
        git_files = [f for f in proc.stdout.splitlines() if f.endswith(".py")]

        if scope != "staged":
            proc2 = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=project_root,
                capture_output=True,
                text=True,
            )
            git_files.extend(
                [f for f in proc2.stdout.splitlines() if f.endswith(".py")]
            )

        full_paths = [str(Path(project_root) / f) for f in set(git_files)]
        existing_py = {str(Path(f).resolve()) for f in py_files}
        resolved = [f for f in full_paths if str(Path(f).resolve()) in existing_py]

        if resolved:
            return dedup(resolved)
    except Exception:
        pass

    return dedup(py_files)


def _compute_dynamic_budget_ms(
    configured_ms: int,
    file_count: int,
    scope: str | None,
) -> int:
    """Scale the latency budget based on project size and scope.

    Tiers (based on file count):
      <=20 files  → 30s
      <=100 files → 60s
      <=500 files → 120s
      >500 files  → 300s
      full_sweep  → 600s (10 min)

    The configured value is treated as a floor — if the user set an explicit
    budget in lintgate.yaml that exceeds the dynamic value, honour it.
    """
    if scope == "full_sweep":
        dynamic = 600_000
    elif file_count > 500:
        dynamic = 300_000
    elif file_count > 100:
        dynamic = 120_000
    elif file_count > 20:
        dynamic = 60_000
    else:
        dynamic = 30_000
    return max(configured_ms, dynamic)


def _collect_files_for_event(project_root, scope, files, helpers):
    """Collect Python files for the supervision event."""
    return _resolve_scope_files(project_root, scope, files, helpers)


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


def _save_run_details_for_drilldown(
    mesh_result, current_finding_index, compact, helpers
):
    """Save full details so controlplane_get_details can drill down."""
    with contextlib.suppress(Exception):
        from lintgate.state import save_controlplane_run

        full_details = helpers["_build_cp_full_details"](
            mesh_result, current_finding_index
        )
        save_controlplane_run(compact["run_id"], full_details)


def _check_ship_gate_parity(project_root: str, strictness: str) -> dict[str, Any]:
    """Run ship_main.py --preflight --json if strict, or return advisory."""
    if strictness != "strict":
        return {
            "status": "stale",
            "message": "Gate parity check skipped (strictness < strict).",
            "command_to_verify": "python scripts/ship_main.py --preflight",
        }

    ship_main_path = os.path.join(project_root, "scripts", "ship_main.py")
    if not os.path.exists(ship_main_path):
        return {
            "status": "error",
            "error": "scripts/ship_main.py not found",
        }

    import sys

    try:
        proc = subprocess.run(
            [sys.executable, ship_main_path, "--preflight", "--json"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=180,
        )
        try:
            return json.loads(proc.stdout)
        except Exception:
            return {
                "status": "error",
                "exit_code": proc.returncode,
                "error": "Failed to parse json from ship_main.py",
                "stderr": proc.stderr[-200:] if proc.stderr else "",
            }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _impl_controlplane_run(path, channels, strictness, scope, files, helpers):
    """Core implementation of controlplane_run."""
    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.reporter import (
        build_finding_index,
        format_mesh_report_compact,
    )
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
        cp_config = ControlPlaneConfig(enabled=True)

    # Channels
    channel_registry = _build_channel_registry()
    active_channels, requested, unknown = _select_channels(channels, channel_registry)

    # Schema wiring validation (runtime advisory)
    wiring_findings = _validate_channel_wiring(
        [ch.name for ch in active_channels]
    )

    # Event
    files_for_event = _collect_files_for_event(project_root, scope, files, helpers)

    # Dynamic budget: scale with project size unless explicitly configured
    cp_config.latency_budget_ms = _compute_dynamic_budget_ms(
        cp_config.latency_budget_ms, len(files_for_event), scope
    )
    event = _build_supervision_event(
        project_root, files_for_event, strictness, requested
    )

    # Session + behavior injection
    session = _setup_session(project_root, cp_config)

    # Record controlplane_run as a tool event for behavioral tracking (#191)
    if session is not None and cp_config.channel_enabled("behavior"):
        with contextlib.suppress(Exception):
            from lintgate.controlplane.behavior_compass import record_tool_event
            from lintgate.controlplane.session_memory import (
                load_behavior_compass,
                save_behavior_compass,
            )

            compass = load_behavior_compass(session)
            record_tool_event(compass, "controlplane_run", {}, "")
            save_behavior_compass(session, compass)

    _inject_behavior_priors(event, session, cp_config)

    # Run mesh
    mesh_result = run_mesh(event, cp_config, active_channels, session=session)

    # Post-mesh schema validation: check channel outputs match declared schemas
    _append_schema_findings(mesh_result, wiring_findings)

    # Finding index and delta
    current_finding_index = build_finding_index(mesh_result)
    previous_finding_index = None
    if session is not None and session.snapshots:
        previous_finding_index = session.snapshots[-1].finding_index

    # Cycle Detection #147
    cycle_alerts = None
    if session is not None:
        import dataclasses

        from lintgate.orchestration.cycle_detector import (
            EditCycleState,
            detect_cycles,
            track_event,
        )

        state = EditCycleState(**session.edit_cycle_state)
        finding_list = [{"fingerprint": fp} for fp in current_finding_index]
        state = track_event(
            state,
            {
                "tool_name": "controlplane_run",
                "status": "success",
                "findings": finding_list,
            },
        )
        detected = detect_cycles(state)
        session.edit_cycle_state = dataclasses.asdict(state)

        alerts = []
        for c in detected:
            if c.cycle_detected:
                if c.reason == "CYCLE_SAME_FILE":
                    alerts.append(
                        f"Repeated edits to {c.diagnostics.get('file')} without resolving issues."
                    )
                elif c.reason == "CYCLE_SAME_FINDING":
                    alerts.append(
                        f"Finding persisted across {c.diagnostics.get('persistence_count')} runs."
                    )
                elif c.reason == "CYCLE_REPLACE_FAIL":
                    alerts.append(
                        f"Failed to apply edits {c.diagnostics.get('consecutive_failures')} times."
                    )
        if alerts:
            cycle_alerts = alerts

    # Persist session
    if session is not None:
        from lintgate.orchestration.continuity import generate_transfer_packet

        with contextlib.suppress(AttributeError, TypeError):
            session.latest_transfer_packet = generate_transfer_packet(session).to_json()

        # Accumulate delivery metrics
        delivery_total = 0
        delivery_skipped = 0
        channels_seen = set()
        for cr in mesh_result.channel_results:
            if cr.channel == "behavior":
                delivery_skipped += cr.metrics.get("suppressed_nudges", 0)
            if cr.findings:
                delivery_total += len(cr.findings)
                channels_seen.add(cr.channel)

        delivery_metrics = {
            "delivered": delivery_total,
            "skipped": delivery_skipped,
            "channels": list(channels_seen),
        }
        session.delivery_health_summary = delivery_metrics
        # Also store in the current snapshot
        if session.snapshots:
            session.snapshots[-1].delivery_metrics = delivery_metrics

    _persist_session_after_mesh(session, mesh_result, current_finding_index, cp_config)

    # Compact output
    ship_gate_parity = _check_ship_gate_parity(project_root, strictness)

    # Extract proven resolutions for the compact report summary
    proven_resolutions = []
    for cr in mesh_result.channel_results:
        for f in cr.findings:
            if hasattr(f, "proven_resolution") and f.proven_resolution:
                proven_resolutions.append(
                    {
                        "finding": f.kind,
                        "resolution": f.proven_resolution.get("repertoire"),
                        "confidence": f.proven_resolution.get("confidence"),
                    }
                )

    # Session exit gate (#205): check for persistent unresolved test failures
    exit_gate_advisories = None
    persistent_failure_findings = None
    if session is not None and len(session.snapshots) >= 2:
        from lintgate.controlplane.session_memory import (
            check_session_exit_gate,
            escalate_persistent_failures,
        )

        exit_gate_advisories = check_session_exit_gate(session) or None
        persistent_failure_findings = escalate_persistent_failures(session) or None

    compact = format_mesh_report_compact(
        mesh_result,
        cp_config,
        previous_finding_index=previous_finding_index,
        ship_gate_parity=ship_gate_parity,
        cycle_alerts=cycle_alerts,
        proven_resolutions=proven_resolutions,
    )

    # Persist runtime state and drill-down details
    _persist_runtime_state(mesh_result, project_root, session)
    _save_run_details_for_drilldown(
        mesh_result, current_finding_index, compact, helpers
    )

    # Final assembly
    if unknown:
        compact["unknown_channels"] = unknown
    compact.pop("finding_index", None)

    # Refactor state integration (#199): auto-update finding counts
    with contextlib.suppress(Exception):
        from lintgate.refactor_state import update_finding_counts

        run_id_for_refactor = compact.get("run_id", "")
        counts_for_refactor = compact.get("counts", {})
        if run_id_for_refactor:
            update_finding_counts(
                project_root,
                run_id_for_refactor,
                {
                    "blocking": counts_for_refactor.get("blocking", 0),
                    "warning": counts_for_refactor.get("warning", 0),
                    "informational": counts_for_refactor.get("informational", 0),
                },
            )

    # Session exit gate (#205): surface persistent failure advisories
    if exit_gate_advisories:
        compact["session_exit_gate"] = {
            "advisories": exit_gate_advisories,
            "persistent_failures": len(persistent_failure_findings or []),
        }
    if persistent_failure_findings:
        compact["persistent_test_failures"] = persistent_failure_findings[:10]

    # Theory staleness detection (#182): check if uncommitted files lack theory grounding
    git_ctx = getattr(mesh_result, "git_context", None)
    if git_ctx and (git_ctx.get("modified_files") or git_ctx.get("untracked_files")):
        with contextlib.suppress(Exception):
            from lintgate.theory_extractor import check_theory_staleness

            # Use cached theory profile if available, otherwise skip (cheap check)
            theory_profile = None
            if session is not None:
                theory_profile = getattr(session, "theory_profile_cache", None)
            staleness = check_theory_staleness(project_root, theory_profile, git_ctx)
            if staleness.get("stale"):
                compact["theory_staleness"] = {
                    "stale": True,
                    "uncovered_files": staleness["uncovered_files"][:10],
                    "total_uncommitted_py": staleness["total_uncommitted_py"],
                    "recommendation": staleness["recommendation"],
                }
                # Add to next_actions if not already present
                if "next_actions" in compact:
                    compact["next_actions"].append(
                        {
                            "tool": "build_theory_pack",
                            "args": {"path": project_root},
                            "reason": staleness["recommendation"],
                            "priority": 2,
                        }
                    )

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

    result = {
        "total_matching": len(all_findings),
        "findings": all_findings[:max_issues],
    }
    if len(all_findings) > max_issues:
        result["truncated"] = len(all_findings) - max_issues
    return result


def _build_details_next_actions(
    run_id: str, output: dict[str, Any]
) -> list[dict[str, Any]]:
    """Build recommended next actions based on drill-down details."""
    actions = []
    repairs = output.get("repairs", [])
    if repairs:
        safe_count = sum(1 for r in repairs if r.get("safe"))
        if safe_count > 0:
            actions.append(
                {
                    "tool": "controlplane_apply_repairs",
                    "args": {"path": ".", "safe_only": True},
                    "reason": f"Apply {safe_count} safe auto-repairs found in these details.",
                    "priority": 1,
                }
            )

    findings = output.get("findings", [])
    if findings:
        actions.append(
            {
                "tool": "Bash / your file edit tools",
                "reason": "Address the findings listed above by editing the corresponding files.",
                "priority": 2,
            }
        )

    truncate_count = output.get("truncated", 0)
    if truncate_count > 0:
        actions.append(
            {
                "tool": "controlplane_get_details",
                "args": {
                    "run_id": run_id,
                    "max_issues": len(findings) + truncate_count,
                },
                "reason": f"View the remaining {truncate_count} truncated findings.",
                "priority": 3,
            }
        )

    return actions


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


def _impl_controlplane_get_details(
    run_id, channel, severity, max_issues, sections, helpers
):
    """Core implementation of controlplane_get_details."""
    from lintgate.state import load_controlplane_run

    details = load_controlplane_run(run_id)
    if details is None:
        raise ValueError(f"No ControlPlane run found with run_id: {run_id}")

    sections_set = set(
        sections
        or [
            "findings",
            "channel_details",
            "evidence",
            "repairs",
            "coherence",
            "next_actions",
            "proven_resolutions",
        ]
    )
    output: dict[str, Any] = {
        "run_id": run_id,
        "duration_ms": details.get("duration_ms", 0),
    }

    if "coherence" in sections_set:
        output["coherence"] = details.get("coherence", {})

    if "findings" in sections_set:
        output.update(_extract_findings(details, channel, severity, max_issues))
        # Annotate findings with delegation suitability (#195)
        with contextlib.suppress(Exception):
            from lintgate.controlplane.delegation import (
                annotate_findings_with_suitability,
            )

            if "findings" in output:
                annotate_findings_with_suitability(output["findings"], details)

    if "channel_details" in sections_set:
        output["channel_details"] = _extract_channel_details(details, channel)

    if "repairs" in sections_set:
        output["repairs"] = _extract_repairs(details, channel)

    if "evidence" in sections_set:
        evidence = _extract_evidence(details, channel)
        if evidence:
            output["evidence"] = evidence

    if "proven_resolutions" in sections_set:
        # Extract proven resolutions from findings in the run details
        resolutions = []
        for ch_name, ch_data in _filter_channels(details.get("channels", {}), channel):
            for f in ch_data.get("findings", []):
                if f.get("proven_resolution"):
                    resolutions.append(
                        {
                            "channel": ch_name,
                            "finding": f.get("kind"),
                            "message": f.get("message"),
                            "resolution": f["proven_resolution"].get("repertoire"),
                            "confidence": f["proven_resolution"].get("confidence"),
                        }
                    )
        if resolutions:
            output["proven_resolutions"] = resolutions

    if "next_actions" in sections_set:
        output["next_actions"] = _build_details_next_actions(run_id, output)

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
                "transfer_telemetry": {
                    "latest_packet": session.latest_transfer_packet,
                    "packet_age_hours": round(
                        (time.time() - session.last_active) / 3600, 2
                    )
                    if session.latest_transfer_packet
                    else None,
                }
                if hasattr(session, "latest_transfer_packet")
                else {},
                "delivery_health": session.delivery_health_summary
                if hasattr(session, "delivery_health_summary")
                else {},
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


def _generate_living_context_patches(
    session, project_root, accepted_rules, actions_taken
):
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
    path,
    run_id,
    disagreement,
    accepted_constraints,
    rejected_constraints,
    helpers,
    *,
    tuned_findings=None,
    test_failure_classifications=None,
):
    """Core implementation of controlplane_agent_feedback."""
    from lintgate.controlplane.session_memory import get_or_create_session, save_session

    project_root = helpers["_validate_project_root"](path)
    session = get_or_create_session(project_root)
    actions_taken: list[str] = []

    if disagreement:
        _record_disagreement(session, run_id, disagreement, actions_taken)

    accepted_rules = _process_accepted_constraints(
        session, accepted_constraints, actions_taken
    )
    _process_rejected_constraints(session, rejected_constraints, actions_taken)
    _generate_living_context_patches(
        session, project_root, accepted_rules, actions_taken
    )

    # Process signal tunings
    tuned_results: list[str] = []
    rejected_tunings: list[dict] = []
    if tuned_findings:
        tuned_results, rejected_tunings = _process_tuned_findings(
            tuned_findings, project_root, actions_taken
        )

    # Process test failure classifications (#205)
    if test_failure_classifications:
        _process_test_failure_classifications(
            test_failure_classifications, session, actions_taken
        )

    save_session(session)

    result = {
        "session_id": session.session_id,
        "actions_taken": actions_taken,
        "total_disagreements": len(session.agent_disagreements),
        "proposed_constraints": len(session.proposed_constraints),
        "active_proposals": sum(
            1 for c in session.proposed_constraints if c.get("status") == "proposed"
        ),
    }
    if tuned_results:
        result["tuned"] = tuned_results
    if rejected_tunings:
        result["rejected_tunings"] = rejected_tunings

    return json.dumps(result, indent=2)


def _process_tuned_findings(
    tuned_findings: list[dict],
    project_root: str,
    actions_taken: list[str],
) -> tuple[list[str], list[dict]]:
    """Process signal tuning requests from agent feedback."""
    from lintgate.signal_tunings import VALID_ACTIONS, apply_tuning

    tuned: list[str] = []
    rejected: list[dict] = []

    for tf in tuned_findings:
        sig = tf.get("signature", "")
        action = tf.get("action", "")
        rationale = tf.get("rationale", "")

        if not sig:
            rejected.append({"signature": sig, "reason": "missing signature"})
            continue
        if action not in VALID_ACTIONS:
            rejected.append({"signature": sig, "reason": f"invalid action: {action}"})
            continue
        if action != "reset" and not rationale:
            rejected.append(
                {"signature": sig, "reason": "rationale required for tuning"}
            )
            continue

        result = apply_tuning(
            project_root, sig, action, rationale, tf.get("recurrence_count", 0)
        )
        if result.get("error"):
            rejected.append({"signature": sig, "reason": result["error"]})
        else:
            tuned.append(sig)
            actions_taken.append(f"Tuned finding: {sig} ({action})")

    return tuned, rejected


def _process_test_failure_classifications(
    classifications: list[dict],
    session,
    actions_taken: list[str],
) -> None:
    """Record structured test failure classifications in session memory."""
    from lintgate.controlplane.session_memory import record_test_failure_classification

    valid_types = {"stale_test", "known_regression", "flaky", "out_of_scope"}

    for entry in classifications:
        fp = entry.get("fingerprint", "")
        classification = entry.get("classification", "")
        rationale = entry.get("rationale", "")

        if not fp:
            continue
        if classification not in valid_types:
            actions_taken.append(
                f"Rejected classification for {fp}: invalid type '{classification}'"
            )
            continue

        record_test_failure_classification(session, fp, classification, rationale)
        actions_taken.append(f"Classified test failure {fp} as {classification}")


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
        return {
            "action_id": action_id,
            "command": command,
            "status": "error",
            "error": str(e),
        }


def _impl_controlplane_apply_repairs(path, action_ids, safe_only, helpers):
    """Core implementation of controlplane_apply_repairs."""
    from lintgate.controlplane.session_memory import get_or_create_session, save_session

    project_root = helpers["_validate_project_root"](path)
    session = get_or_create_session(project_root)

    pending_repairs = _collect_pending_repairs(session, action_ids, safe_only)

    results = [
        _execute_single_repair(repair, project_root, session)
        for repair in pending_repairs
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
        scope: Literal["project", "changed", "staged", "files", "full_sweep"]
        | None = None,
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
        return _impl_controlplane_run(path, channels, strictness, scope, files, helpers)

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
                Options: "findings", "channel_details", "evidence", "repairs", "coherence", "next_actions", "proven_resolutions"
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

        return json.dumps(
            {
                "source_file": target_file,
                "test_path": test_path,
                "skeleton": skeleton,
                "note": "Review and customize before saving. Use Write tool to create the file.",
                "next_actions": next_actions,
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
        return _impl_controlplane_agent_feedback(
            path,
            run_id,
            disagreement,
            accepted_constraints,
            rejected_constraints,
            helpers,
            tuned_findings=tuned_findings,
            test_failure_classifications=test_failure_classifications,
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
