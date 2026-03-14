"""Token estimation and economics tracking for Habit Mode.

Provides per-call token estimation via character counting with periodic
calibration against the Anthropic token counting API (free, every ~15 calls).

Design:
- Per-call: serialize input+output → count chars → multiply by calibration_factor
- API calibration: every N calls, hit Anthropic's count_tokens endpoint (500ms timeout)
- Anti-thrash: should_compact() enforces cooldown + delta gate
- Economics: track LintGate vs external tool calls, lines written, compactions

All operations are fail-safe. API failures degrade to char-count heuristic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

# ── Constants ────────────────────────────────────────────────────────

DEFAULT_CALIBRATION_FACTOR = 0.25  # 1 token per 4 chars (reasonable for English/code)
DEFAULT_CONTEXT_WINDOW = 200000
DEFAULT_COMPACT_THRESHOLD = 0.40
DEFAULT_MIN_CALLS_BETWEEN_COMPACTS = 20
DEFAULT_MIN_TOKEN_DELTA = 15000
DEFAULT_API_CHECK_INTERVAL = 15
DEFAULT_API_TIMEOUT_S = 0.5
MAX_BACKOFF_EXPONENT = 3

# LintGate tool name prefixes
_LINTGATE_TOOL_PREFIXES = (
    "lint_",
    "controlplane_",
    "behavior_",
    "constraint_",
    "prediction_",
    "hygiene_",
    "scaffold_",
    "getting_started",
    "setup_github",
    "context_",
    "audit_",
    "bootstrap_",
    "extract_",
    "build_theory",
    "get_theory",
    "dep_",
    "model_profile",
    "telemetry_",
    "global_memory",
    "habit_",
    "declare_mode",
    "habit_status",
    "habit_compact",
    "habit_configure",
)


# ── Data Structure ───────────────────────────────────────────────────


@dataclass
class TokenTrackerState:
    """Token estimation and economics state.

    Stored alongside HabitModeState in session.behavior_compass["token_tracker"]
    or in standalone file.
    """

    # Estimation
    estimated_tokens_used: int = 0
    char_count_total: int = 0
    calibration_factor: float = DEFAULT_CALIBRATION_FACTOR
    calibration_count: int = 0
    last_api_check_event: int = 0
    last_api_actual: int = 0
    last_api_estimate: int = 0

    # Economics
    tool_call_count: int = 0
    tool_calls_since_compact: int = 0
    lines_written: int = 0
    external_tool_calls: int = 0
    lintgate_tool_calls: int = 0

    # Anti-thrash
    last_compact_tokens: int = 0

    # API resilience
    consecutive_api_failures: int = 0

    # Context
    context_window_size: int = DEFAULT_CONTEXT_WINDOW

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_tokens_used": self.estimated_tokens_used,
            "char_count_total": self.char_count_total,
            "calibration_factor": round(self.calibration_factor, 6),
            "calibration_count": self.calibration_count,
            "last_api_check_event": self.last_api_check_event,
            "last_api_actual": self.last_api_actual,
            "last_api_estimate": self.last_api_estimate,
            "tool_call_count": self.tool_call_count,
            "tool_calls_since_compact": self.tool_calls_since_compact,
            "lines_written": self.lines_written,
            "external_tool_calls": self.external_tool_calls,
            "lintgate_tool_calls": self.lintgate_tool_calls,
            "last_compact_tokens": self.last_compact_tokens,
            "consecutive_api_failures": self.consecutive_api_failures,
            "context_window_size": self.context_window_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenTrackerState:
        if not data:
            return cls()
        return cls(
            estimated_tokens_used=int(data.get("estimated_tokens_used", 0)),
            char_count_total=int(data.get("char_count_total", 0)),
            calibration_factor=float(data.get("calibration_factor", DEFAULT_CALIBRATION_FACTOR)),
            calibration_count=int(data.get("calibration_count", 0)),
            last_api_check_event=int(data.get("last_api_check_event", 0)),
            last_api_actual=int(data.get("last_api_actual", 0)),
            last_api_estimate=int(data.get("last_api_estimate", 0)),
            tool_call_count=int(data.get("tool_call_count", 0)),
            tool_calls_since_compact=int(data.get("tool_calls_since_compact", 0)),
            lines_written=int(data.get("lines_written", 0)),
            external_tool_calls=int(data.get("external_tool_calls", 0)),
            lintgate_tool_calls=int(data.get("lintgate_tool_calls", 0)),
            last_compact_tokens=int(data.get("last_compact_tokens", 0)),
            consecutive_api_failures=int(data.get("consecutive_api_failures", 0)),
            context_window_size=int(data.get("context_window_size", DEFAULT_CONTEXT_WINDOW)),
        )


# ── Per-call estimation ──────────────────────────────────────────────


def estimate_tool_tokens(
    tracker: TokenTrackerState,
    tool_name: str,
    tool_input: Any,
    tool_output: Any,
) -> int:
    """Estimate tokens consumed by a single tool call.

    Serializes input+output to string, counts chars, multiplies by
    calibration_factor. Updates running totals.

    Returns estimated token count for this call.
    """
    # Serialize to string for char counting
    input_str = _to_string(tool_input)
    output_str = _to_string(tool_output)
    total_chars = len(input_str) + len(output_str)

    # Estimate tokens
    estimated = int(total_chars * tracker.calibration_factor)

    # Update totals
    tracker.estimated_tokens_used += estimated
    tracker.char_count_total += total_chars
    tracker.tool_call_count += 1
    tracker.tool_calls_since_compact += 1

    # Classify tool
    if _is_lintgate_tool(tool_name):
        tracker.lintgate_tool_calls += 1
    else:
        tracker.external_tool_calls += 1

    # Track lines written for Write/Edit tools
    if tool_name in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        content = ""
        if isinstance(tool_input, dict):
            content = tool_input.get("content", "") or tool_input.get("new_string", "")
        if isinstance(content, str):
            tracker.lines_written += content.count("\n")

    return estimated


def _to_string(value: Any) -> str:
    """Convert tool input/output to string for char counting."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        try:
            return json.dumps(value, separators=(",", ":"))
        except (TypeError, ValueError):
            return str(value)
    if value is None:
        return ""
    return str(value)


