"""Habit mode tracking for the PostToolUse hook.

Contains both Path A (session-backed) and Path B (lightweight/standalone) habit
mode tracking, plus shared helpers for API calibration and compaction.
"""

from __future__ import annotations

import contextlib
from typing import Any


def check_habit_api_calibration(
    tracker: Any,
    event_counter: int,
    cwd: str,
    overrides: dict,
    cp_config: Any,
) -> None:
    """Run token API calibration if interval has elapsed."""
    from lintgate.state import log_metric
    from lintgate.token_tracker import do_api_calibration, should_api_check

    api_interval = overrides.get("token_api_interval", cp_config.habit_mode_token_api_interval)
    if should_api_check(tracker, event_counter, interval=api_interval):
        with contextlib.suppress(Exception):
            result = do_api_calibration(tracker, event_counter, cwd)
            if result:
                log_metric(
                    {
                        "event": "token_estimate",
                        "project": cwd,
                        "source": "api",
                        **result,
                    }
                )


def try_habit_compaction(
    tracker: Any,
    habit_state: Any,
    overrides: dict,
    cp_config: Any,
    cwd: str,
    event_counter: int,
    *,
    session_memory: dict | None = None,
    compass_dict: dict | None = None,
    last_lint_run: dict | None = None,
) -> tuple[bool, dict | None]:
    """Check compaction trigger and build snapshot if needed.

    Returns (did_compact, snapshot_or_None).
    """
    from lintgate.habit_mode import build_compaction_snapshot
    from lintgate.state import log_metric
    from lintgate.token_tracker import (
        get_usage_summary,
        reset_post_compaction,
        should_compact,
    )

    compact_threshold = float(
        overrides.get("compact_threshold", cp_config.habit_mode_compact_threshold)
    )
    if not should_compact(tracker, habit_state.active, threshold=compact_threshold):
        return False, None

    snapshot = None
    with contextlib.suppress(Exception):
        token_summary = get_usage_summary(tracker)
        snapshot = build_compaction_snapshot(
            habit_state,
            cwd,
            session_memory=session_memory,
            compass=compass_dict,
            last_lint_run=last_lint_run,
            token_estimate=token_summary,
        )
        habit_state.compaction_count += 1
        habit_state.last_compaction_event = event_counter
        estimated_before = tracker.estimated_tokens_used
        calls_compacted = tracker.tool_calls_since_compact
        sections_included = sum(1 for v in snapshot.values() if v is not None)
        reset_post_compaction(tracker)
        log_metric(
            {
                "event": "habit_compact",
                "project": cwd,
                "compaction_number": habit_state.compaction_count,
                "habit_score": habit_state.habit_score,
                "estimated_tokens_before": estimated_before,
                "tool_calls_compacted": calls_compacted,
                "sections_included": sections_included,
                "trigger": "auto",
            }
        )
    return snapshot is not None, snapshot


def record_behavior_event(
    cp_config: Any,
    cwd: str,
    tool_name: str,
    tool_input: Any,
    tool_output: Any,
) -> None:
    """Record tool event in behavior compass (all events, including read-only).

    Path A: When session_memory is enabled, piggybacks habit mode tracking
    on the existing compass/session flow for richer signals.
    """
    if not (cp_config.channel_enabled("behavior") and cp_config.session_memory):
        return
    with contextlib.suppress(Exception):
        from lintgate.controlplane.behavior_compass import record_tool_event
        from lintgate.controlplane.session_memory import (
            get_or_create_session,
            load_behavior_compass,
            save_behavior_compass,
            save_session,
        )
        from lintgate.hook_runtime_state import refresh_runtime_state_with_session

        session = get_or_create_session(cwd, cp_config.session_max_age_hours)
        compass = load_behavior_compass(session)
        record_tool_event(compass, tool_name, tool_input, tool_output)
        save_behavior_compass(session, compass)

        # Path A: Habit mode tracking piggybacking on compass/session
        if cp_config.habit_mode_enabled:
            _update_habit_mode_path_a(
                cp_config,
                session,
                compass,
                cwd,
                tool_name,
                tool_input,
                tool_output,
            )
        else:
            refresh_runtime_state_with_session(
                cwd,
                session,
                compass=compass,
                tool_name=tool_name,
                tool_input=tool_input,
                trigger="tool_call",
            )

        save_session(session)


