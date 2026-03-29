"""ControlPlane run implementation — channel selection, file resolution, mesh execution, result assembly.

Extracted from controlplane_tools.py to keep the register() module under 400 lines.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import subprocess
from pathlib import Path
from typing import Any

# ── Channel selection helpers ───────────────────────────────────────────

_ALL_CHANNEL_NAMES = "lint,tests,deps,git,behavior,structure,performance,test_effectiveness,specification,test_hygiene"

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
    "test_hygiene": (
        "Test suite hygiene "
        "(stub detection, weak-only assertions, duplicate tests, file subsumption)"
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
    from lintgate.channels.test_hygiene_channel import TestHygieneChannel

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
        "test_hygiene": TestHygieneChannel(),
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
            from lintgate.types import LintIssue

            for issue in issues:
                code = "WIRE001" if issue.issue_type == "missing_publisher" else "WIRE002"
                findings.append(
                    LintIssue(
                        file="<schema>",
                        line=0,
                        column=0,
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
        from lintgate.types import LintIssue

        # Per-channel output validation (skip-aware)
        for cr in mesh_result.channel_results:
            missing = validate_result(cr.channel, cr.metrics, status=cr.status)
            for key in missing:
                wiring_findings.append(
                    LintIssue(
                        file="<schema>",
                        line=0,
                        column=0,
                        linter="metric_schema",
                        kind="WIRE002",
                        message=(
                            f"Channel '{cr.channel}' declared key '{key}' but did not publish it"
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


def _dedup_files(fs: list[str], limit: int = 50) -> list[str]:
    """Deduplicate file paths by resolved form, capping at *limit*."""
    seen: set[str] = set()
    res: list[str] = []
    for f in fs[:limit]:
        r = str(Path(f).resolve())
        if r not in seen:
            seen.add(r)
            res.append(f)
    return res


def _resolve_explicit_files(project_root: str, files: list[str]) -> list[str]:
    """Resolve an explicit file list (scope='files')."""
    if not files:
        raise ValueError("scope='files' requires a non-empty files list")
    return [
        str((Path(project_root) / f if not Path(f).is_absolute() else Path(f)).resolve())
        for f in files
    ]


def _resolve_git_changed_files(
    project_root: str,
    scope: str | None,
    py_files: list[str],
) -> list[str] | None:
    """Return git-changed Python files, or *None* on failure."""
    try:
        if scope == "staged":
            cmd = ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"]
        else:
            cmd = ["git", "diff", "--name-only", "HEAD", "--diff-filter=ACMR"]

        proc = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        git_files = [f for f in proc.stdout.splitlines() if f.endswith(".py")]

        if scope != "staged":
            proc2 = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                cwd=project_root,
                capture_output=True,
                text=True,
            )
            git_files.extend(f for f in proc2.stdout.splitlines() if f.endswith(".py"))

        full_paths = [str(Path(project_root) / f) for f in set(git_files)]
        existing_py = {str(Path(f).resolve()) for f in py_files}
        resolved = [f for f in full_paths if str(Path(f).resolve()) in existing_py]
        return resolved or None
    except Exception:
        return None


_KNOWN_SCOPES = {"project", "changed", "staged", "full_sweep"}


def _resolve_scope_files(project_root, scope, files, helpers):
    """Resolve files based on requested scope."""
    if scope == "files":
        return _resolve_explicit_files(project_root, files)

    if scope and scope not in _KNOWN_SCOPES:
        raise ValueError(f"Unknown scope: {scope}")

    py_files = helpers["_collect_python_files"](project_root)

    if scope == "full_sweep":
        return _dedup_files(py_files, limit=len(py_files))

    if scope == "project":
        return _dedup_files(py_files)

    # changed / staged / default
    git_resolved = _resolve_git_changed_files(project_root, scope, py_files)
    if git_resolved:
        return _dedup_files(git_resolved)

    return _dedup_files(py_files)


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
    compass.signal_fire_counts = delta.get("signal_fire_counts", compass.signal_fire_counts)
    compass.early_nudge_emitted = delta.get("early_nudge_emitted", compass.early_nudge_emitted)
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
            1 for cr in mesh_result.channel_results for f in cr.findings if f.severity == "blocking"
        )
        warnings = sum(
            1 for cr in mesh_result.channel_results for f in cr.findings if f.severity == "warning"
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


def _check_ship_gate_parity(project_root: str, strictness: str) -> dict[str, Any]:
    """Run ship_main.py --preflight --json if strict, or return advisory."""
    import json

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
            return json.loads(proc.stdout)  # type: ignore[no-any-return]
        except Exception:
            return {
                "status": "error",
                "exit_code": proc.returncode,
                "error": "Failed to parse json from ship_main.py",
                "stderr": proc.stderr[-200:] if proc.stderr else "",
            }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def _record_tool_event_for_behavior(session, cp_config) -> None:
    """Record controlplane_run as a tool event for behavioral tracking (#191)."""
    if session is None or not cp_config.channel_enabled("behavior"):
        return
    with contextlib.suppress(Exception):
        from lintgate.controlplane.behavior_compass import record_tool_event
        from lintgate.controlplane.session_memory import (
            load_behavior_compass,
            save_behavior_compass,
        )

        compass = load_behavior_compass(session)
        record_tool_event(compass, "controlplane_run", {}, "")
        save_behavior_compass(session, compass)


_CYCLE_REASON_TEMPLATES: dict[str, str] = {
    "CYCLE_SAME_FILE": "Repeated edits to {file} without resolving issues.",
    "CYCLE_SAME_FINDING": "Finding persisted across {persistence_count} runs.",
    "CYCLE_REPLACE_FAIL": "Failed to apply edits {consecutive_failures} times.",
}


def _compute_finding_recurrence(session) -> dict[str, int]:
    """Build fingerprint → run-count map from session snapshot history.

    Counts how many previous snapshots contained each finding fingerprint,
    enabling the compact reporter to surface recurrence data like
    "seen in 5 of 8 runs".
    """
    recurrence: dict[str, int] = {}
    for snapshot in session.snapshots:
        idx = getattr(snapshot, "finding_index", None)
        if not isinstance(idx, dict):
            continue
        for fp in idx:
            recurrence[fp] = recurrence.get(fp, 0) + 1
    return recurrence


def _detect_edit_cycles(session, current_finding_index: dict) -> list[str] | None:
    """Detect edit cycles (#147) and return alert strings, or None."""
    if session is None:
        return None

    import dataclasses as _dc

    from lintgate.orchestration.cycle_detector import (
        EditCycleState,
        detect_cycles,
        track_event,
    )

    state = EditCycleState(**session.edit_cycle_state)
    finding_list = [{"fingerprint": fp} for fp in current_finding_index]
    state = track_event(
        state,
        {"tool_name": "controlplane_run", "status": "success", "findings": finding_list},
    )
    detected = detect_cycles(state)
    session.edit_cycle_state = _dc.asdict(state)

    alerts: list[str] = []
    for c in detected:
        if not c.cycle_detected:
            continue
        template = _CYCLE_REASON_TEMPLATES.get(c.reason) if c.reason else None
        if template:
            alerts.append(template.format_map(c.diagnostics))
    return alerts or None


def _accumulate_delivery_metrics(session, mesh_result) -> None:
    """Accumulate delivery metrics into session and latest snapshot."""
    if session is None:
        return

    from lintgate.orchestration.continuity import generate_transfer_packet

    with contextlib.suppress(AttributeError, TypeError):
        session.latest_transfer_packet = generate_transfer_packet(session).to_json()

    delivery_total = 0
    delivery_skipped = 0
    channels_seen: set[str] = set()
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
    if session.snapshots:
        session.snapshots[-1].delivery_metrics = delivery_metrics


def _extract_proven_resolutions(mesh_result) -> list[dict[str, Any]]:
    """Extract proven resolutions from mesh channel findings."""
    resolutions: list[dict[str, Any]] = []
    for cr in mesh_result.channel_results:
        for f in cr.findings:
            if hasattr(f, "proven_resolution") and f.proven_resolution:
                resolutions.append(
                    {
                        "finding": f.kind,
                        "resolution": f.proven_resolution.get("repertoire"),
                        "confidence": f.proven_resolution.get("confidence"),
                    }
                )
    return resolutions


def _check_exit_gate(session) -> tuple[list | None, list | None]:
    """Session exit gate (#205): check for persistent unresolved test failures."""
    if session is None or len(session.snapshots) < 2:
        return None, None

    from lintgate.controlplane.session_memory import (
        check_session_exit_gate,
        escalate_persistent_failures,
    )

    advisories = check_session_exit_gate(session) or None
    failures = escalate_persistent_failures(session) or None
    return advisories, failures


def _update_refactor_state(compact: dict, project_root: str) -> None:
    """Refactor state integration (#199): auto-update finding counts."""
    with contextlib.suppress(Exception):
        from lintgate.refactor_state import update_finding_counts

        run_id = compact.get("run_id", "")
        counts = compact.get("counts", {})
        if run_id:
            update_finding_counts(
                project_root,
                run_id,
                {
                    "blocking": counts.get("blocking", 0),
                    "warning": counts.get("warning", 0),
                    "informational": counts.get("informational", 0),
                },
            )


def _check_theory_staleness_for_compact(
    compact: dict,
    mesh_result,
    session,
    project_root: str,
) -> None:
    """Theory staleness detection (#182): enrich compact if uncommitted files lack grounding."""
    git_ctx = getattr(mesh_result, "git_context", None)
    if not git_ctx:
        return
    if not (git_ctx.get("modified_files") or git_ctx.get("untracked_files")):
        return

    with contextlib.suppress(Exception):
        from lintgate.theory_extractor import check_theory_staleness

        theory_profile = None
        if session is not None:
            theory_profile = getattr(session, "theory_profile_cache", None)
        staleness = check_theory_staleness(project_root, theory_profile, git_ctx)
        if not staleness.get("stale"):
            return
        compact["theory_staleness"] = {
            "stale": True,
            "uncovered_files": staleness["uncovered_files"][:10],
            "total_uncommitted_py": staleness["total_uncommitted_py"],
            "recommendation": staleness["recommendation"],
        }
        if "next_actions" in compact:
            compact["next_actions"].append(
                {
                    "tool": "build_theory_pack",
                    "args": {"path": project_root},
                    "reason": staleness["recommendation"],
                    "priority": 2,
                }
            )


def _apply_exit_gate_to_compact(
    compact: dict,
    exit_gate_advisories: list | None,
    persistent_failure_findings: list | None,
) -> None:
    """Surface session exit gate advisories in compact output."""
    if exit_gate_advisories:
        compact["session_exit_gate"] = {
            "advisories": exit_gate_advisories,
            "persistent_failures": len(persistent_failure_findings or []),
        }
    if persistent_failure_findings:
        compact["persistent_test_failures"] = persistent_failure_findings[:10]


def _setup_run(path, channels, strictness, scope, files, helpers):
    """Phase 1: Config loading, channel selection, event creation, session init.

    Returns (project_root, cp_config, active_channels, requested, unknown,
             event, session, wiring_findings).
    """
    from lintgate.config import load_controlplane_config
    from lintgate.controlplane.types import ControlPlaneConfig

    project_root = helpers["_validate_project_root"](path)

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("controlplane", project_root)

    cp_config = load_controlplane_config(project_root)
    if not cp_config:
        cp_config = ControlPlaneConfig(enabled=True)

    channel_registry = _build_channel_registry()
    active_channels, requested, unknown = _select_channels(channels, channel_registry)
    wiring_findings = _validate_channel_wiring([ch.name for ch in active_channels])
    files_for_event = _collect_files_for_event(project_root, scope, files, helpers)

    cp_config.latency_budget_ms = _compute_dynamic_budget_ms(
        cp_config.latency_budget_ms,
        len(files_for_event),
        scope,
    )
    event = _build_supervision_event(project_root, files_for_event, strictness, requested)

    session = _setup_session(project_root, cp_config)

    return (
        project_root,
        cp_config,
        active_channels,
        requested,
        unknown,
        event,
        session,
        wiring_findings,
    )


def _execute_channels(event, cp_config, active_channels, session, wiring_findings):
    """Phase 2: Run mesh and post-mesh schema validation."""
    from lintgate.controlplane.runtime import run_mesh

    mesh_result = run_mesh(event, cp_config, active_channels, session=session)
    _append_schema_findings(mesh_result, wiring_findings)
    return mesh_result


@dataclasses.dataclass
class _RunContext:
    """Bundles state accumulated across controlplane_run phases."""

    project_root: str
    cp_config: Any
    session: Any
    strictness: str
    unknown: list[str]
    helpers: dict[str, Any]
    mesh_result: Any = None
    finding_index: dict = dataclasses.field(default_factory=dict)


def _build_run_result(ctx: _RunContext) -> str:
    """Phase 3: Result assembly — compact report, coherence, next_actions.

    Returns a slim tool_response JSON string (analysis saved to disk).
    """
    from lintgate.controlplane.reporter import format_mesh_report_compact
    from mcp_tools._disk_helpers import tool_response

    mesh_result = ctx.mesh_result
    session = ctx.session
    finding_index = ctx.finding_index

    _accumulate_delivery_metrics(session, mesh_result)
    _persist_session_after_mesh(session, mesh_result, finding_index, ctx.cp_config)

    previous_finding_index = None
    finding_recurrence: dict[str, int] | None = None
    if session is not None and session.snapshots:
        previous_finding_index = session.snapshots[-1].finding_index
        # Build recurrence map: fingerprint → number of snapshots it appeared in
        finding_recurrence = _compute_finding_recurrence(session)

    compact = format_mesh_report_compact(
        mesh_result,
        ctx.cp_config,
        previous_finding_index=previous_finding_index,
        ship_gate_parity=_check_ship_gate_parity(ctx.project_root, ctx.strictness),
        cycle_alerts=_detect_edit_cycles(session, finding_index),
        proven_resolutions=_extract_proven_resolutions(mesh_result),
        finding_recurrence=finding_recurrence,
    )

    _persist_runtime_state(mesh_result, ctx.project_root, session)
    _save_run_details_for_drilldown(mesh_result, finding_index, compact, ctx.helpers)

    # Final enrichments
    if ctx.unknown:
        compact["unknown_channels"] = ctx.unknown
    compact.pop("finding_index", None)

    _update_refactor_state(compact, ctx.project_root)

    exit_gate_advisories, persistent_failure_findings = _check_exit_gate(session)
    _apply_exit_gate_to_compact(compact, exit_gate_advisories, persistent_failure_findings)
    _check_theory_staleness_for_compact(compact, mesh_result, session, ctx.project_root)

    onboarding = ctx.helpers["_build_onboarding_status"](ctx.project_root)
    if onboarding.get("config_state") != "config_enabled":
        compact["onboarding"] = onboarding

    # Build NL summary from compact counts and coherence
    counts = compact.get("counts", {})
    coherence = compact.get("coherence", {})
    top_blockers = []
    for issue in compact.get("blocking_issues", [])[:5]:
        top_blockers.append(
            f"  {issue.get('kind', '?'):24s} {issue.get('file', '?'):30s} "
            f"{issue.get('message', '')[:60]}"
        )

    lines = [
        f"{counts.get('blocking', 0)} blocking, {counts.get('warning', 0)} warnings "
        f"across {counts.get('channels_run', 0)} channels.",
    ]
    if coherence.get("state"):
        lines.append(f"Coherence: {coherence['state']}")
    if top_blockers:
        lines.append("\nTop blockers:")
        lines.extend(top_blockers)

    summary = "\n".join(lines)
    return tool_response(
        compact,
        "controlplane_run",
        ctx.project_root,
        summary,
        run_id=compact.get("run_id", ""),
        next_actions=compact.get("next_actions"),
        extra={
            "run_id": compact.get("run_id"),
            "counts": counts,
            "coherence": coherence.get("state", "unknown"),
        },
    )


def _impl_controlplane_run(path, channels, strictness, scope, files, helpers):
    """Core implementation of controlplane_run."""
    from lintgate.controlplane.reporter import build_finding_index

    # Phase 1 — setup
    (
        project_root,
        cp_config,
        active_channels,
        requested,
        unknown,
        event,
        session,
        wiring_findings,
    ) = _setup_run(path, channels, strictness, scope, files, helpers)

    # Behavior tracking
    _record_tool_event_for_behavior(session, cp_config)
    _inject_behavior_priors(event, session, cp_config)

    # Phase 2 — execute channels
    mesh_result = _execute_channels(
        event,
        cp_config,
        active_channels,
        session,
        wiring_findings,
    )

    # Phase 3 — build result
    ctx = _RunContext(
        project_root=project_root,
        cp_config=cp_config,
        session=session,
        strictness=strictness,
        unknown=unknown,
        helpers=helpers,
        mesh_result=mesh_result,
        finding_index=build_finding_index(mesh_result),
    )
    return _build_run_result(ctx)