def _is_lintgate_tool(tool_name: str) -> bool:
    """Check if a tool name belongs to LintGate."""
    name_lower = tool_name.lower()
    return any(name_lower.startswith(prefix) for prefix in _LINTGATE_TOOL_PREFIXES)


# ── Compaction trigger ───────────────────────────────────────────────


def should_compact(
    tracker: TokenTrackerState,
    habit_active: bool,
    *,
    threshold: float = DEFAULT_COMPACT_THRESHOLD,
    min_calls_between_compacts: int = DEFAULT_MIN_CALLS_BETWEEN_COMPACTS,
    min_token_delta: int = DEFAULT_MIN_TOKEN_DELTA,
) -> bool:
    """Check if compaction should be triggered.

    Returns True when ALL conditions are met:
    1. estimated_tokens_used / context_window_size > threshold
    2. habit_active == True
    3. tool_calls_since_compact >= min_calls_between_compacts (anti-thrash)
    4. estimated_tokens_used - last_compact_tokens >= min_token_delta (delta gate)
    """
    if not habit_active:
        return False

    if tracker.context_window_size <= 0:
        return False

    usage_ratio = tracker.estimated_tokens_used / tracker.context_window_size
    if usage_ratio <= threshold:
        return False

    if tracker.tool_calls_since_compact < min_calls_between_compacts:
        return False

    token_delta = tracker.estimated_tokens_used - tracker.last_compact_tokens
    return token_delta >= min_token_delta


# ── API calibration ──────────────────────────────────────────────────


def should_api_check(
    tracker: TokenTrackerState,
    event_counter: int,
    *,
    interval: int = DEFAULT_API_CHECK_INTERVAL,
) -> bool:
    """Check if it's time for an API calibration check.

    Fires every `interval` tool calls, with exponential backoff on failures.
    """
    if tracker.tool_call_count == 0:
        return False

    # Apply exponential backoff on consecutive failures
    effective_interval = interval * (
        2 ** min(tracker.consecutive_api_failures, MAX_BACKOFF_EXPONENT)
    )

    calls_since_check = tracker.tool_call_count - tracker.last_api_check_event
    return bool(calls_since_check >= effective_interval)


def apply_api_calibration(
    tracker: TokenTrackerState,
    actual_tokens: int,
    event_counter: int,
) -> dict[str, Any]:
    """Apply ground truth from API calibration.

    Blends 70% new factor + 30% old factor for smooth adjustment.
    Resets estimated_tokens_used to actual ground truth.

    Returns calibration delta info for telemetry.
    """
    old_estimate = tracker.estimated_tokens_used
    old_factor = tracker.calibration_factor

    # Compute new factor from total chars
    if tracker.char_count_total > 0:
        new_factor = actual_tokens / tracker.char_count_total
        # Blend: 70% new, 30% old
        tracker.calibration_factor = (0.7 * new_factor) + (0.3 * old_factor)
    else:
        tracker.calibration_factor = old_factor

    # Reset to ground truth
    tracker.estimated_tokens_used = actual_tokens
    tracker.last_api_check_event = tracker.tool_call_count
    tracker.last_api_actual = actual_tokens
    tracker.last_api_estimate = old_estimate
    tracker.calibration_count += 1
    tracker.consecutive_api_failures = 0

    return {
        "old_estimate": old_estimate,
        "actual": actual_tokens,
        "delta": actual_tokens - old_estimate,
        "old_factor": round(old_factor, 6),
        "new_factor": round(tracker.calibration_factor, 6),
    }