def _log_feature_telemetry(behavior_compass: dict, cwd: str, log_fn: Any) -> None:
    """Emit one-shot feature-usage telemetry per session."""
    for key, feature in [
        ("_feature_habit_mode_logged", "habit_mode"),
        ("_feature_token_tracking_logged", "token_tracking"),
    ]:
        if not behavior_compass.get(key, False):
            with contextlib.suppress(Exception):
                log_fn(feature, cwd, {"source": "hook_posttooluse"})
            behavior_compass[key] = True


def _detect_test_results(
    tool_name: str, tool_output: Any, compass: Any, habit_state: Any, detect_fn: Any
) -> None:
    """Detect test results from Bash tool output."""
    if tool_name != "Bash":
        return
    cmd_sig = ""
    if compass.action_history:
        cmd_sig = compass.action_history[-1].get("sig", "")
    out_str = tool_output if isinstance(tool_output, str) else ""
    detect_fn(habit_state, out_str, cmd_sig)


def _apply_context_window_override(tracker: Any, overrides: dict) -> None:
    """Apply context_window_size override from config."""
    context_window_size = overrides.get("context_window_size")
    if context_window_size is not None:
        with contextlib.suppress(Exception):
            tracker.context_window_size = int(context_window_size)


def _run_auto_detect(
    habit_state: Any, compass: Any, overrides: dict, cp_config: Any, *, auto_detect_enabled: bool
) -> Any:
    """Run habit mode auto-detection or track events if declaration-driven."""
    if auto_detect_enabled:
        from lintgate.habit_mode import update_mode

        return update_mode(
            habit_state,
            compass.event_counter,
            enter_score=overrides.get("enter_score", cp_config.habit_mode_enter_score),
            exit_score=overrides.get("exit_score", cp_config.habit_mode_exit_score),
            sustain_calls=overrides.get("sustain_calls", cp_config.habit_mode_sustain_calls),
        )
    if habit_state.active:
        habit_state.total_events_in_habit += 1
    return None


def _update_habit_mode_path_a(
    cp_config: Any,
    session: Any,
    compass: Any,
    cwd: str,
    tool_name: str,
    tool_input: Any,
    tool_output: Any,
) -> None:
    """Path A: Habit mode tracking with full session/compass context.

    Called from record_behavior_event when session_memory is enabled.
    Piggybacks on existing compass action_history for richer signals.
    """
    with contextlib.suppress(Exception):
        from lintgate.habit_mode import (
            detect_test_result,
            load_habit_state,
            save_habit_state,
            track_active_files,
            update_signals,
        )
        from lintgate.hook_runtime_state import refresh_runtime_state_with_session
        from lintgate.state import load_last_run, log_feature_usage, log_metric
        from lintgate.token_tracker import (
            estimate_tool_tokens,
            load_tracker_state,
            save_tracker_state,
        )

        habit_state = load_habit_state(session.behavior_compass)
        tracker = load_tracker_state(session.behavior_compass)

        _log_feature_telemetry(session.behavior_compass, cwd, log_feature_usage)

        update_signals(habit_state, compass.action_history)
        track_active_files(habit_state, tool_name, tool_input)
        estimate_tool_tokens(tracker, tool_name, tool_input, tool_output)

        _detect_test_results(tool_name, tool_output, compass, habit_state, detect_test_result)

        overrides = session.behavior_compass.get("habit_config_overrides", {})
        if not isinstance(overrides, dict):
            overrides = {}

        _apply_context_window_override(tracker, overrides)

        transition = _run_auto_detect(
            habit_state,
            compass,
            overrides,
            cp_config,
            auto_detect_enabled=bool(
                overrides.get("auto_detect", cp_config.habit_mode_auto_detect)
            ),
        )

        check_habit_api_calibration(tracker, compass.event_counter, cwd, overrides, cp_config)

        if transition:
            with contextlib.suppress(Exception):
                log_metric(
                    {
                        "event": "habit_mode_transition",
                        "project": cwd,
                        "transition": transition,
                        "habit_score": habit_state.habit_score,
                        "trigger": "auto_detect",
                        "event_counter": compass.event_counter,
                    }
                )

        # Auto-compaction trigger in active habit mode.
        did_compact, snapshot = try_habit_compaction(
            tracker,
            habit_state,
            overrides,
            cp_config,
            cwd,
            compass.event_counter,
            session_memory=session.to_dict(),
            compass_dict=compass.to_dict(),
            last_lint_run=load_last_run(cwd),
        )
        if did_compact and snapshot:
            session.behavior_compass["habit_last_snapshot"] = snapshot

        save_habit_state(session.behavior_compass, habit_state)
        save_tracker_state(session.behavior_compass, tracker)

        # Write-through to standalone file so MCP tools (separate process)
        # can read habit state even when session_memory is enabled.
        with contextlib.suppress(Exception):
            from lintgate.habit_mode import save_habit_state_standalone

            action_ring = list((compass.action_history or [])[-20:])
            save_habit_state_standalone(
                cwd,
                habit_state,
                action_ring,
                tracker_dict=tracker.to_dict(),
                config_overrides=overrides,
                last_snapshot=session.behavior_compass.get("habit_last_snapshot"),
            )

        refresh_runtime_state_with_session(
            cwd,
            session,
            compass=compass,
            habit_state=habit_state,
            tracker=tracker,
            tool_name=tool_name,
            tool_input=tool_input,
            trigger="compaction" if did_compact else "tool_call",
            transition=transition,
        )


