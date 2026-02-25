"""ControlPlane session management helpers for the PostToolUse hook.

Handles session setup, global priors, behavior delta application, constraint
proposing, finding index tracking, and post-run session processing.
"""

from __future__ import annotations

import contextlib
import os
import re
from typing import Any

# ── Session telemetry counter helpers ────────────────────────────────

_SESSION_TELEMETRY_UPDATE_CAP = 10
_SESSION_TELEMETRY_COUNTER_KEY = "_model_profile_telem_updates"


def session_telemetry_updates_used(session: Any) -> int:
    """Return telemetry updates applied in the current session."""
    if session is None or not hasattr(session, "behavior_compass"):
        return 0
    bc = session.behavior_compass
    if not isinstance(bc, dict):
        return 0
    value = bc.get(_SESSION_TELEMETRY_COUNTER_KEY, 0)
    return value if isinstance(value, int) and value >= 0 else 0


def can_apply_session_telemetry(session: Any) -> bool:
    """Check whether this session still has telemetry update budget."""
    return session_telemetry_updates_used(session) < _SESSION_TELEMETRY_UPDATE_CAP


def mark_session_telemetry_applied(session: Any) -> None:
    """Increment the per-session telemetry update counter."""
    if session is None or not hasattr(session, "behavior_compass"):
        return
    bc = session.behavior_compass
    if not isinstance(bc, dict):
        return
    bc[_SESSION_TELEMETRY_COUNTER_KEY] = session_telemetry_updates_used(session) + 1


# ── Model key resolution ─────────────────────────────────────────────


def resolve_event_model_key(input_data: dict[str, Any]) -> str | None:
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


def select_telemetry_profile(store: Any, input_data: dict[str, Any]):
    """Pick the exact model profile for telemetry updates.

    Ambiguous fallback (e.g., "most recently updated profile") is intentionally
    disallowed to prevent cross-model contamination.
    """
    model_key = resolve_event_model_key(input_data)
    if not model_key:
        return None
    profile = store.profiles.get(model_key)
    if profile and profile.is_usable():
        return profile
    return None


# ── Global priors ────────────────────────────────────────────────────


def load_global_priors(cp_config: Any) -> dict | None:
    """Load global behavior profile priors if enabled and sufficient data exists."""
    if not (cp_config.global_memory_enabled and cp_config.channel_enabled("behavior")):
        return None
    try:
        from lintgate.controlplane.global_behavior_profile import (
            MIN_SAMPLE_SIZE,
            load_global_profile,
        )

        gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
        if gp.session_count >= MIN_SAMPLE_SIZE:
            return {
                "enabled": True,
                "alpha": cp_config.global_memory_alpha,
                "decay_horizon": cp_config.global_memory_decay_horizon,
                "computed_bias_adjustments": gp.computed_bias_adjustments,
            }
    except Exception:
        pass
    return None


# ── Session setup and gate ───────────────────────────────────────────


def setup_session_and_gate(
    cp_config: Any,
    cwd: str,
    tool_name: str,
    event: Any,
    channels: list,
    global_priors: dict | None,
) -> tuple[Any, str | None]:
    """Set up session memory, theory profile, and session gate. Returns (session, advisory)."""
    session = None
    advisory: str | None = None

    if cp_config.session_memory:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import get_or_create_session

            session = get_or_create_session(cwd, cp_config.session_max_age_hours)

    if session is not None and cp_config.channel_enabled("behavior"):
        event.raw_input["behavior_compass"] = session.behavior_compass

    if global_priors is not None:
        event.raw_input["behavior_global_priors"] = global_priors

    # Cache theory profile once per mesh run
    if session is not None and cp_config.inquiry.any_enabled():
        try:
            from lintgate.theory_extractor import extract_theory

            session.theory_profile_cache = extract_theory(cwd).get("theory_profile")
        except Exception:
            session.theory_profile_cache = None

        if session.theory_profile_cache is not None:
            event.raw_input["theory_profile"] = session.theory_profile_cache

    # Advisory gate: warn when editing without sufficient theory context
    if (
        session is not None
        and cp_config.inquiry.session_gate
        and tool_name in ("Write", "Edit", "MultiEdit")
        and not session.behavior_compass.get("_session_ready", False)
    ):
        with contextlib.suppress(Exception):
            from lintgate.context_auditor import check_session_readiness

            readiness = check_session_readiness(cwd, theory_profile=session.theory_profile_cache)
            if not readiness.ready:
                advisory = (
                    f"[Session Advisory] Context not ready for deep supervision. "
                    f"Missing: {', '.join(readiness.missing)}. "
                    f"{readiness.recommendation}"
                )
                channels[:] = [ch for ch in channels if ch.name != "behavior"]
            else:
                session.behavior_compass["_session_ready"] = True

    return session, advisory