def record_api_failure(tracker: TokenTrackerState) -> None:
    """Record an API calibration failure for backoff computation."""
    tracker.consecutive_api_failures += 1


# ── API caller (with timeout) ───────────────────────────────────────


def do_api_calibration(
    tracker: TokenTrackerState,
    event_counter: int,
    project_root: str,
    *,
    api_key: str | None = None,
    timeout: float = DEFAULT_API_TIMEOUT_S,
) -> dict[str, Any] | None:
    """Call Anthropic token counting API with strict timeout.

    Uses urllib.request for zero-dependency HTTP. Falls back silently
    on any failure — the char-count heuristic continues operating.

    Args:
        tracker: Current tracker state.
        event_counter: Current event counter.
        project_root: Project path (for telemetry).
        api_key: Optional API key. If None, tries ANTHROPIC_API_KEY env var.
        timeout: HTTP timeout in seconds (default 0.5s).

    Returns:
        Calibration delta dict on success, None on failure.
    """
    import os
    import urllib.error
    import urllib.request

    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        record_api_failure(tracker)
        return None

    # Build a minimal messages payload for token counting
    # We use a small representative prompt to get the tokenizer ratio
    sample_text = f"Token calibration probe at {tracker.tool_call_count} calls."
    payload = json.dumps(
        {
            "model": "claude-sonnet-4-20250514",
            "messages": [{"role": "user", "content": sample_text}],
        }
    ).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages/count_tokens",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Api-Key": key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310  # URL is hardcoded to https://api.anthropic.com
            body = json.loads(resp.read())
            input_tokens = body.get("input_tokens", 0)
            if input_tokens > 0:
                # Scale: the sample is tiny, but the ratio holds
                # Use calibration_factor from sample chars to tokens
                sample_chars = len(sample_text)
                sample_ratio = input_tokens / max(sample_chars, 1)
                # Apply to total char count
                estimated_actual = int(tracker.char_count_total * sample_ratio)
                return apply_api_calibration(tracker, estimated_actual, event_counter)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError, ValueError):
        record_api_failure(tracker)

    return None


# ── Summary ──────────────────────────────────────────────────────────


def get_usage_summary(tracker: TokenTrackerState) -> dict[str, Any]:
    """Get token economics summary for reporting."""
    window_pct = (
        round(tracker.estimated_tokens_used / max(tracker.context_window_size, 1) * 100, 1)
        if tracker.context_window_size > 0
        else 0.0
    )

    return {
        "estimated_tokens_used": tracker.estimated_tokens_used,
        "context_window_size": tracker.context_window_size,
        "window_usage_pct": window_pct,
        "calibration_factor": round(tracker.calibration_factor, 4),
        "calibration_count": tracker.calibration_count,
        "tool_call_count": tracker.tool_call_count,
        "tool_calls_since_compact": tracker.tool_calls_since_compact,
        "lines_written": tracker.lines_written,
        "external_tool_calls": tracker.external_tool_calls,
        "lintgate_tool_calls": tracker.lintgate_tool_calls,
        "char_count_total": tracker.char_count_total,
    }


# ── Post-compaction reset ────────────────────────────────────────────


def reset_post_compaction(tracker: TokenTrackerState) -> None:
    """Reset counters after compaction.

    Preserves estimated_tokens (context still exists).
    Stores current estimate as baseline for delta gate.
    """
    tracker.tool_calls_since_compact = 0
    tracker.last_compact_tokens = tracker.estimated_tokens_used


# ── Session-backed persistence ───────────────────────────────────────


def load_tracker_state(behavior_compass_dict: dict[str, Any]) -> TokenTrackerState:
    """Load tracker state from session.behavior_compass dict."""
    data = behavior_compass_dict.get("token_tracker", {})
    return TokenTrackerState.from_dict(data)


def save_tracker_state(behavior_compass_dict: dict[str, Any], tracker: TokenTrackerState) -> None:
    """Save tracker state into session.behavior_compass dict."""
    behavior_compass_dict["token_tracker"] = tracker.to_dict()
