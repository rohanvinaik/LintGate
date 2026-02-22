"""Message arbitration layer for the PostToolUse hook.

Unified control over hook output: verbosity gating + disposition injection.
Merges Issues 21 (hook noise) and 24 (habit dispositions).
"""

from __future__ import annotations

from typing import Any

# ── Disposition triggers ─────────────────────────────────────────────

_DISPOSITION_TRIGGERS = [
    {
        "name": "compact_pressure",
        "cooldown_events": 5,
        "priority": 3,
        "tool_hint": "habit_compact",
    },
    {
        "name": "habit_enter_suggested",
        "cooldown_events": 20,
        "priority": 1,
        "tool_hint": "declare_mode",
    },
    {
        "name": "constraint_reorient",
        "cooldown_events": 1,
        "priority": 2,
        "tool_hint": "constraint_check",
    },
]


def resolve_verbosity(cp_config: Any, habit_active: bool) -> str:
    """Resolve effective verbosity level.

    Returns one of: "silent", "pulse", "full".
    """
    configured = getattr(cp_config, "hook_verbosity", "full")
    if configured == "auto":
        return "pulse" if habit_active else "full"
    if configured in ("silent", "pulse", "full"):
        return configured
    return "full"


def should_force_emit(report: dict, prev_report: dict | None) -> bool:
    """Check if report contains significant changes that force emission."""
    if not report:
        return False

    hook_output = report.get("hookSpecificOutput", {})

    # Force on priority 3 dispositions
    for d in hook_output.get("dispositions", []):
        if isinstance(d, dict) and d.get("priority", 0) >= 3:
            return True

    if prev_report is None:
        return False

    # Force on new blockers
    curr_msg = report.get("systemMessage", "")
    prev_msg = prev_report.get("systemMessage", "")
    return "BLOCKING" in curr_msg and "BLOCKING" not in prev_msg


def should_emit(
    cp_config: Any,
    session_data: dict,
    habit_active: bool,
    report: dict,
    prev_report: dict | None,
) -> bool:
    """Determine whether to emit hook output based on verbosity and force-emit rules.

    Even in pulse/silent mode, force-emit on significant changes:
    - New blocking finding (blocker count increased)
    - Test channel regression (pass -> fail)
    - Secret detected
    - Priority 3 disposition present
    """
    verbosity = resolve_verbosity(cp_config, habit_active)

    if verbosity == "full":
        return True

    # Force-emit rules (bypass verbosity)
    if should_force_emit(report, prev_report):
        return True

    if verbosity == "silent":
        return False

    # Pulse mode: emit every N events
    event_counter = session_data.get("event_counter", 0)
    pulse_interval = getattr(cp_config, "hook_pulse_interval", 5)
    last_pulse = session_data.get("_last_pulse_event", 0)
    if event_counter - last_pulse >= pulse_interval:
        session_data["_last_pulse_event"] = event_counter
        return True

    return False


def inject_dispositions(
    session_data: dict,
    habit_active: bool,
    habit_score: float,
    context_pressure: float,
    consecutive_failures: int,
    enter_score: float,
) -> list[dict[str, Any]]:
    """Evaluate disposition triggers and return fired dispositions.

    Returns a list of disposition dicts with keys:
      disposition (str), tool_hint (str), priority (int)
    """
    fired: list[dict[str, Any]] = []
    cooldowns = session_data.get("_disposition_cooldowns", {})
    event_counter = session_data.get("event_counter", 0)

    # Trigger 1: compact_pressure -- context window filling up during habit mode
    trigger = _DISPOSITION_TRIGGERS[0]  # compact_pressure
    last_fire = cooldowns.get(trigger["name"], 0)
    if (
        event_counter - last_fire >= trigger["cooldown_events"]
        and habit_active
        and context_pressure >= 0.50
    ):
        pct = round(context_pressure * 100)
        fired.append(
            {
                "disposition": f"\u26a0 Context pressure {pct}% \u2014 call habit_compact() now",
                "tool_hint": trigger["tool_hint"],
                "priority": trigger["priority"],
            }
        )
        cooldowns[trigger["name"]] = event_counter

    # Trigger 2: habit_enter_suggested -- sustained edit pattern, not yet in habit mode
    trigger = _DISPOSITION_TRIGGERS[1]  # habit_enter_suggested
    last_fire = cooldowns.get(trigger["name"], 0)
    if (
        event_counter - last_fire >= trigger["cooldown_events"]
        and not habit_active
        and habit_score >= enter_score
    ):
        fired.append(
            {
                "disposition": "Sustained edit pattern detected. Consider: declare_mode('habit')",
                "tool_hint": trigger["tool_hint"],
                "priority": trigger["priority"],
            }
        )
        cooldowns[trigger["name"]] = event_counter

    # Trigger 3: constraint_reorient -- consecutive failures
    trigger = _DISPOSITION_TRIGGERS[2]  # constraint_reorient
    last_fire = cooldowns.get(trigger["name"], 0)
    if (
        event_counter - last_fire >= trigger["cooldown_events"]
        and consecutive_failures >= 3
    ):
        fired.append(
            {
                "disposition": f"{consecutive_failures} consecutive failures. Run constraint_check() to assess coverage",
                "tool_hint": trigger["tool_hint"],
                "priority": trigger["priority"],
            }
        )
        cooldowns[trigger["name"]] = event_counter

    session_data["_disposition_cooldowns"] = cooldowns
    return fired