# ── Behavior delta application ───────────────────────────────────────


def apply_behavior_delta(
    session: Any,
    cr: Any,
    cp_config: Any,
    input_data: dict,
) -> list[dict[str, Any]]:
    """Apply behavior compass delta and return finding dicts for delivery."""
    findings = [f.to_dict() for f in cr.findings]

    # Apply compass delta (cooldown counters, nudge flags)
    if "behavior_compass_delta" in cr.metrics:
        from lintgate.controlplane.session_memory import (
            load_behavior_compass,
            save_behavior_compass,
        )

        delta = cr.metrics["behavior_compass_delta"]
        existing_telem = session_telemetry_updates_used(session)
        bc = load_behavior_compass(session)
        for key in (
            "last_fired",
            "signal_fire_counts",
            "early_nudge_emitted",
            "pending_nudge_signals",
            "pending_nudge_constraint_check_count",
            "nudge_outcomes",
        ):
            if key in delta:
                setattr(bc, key, delta[key])
        save_behavior_compass(session, bc)
        if existing_telem > 0:
            session.behavior_compass[_SESSION_TELEMETRY_COUNTER_KEY] = existing_telem
        # Merge theory coda dedup state
        if "_theory_recent_codas" in delta:
            existing_codas = session.behavior_compass.get("_theory_recent_codas", {})
            existing_codas.update(delta["_theory_recent_codas"])
            session.behavior_compass["_theory_recent_codas"] = existing_codas

    # Apply global profile delta
    if cp_config.global_memory_enabled and "global_profile_delta" in cr.metrics:
        with contextlib.suppress(Exception):
            from lintgate.controlplane.global_behavior_profile import (
                apply_session_delta,
                load_global_profile,
                save_global_profile,
            )

            gp = load_global_profile(ttl_days=cp_config.global_memory_ttl_days)
            apply_session_delta(
                gp,
                cr.metrics["global_profile_delta"],
                session_id=session.session_id if session else "",
            )
            save_global_profile(gp)

    # Model profile telemetry refinement
    with contextlib.suppress(Exception):
        from lintgate.controlplane.model_profiles import (
            apply_telemetry_update,
            load_profiles,
            save_profiles,
        )

        store = load_profiles()
        active = select_telemetry_profile(store, input_data)
        signal_fires = {}
        event_count = 0
        if session:
            bc_data = session.behavior_compass
            if isinstance(bc_data, dict):
                signal_fires = bc_data.get("signal_fire_counts", {})
                event_count = bc_data.get("event_counter", 0)
        if (
            active is not None
            and signal_fires
            and event_count >= 10
            and can_apply_session_telemetry(session)
        ):
            apply_telemetry_update(active, signal_fires, event_count)
            mark_session_telemetry_applied(session)
            save_profiles(store)

    return findings


# ── Snapshot behavior recording ──────────────────────────────────────


