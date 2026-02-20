#!/usr/bin/env python3
"""LintGate PostToolUse hook — intelligent change-aware linting for Claude Code.

This is the main entry point. Claude Code fires this after every
Write, Edit, MultiEdit, or Bash tool use. It classifies the change,
selects appropriate linters, runs them, and reports structured JSON
back to the agent via systemMessage.

Protocol (from hookify reference):
- stdin: JSON with tool_name, tool_input, tool_output, cwd, etc.
- stdout: JSON with systemMessage and optional hookSpecificOutput
- exit: ALWAYS 0 (non-zero blocks the hook system)

Design principles:
- Silent success: {} when no issues found (zero noise)
- Fast path: read-only Bash commands exit immediately
- Graceful degradation: missing tools are skipped, never crash
- Total timeout: 8 seconds max for entire hook
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import time
from typing import Any

try:
    from lintgate.agent_reporter import format_report
    from lintgate.change_classifier import classify_change
    from lintgate.config import load_config
    from lintgate.lint_runner import run_linters
    from lintgate.registry import build_registry
    from lintgate.results_aggregator import aggregate_results
    from lintgate.state import load_last_run, log_metric, save_run, update_issue_memory
    from lintgate.tier_selector import select_tier
except ModuleNotFoundError:
    _LINTGATE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _LINTGATE_DIR not in sys.path:
        sys.path.insert(0, _LINTGATE_DIR)
    from lintgate.agent_reporter import format_report
    from lintgate.change_classifier import classify_change
    from lintgate.config import load_config
    from lintgate.lint_runner import run_linters
    from lintgate.registry import build_registry
    from lintgate.results_aggregator import aggregate_results
    from lintgate.state import load_last_run, log_metric, save_run, update_issue_memory
    from lintgate.tier_selector import select_tier


def _resolve_event_model_key(input_data: dict[str, Any]) -> str | None:
    """Resolve model identity from hook payload fields/env vars.

    Returns canonical provider:model key, or None when unavailable/unresolvable.
    """
    from lintgate.controlplane.model_profiles import resolve_model_key

    candidates: list[str | None] = [
        input_data.get("model"),
        input_data.get("model_id"),
        input_data.get("model_name"),
        input_data.get("assistant_model"),
    ]

    metadata = input_data.get("metadata")
    if isinstance(metadata, dict):
        candidates.extend(
            [
                metadata.get("model"),
                metadata.get("model_id"),
                metadata.get("model_name"),
            ]
        )

    session_meta = input_data.get("session")
    if isinstance(session_meta, dict):
        candidates.extend(
            [
                session_meta.get("model"),
                session_meta.get("model_id"),
                session_meta.get("model_name"),
            ]
        )

    tool_input = input_data.get("tool_input")
    if isinstance(tool_input, dict):
        candidates.extend(
            [
                tool_input.get("model"),
                tool_input.get("model_id"),
            ]
        )

    for env_key in ("LINTGATE_MODEL_ID", "CLAUDE_MODEL", "OPENAI_MODEL", "MODEL"):
        candidates.append(os.environ.get(env_key))

    for raw in candidates:
        if not isinstance(raw, str) or not raw.strip():
            continue
        canonical = resolve_model_key(raw)
        if canonical:
            return canonical

    return None


def _select_telemetry_profile(store: Any, input_data: dict[str, Any]):
    """Pick the exact model profile for telemetry updates.

    Ambiguous fallback (e.g., "most recently updated profile") is intentionally
    disallowed to prevent cross-model contamination.
    """
    model_key = _resolve_event_model_key(input_data)
    if not model_key:
        return None
    profile = store.profiles.get(model_key)
    if profile and profile.is_usable():
        return profile
    return None


_SESSION_TELEMETRY_UPDATE_CAP = 10
_SESSION_TELEMETRY_COUNTER_KEY = "_model_profile_telem_updates"


def _session_telemetry_updates_used(session: Any) -> int:
    """Return telemetry updates applied in the current session."""
    if session is None or not hasattr(session, "behavior_compass"):
        return 0
    bc = session.behavior_compass
    if not isinstance(bc, dict):
        return 0
    value = bc.get(_SESSION_TELEMETRY_COUNTER_KEY, 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _can_apply_session_telemetry(session: Any) -> bool:
    """Check whether this session still has telemetry update budget."""
    return _session_telemetry_updates_used(session) < _SESSION_TELEMETRY_UPDATE_CAP


def _mark_session_telemetry_applied(session: Any) -> None:
    """Increment the per-session telemetry update counter."""
    if session is None or not hasattr(session, "behavior_compass"):
        return
    bc = session.behavior_compass
    if not isinstance(bc, dict):
        return
    bc[_SESSION_TELEMETRY_COUNTER_KEY] = _session_telemetry_updates_used(session) + 1


def main() -> None:
    """Main hook entry point."""
    start = time.perf_counter()

    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        # No input or malformed — pass through silently
        _exit_clean()

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    tool_output = input_data.get("tool_output", "")
    cwd = input_data.get("cwd", os.getcwd())

    # Quick exit: not a tool we care about
    if tool_name not in ("Write", "Edit", "MultiEdit", "Bash"):
        _exit_clean()

    # Phase 0: Load config (fast — reads one YAML file or auto-detects)
    try:
        config = load_config(cwd)
    except Exception:
        config = _fallback_config(cwd)

    # ControlPlane dispatch: if enabled, run the supervision mesh instead
    try:
        from lintgate.config import load_controlplane_config

        cp_config = load_controlplane_config(cwd)
        if cp_config and cp_config.enabled:
            _run_controlplane(input_data, config, cp_config, cwd, start)
            return  # controlplane handles output and exit
    except Exception:
        pass  # Fall through to legacy pipeline on any error

    # Phase 1: Classify the change
    classification = classify_change(tool_name, tool_input, tool_output, cwd, config)

    # Quick exit: no-op changes
    if classification.risk_level == "none":
        _exit_clean()

    # Phase 1.5: Dependency health check (fast path, < 5ms)
    dep_warnings: list[str] = []
    if classification.change_kind in ("dependency", "build"):
        with contextlib.suppress(Exception):
            from lintgate.dependency_health import quick_dependency_check

            dep_warnings = quick_dependency_check(cwd, classification.change_kind, tool_input)

    # Phase 2: Select lint tier
    tier = select_tier(classification, config)

    # Quick exit: tier says skip
    if tier.skip:
        _exit_clean()

    # Phase 3: Build linter registry and run
    registry = build_registry(config)
    remaining_ms = config.total_timeout_ms - int((time.perf_counter() - start) * 1000)
    remaining_ms = max(remaining_ms, 2000)  # At least 2s for linters

    linter_results = run_linters(tier, config, registry, timeout_ms=remaining_ms)

    # Phase 4: Aggregate results
    aggregated = aggregate_results(
        linter_results,
        config,
        tier_name=tier.name,
        tier_reason=tier.reason,
    )

    # Track recurring issue patterns before formatting.
    all_issues = [*aggregated.blocking, *aggregated.warnings, *aggregated.informational]
    recurrence = {"repeated_issue_count": 0, "unique_signatures_tracked": 0, "top_repeated": []}
    with contextlib.suppress(Exception):
        recurrence = update_issue_memory(cwd, all_issues)

    # Phase 3.5: Update pattern bank (categorical anti-tail-chasing)
    pattern_report = {"alerted_patterns": [], "top_categories": []}
    with contextlib.suppress(Exception):
        from lintgate.pattern_bank import update_pattern_bank

        pattern_report = update_pattern_bank(cwd, all_issues)

    # Phase 5: Format report
    last_run = load_last_run(cwd)
    report = format_report(
        aggregated,
        last_run,
        recurrence_summary=recurrence,
        pattern_report=pattern_report,
    )

    # Save state for delta tracking
    with contextlib.suppress(Exception):
        save_run(cwd, aggregated)

    # Log metrics (non-blocking)
    with contextlib.suppress(Exception):
        elapsed_ms = (time.perf_counter() - start) * 1000
        log_metric(
            {
                "event": "lint_run",
                "project": cwd,
                "tier": tier.name,
                "change_kind": classification.change_kind,
                "risk_level": classification.risk_level,
                "files": classification.files_changed,
                "blocking_count": len(aggregated.blocking),
                "warning_count": len(aggregated.warnings),
                "info_count": len(aggregated.informational),
                "linters_run": aggregated.metrics.get("linters_run", 0),
                "duration_ms": round(elapsed_ms, 1),
                "repeated_issue_count": recurrence.get("repeated_issue_count", 0),
            }
        )

    # Inject dependency health warnings into report
    if dep_warnings:
        if not report:
            report = {}
        dep_msg = "\n".join(dep_warnings)
        existing = report.get("systemMessage", "")
        if existing:
            report["systemMessage"] = existing + "\n\n--- Dependency Health ---\n" + dep_msg
        else:
            report["systemMessage"] = "--- Dependency Health ---\n" + dep_msg

    # Output
    if report:
        print(json.dumps(report))
    else:
        print(json.dumps({}))

    sys.exit(0)


def _run_controlplane(input_data: dict, config, cp_config, cwd: str, start: float) -> None:
    """Run the ControlPlane supervision mesh.

    This replaces the legacy 7-phase pipeline when controlplane is enabled.
    Constructs a SupervisionEvent, runs the mesh, formats the report, and outputs.
    """
    from lintgate.channels.dependency_channel import DependencyChannel
    from lintgate.channels.git_channel import GitChannel
    from lintgate.channels.lint_channel import LintChannel
    from lintgate.channels.structure_channel import StructureChannel
    from lintgate.channels.test_channel import TestChannel
    from lintgate.controlplane.reporter import format_mesh_report
    from lintgate.controlplane.runtime import run_mesh
    from lintgate.controlplane.types import SupervisionEvent

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    tool_output = input_data.get("tool_output", "")

    # Classify the change (reuse existing classifier)
    classification = classify_change(tool_name, tool_input, tool_output, cwd, config)

    # Behavior compass: record ALL tool events (including read-only) before risk_level gate.
    # This ensures bash:read ratio and action history capture events that other channels skip.
    if cp_config.channel_enabled("behavior") and cp_config.session_memory:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.behavior_compass import record_tool_event
            from lintgate.controlplane.session_memory import (
                get_or_create_session,
                load_behavior_compass,
                save_behavior_compass,
                save_session,
            )

            _beh_session = get_or_create_session(cwd, cp_config.session_max_age_hours)
            _compass = load_behavior_compass(_beh_session)
            record_tool_event(_compass, tool_name, tool_input, tool_output)
            save_behavior_compass(_beh_session, _compass)
            save_session(_beh_session)

    # Global behavior profile: load priors if enabled
    _global_priors = None
    if cp_config.global_memory_enabled and cp_config.channel_enabled("behavior"):
        with contextlib.suppress(Exception):
            from lintgate.controlplane.global_behavior_profile import (
                MIN_SAMPLE_SIZE,
                load_global_profile,
            )

            _gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
            if _gp.session_count >= MIN_SAMPLE_SIZE:
                _global_priors = {
                    "enabled": True,
                    "alpha": cp_config.global_memory_alpha,
                    "decay_horizon": cp_config.global_memory_decay_horizon,
                    "computed_bias_adjustments": _gp.computed_bias_adjustments,
                }

    # Quick exit: no-op changes
    if classification.risk_level == "none":
        _exit_clean()

    # Build the SupervisionEvent
    event = SupervisionEvent(
        surface="hook",
        project_root=cwd,
        tool_name=tool_name,
        files_changed=classification.files_changed,
        change_classification=classification,
        raw_input=input_data,
    )

    # Build channel list
    channels = [
        LintChannel(),
        TestChannel(),
        DependencyChannel(),
        GitChannel(),
    ]

    if cp_config.channel_enabled("structure"):
        channels.append(StructureChannel())

    if cp_config.channel_enabled("behavior"):
        from lintgate.channels.behavior_channel import BehaviorChannel

        channels.append(BehaviorChannel())

    # Session memory: load or create session if enabled
    session = None
    proposed_constraints: list[dict] = []
    if cp_config.session_memory:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import get_or_create_session

            session = get_or_create_session(cwd, cp_config.session_max_age_hours)

    # Inject behavior compass into event for BehaviorChannel (read-only for channel)
    if session is not None and cp_config.channel_enabled("behavior"):
        event.raw_input["behavior_compass"] = session.behavior_compass

    # Inject global behavior priors if available
    if _global_priors is not None:
        event.raw_input["behavior_global_priors"] = _global_priors

    # Architecture of Inquiry: cache theory profile once per mesh run
    if session is not None and cp_config.inquiry.any_enabled():
        try:
            from lintgate.theory_extractor import extract_theory

            _theory_result = extract_theory(cwd)
            session.theory_profile_cache = _theory_result.get("theory_profile")
        except Exception:
            # Graceful fallback: missing docs, parse errors, etc.
            # All downstream consumers treat None as no-op.
            session.theory_profile_cache = None

        # Inject theory profile into event for channels (read-only)
        if session.theory_profile_cache is not None:
            event.raw_input["theory_profile"] = session.theory_profile_cache

    # Advisory gate: warn when editing without sufficient theory context
    _session_advisory: str | None = None
    if (
        session is not None
        and cp_config.inquiry.session_gate
        and tool_name in ("Write", "Edit", "MultiEdit")
        and not session.behavior_compass.get("_session_ready", False)
    ):
        with contextlib.suppress(Exception):
            from lintgate.context_auditor import check_session_readiness

            _readiness = check_session_readiness(
                cwd,
                theory_profile=session.theory_profile_cache,
            )
            if not _readiness.ready:
                _session_advisory = (
                    f"[Session Advisory] Context not ready for deep supervision. "
                    f"Missing: {', '.join(_readiness.missing)}. "
                    f"{_readiness.recommendation}"
                )
                # Short-circuit expensive channels: remove behavior channel
                channels = [ch for ch in channels if ch.name != "behavior"]
            else:
                session.behavior_compass["_session_ready"] = True

    # Run the mesh (with session for trajectory-aware coherence)
    mesh_result = run_mesh(event, cp_config, channels, session=session)

    # Build finding index for session delta tracking
    _finding_index: dict = {}
    with contextlib.suppress(Exception):
        from lintgate.controlplane.reporter import build_finding_index

        _finding_index = build_finding_index(mesh_result)

    # Session memory: record snapshot and run constraint proposer
    if session is not None:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import record_mesh_run, save_session

            snapshot = record_mesh_run(session, mesh_result, finding_index=_finding_index)

            # Enrich snapshot with behavioral fields
            for cr in mesh_result.channel_results:
                if cr.channel == "behavior":
                    snapshot.behavior_alerts = [f.kind for f in cr.findings]
                    # v2: Apply compass delta (cooldown counters, nudge flags)
                    if "behavior_compass_delta" in cr.metrics:
                        from lintgate.controlplane.session_memory import (
                            load_behavior_compass as _lbc,
                        )
                        from lintgate.controlplane.session_memory import (
                            save_behavior_compass as _sbc,
                        )

                        _delta = cr.metrics["behavior_compass_delta"]
                        _existing_telem_updates = _session_telemetry_updates_used(session)
                        _bc = _lbc(session)
                        _bc.last_fired = _delta.get("last_fired", _bc.last_fired)
                        _bc.signal_fire_counts = _delta.get(
                            "signal_fire_counts", _bc.signal_fire_counts
                        )
                        _bc.early_nudge_emitted = _delta.get(
                            "early_nudge_emitted", _bc.early_nudge_emitted
                        )
                        _bc.pending_nudge_signals = _delta.get(
                            "pending_nudge_signals", _bc.pending_nudge_signals
                        )
                        _bc.pending_nudge_precheck_count = _delta.get(
                            "pending_nudge_precheck_count",
                            _bc.pending_nudge_precheck_count,
                        )
                        _bc.nudge_outcomes = _delta.get("nudge_outcomes", _bc.nudge_outcomes)
                        _sbc(session, _bc)
                        if _existing_telem_updates > 0:
                            session.behavior_compass[_SESSION_TELEMETRY_COUNTER_KEY] = (
                                _existing_telem_updates
                            )
                        # Merge theory coda dedup state (not a compass field, stored as extra key)
                        # Use dict merge so prior signals' codas aren't dropped
                        if "_theory_recent_codas" in _delta:
                            _existing_codas = session.behavior_compass.get(
                                "_theory_recent_codas", {}
                            )
                            _existing_codas.update(_delta["_theory_recent_codas"])
                            session.behavior_compass["_theory_recent_codas"] = _existing_codas

                    # v3: Apply global profile delta
                    if cp_config.global_memory_enabled and "global_profile_delta" in cr.metrics:
                        with contextlib.suppress(Exception):
                            from lintgate.controlplane.global_behavior_profile import (
                                apply_session_delta as _apply_gp,
                            )
                            from lintgate.controlplane.global_behavior_profile import (
                                load_global_profile as _load_gp,
                            )
                            from lintgate.controlplane.global_behavior_profile import (
                                save_global_profile as _save_gp,
                            )

                            _gp = _load_gp(ttl_days=cp_config.global_memory_ttl_days)
                            _gp_delta = cr.metrics["global_profile_delta"]
                            _session_id = session.session_id if session else ""
                            _apply_gp(_gp, _gp_delta, session_id=_session_id)
                            _save_gp(_gp)

                    # v4: Model profile telemetry refinement
                    with contextlib.suppress(Exception):
                        from lintgate.controlplane.model_profiles import (
                            apply_telemetry_update as _apply_telem,
                        )
                        from lintgate.controlplane.model_profiles import (
                            load_profiles as _load_mp,
                        )
                        from lintgate.controlplane.model_profiles import (
                            save_profiles as _save_mp,
                        )

                        _mp_store = _load_mp()
                        _active = _select_telemetry_profile(_mp_store, input_data)
                        _signal_fires = {}
                        if session:
                            _bc_data = session.behavior_compass
                            if isinstance(_bc_data, dict):
                                _signal_fires = _bc_data.get(
                                    "signal_fire_counts", {}
                                )
                            _event_count = (
                                _bc_data.get("event_counter", 0)
                                if isinstance(_bc_data, dict)
                                else 0
                            )
                        else:
                            _event_count = 0
                        if (
                            _active is not None
                            and _signal_fires
                            and _event_count >= 10
                            and _can_apply_session_telemetry(session)
                        ):
                            _apply_telem(
                                _active, _signal_fires, _event_count
                            )
                            _mark_session_telemetry_applied(session)
                            _save_mp(_mp_store)

                    break

            # Record tool-level fields on snapshot
            snapshot.action_type = tool_name.lower()
            if tool_name == "Bash":
                from lintgate.controlplane.behavior_compass import (
                    extract_error_sig,
                    normalize_command_sig,
                )

                cmd = ""
                if isinstance(tool_input, dict):
                    cmd = tool_input.get("command", "")
                elif isinstance(tool_input, str):
                    cmd = tool_input
                snapshot.command_signature = normalize_command_sig(cmd)
                snapshot.error_signature = extract_error_sig(
                    tool_output if isinstance(tool_output, str) else ""
                )
                output_str = tool_output if isinstance(tool_output, str) else str(tool_output)
                exit_match = re.search(
                    r"(?:exit[_ ]code|exit[_ ]status|exitstatus)[: =]+(\d+)",
                    output_str,
                    re.IGNORECASE,
                )
                if exit_match:
                    snapshot.exit_code = int(exit_match.group(1))
                elif "error" in output_str.lower() or "failed" in output_str.lower():
                    snapshot.exit_code = 1
                else:
                    snapshot.exit_code = 0

        # Run constraint proposer if pattern alerts exist
        with contextlib.suppress(Exception):
            from lintgate.controlplane.constraint_proposer import (
                propose_constraints_from_patterns,
                store_proposals_in_session,
            )

            pattern_alerts: list[dict] = []
            # Extract recurring pattern report from lint channel
            for cr in mesh_result.channel_results:
                if cr.channel == "lint":
                    pattern_alerts.extend(cr.metrics.get("pattern_alerts", []))
                    break

            # Promote recurring behavior findings via session trend history.
            # This mirrors lint pattern alerts with synthetic recurrence metadata.
            for key, counts in session.pattern_trend.items():
                if "|" not in key:
                    continue
                linter, kind = key.split("|", 1)
                if linter != "behavior_channel":
                    continue
                recent = counts[-5:]
                recent_run_count = sum(1 for c in recent if c > 0)
                if recent_run_count <= 0:
                    continue
                pattern_alerts.append(
                    {
                        "linter": linter,
                        "kind": kind,
                        "alert_reason": "recurring_across_runs",
                        "recent_run_count": recent_run_count,
                    }
                )

            if pattern_alerts:
                pattern_report = {"alerted_patterns": pattern_alerts}
                proposals = propose_constraints_from_patterns(
                    pattern_report,
                    session=session,
                    threshold=cp_config.constraint_proposal_threshold,
                    config=cp_config,
                )
                if proposals:
                    store_proposals_in_session(session, proposals)

            proposed_constraints = session.proposed_constraints

        # Save session after all updates
        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import save_session

            save_session(session)

        # Clear transient theory profile cache (per-run only, not persisted)
        session.theory_profile_cache = None

    # Save full run details for controlplane_get_details drill-down
    if _finding_index:
        with contextlib.suppress(Exception):
            from lintgate.state import save_controlplane_run

            _cp_details: dict = {
                "coherence": {
                    "state": mesh_result.coherence.state,
                    "summary": mesh_result.coherence.summary,
                    "recommended_action": mesh_result.coherence.recommended_action,
                    "silent_channels": list(mesh_result.coherence.silent_channels),
                    "loud_channels": list(mesh_result.coherence.loud_channels),
                },
                "duration_ms": mesh_result.duration_ms,
                "partial": mesh_result.partial,
                "incomplete_channels": mesh_result.incomplete_channels,
                "finding_index": _finding_index,
                "channels": {},
            }
            for _cr in mesh_result.channel_results:
                if _cr.status == "skip":
                    continue
                _cp_details["channels"][_cr.channel] = {
                    "status": _cr.status,
                    "severity": _cr.severity,
                    "duration_ms": round(_cr.duration_ms, 1),
                    "error": _cr.error_message,
                    "findings": [f.to_dict() for f in _cr.findings],
                    "repairs": [
                        {
                            "action_id": r.action_id,
                            "kind": r.kind,
                            "summary": r.summary,
                            "safe": r.safe,
                            "payload": r.payload,
                        }
                        for r in _cr.repairs
                    ],
                    "metrics": _cr.metrics,
                }
            _run_id = mesh_result.event.event_id if mesh_result.event else ""
            if _run_id:
                save_controlplane_run(_run_id, _cp_details)

    # Format report (with proposed constraints if any)
    report = format_mesh_report(mesh_result, cp_config, proposed_constraints=proposed_constraints)

    # Log metrics (non-blocking)
    with contextlib.suppress(Exception):
        elapsed_ms = (time.perf_counter() - start) * 1000
        log_metric(
            {
                "event": "controlplane_run",
                "project": cwd,
                "tool_name": tool_name,
                "change_kind": classification.change_kind,
                "risk_level": classification.risk_level,
                "coherence_state": mesh_result.coherence.state,
                "channels_run": len([r for r in mesh_result.channel_results if r.status != "skip"]),
                "partial": mesh_result.partial,
                "duration_ms": round(elapsed_ms, 1),
                "session_active": session is not None,
            }
        )

    # Inject session advisory if present
    if _session_advisory and report:
        existing_msg = report.get("systemMessage", "")
        if existing_msg:
            report["systemMessage"] = _session_advisory + "\n\n" + existing_msg
        else:
            report["systemMessage"] = _session_advisory

    # Output
    if report:
        print(json.dumps(report))
    else:
        print(json.dumps({}))

    sys.exit(0)


def _fallback_config(cwd: str):
    """Minimal config when loading fails."""
    from lintgate.types import ProjectConfig

    return ProjectConfig(project_root=cwd)


def _exit_clean() -> None:
    """Exit cleanly with empty output."""
    print(json.dumps({}))
    sys.exit(0)


if __name__ == "__main__":
    main()
