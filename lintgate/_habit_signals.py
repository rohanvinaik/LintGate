"""Habit Mode signal computation, scoring, and mode management.

Signal collection from tool event streams, habit score calculation,
mode enter/exit logic, user message handling, and declaration API.
"""

from __future__ import annotations

import re
import statistics
from typing import Any

from lintgate._habit_types import (
    _CONTINUATION_KEYWORDS,
    _DIRECTIVE_KEYWORDS,
    _INSPECT_TOOLS,
    _INTENT_EXECUTE,
    _INTENT_GATHER,
    _META_TOOLS,
    _MODIFY_TOOLS,
    _SCORE_WEIGHTS,
    DEFAULT_ENTER_SCORE,
    DEFAULT_EXIT_SCORE,
    DEFAULT_SUSTAIN_CALLS,
    MAX_ACTIVE_FILES,
    WINDOW_SIZE,
    HabitModeState,
)


def _compute_same_file_ratio(window: list[dict[str, Any]]) -> float:
    """Proportion of file ops targeting already-seen files."""
    seen_files: set[str] = set()
    file_ops = 0
    repeat_ops = 0
    for e in window:
        tool = e.get("tool", "")
        if tool not in _INSPECT_TOOLS and tool not in _MODIFY_TOOLS:
            continue
        file_ops += 1
        sig_str = e.get("sig", "")
        if not sig_str:
            continue
        if sig_str in seen_files:
            repeat_ops += 1
        seen_files.add(sig_str)
    return repeat_ops / max(file_ops, 1)


def _compute_inter_tool_gap_median(window: list[dict[str, Any]]) -> float:
    """Median of last 10 inter-tool time gaps."""
    timestamps = [e.get("ts", 0.0) for e in window if e.get("ts")]
    if len(timestamps) < 2:
        return 0.0
    gaps = [timestamps[i + 1] - timestamps[i] for i in range(len(timestamps) - 1)]
    recent_gaps = gaps[-10:] if len(gaps) > 10 else gaps
    if not recent_gaps:
        return 0.0
    # Keep median computation index-free to avoid accidental out-of-range access.
    return float(statistics.median(recent_gaps))


def _compute_edit_streak(window: list[dict[str, Any]]) -> int:
    """Consecutive Edit/Write/MultiEdit at end of window."""
    streak = 0
    for e in reversed(window):
        if e.get("tool") in _MODIFY_TOOLS:
            streak += 1
        else:
            break
    return streak


def _detect_test_in_window(window: list[dict[str, Any]]) -> bool:
    """Check for pytest/test in recent Bash calls."""
    for e in window:
        if e.get("tool") == "Bash":
            cmd_sig = e.get("sig", "")
            if any(kw in cmd_sig.lower() for kw in ("pytest", "test")):
                return True
    return False


def update_signals(state: HabitModeState, action_history: list[dict[str, Any]]) -> None:
    """Compute all HabitSignals from the sliding window of tool events.

    Pure computation — no I/O, no side effects beyond state.signals mutation.
    Works with both compass.action_history (Path A) and minimal action ring (Path B).
    Each entry needs at least: {"tool": str, "ts": float, "intent": str}
    Optional: {"sig": str}
    """
    window = action_history[-WINDOW_SIZE:] if len(action_history) > WINDOW_SIZE else action_history
    if not window:
        return

    sig = state.signals
    n = len(window)

    # Count tool categories
    read_count = sum(1 for e in window if e.get("tool") in _INSPECT_TOOLS)
    edit_count = sum(1 for e in window if e.get("tool") in _MODIFY_TOOLS)
    task_count = sum(1 for e in window if e.get("tool") == "Task")

    sig.read_edit_ratio = read_count / max(edit_count, 1)

    # Intent percentages
    gather_count = sum(1 for e in window if e.get("intent") in _INTENT_GATHER)
    execute_count = sum(1 for e in window if e.get("intent") in _INTENT_EXECUTE)
    sig.gather_pct = gather_count / n
    sig.execute_pct = execute_count / n

    sig.sub_agent_freq = task_count / n
    sig.same_file_ratio = _compute_same_file_ratio(window)
    sig.inter_tool_gap_median = _compute_inter_tool_gap_median(window)
    sig.edit_streak = _compute_edit_streak(window)
    sig.test_in_last_n = _detect_test_in_window(window)


