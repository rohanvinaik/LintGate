"""Habit Mode tools — context window management for sustained execution.

4 MCP tools:
- declare_mode: Agent self-declares "habit" or "standard" mode
- habit_status: Read-only status check with signals and token economics
- habit_compact: Trigger compaction now, returns structured snapshot
- habit_configure: Runtime threshold adjustment (session-scoped)
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

# ── Module-level helpers ─────────────────────────────────────────────


def _load_state(project_root: str) -> tuple:
    """Load habit state + tracker from session or standalone.

    Returns (HabitModeState, TokenTrackerState, event_counter, save_fn).
    save_fn accepts (state, tracker) to persist.
    """
    from lintgate.habit_mode import HabitModeState
    from lintgate.token_tracker import TokenTrackerState

    # Try session-backed first
    try:
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            save_session,
        )
        from lintgate.habit_mode import load_habit_state, save_habit_state
        from lintgate.token_tracker import load_tracker_state, save_tracker_state

        session = get_or_create_session(project_root)
        state = load_habit_state(session.behavior_compass)
        tracker = load_tracker_state(session.behavior_compass)
        event_counter = session.behavior_compass.get("event_counter", 0)

        # Guard: if session has no habit data, don't return empty state —
        # the hook writes to a different process's session, so try standalone.
        if abs(state.habit_score) < 1e-12 and not session.behavior_compass.get("habit_mode"):
            raise ValueError("No habit data in session")  # Falls through to Path B

        def save_fn(s: Any, t: Any) -> None:
            save_habit_state(session.behavior_compass, s)
            save_tracker_state(session.behavior_compass, t)
            save_session(session)

        return state, tracker, event_counter, save_fn
    except Exception:
        pass

    # Fall back to standalone
    try:
        from lintgate.habit_mode import (
            load_habit_state_standalone,
            load_standalone_extras,
            save_habit_state_standalone,
        )

        state, action_ring = load_habit_state_standalone(project_root)
        extras = load_standalone_extras(project_root)
        raw_tracker = extras.get("token_tracker", {})
        if not isinstance(raw_tracker, dict):
            raw_tracker = {}
        tracker = TokenTrackerState.from_dict(raw_tracker)
        config_overrides = extras.get("config_overrides", {})
        if not isinstance(config_overrides, dict):
            config_overrides = {}
        last_snapshot = extras.get("habit_last_snapshot")
        if not isinstance(last_snapshot, dict):
            last_snapshot = None

        def save_fn(s: Any, t: Any) -> None:
            save_habit_state_standalone(
                project_root,
                s,
                action_ring,
                tracker_dict=t.to_dict(),
                config_overrides=config_overrides,
                last_snapshot=last_snapshot,
            )

        return state, tracker, tracker.tool_call_count, save_fn
    except Exception:
        pass

    # Last resort: in-memory only
    def noop_save(s: Any, t: Any) -> None:
        pass

    return HabitModeState(), TokenTrackerState(), 0, noop_save


def _load_session_context(project_root: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load session memory and compass dicts for compaction snapshot."""
    from lintgate.controlplane.session_memory import get_or_create_session

    session = get_or_create_session(project_root)
    session_dict = session.to_dict()
    compass_dict = session.behavior_compass
    return session_dict, compass_dict


# ── Implementation functions ─────────────────────────────────────────


def _impl_declare_mode(project_root: str, mode: str) -> str:
    """Implementation for declare_mode tool."""
    from lintgate.state import log_metric

    if mode not in ("habit", "standard"):
        return json.dumps({"error": "mode must be 'habit' or 'standard'"})

    state, tracker, event_counter, save_fn = _load_state(project_root)

    from lintgate.habit_mode import declare_mode as _declare_mode_fn

    transition = _declare_mode_fn(state, mode, event_counter)

    save_fn(state, tracker)

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("habit_mode", project_root, {"tool": "declare_mode"})

    # Log transition metric
    if transition:
        with contextlib.suppress(Exception):
            log_metric(
                {
                    "event": "habit_mode_transition",
                    "project": project_root,
                    "transition": transition,
                    "habit_score": state.habit_score,
                    "trigger": "declaration",
                    "event_counter": event_counter,
                }
            )

    return json.dumps(
        {
            "status": "ok",
            "mode": mode,
            "habit_score": round(state.habit_score, 3),
            "active": state.active,
            "message": f"Habit mode {'activated' if state.active else 'deactivated'}.",
        }
    )