def record_snapshot_behavior(
    snapshot: Any,
    tool_name: str,
    tool_input: Any,
    tool_output: Any,
) -> None:
    """Record tool-level behavioral fields on a snapshot."""
    snapshot.behavior.action_type = tool_name.lower()
    if tool_name != "Bash":
        return

    from lintgate.controlplane.behavior_compass import extract_error_sig, normalize_command_sig

    cmd = (
        tool_input.get("command", "")
        if isinstance(tool_input, dict)
        else (tool_input if isinstance(tool_input, str) else "")
    )
    snapshot.behavior.command_signature = normalize_command_sig(cmd)
    snapshot.behavior.error_signature = extract_error_sig(
        tool_output if isinstance(tool_output, str) else ""
    )
    output_str = tool_output if isinstance(tool_output, str) else str(tool_output)
    exit_match = re.search(
        r"(?:exit[_ ]code|exit[_ ]status|exitstatus)[: =]+(\d+)",
        output_str,
        re.IGNORECASE,
    )
    if exit_match:
        snapshot.behavior.exit_code = int(exit_match.group(1))
    elif "error" in output_str.lower() or "failed" in output_str.lower():
        snapshot.behavior.exit_code = 1
    else:
        snapshot.behavior.exit_code = 0


# ── Constraint proposer ──────────────────────────────────────────────


def run_constraint_proposer(session: Any, mesh_result: Any, cp_config: Any) -> list[dict]:
    """Run constraint proposer on pattern alerts and return proposed constraints."""
    proposed: list[dict] = []
    with contextlib.suppress(Exception):
        from lintgate.controlplane.constraint_proposer import (
            propose_constraints_from_patterns,
            store_proposals_in_session,
        )

        pattern_alerts: list[dict] = []
        for cr in mesh_result.channel_results:
            if cr.channel == "lint":
                pattern_alerts.extend(cr.metrics.get("pattern_alerts", []))
                break

        # Promote recurring behavior findings via session trend history
        for key, counts in session.pattern_trend.items():
            if "|" not in key:
                continue
            linter, kind = key.split("|", 1)
            if linter != "behavior_channel":
                continue
            recent = counts[-5:]
            recent_run_count = sum(1 for c in recent if c > 0)
            if recent_run_count > 0:
                pattern_alerts.append(
                    {
                        "linter": linter,
                        "kind": kind,
                        "alert_reason": "recurring_across_runs",
                        "recent_run_count": recent_run_count,
                    }
                )

        if pattern_alerts:
            proposals = propose_constraints_from_patterns(
                {"alerted_patterns": pattern_alerts},
                session=session,
                threshold=cp_config.constraint_proposal_threshold,
                config=cp_config,
            )
            if proposals:
                store_proposals_in_session(session, proposals)

        proposed = session.proposed_constraints
    return proposed


# ── Run detail persistence ───────────────────────────────────────────


def save_run_details(mesh_result: Any, finding_index: dict) -> None:
    """Persist full run details for controlplane_get_details drill-down."""
    if not finding_index:
        return
    with contextlib.suppress(Exception):
        from lintgate.state import save_controlplane_run

        details: dict = {
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
            "finding_index": finding_index,
            "channels": {},
        }
        for cr in mesh_result.channel_results:
            if cr.status == "skip":
                continue
            details["channels"][cr.channel] = {
                "status": cr.status,
                "severity": cr.severity,
                "duration_ms": round(cr.duration_ms, 1),
                "error": cr.error_message,
                "findings": [f.to_dict() for f in cr.findings],
                "repairs": [
                    {
                        "action_id": r.action_id,
                        "kind": r.kind,
                        "summary": r.summary,
                        "safe": r.safe,
                        "payload": r.payload,
                    }
                    for r in cr.repairs
                ],
                "metrics": cr.metrics,
            }
        run_id = mesh_result.event.event_id if mesh_result.event else ""
        if run_id:
            save_controlplane_run(run_id, details)


# ── Finding index extraction ─────────────────────────────────────────


