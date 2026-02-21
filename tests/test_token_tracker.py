"""Tests for Token Tracker — estimation, calibration, compaction triggers, economics."""

from __future__ import annotations

import pytest

from lintgate.token_tracker import (
    DEFAULT_CALIBRATION_FACTOR,
    DEFAULT_CONTEXT_WINDOW,
    TokenTrackerState,
    apply_api_calibration,
    estimate_tool_tokens,
    get_usage_summary,
    load_tracker_state,
    record_api_failure,
    reset_post_compaction,
    save_tracker_state,
    should_api_check,
    should_compact,
)


# ── TokenTrackerState serialization ──────────────────────────────────


class TestTokenTrackerState:
    def test_roundtrip(self):
        tracker = TokenTrackerState(
            estimated_tokens_used=5000,
            char_count_total=20000,
            calibration_factor=0.27,
            calibration_count=3,
            tool_call_count=50,
            lines_written=200,
            external_tool_calls=40,
            lintgate_tool_calls=10,
        )
        d = tracker.to_dict()
        restored = TokenTrackerState.from_dict(d)
        assert restored.estimated_tokens_used == 5000
        assert restored.calibration_factor == pytest.approx(0.27, abs=0.001)
        assert restored.calibration_count == 3
        assert restored.external_tool_calls == 40

    def test_from_empty_dict(self):
        tracker = TokenTrackerState.from_dict({})
        assert tracker.estimated_tokens_used == 0
        assert tracker.calibration_factor == DEFAULT_CALIBRATION_FACTOR

    def test_from_none(self):
        tracker = TokenTrackerState.from_dict(None)
        assert tracker.tool_call_count == 0


# ── Per-call estimation ──────────────────────────────────────────────


class TestEstimateToolTokens:
    def test_basic_estimation(self):
        tracker = TokenTrackerState()
        tokens = estimate_tool_tokens(tracker, "Read", {"file_path": "/a.py"}, "content here")
        assert tokens > 0
        assert tracker.estimated_tokens_used == tokens
        assert tracker.tool_call_count == 1
        assert tracker.char_count_total > 0

    def test_lintgate_tool_classification(self):
        tracker = TokenTrackerState()
        estimate_tool_tokens(tracker, "lint_files", {}, "")
        assert tracker.lintgate_tool_calls == 1
        assert tracker.external_tool_calls == 0

    def test_external_tool_classification(self):
        tracker = TokenTrackerState()
        estimate_tool_tokens(tracker, "Read", {}, "")
        assert tracker.external_tool_calls == 1
        assert tracker.lintgate_tool_calls == 0

    def test_lines_written_tracking(self):
        tracker = TokenTrackerState()
        estimate_tool_tokens(tracker, "Write", {"content": "line1\nline2\nline3\n"}, "ok")
        assert tracker.lines_written == 3

    def test_edit_lines_written(self):
        tracker = TokenTrackerState()
        estimate_tool_tokens(tracker, "Edit", {"new_string": "new\ncode\n"}, "ok")
        assert tracker.lines_written == 2

    def test_accumulates_across_calls(self):
        tracker = TokenTrackerState()
        t1 = estimate_tool_tokens(tracker, "Read", {}, "short")
        t2 = estimate_tool_tokens(tracker, "Read", {}, "another read")
        assert tracker.estimated_tokens_used == t1 + t2
        assert tracker.tool_call_count == 2

    def test_string_input(self):
        """String tool input should work (not just dicts)."""
        tracker = TokenTrackerState()
        tokens = estimate_tool_tokens(tracker, "Bash", "git status", "on branch main")
        assert tokens > 0

    def test_none_input(self):
        """None input/output should not crash."""
        tracker = TokenTrackerState()
        tokens = estimate_tool_tokens(tracker, "Read", None, None)
        assert tokens == 0


# ── Compaction trigger ───────────────────────────────────────────────


class TestShouldCompact:
    def test_triggers_when_all_conditions_met(self):
        tracker = TokenTrackerState(
            estimated_tokens_used=100000,
            tool_calls_since_compact=25,
            last_compact_tokens=50000,
            context_window_size=DEFAULT_CONTEXT_WINDOW,
        )
        assert should_compact(tracker, habit_active=True) is True

    def test_not_when_habit_inactive(self):
        tracker = TokenTrackerState(
            estimated_tokens_used=100000,
            tool_calls_since_compact=25,
            last_compact_tokens=50000,
        )
        assert should_compact(tracker, habit_active=False) is False

    def test_not_when_below_threshold(self):
        tracker = TokenTrackerState(
            estimated_tokens_used=1000,
            tool_calls_since_compact=25,
            last_compact_tokens=0,
        )
        assert should_compact(tracker, habit_active=True) is False

    def test_not_when_insufficient_calls(self):
        """Anti-thrash: not enough calls since last compact."""
        tracker = TokenTrackerState(
            estimated_tokens_used=100000,
            tool_calls_since_compact=5,  # Below 20 min
            last_compact_tokens=50000,
        )
        assert should_compact(tracker, habit_active=True) is False

    def test_not_when_insufficient_delta(self):
        """Delta gate: not enough new tokens since last compact."""
        tracker = TokenTrackerState(
            estimated_tokens_used=100000,
            tool_calls_since_compact=25,
            last_compact_tokens=99000,  # Only 1000 new tokens
        )
        assert should_compact(tracker, habit_active=True) is False