def _impl_habit_status(project_root: str) -> str:
    """Implementation for habit_status tool."""
    state, tracker, event_counter, _save_fn = _load_state(project_root)

    from lintgate.token_tracker import get_usage_summary

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("habit_mode", project_root, {"tool": "habit_status"})
        log_feature_usage("token_tracking", project_root, {"tool": "habit_status"})

    result = {
        "active": state.active,
        "habit_score": round(state.habit_score, 3),
        "declared": state.declared,
        "signals": state.signals.to_dict(),
        "active_files": state.active_files[:5],
        "last_test_status": state.last_test_status,
        "compaction_count": state.compaction_count,
        "events_in_habit": state.total_events_in_habit,
        "token_economics": get_usage_summary(tracker),
    }

    # Add prescriptive spec coverage if specs exist
    with contextlib.suppress(Exception):
        from lintgate.specification.prescriptive.spec import load_all_specs

        all_specs = load_all_specs(project_root)
        if all_specs:
            result["prescriptive_specs"] = {
                "total": len(all_specs),
                "problem_classes": {
                    pc: sum(1 for s in all_specs.values() if s.problem_class == pc)
                    for pc in ("pure", "stateful", "distributed")
                },
            }

    return json.dumps(result, indent=2)


def _impl_habit_compact(project_root: str) -> str:
    """Implementation for habit_compact tool."""
    from lintgate.habit_mode import build_compaction_snapshot
    from lintgate.state import log_metric
    from lintgate.token_tracker import get_usage_summary, reset_post_compaction

    state, tracker, event_counter, save_fn = _load_state(project_root)

    # Gather all available context for the snapshot
    session_memory = None
    compass = None
    last_lint_run = None
    theory_pack = None
    issue_memory = None

    with contextlib.suppress(Exception):
        session_memory, compass = _load_session_context(project_root)

    with contextlib.suppress(Exception):
        from lintgate.state import load_last_run

        last_lint_data = load_last_run(project_root)
        if last_lint_data:
            last_lint_run = last_lint_data

    with contextlib.suppress(Exception):
        from lintgate.theory_extractor import build_theory_pack

        theory_pack = build_theory_pack(project_root)

    # Build the snapshot
    snapshot = build_compaction_snapshot(
        state,
        project_root,
        session_memory=session_memory,
        compass=compass,
        last_lint_run=last_lint_run,
        theory_pack=theory_pack,
        issue_memory=issue_memory,
        token_estimate=get_usage_summary(tracker),
    )

    # Inject prescriptive spec state into snapshot
    with contextlib.suppress(Exception):
        from lintgate.specification.prescriptive.spec import load_all_specs

        all_specs = load_all_specs(project_root)
        if all_specs:
            total_sigma = sum(s.prescriptive_sigma for s in all_specs.values())
            n = len(all_specs)
            snapshot["prescriptive_specs"] = {
                "total_specs": n,
                "problem_classes": {
                    pc: sum(1 for s in all_specs.values() if s.problem_class == pc)
                    for pc in ("pure", "stateful", "distributed")
                },
                "mean_prescriptive_sigma": round(total_sigma / n, 2) if n else 0.0,
                "targets": [s.target_key for s in list(all_specs.values())[:10]],
            }

    # Update state
    estimated_before = tracker.estimated_tokens_used
    calls_compacted = tracker.tool_calls_since_compact
    state.compaction_count += 1
    state.last_compaction_event = event_counter
    reset_post_compaction(tracker)

    save_fn(state, tracker)

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("habit_mode", project_root, {"tool": "habit_compact"})
        log_feature_usage("token_tracking", project_root, {"tool": "habit_compact"})

    # Log metric
    with contextlib.suppress(Exception):
        log_metric(
            {
                "event": "habit_compact",
                "project": project_root,
                "compaction_number": state.compaction_count,
                "habit_score": state.habit_score,
                "estimated_tokens_before": estimated_before,
                "tool_calls_compacted": calls_compacted,
            }
        )

    return json.dumps(snapshot, indent=2)