def extract_finding_indexes(
    session: Any,
) -> tuple[dict | None, dict | None, int]:
    """Extract previous and baseline finding indexes from session snapshots."""
    previous_finding_index: dict | None = None
    baseline_finding_index: dict | None = None
    snapshot_count = 0
    if session is not None:
        with contextlib.suppress(Exception):
            if session.snapshots:
                snapshot_count = len(session.snapshots)
                previous_finding_index = session.snapshots[-1].finding_index
                baseline_finding_index = session.snapshots[0].finding_index
    return previous_finding_index, baseline_finding_index, snapshot_count


# ── Post-process session ─────────────────────────────────────────────


def post_process_session(
    session: Any,
    mesh_result: Any,
    finding_index: dict,
    cp_config: Any,
    input_data: dict,
    tool_name: str,
    tool_input: Any,
    tool_output: str,
) -> tuple[list[dict], list[dict]]:
    """Post-process session after mesh run: record, apply deltas, propose constraints."""
    proposed_constraints: list[dict] = []
    behavior_findings: list[dict] = []
    if session is None:
        return proposed_constraints, behavior_findings

    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import record_mesh_run

        snapshot = record_mesh_run(session, mesh_result, finding_index=finding_index)

        for cr in mesh_result.channel_results:
            if cr.channel == "behavior":
                behavior_findings = apply_behavior_delta(
                    session,
                    cr,
                    cp_config,
                    input_data,
                )
                snapshot.behavior.behavior_alerts = [f.get("kind") for f in behavior_findings]

                # Track resolutions and capture repertoire
                from lintgate.orchestration.repertoire import RepertoireManager

                rep_mgr = RepertoireManager(session.behavior_compass)
                kinds = {f.get("kind") for f in behavior_findings if f.get("kind")}
                rep_mgr.track_findings(
                    kinds,
                    session.behavior_compass.event_counter,
                    len(session.behavior_compass.action_history),
                )

                # Update compliance stats
                from lintgate.orchestration.compliance import ComplianceManager

                comp_mgr = ComplianceManager(session.behavior_compass)
                outcomes = cr.metrics.get("global_profile_delta", {}).get("nudge_outcomes", {})
                if outcomes:
                    comp_mgr.record_outcomes(outcomes)

                # Generate session transfer packet for continuity
                from lintgate.orchestration.continuity import generate_transfer_packet

                packet = generate_transfer_packet(session)
                session.behavior_compass["last_transfer_packet"] = packet.to_json()
                snapshot.behavior.transfer_packet = packet.to_json()
                break

        record_snapshot_behavior(snapshot, tool_name, tool_input, tool_output)

    proposed_constraints = run_constraint_proposer(session, mesh_result, cp_config)

    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import save_session

        save_session(session)
    session.theory_profile_cache = None

    return proposed_constraints, behavior_findings


# ── Telemetry accumulation ───────────────────────────────────────────


def accumulate_session_telemetry(report: dict | None, session: Any) -> None:
    """Merge telemetry counters from report into session memory."""
    telemetry = report.get("_telemetry", {}) if report else {}
    if not telemetry or session is None:
        return
    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import save_session

        existing = session.behavior_compass.get("telemetry_counters", {})
        if not isinstance(existing, dict):
            existing = {}
        for key, value in telemetry.items():
            existing[key] = existing.get(key, 0) + value
        session.behavior_compass["telemetry_counters"] = existing
        save_session(session)


