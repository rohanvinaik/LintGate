"""ControlPlane post-processing, persistence, and telemetry operations.

Split from controlplane.py — contains constraint proposer, run detail
persistence, finding index extraction, post-process session, telemetry
accumulation, and runtime refresh.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

# ── Post-processing + persistence ─────────────────────────────────


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


def save_run_details(
    mesh_result: Any,
    finding_index: dict,
    compliance_outcome: str | None = None,
) -> None:
    """Persist full run details for controlplane_get_details drill-down."""
    if not finding_index:
        return
    with contextlib.suppress(Exception):
        from lintgate.state import save_controlplane_run

        details: dict = {
            "compliance_outcome": compliance_outcome,
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
) -> tuple[dict | None, dict | None, int, str | None, dict | None]:
    """Extract previous and baseline finding indexes, and last disposition/nudge."""
    previous_finding_index: dict | None = None
    baseline_finding_index: dict | None = None
    snapshot_count: int = 0
    last_disposition: str | None = None
    last_nudge: dict | None = None
    if session is not None:
        with contextlib.suppress(Exception):
            if session.snapshots:
                snapshot_count = len(session.snapshots)
                last_snap = session.snapshots[-1]
                previous_finding_index = last_snap.finding_index
                last_disposition = last_snap.disposition
                last_nudge = last_snap.last_nudge
                baseline_finding_index = session.snapshots[0].finding_index
    return (
        previous_finding_index,
        baseline_finding_index,
        snapshot_count,
        last_disposition,
        last_nudge,
    )


# ── Post-process session ─────────────────────────────────────────────


@dataclass
class PostProcessContext:
    """Bundled context for post-processing a ControlPlane mesh run."""

    session: Any
    mesh_result: Any
    finding_index: dict
    cp_config: Any
    input_data: dict
    tool_name: str
    tool_input: Any
    tool_output: str
    disposition: str | None = None
    last_nudge: dict | None = None
    compliance_outcome: str | None = None


def _record_and_apply_deltas(ctx: PostProcessContext) -> None:
    """Record mesh run snapshot, apply behavior deltas, and record snapshot behavior."""
    from lintgate.controlplane.session_memory import record_mesh_run

    from .controlplane import apply_behavior_delta, record_snapshot_behavior

    snapshot = record_mesh_run(
        ctx.session,
        ctx.mesh_result,
        finding_index=ctx.finding_index,
        disposition=ctx.disposition,
        last_nudge=ctx.last_nudge,
        compliance_outcome=ctx.compliance_outcome,
    )

    for cr in ctx.mesh_result.channel_results:
        if cr.channel == "behavior":
            snapshot.behavior.behavior_alerts = apply_behavior_delta(
                ctx.session,
                cr,
                ctx.cp_config,
                ctx.input_data,
            )
            break

    record_snapshot_behavior(snapshot, ctx.tool_name, ctx.tool_input, ctx.tool_output)


def post_process_session(ctx: PostProcessContext) -> list[dict]:
    """Post-process session after mesh run: record, apply deltas, propose constraints."""
    if ctx.session is None:
        return []

    with contextlib.suppress(Exception):
        _record_and_apply_deltas(ctx)

    proposed_constraints = run_constraint_proposer(ctx.session, ctx.mesh_result, ctx.cp_config)

    with contextlib.suppress(Exception):
        from lintgate.controlplane.session_memory import save_session

        save_session(ctx.session)
    ctx.session.theory_profile_cache = None

    return proposed_constraints


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


# ── Runtime refresh after run ────────────────────────────────────────


def refresh_runtime_after_run(
    cwd: str,
    session: Any,
    cp_config: Any,
    mesh_result: Any,
    tool_name: str,
    tool_input: Any,
) -> None:
    """Refresh runtime state after a controlplane run."""
    from lintgate.hooks.runtime_state import (
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
            )