def _extract_file_paths(tool_input: dict[str, Any]) -> list[str]:
    """Extract file paths from a tool input dict."""
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if file_path and isinstance(file_path, str):
        return [file_path]
    files = tool_input.get("files", [])
    if isinstance(files, list):
        return [fp for fp in files[:5] if isinstance(fp, str) and fp]
    return []


def track_active_files(
    state: HabitModeState,
    tool_name: str,
    tool_input: dict[str, Any] | str,
) -> None:
    """Extract file paths from tool input and maintain MRU active_files list.

    Capped at MAX_ACTIVE_FILES entries.
    """
    if isinstance(tool_input, str):
        return
    for fp in _extract_file_paths(tool_input):
        _add_to_mru(state.active_files, fp)


def _add_to_mru(files: list[str], path: str) -> None:
    """Add a path to MRU list, moving to front if already present."""
    if path in files:
        files.remove(path)
    files.insert(0, path)
    while len(files) > MAX_ACTIVE_FILES:
        files.pop()


def _classify_test_output(out_lower: str) -> str | None:
    """Classify pytest output as 'pass', 'fail', or None (indeterminate).

    Excludes "0 failed" / "0 errors" patterns to avoid misclassifying
    passing runs that print "0 failed" in the summary.
    """
    zero_fail = bool(re.search(r"\b0\s+failed", out_lower))
    zero_error = bool(re.search(r"\b0\s+error", out_lower))
    has_fail = "failed" in out_lower and not zero_fail
    has_error = "error" in out_lower and not zero_error
    if "passed" in out_lower and not has_fail and not has_error:
        return "pass"
    if has_fail or has_error:
        return "fail"
    return None


def detect_test_result(
    state: HabitModeState,
    tool_output: str,
    command_sig: str,
) -> None:
    """Detect pass/fail from Bash test output.

    Cheap keyword matching — not precise, but good enough for habit mode.
    """
    if not command_sig:
        return
    sig_lower = command_sig.lower()
    if "pytest" not in sig_lower and "test" not in sig_lower:
        return
    out_lower = tool_output.lower() if tool_output else ""
    if not out_lower:
        return
    result = _classify_test_output(out_lower)
    if result:
        state.last_test_status = result


def _score_component(value: float, full: float, half: float, *, op: str = "lt") -> float:
    """Score a signal: 1.0 at full threshold, 0.5 at half, 0.0 otherwise.

    op: "lt" (lower-is-better, <), "gt" (strict >, for floats), "gte" (>=, for ints).
    """
    if op == "lt":
        if value < full:
            return 1.0
        return 0.5 if value < half else 0.0
    if op == "gte":
        if value >= full:
            return 1.0
        return 0.5 if value >= half else 0.0
    # op == "gt"
    if value > full:
        return 1.0
    return 0.5 if value > half else 0.0


def compute_habit_score(state: HabitModeState) -> float:
    """Compute weighted composite habit score (0.0-1.0).

    Each signal contributes a 0.0-1.0 component multiplied by its weight.
    Thresholds define "full" (1.0) and "half" (0.5) contribution.
    """
    sig = state.signals
    score = (
        _SCORE_WEIGHTS["read_edit_ratio"] * _score_component(sig.read_edit_ratio, 2.0, 3.0, op="lt")
        + _SCORE_WEIGHTS["execute_pct"] * _score_component(sig.execute_pct, 0.5, 0.3, op="gt")
        + _SCORE_WEIGHTS["edit_streak"] * _score_component(sig.edit_streak, 3, 2, op="gte")
        + _SCORE_WEIGHTS["sub_agent_freq"]
        * _score_component(sig.sub_agent_freq, 0.05, 0.15, op="lt")
        + _SCORE_WEIGHTS["same_file_ratio"]
        * _score_component(sig.same_file_ratio, 0.6, 0.4, op="gt")
        + (
            _SCORE_WEIGHTS["inter_tool_gap"]
            * _score_component(sig.inter_tool_gap_median, 3.0, 5.0, op="lt")
            if sig.inter_tool_gap_median > 0
            else 0.0
        )
        + (_SCORE_WEIGHTS["declared"] if state.declared else 0.0)
    )
    return min(score, 1.0)