# ── Path B: Lightweight habit tracking (no session_memory) ───────────


def _load_standalone_state(cwd: str):
    """Load standalone habit state, extras, tracker, overrides, scheduler, snapshot."""
    from lintgate.habit_mode import (
        load_habit_state_standalone,
        load_standalone_extras,
    )
    from lintgate.token_tracker import TokenTrackerState

    habit_state, action_ring = load_habit_state_standalone(cwd)
    extras = load_standalone_extras(cwd)
    raw_tracker = extras.get("token_tracker", {})
    if not isinstance(raw_tracker, dict):
        raw_tracker = {}
    tracker = TokenTrackerState.from_dict(raw_tracker)
    standalone_overrides = extras.get("config_overrides", {})
    if not isinstance(standalone_overrides, dict):
        standalone_overrides = {}
    standalone_scheduler = extras.get("write_scheduler", {})
    if not isinstance(standalone_scheduler, dict):
        standalone_scheduler = {}
    last_snapshot = extras.get("habit_last_snapshot")
    if not isinstance(last_snapshot, dict):
        last_snapshot = None
    signal_fires = extras.get("signal_fire_counts", {})
    if not isinstance(signal_fires, dict):
        signal_fires = {}

    return (
        habit_state,
        action_ring,
        extras,
        tracker,
        standalone_overrides,
        standalone_scheduler,
        last_snapshot,
        signal_fires,
    )


def _build_action_entry(tool_name: str, tool_input: Any) -> tuple[str, str]:
    """Extract sig and command_text from tool input for the action ring."""
    sig = ""
    command_text = ""
    if isinstance(tool_input, dict):
        sig = str(tool_input.get("file_path") or tool_input.get("path") or "")
        command_text = str(tool_input.get("command", ""))
    elif isinstance(tool_input, str):
        command_text = tool_input
    if tool_name == "Bash":
        sig = command_text
    return sig, command_text


def _update_action_ring(
    action_ring: list,
    tool_name: str,
    tool_input: Any,
) -> tuple[list, str]:
    """Append to and trim the action ring buffer. Returns (ring, command_text)."""
    import time

    from lintgate.habit_mode import MAX_ACTION_RING, quick_intent

    sig, command_text = _build_action_entry(tool_name, tool_input)
    action_ring.append(
        {
            "tool": tool_name,
            "ts": time.time(),
            "intent": quick_intent(tool_name),
            "sig": sig,
        }
    )
    if len(action_ring) > MAX_ACTION_RING:
        action_ring = action_ring[-MAX_ACTION_RING:]
    return action_ring, command_text


def _detect_bash_signals(
    tool_name: str,
    tool_output: Any,
    command_text: str,
    habit_state: Any,
    signal_fires: dict,
) -> None:
    """Handle Bash-specific test result detection and signal fire tracking."""
    import re

    from lintgate.habit_mode import detect_test_result

    if tool_name != "Bash":
        return
    out_str = tool_output if isinstance(tool_output, str) else ""
    if command_text and re.search(r"\b(pytest|test)\b", command_text.lower()):
        detect_test_result(habit_state, out_str, command_text)
    # Track command failures as signal fires
    if out_str and ("error" in out_str.lower() or "traceback" in out_str.lower()):
        signal_fires["command_failure"] = signal_fires.get("command_failure", 0) + 1


def _apply_path_b_telemetry(event_counter: int, signal_fires: dict) -> dict:
    """Apply signal fires to model profiles every 50 events.

    Returns the (possibly cleared) signal_fires dict.
    """
    if not (event_counter > 0 and event_counter % 50 == 0 and signal_fires):
        return signal_fires
    with contextlib.suppress(Exception):
        from lintgate.controlplane.model_profiles import (
            apply_telemetry_update,
            load_profiles,
            save_profiles,
        )

        store = load_profiles()
        for _profile in store.profiles.values():
            if _profile.confidence > 0 and sum(signal_fires.values()) > 0:
                apply_telemetry_update(_profile, signal_fires, event_counter)
                break
        save_profiles(store)
        return {}  # Reset after application
    return signal_fires