# ── API calibration ──────────────────────────────────────────────────


class TestShouldApiCheck:
    def test_fires_at_interval(self):
        tracker = TokenTrackerState(tool_call_count=15, last_api_check_event=0)
        assert should_api_check(tracker, 15, interval=15) is True

    def test_not_before_interval(self):
        tracker = TokenTrackerState(tool_call_count=10, last_api_check_event=0)
        assert should_api_check(tracker, 10, interval=15) is False

    def test_exponential_backoff(self):
        tracker = TokenTrackerState(
            tool_call_count=30,
            last_api_check_event=0,
            consecutive_api_failures=2,
        )
        # With 2 failures, effective interval = 15 * 4 = 60
        assert should_api_check(tracker, 30, interval=15) is False

        tracker.tool_call_count = 60
        assert should_api_check(tracker, 60, interval=15) is True

    def test_zero_calls(self):
        tracker = TokenTrackerState(tool_call_count=0)
        assert should_api_check(tracker, 0) is False


class TestApplyApiCalibration:
    def test_calibration_adjusts_factor(self):
        tracker = TokenTrackerState(
            estimated_tokens_used=5000,
            char_count_total=20000,
            calibration_factor=0.25,
        )
        result = apply_api_calibration(tracker, 6000, 50)
        assert tracker.estimated_tokens_used == 6000  # Reset to actual
        assert tracker.calibration_count == 1
        assert tracker.consecutive_api_failures == 0
        # New factor should be blend of old and new
        expected_new = 6000 / 20000  # 0.30
        expected_blended = 0.7 * expected_new + 0.3 * 0.25
        assert tracker.calibration_factor == pytest.approx(expected_blended, abs=0.01)

    def test_calibration_with_zero_chars(self):
        tracker = TokenTrackerState(char_count_total=0, calibration_factor=0.25)
        apply_api_calibration(tracker, 100, 10)
        assert tracker.calibration_factor == 0.25  # Unchanged

    def test_failure_increments_counter(self):
        tracker = TokenTrackerState(consecutive_api_failures=2)
        record_api_failure(tracker)
        assert tracker.consecutive_api_failures == 3


# ── Summary ──────────────────────────────────────────────────────────


class TestGetUsageSummary:
    def test_basic_summary(self):
        tracker = TokenTrackerState(
            estimated_tokens_used=50000,
            tool_call_count=100,
            lines_written=500,
            external_tool_calls=80,
            lintgate_tool_calls=20,
        )
        summary = get_usage_summary(tracker)
        assert summary["estimated_tokens_used"] == 50000
        assert summary["tool_call_count"] == 100
        assert summary["window_usage_pct"] == 25.0  # 50000/200000 * 100


# ── Post-compaction reset ────────────────────────────────────────────


class TestResetPostCompaction:
    def test_resets_counters(self):
        tracker = TokenTrackerState(
            estimated_tokens_used=80000,
            tool_calls_since_compact=30,
            last_compact_tokens=40000,
        )
        reset_post_compaction(tracker)
        assert tracker.tool_calls_since_compact == 0
        assert tracker.last_compact_tokens == 80000  # Stored for delta gate
        assert tracker.estimated_tokens_used == 80000  # Not reset


# ── Session-backed persistence ───────────────────────────────────────


class TestTrackerPersistence:
    def test_roundtrip(self):
        compass_dict: dict = {}
        tracker = TokenTrackerState(
            estimated_tokens_used=10000,
            calibration_factor=0.28,
            tool_call_count=50,
        )
        save_tracker_state(compass_dict, tracker)
        restored = load_tracker_state(compass_dict)
        assert restored.estimated_tokens_used == 10000
        assert restored.calibration_factor == pytest.approx(0.28, abs=0.001)
        assert restored.tool_call_count == 50

    def test_empty_dict_returns_fresh(self):
        tracker = load_tracker_state({})
        assert tracker.estimated_tokens_used == 0