def update_mode(
    state: HabitModeState,
    event_counter: int,
    *,
    enter_score: float = DEFAULT_ENTER_SCORE,
    exit_score: float = DEFAULT_EXIT_SCORE,
    sustain_calls: int = DEFAULT_SUSTAIN_CALLS,
) -> str | None:
    """Update habit mode state based on current score.

    Returns "enter", "exit", or None.

    Hysteresis:
    - Enter: habit_score >= enter_score sustained for sustain_calls consecutive events
    - Exit: habit_score < exit_score OR user_message_detected (instant)
    """
    state.habit_score = compute_habit_score(state)

    # Check for user message override (instant exit)
    if state.user_message_detected and state.active:
        state.active = False
        state.sustain_counter = 0
        state.declared = False
        state.user_message_detected = False
        return "exit"

    # Clear user message flag even if not active
    state.user_message_detected = False

    if state.active:
        state.total_events_in_habit += 1
        # Check exit condition
        if state.habit_score < exit_score:
            state.active = False
            state.sustain_counter = 0
            return "exit"
        return None
    else:
        # Check enter condition with sustain
        if state.habit_score >= enter_score:
            state.sustain_counter += 1
            if state.sustain_counter >= sustain_calls:
                state.active = True
                state.entered_at_event = event_counter
                state.total_events_in_habit = 0
                return "enter"
        else:
            state.sustain_counter = 0
        return None


def _classify_user_message(text: str) -> str:
    """Classify a user message as directive, continuation, or clarification.

    Returns: "directive", "continuation", or "clarification"

    Rules:
    - Short (<= 15 chars) and matches continuation keywords -> continuation
    - Contains directive keywords -> directive
    - Long (> 50 chars) or multi-sentence -> directive
    - Short with ? -> clarification
    - Default: directive (safe fallback)
    """
    stripped = text.strip().lower()

    if not stripped:
        return "continuation"

    # Check continuation first (short confirmations)
    if len(stripped) <= 15 and stripped.rstrip("!.") in _CONTINUATION_KEYWORDS:
        return "continuation"

    # Check directive keywords
    for kw in _DIRECTIVE_KEYWORDS:
        if kw in stripped:
            return "directive"

    # Long messages are likely new instructions
    if len(stripped) > 50:
        return "directive"

    # Multi-sentence detection
    sentences = [s.strip() for s in stripped.split(".") if s.strip()]
    if len(sentences) > 1:
        return "directive"

    # Short questions -> clarification
    if stripped.endswith("?") and len(stripped) <= 50:
        return "clarification"

    # Default to directive for safety
    return "directive"


def signal_user_message(state: HabitModeState, message_text: str) -> str:
    """Handle a user message in the context of habit mode.

    Graded response:
    - "directive" -> catastrophic reset (active->False, sustain->0, declared->False, score->0.0)
    - "continuation" -> no effect
    - "clarification" -> gentle decay (-0.15 from habit_score)

    Returns the classification string.
    """
    msg_type = _classify_user_message(message_text)

    if msg_type == "directive":
        state.user_message_detected = True
        # If currently active, immediately collapse
        if state.active:
            state.active = False
            state.sustain_counter = 0
            state.declared = False
            state.habit_score = 0.0
    elif msg_type == "clarification":
        state.habit_score = max(0.0, state.habit_score - 0.15)
    # "continuation" -> no effect

    return msg_type


def declare_mode(
    state: HabitModeState,
    mode: str,
    event_counter: int,
) -> str | None:
    """Agent self-declares mode. Immediate — skips sustain wait.

    Args:
        state: Current habit mode state.
        mode: "habit" or "standard"
        event_counter: Current event counter.

    Returns: "enter", "exit", or None.
    """
    if mode == "habit":
        if not state.active:
            state.active = True
            state.declared = True
            state.entered_at_event = event_counter
            state.total_events_in_habit = 0
            state.sustain_counter = DEFAULT_SUSTAIN_CALLS  # Skip sustain
            state.habit_score = compute_habit_score(state)
            return "enter"
        else:
            state.declared = True
            return None
    elif mode == "standard":
        was_active = state.active
        state.active = False
        state.declared = False
        state.sustain_counter = 0
        return "exit" if was_active else None
    return None


def quick_intent(tool_name: str) -> str:
    """Cheap intent classification for standalone path (no command_sig).

    Maps tool names to intent categories without parsing Bash commands.
    """
    if tool_name in _INSPECT_TOOLS:
        return "inspect"
    if tool_name in _MODIFY_TOOLS:
        return "modify"
    if tool_name in _META_TOOLS:
        return "meta"
    if tool_name == "Bash":
        return "execute"
    return "unknown"