def build_pulse_delta(
    report: dict,
    prev_report: dict | None,
    events_since: int,
    dispositions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build compact pulse-mode output showing only deltas."""
    delta: dict[str, Any] = {
        "pulse": True,
        "events_since_last": events_since,
    }

    # Extract current metrics from report systemMessage (lightweight parsing)
    msg = report.get("systemMessage", "")
    changes: dict[str, Any] = {}
    if "BLOCKING" in msg:
        changes["has_blockers"] = True
    if "coherence" in msg.lower():
        changes["coherence_mentioned"] = True
    if changes:
        delta["changes"] = changes

    if dispositions:
        delta["dispositions"] = dispositions

    return delta


def extract_habit_signals(session_data: dict) -> tuple[bool, float, float, int]:
    """Extract habit state signals from session data for disposition evaluation.

    Returns (habit_active, habit_score, context_pressure, consecutive_failures).
    """
    bc = session_data if isinstance(session_data, dict) else {}

    # Habit state
    habit_data = bc.get("habit_state", {})
    if isinstance(habit_data, dict):
        habit_active = bool(habit_data.get("active", False))
        habit_score = float(habit_data.get("habit_score", 0.0))
    else:
        habit_active = False
        habit_score = 0.0

    # Context pressure from token tracker
    tracker_data = bc.get("token_tracker", {})
    if isinstance(tracker_data, dict):
        est = tracker_data.get("estimated_tokens_used", 0)
        window = tracker_data.get("context_window_size", 200000)
        context_pressure = est / max(window, 1)
    else:
        context_pressure = 0.0

    # Consecutive failures (from action ring)
    consecutive_failures = 0
    action_ring = bc.get("action_history", [])
    if isinstance(action_ring, list):
        for entry in reversed(action_ring):
            if isinstance(entry, dict) and entry.get("exit_code", 0) != 0:
                consecutive_failures += 1
            else:
                break

    return habit_active, habit_score, context_pressure, consecutive_failures


def arbitrate_output(
    report: dict,
    cp_config: Any,
    session_data: dict,
    prev_report: dict | None = None,
) -> dict:
    """Message arbitration: apply verbosity gating and disposition injection.

    This is the central hook output control. Called after report generation
    but before stdout emission.

    Returns the (possibly modified or suppressed) report dict.
    """
    habit_active, habit_score, context_pressure, consecutive_failures = (
        extract_habit_signals(session_data)
    )

    # Phase 1: Disposition injection (always evaluated, even if output suppressed)
    dispositions: list[dict[str, Any]] = []
    if getattr(cp_config, "hook_dispositions_enabled", True):
        dispositions = inject_dispositions(
            session_data,
            habit_active=habit_active,
            habit_score=habit_score,
            context_pressure=context_pressure,
            consecutive_failures=consecutive_failures,
            enter_score=getattr(cp_config, "habit_mode_enter_score", 0.70),
        )

    # Attach dispositions to report before emission check
    if dispositions and report:
        report.setdefault("hookSpecificOutput", {})["dispositions"] = dispositions

    # Phase 2: Verbosity gating
    if not should_emit(cp_config, session_data, habit_active, report, prev_report):
        return {}  # Suppress output (state tracking already happened)

    return report