def _impl_habit_configure(
    project_root: str,
    compact_threshold: float | None,
    enter_score: float | None,
    exit_score: float | None,
    sustain_calls: int | None,
    token_api_interval: int | None,
    context_window_size: int | None,
) -> str:
    """Implementation for habit_configure tool."""
    overrides: dict[str, int | float] = {}

    if compact_threshold is not None:
        overrides["compact_threshold"] = max(0.1, min(0.9, compact_threshold))
    if enter_score is not None:
        overrides["enter_score"] = max(0.3, min(0.95, enter_score))
    if exit_score is not None:
        overrides["exit_score"] = max(0.1, min(0.8, exit_score))
    if sustain_calls is not None:
        overrides["sustain_calls"] = max(1, min(20, sustain_calls))
    if token_api_interval is not None:
        overrides["token_api_interval"] = max(5, min(100, token_api_interval))
    if context_window_size is not None:
        overrides["context_window_size"] = max(10000, min(500000, context_window_size))

    # Try to store in session memory, else use standalone
    stored = False
    try:
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            save_session,
        )

        session = get_or_create_session(project_root)
        session.behavior_compass.setdefault("habit_config_overrides", {}).update(overrides)
        save_session(session)
        stored = True
    except Exception:
        pass

    if not stored:
        # Store in standalone file alongside habit state
        try:
            from lintgate.habit_mode import (
                load_habit_state_standalone,
                load_standalone_extras,
                save_habit_state_standalone,
            )

            state, action_ring = load_habit_state_standalone(project_root)
            extras = load_standalone_extras(project_root)
            existing_overrides = extras.get("config_overrides", {})
            if not isinstance(existing_overrides, dict):
                existing_overrides = {}
            merged_overrides = {**existing_overrides, **overrides}
            tracker_dict = extras.get("token_tracker")
            if not isinstance(tracker_dict, dict):
                tracker_dict = None
            last_snapshot = extras.get("habit_last_snapshot")
            if not isinstance(last_snapshot, dict):
                last_snapshot = None
            save_habit_state_standalone(
                project_root,
                state,
                action_ring,
                tracker_dict=tracker_dict,
                config_overrides=merged_overrides,
                last_snapshot=last_snapshot,
            )
        except Exception:
            pass

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("habit_mode", project_root, {"tool": "habit_configure"})

    return json.dumps(
        {
            "status": "ok",
            "overrides_applied": overrides,
            "message": f"Applied {len(overrides)} configuration override(s) for this session.",
        }
    )


def _impl_habit_bootstrap(project_root: str) -> str:
    """Implementation for habit_bootstrap tool."""
    try:
        from mneme.ingest.session_parser import iter_sessions  # type: ignore[import-not-found]
    except ImportError:
        return json.dumps({"error": "mneme package not available for session parsing"})

    from lintgate._habit_bootstrap import HabitBootstrapper

    # Collect sessions for this project
    sessions = []
    for session in iter_sessions():
        sp = session.project_path or session.cwd or ""
        if sp == project_root or sp.rstrip("/") == project_root.rstrip("/"):
            sessions.append(session)

    if not sessions:
        return json.dumps({"error": f"No sessions found for {project_root}"})

    bootstrapper = HabitBootstrapper()
    summary = bootstrapper.bootstrap_project(sessions)

    with contextlib.suppress(Exception):
        from lintgate.state import log_feature_usage

        log_feature_usage("habit_mode", project_root, {"tool": "habit_bootstrap"})

    return json.dumps(summary, indent=2)


