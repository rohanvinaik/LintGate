"""Compass-aware Claude Code hooks.

Each module follows the stdin JSON → stdout JSON protocol.
All hooks are advisory-only by default (never block).

Submodules:
    arbitration     – Message arbitration (verbosity gating + disposition injection)
    controlplane    – ControlPlane session management helpers
    habit           – Habit mode tracking (Path A + Path B)
    posttooluse     – PostToolUse hook entry point
    pre_compact     – PreCompact hook
    pre_tool        – PreToolUse quality gate
    pretooluse      – PreToolUse system mutation guard
    runtime_state   – Runtime state persistence + dynamic rule files
    session_end     – SessionEnd hook
    session_start   – SessionStart hook
    stop_gate       – Stop gate hook
    user_prompt     – UserPromptSubmit hook
"""

from lintgate.hooks.arbitration import (
    arbitrate_output,
    build_pulse_delta,
    extract_habit_signals,
    inject_dispositions,
    resolve_verbosity,
    should_emit,
    should_force_emit,
)
from lintgate.hooks.controlplane import (
    accumulate_session_telemetry,
    apply_behavior_delta,
    can_apply_session_telemetry,
    extract_finding_indexes,
    load_global_priors,
    mark_session_telemetry_applied,
    post_process_session,
    record_snapshot_behavior,
    refresh_runtime_after_run,
    resolve_event_model_key,
    run_constraint_proposer,
    save_run_details,
    select_telemetry_profile,
    session_telemetry_updates_used,
    setup_session_and_gate,
)
from lintgate.hooks.habit import (
    record_behavior_event,
    record_habit_event_lightweight,
)
from lintgate.hooks.runtime_state import (
    refresh_runtime_state_lightweight,
    refresh_runtime_state_with_session,
)

__all__ = [
    # arbitration
    "arbitrate_output",
    "build_pulse_delta",
    "extract_habit_signals",
    "inject_dispositions",
    "resolve_verbosity",
    "should_emit",
    "should_force_emit",
    # controlplane
    "accumulate_session_telemetry",
    "apply_behavior_delta",
    "can_apply_session_telemetry",
    "extract_finding_indexes",
    "load_global_priors",
    "mark_session_telemetry_applied",
    "post_process_session",
    "record_snapshot_behavior",
    "refresh_runtime_after_run",
    "resolve_event_model_key",
    "run_constraint_proposer",
    "save_run_details",
    "select_telemetry_profile",
    "session_telemetry_updates_used",
    "setup_session_and_gate",
    # habit
    "record_behavior_event",
    "record_habit_event_lightweight",
    # runtime_state
    "refresh_runtime_state_lightweight",
    "refresh_runtime_state_with_session",
]