def deliver_behavioral_findings(
    session: Any,
    findings: list[dict[str, Any]],
    cp_config: Any,
    project_root: str,
) -> str | None:
    """Route findings through delivery abstraction and store pending for MCP.

    Returns (advisory_text, pending_findings).
    """
    if not findings:
        return None, []

    from lintgate.orchestration.delivery import deliver_finding
    from lintgate.renderers import build_default_registry
    from lintgate.renderers.host_adapter import resolve_delivery_channels

    registry = build_default_registry()
    hosts = list(registry.detect_runtime_hosts(project_root))
    if not hosts:
        detected = registry.detect_host(project_root)
        if detected:
            hosts = [detected]

    # For now, we take the first detected host's capabilities for routing
    if not hosts:
        return None

    adapter = registry.get_adapter(hosts[0])
    if not adapter:
        return None

    preferred = resolve_delivery_channels(adapter.capabilities)

    delivered_msgs = []
    pending_for_mcp = []

    from lintgate.orchestration.repertoire import RepertoireManager

    rep_mgr = RepertoireManager(session.behavior_compass) if session else None

    for finding in findings:
        # Add resolution hint if available
        if rep_mgr:
            hint = rep_mgr.get_resolution_hint(finding.get("kind", ""))
            if hint:
                finding["hint"] = hint

        # Map finding severity to authority level (for now)
        finding["authority_level"] = finding.get("severity", "nudge").lower()

        payload, chan_type = deliver_finding(finding, preferred)
        if payload:
            if chan_type in ("hook_text", "rule_file"):
                delivered_msgs.append(payload)
            elif chan_type == "mcp_status":
                pending_for_mcp.append(finding)

    # Store pending for MCP micro-refresh surface
    if session:
        session.behavior_compass["pending_behavioral_findings"] = pending_for_mcp

    return "\n\n".join(delivered_msgs) if delivered_msgs else None, pending_for_mcp


# ── Runtime refresh after run ────────────────────────────────────────


def refresh_runtime_after_run(
    cwd: str,
    session: Any,
    cp_config: Any,
    mesh_result: Any,
    tool_name: str,
    tool_input: Any,
    pending_findings: list[dict[str, Any]] | None = None,
) -> None:
    """Refresh runtime state after a controlplane run."""
    from lintgate.hook_runtime_state import (
        refresh_runtime_state_lightweight,
        refresh_runtime_state_with_session,
    )

    if session is not None:
        refresh_runtime_state_with_session(
            cwd,
            session,
            mesh_result=mesh_result,
            tool_name=tool_name,
            tool_input=tool_input,
            trigger="lint_complete",
        )
        with contextlib.suppress(Exception):
            from lintgate.controlplane.session_memory import save_session

            save_session(session)
    else:
        scheduler_dict: dict[str, Any] | None = None
        if cp_config.habit_mode_enabled and not cp_config.session_memory:
            with contextlib.suppress(Exception):
                from lintgate.habit_mode import (
                    load_habit_state_standalone,
                    load_standalone_extras,
                    save_habit_state_standalone,
                )

                extras = load_standalone_extras(cwd)
                raw_scheduler = extras.get("write_scheduler", {})
                if isinstance(raw_scheduler, dict):
                    scheduler_dict = raw_scheduler
                updated = refresh_runtime_state_lightweight(
                    cwd,
                    mesh_result=mesh_result,
                    tool_name=tool_name,
                    tool_input=tool_input,
                    trigger="lint_complete",
                    scheduler_dict=scheduler_dict,
                    pending_findings=pending_findings,
                )
                if isinstance(updated, dict):
                    scheduler_dict = updated
                    habit_state, action_ring = load_habit_state_standalone(cwd)
                    save_habit_state_standalone(
                        cwd,
                        habit_state,
                        action_ring,
                        tracker_dict=extras.get("token_tracker")
                        if isinstance(extras.get("token_tracker"), dict)
                        else None,
                        config_overrides=extras.get("config_overrides")
                        if isinstance(extras.get("config_overrides"), dict)
                        else None,
                        last_snapshot=extras.get("habit_last_snapshot")
                        if isinstance(extras.get("habit_last_snapshot"), dict)
                        else None,
                        scheduler_dict=scheduler_dict,
                    )
        else:
            refresh_runtime_state_lightweight(
                cwd,
                mesh_result=mesh_result,
                tool_name=tool_name,
                tool_input=tool_input,
                trigger="lint_complete",
                pending_findings=pending_findings,
            )