# ── Registration ─────────────────────────────────────────────────────


from mcp_tools._disk_helpers import tool_response

def register(mcp, helpers):
    """Register habit mode tools on the shared MCP instance."""

    @mcp.tool()
    def declare_mode(path: str, mode: str) -> str:
        """Agent self-declares "habit" or "standard" mode.

        Primary trigger for habit mode (immediate, no sustain wait).
        Use "habit" when entering sustained refactoring/execution work.
        Use "standard" to exit and resume normal operation.

        Args:
            path: Project root path.
            mode: "habit" or "standard".
        """
        project_root = helpers["_validate_project_root"](path)
        return _impl_declare_mode(project_root, mode)

    @mcp.tool()
    def habit_status(path: str) -> str:
        """Read-only habit mode status check.

        Returns: active state, habit score, signals, active files,
        test status, compaction count, and token economics.
        """
        import json as _json
        project_root = helpers["_validate_project_root"](path)
        result_json = _impl_habit_status(project_root)
        result = _json.loads(result_json)
        score = result.get("habit_score", 0)
        active = "active" if result.get("active") else "inactive"
        compactions = result.get("compaction_count", 0)
        summary = f"Habit mode: {active}, score={score}, compactions={compactions}."
        return tool_response(result, "habit_status", project_root, summary)

    @mcp.tool()
    def habit_compact(path: str) -> str:
        """Trigger compaction NOW. Returns the structured Habit State Snapshot.

        Loads all available context (session, compass, last lint run, theory pack,
        issue memory, token state) and builds a compaction snapshot optimized for
        post-compact context injection.

        Use this when approaching context window limits during sustained work.
        """
        import json as _json
        project_root = helpers["_validate_project_root"](path)
        result_json = _impl_habit_compact(project_root)
        result = _json.loads(result_json)
        comp_num = result.get("compaction_number", 0)
        score = result.get("habit_score", 0)
        summary = f"Compaction #{comp_num} complete. Habit score: {score}."
        return tool_response(result, "habit_compact", project_root, summary)

    @mcp.tool()
    def habit_configure(
        path: str,
        compact_threshold: float | None = None,
        enter_score: float | None = None,
        exit_score: float | None = None,
        sustain_calls: int | None = None,
        token_api_interval: int | None = None,
        context_window_size: int | None = None,
    ) -> str:
        """Runtime threshold adjustment (session-scoped).

        Adjusts habit mode thresholds for the current session.
        Values are clamped to safe ranges.

        Args:
            path: Project root path.
            compact_threshold: Compact at this % of context window (0.1-0.9).
            enter_score: habitScore threshold to enter (0.3-0.95).
            exit_score: habitScore threshold to exit (0.1-0.8).
            sustain_calls: Must sustain enter_score for N calls (1-20).
            token_api_interval: API calibration every N tool calls (5-100).
            context_window_size: Context window size in tokens (10000-500000).
        """
        project_root = helpers["_validate_project_root"](path)
        return _impl_habit_configure(
            project_root,
            compact_threshold,
            enter_score,
            exit_score,
            sustain_calls,
            token_api_interval,
            context_window_size,
        )

    @mcp.tool()
    def habit_bootstrap(path: str) -> str:
        """Bootstrap habit state from historical Claude Code session data.

        Parses session JSONL files for this project and seeds habit state
        with action ring, error memory, and token calibration.

        Args:
            path: Project root path.
        """
        import json as _json
        project_root = helpers["_validate_project_root"](path)
        result_json = _impl_habit_bootstrap(project_root)
        result = _json.loads(result_json)
        if "error" in result:
            return result_json
        sessions = result.get("sessions_parsed", 0)
        summary = f"Habit bootstrap: {sessions} sessions parsed."
        return tool_response(result, "habit_bootstrap", project_root, summary)

    return {
        "declare_mode": declare_mode,
        "habit_status": habit_status,
        "habit_compact": habit_compact,
        "habit_configure": habit_configure,
    }