def _run_mode_transition(
    habit_state: Any,
    event_counter: int,
    overrides: dict,
    cp_config: Any,
    cwd: str,
) -> str | None:
    """Run auto-detect mode transition and log if triggered."""
    from lintgate.habit_mode import update_mode
    from lintgate.state import log_metric

    auto_detect_enabled = bool(overrides.get("auto_detect", cp_config.habit_mode_auto_detect))
    transition = None
    if auto_detect_enabled:
        transition = update_mode(
            habit_state,
            event_counter,
            enter_score=overrides.get("enter_score", cp_config.habit_mode_enter_score),
            exit_score=overrides.get("exit_score", cp_config.habit_mode_exit_score),
            sustain_calls=overrides.get("sustain_calls", cp_config.habit_mode_sustain_calls),
        )
    elif habit_state.active:
        habit_state.total_events_in_habit += 1

    if transition:
        with contextlib.suppress(Exception):
            log_metric(
                {
                    "event": "habit_mode_transition",
                    "project": cwd,
                    "transition": transition,
                    "habit_score": habit_state.habit_score,
                    "trigger": "auto_detect_lightweight",
                    "event_counter": event_counter,
                }
            )
    return transition


def record_habit_event_lightweight(
    cp_config: Any,
    cwd: str,
    tool_name: str,
    tool_input: Any,
    tool_output: Any,
) -> None:
    """Path B: Lightweight habit mode tracking when session_memory is off.

    Uses standalone file-backed state with a minimal action ring buffer.
    Called from _run_controlplane when habit_mode is enabled but session_memory is not.
    """
    if not cp_config.habit_mode_enabled:
        return
    # Skip if session_memory is on -- Path A handles it
    if cp_config.session_memory and cp_config.channel_enabled("behavior"):
        return

    with contextlib.suppress(Exception):
        from lintgate.habit_mode import (
            save_habit_state_standalone,
            track_active_files,
            update_signals,
        )
        from lintgate.hook_runtime_state import refresh_runtime_state_lightweight
        from lintgate.token_tracker import estimate_tool_tokens

        (
            habit_state,
            action_ring,
            extras,
            tracker,
            standalone_overrides,
            standalone_scheduler,
            last_snapshot,
            signal_fires,
        ) = _load_standalone_state(cwd)

        _apply_context_window_override(tracker, standalone_overrides)

        # Maintain minimal action ring buffer
        action_ring, command_text = _update_action_ring(action_ring, tool_name, tool_input)

        update_signals(habit_state, action_ring)
        track_active_files(habit_state, tool_name, tool_input)
        estimate_tool_tokens(tracker, tool_name, tool_input, tool_output)

        # Bash-specific signal detection
        _detect_bash_signals(tool_name, tool_output, command_text, habit_state, signal_fires)

        event_counter = tracker.tool_call_count

        # Path B telemetry: apply signal fires every 50 events
        signal_fires = _apply_path_b_telemetry(event_counter, signal_fires)

        transition = _run_mode_transition(
            habit_state, event_counter, standalone_overrides, cp_config, cwd
        )

        check_habit_api_calibration(tracker, event_counter, cwd, standalone_overrides, cp_config)

        did_compact, compact_snapshot = try_habit_compaction(
            tracker,
            habit_state,
            standalone_overrides,
            cp_config,
            cwd,
            event_counter,
        )
        if did_compact and compact_snapshot:
            last_snapshot = compact_snapshot

        updated_scheduler = refresh_runtime_state_lightweight(
            cwd,
            habit_state=habit_state,
            tracker=tracker,
            tool_name=tool_name,
            tool_input=tool_input,
            trigger="compaction" if did_compact else "tool_call",
            transition=transition,
            scheduler_dict=standalone_scheduler,
        )
        if isinstance(updated_scheduler, dict):
            standalone_scheduler = updated_scheduler

        save_habit_state_standalone(
            cwd,
            habit_state,
            action_ring,
            tracker_dict=tracker.to_dict(),
            config_overrides=standalone_overrides,
            last_snapshot=last_snapshot,
            scheduler_dict=standalone_scheduler,
            signal_fire_counts=signal_fires if signal_fires else None,
        )
