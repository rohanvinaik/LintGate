"""Tests for lintgate._habit_signals — signal computation, scoring, mode management, and persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from lintgate._habit_compact import _enforce_snapshot_cap, build_compaction_snapshot
from lintgate._habit_persist import (
    _standalone_path,
    load_habit_state,
    load_habit_state_standalone,
    load_standalone_extras,
    save_habit_state,
    save_habit_state_standalone,
)
from lintgate._habit_signals import (
    _add_to_mru,
    _classify_test_output,
    _classify_user_message,
    _compute_edit_streak,
    _compute_inter_tool_gap_median,
    _compute_same_file_ratio,
    _detect_test_in_window,
    _extract_file_paths,
    _score_component,
    compute_habit_score,
    declare_mode,
    detect_test_result,
    quick_intent,
    signal_user_message,
    track_active_files,
    update_mode,
    update_signals,
)
from lintgate._habit_types import (
    DEFAULT_SUSTAIN_CALLS,
    MAX_ACTIVE_FILES,
    SNAPSHOT_MAX_CHARS,
    WINDOW_SIZE,
    HabitModeState,
    HabitSignals,
)
from lintgate.hook_posttooluse import _record_habit_event_lightweight
from lintgate.token_tracker import TokenTrackerState


def _load_tool_result(json_str):
    import json
    import os
    r = json.loads(json_str)
    if isinstance(r, dict) and "file" in r and "analysis_id" in r and os.path.isfile(r.get("file","")):
        with open(r["file"]) as f: return json.loads(f.read())
    return r


# ── _compute_same_file_ratio ────────────────────────────────────────


class TestComputeSameFileRatio:
    # test_empty_window removed (byte-identical to test_habit_mode.py)

    def test_no_file_ops(self):
        window = [{"tool": "Bash", "sig": "pytest"}, {"tool": "Task", "sig": "x"}]
        assert _compute_same_file_ratio(window) == 0.0

    def test_all_unique_files(self):
        window = [
            {"tool": "Read", "sig": "/a.py"},
            {"tool": "Read", "sig": "/b.py"},
            {"tool": "Edit", "sig": "/c.py"},
        ]
        assert _compute_same_file_ratio(window) == 0.0

    def test_all_same_file(self):
        window = [
            {"tool": "Read", "sig": "/a.py"},
            {"tool": "Edit", "sig": "/a.py"},
            {"tool": "Read", "sig": "/a.py"},
        ]
        # 3 file_ops, first /a.py is new, second /a.py is repeat, third /a.py is repeat
        # repeat_ops = 2, file_ops = 3
        assert _compute_same_file_ratio(window) == pytest.approx(2 / 3)

    def test_missing_sig(self):
        window = [
            {"tool": "Read"},
            {"tool": "Edit", "sig": ""},
        ]
        # 2 file_ops, but no sig to track -> 0 repeats
        assert _compute_same_file_ratio(window) == 0.0

    def test_mixed_inspect_and_modify(self):
        window = [
            {"tool": "Grep", "sig": "/x.py"},
            {"tool": "Write", "sig": "/x.py"},
        ]
        # First is new, second is repeat -> 1/2
        assert _compute_same_file_ratio(window) == pytest.approx(0.5)

    def test_non_file_tools_ignored(self):
        window = [
            {"tool": "Bash", "sig": "/a.py"},
            {"tool": "Read", "sig": "/a.py"},
        ]
        # Bash is not inspect/modify, so only 1 file_op, 0 repeats
        assert _compute_same_file_ratio(window) == 0.0


# ── _compute_inter_tool_gap_median ──────────────────────────────────


class TestComputeInterToolGapMedian:
    def test_empty_window(self):
        assert _compute_inter_tool_gap_median([]) == 0.0

    def test_single_event(self):
        assert _compute_inter_tool_gap_median([{"ts": 1.0}]) == 0.0

    def test_two_events(self):
        window = [{"ts": 1.0}, {"ts": 4.0}]
        assert _compute_inter_tool_gap_median(window) == 3.0

    def test_three_events_returns_median(self):
        # ts=0.0 is falsy so filtered out by `if e.get("ts")`
        # Use non-zero timestamps to get all three included
        window = [{"ts": 1.0}, {"ts": 2.0}, {"ts": 6.0}]
        # gaps: [1.0, 4.0], median = 2.5
        assert _compute_inter_tool_gap_median(window) == 2.5

    def test_only_last_10_gaps_used(self):
        # 13 timestamps => 12 gaps, only last 10 should be used
        timestamps = list(range(13))
        window = [{"ts": float(t)} for t in timestamps]
        # gaps are all 1.0, so median is 1.0 regardless
        assert _compute_inter_tool_gap_median(window) == 1.0

    def test_uneven_gaps_last_10(self):
        # Create 12 gaps where first 2 are large but last 10 are small
        # ts: 0, 100, 200, 201, 202, ..., 209
        tss = [0.0, 100.0, 200.0] + [200.0 + i for i in range(1, 11)]
        window = [{"ts": t} for t in tss]
        # 12 gaps total, last 10: gaps from ts[2..12] = 100, 1, 1, ..., 1
        # last 10 gaps: [100.0, 1, 1, 1, 1, 1, 1, 1, 1, 1]
        # median of 9 ones and 1 hundred = 1.0
        assert _compute_inter_tool_gap_median(window) == 1.0

    def test_zero_timestamps_skipped(self):
        window: list[dict[str, Any]] = [{"ts": 0.0}, {"ts": 0}, {"ts": 5.0}]
        # ts=0 and ts=0 are falsy, filtered out by `if e.get("ts")`
        # Only ts=5.0 survives, len<2 -> 0.0
        assert _compute_inter_tool_gap_median(window) == 0.0

    def test_missing_ts_ignored(self):
        window: list[dict[str, Any]] = [{"tool": "Read"}, {"ts": 5.0}]
        # Only one timestamp -> 0.0
        assert _compute_inter_tool_gap_median(window) == 0.0


# ── _compute_edit_streak ────────────────────────────────────────────


class TestComputeEditStreak:
    def test_empty_window(self):
        assert _compute_edit_streak([]) == 0

    def test_no_edits(self):
        window = [{"tool": "Read"}, {"tool": "Bash"}]
        assert _compute_edit_streak(window) == 0

    def test_trailing_edits(self):
        window = [
            {"tool": "Read"},
            {"tool": "Edit"},
            {"tool": "Write"},
        ]
        assert _compute_edit_streak(window) == 2

    def test_edit_broken_by_read(self):
        window = [
            {"tool": "Edit"},
            {"tool": "Read"},
            {"tool": "Edit"},
        ]
        assert _compute_edit_streak(window) == 1

    def test_all_modify_tools(self):
        window = [
            {"tool": "Write"},
            {"tool": "Edit"},
            {"tool": "MultiEdit"},
            {"tool": "NotebookEdit"},
        ]
        assert _compute_edit_streak(window) == 4

    def test_missing_tool_key_breaks_streak(self):
        window = [{"tool": "Edit"}, {}, {"tool": "Write"}]
        assert _compute_edit_streak(window) == 1


# ── _detect_test_in_window ──────────────────────────────────────────


class TestDetectTestInWindow:
    def test_empty_window(self):
        assert _detect_test_in_window([]) is False

    def test_no_bash_calls(self):
        window = [{"tool": "Read", "sig": "pytest"}]
        assert _detect_test_in_window(window) is False

    def test_bash_with_pytest(self):
        window = [{"tool": "Bash", "sig": "pytest tests/"}]
        assert _detect_test_in_window(window) is True

    def test_bash_with_test_keyword(self):
        window = [{"tool": "Bash", "sig": "python -m test_something"}]
        assert _detect_test_in_window(window) is True

    def test_case_insensitive(self):
        window = [{"tool": "Bash", "sig": "PYTEST -v"}]
        assert _detect_test_in_window(window) is True

    def test_bash_without_test_keyword(self):
        window = [{"tool": "Bash", "sig": "ls -la"}]
        assert _detect_test_in_window(window) is False

    def test_missing_sig(self):
        window = [{"tool": "Bash"}]
        assert _detect_test_in_window(window) is False


# ── update_signals ──────────────────────────────────────────────────


class TestUpdateSignals:
    def test_empty_history(self):
        state = HabitModeState()
        update_signals(state, [])
        # Should be a no-op, signals stay at defaults
        assert state.signals.read_edit_ratio == 0.0

    def test_basic_signal_computation(self):
        state = HabitModeState()
        history = [
            {"tool": "Read", "ts": 1.0, "intent": "inspect", "sig": "/a.py"},
            {"tool": "Read", "ts": 2.0, "intent": "inspect", "sig": "/b.py"},
            {"tool": "Edit", "ts": 3.0, "intent": "modify", "sig": "/a.py"},
            {"tool": "Bash", "ts": 4.0, "intent": "execute", "sig": "pytest"},
        ]
        update_signals(state, history)
        # read_edit_ratio: 2 reads / max(1 edit, 1) = 2.0
        assert state.signals.read_edit_ratio == 2.0
        # gather_pct: 2 inspect / 4 = 0.5
        assert state.signals.gather_pct == 0.5
        # execute_pct: modify + execute = 2 / 4 = 0.5
        assert state.signals.execute_pct == 0.5
        # sub_agent_freq: 0 Task / 4 = 0.0
        assert state.signals.sub_agent_freq == 0.0
        # edit_streak: last event is Bash, not modify -> 0
        assert state.signals.edit_streak == 0
        # test_in_last_n: Bash sig contains "pytest"
        assert state.signals.test_in_last_n is True

    def test_window_truncation(self):
        state = HabitModeState()
        # Create history longer than WINDOW_SIZE
        history = [
            {"tool": "Read", "ts": float(i), "intent": "inspect"} for i in range(WINDOW_SIZE + 10)
        ]
        update_signals(state, history)
        # Should use only the last WINDOW_SIZE entries
        # All are Reads with inspect intent
        assert state.signals.gather_pct == 1.0

    def test_task_sub_agent_frequency(self):
        state = HabitModeState()
        history = [
            {"tool": "Task", "ts": 1.0, "intent": "meta"},
            {"tool": "Task", "ts": 2.0, "intent": "meta"},
            {"tool": "Read", "ts": 3.0, "intent": "inspect"},
            {"tool": "Read", "ts": 4.0, "intent": "inspect"},
        ]
        update_signals(state, history)
        assert state.signals.sub_agent_freq == 0.5


# ── _extract_file_paths ────────────────────────────────────────────


class TestExtractFilePaths:
    def test_file_path_key(self):
        assert _extract_file_paths({"file_path": "/a.py"}) == ["/a.py"]

    def test_path_key(self):
        assert _extract_file_paths({"path": "/b.py"}) == ["/b.py"]

    def test_file_path_takes_precedence(self):
        result = _extract_file_paths({"file_path": "/a.py", "path": "/b.py"})
        assert result == ["/a.py"]

    def test_files_list(self):
        result = _extract_file_paths({"files": ["/a.py", "/b.py", "/c.py"]})
        assert result == ["/a.py", "/b.py", "/c.py"]

    def test_files_list_capped_at_5(self):
        paths = [f"/{i}.py" for i in range(10)]
        result = _extract_file_paths({"files": paths})
        assert len(result) == 5

    def test_empty_dict(self):
        assert _extract_file_paths({}) == []

    def test_non_string_file_path(self):
        assert _extract_file_paths({"file_path": 123}) == []

    def test_empty_string_file_path(self):
        assert _extract_file_paths({"file_path": ""}) == []

    def test_files_with_non_string_entries(self):
        result = _extract_file_paths({"files": ["/a.py", 123, "", "/b.py"]})
        assert result == ["/a.py", "/b.py"]

    def test_files_not_a_list(self):
        assert _extract_file_paths({"files": "not_a_list"}) == []


# ── track_active_files ──────────────────────────────────────────────


class TestTrackActiveFiles:
    def test_adds_file_to_state(self):
        state = HabitModeState()
        track_active_files(state, "Read", {"file_path": "/a.py"})
        assert "/a.py" in state.active_files

    def test_string_input_ignored(self):
        state = HabitModeState()
        track_active_files(state, "Read", "some string")
        assert state.active_files == []

    def test_multiple_files_tracked(self):
        state = HabitModeState()
        track_active_files(state, "Read", {"file_path": "/a.py"})
        track_active_files(state, "Edit", {"file_path": "/b.py"})
        assert state.active_files == ["/b.py", "/a.py"]

    def test_duplicate_moved_to_front(self):
        state = HabitModeState()
        track_active_files(state, "Read", {"file_path": "/a.py"})
        track_active_files(state, "Read", {"file_path": "/b.py"})
        track_active_files(state, "Read", {"file_path": "/a.py"})
        assert state.active_files[0] == "/a.py"
        assert len(state.active_files) == 2


# ── _add_to_mru ─────────────────────────────────────────────────────


class TestAddToMru:
    def test_add_new_path(self):
        files: list[str] = []
        _add_to_mru(files, "/a.py")
        assert files == ["/a.py"]

    def test_move_existing_to_front(self):
        files = ["/b.py", "/a.py"]
        _add_to_mru(files, "/a.py")
        assert files == ["/a.py", "/b.py"]

    def test_cap_at_max_active_files(self):
        files = [f"/{i}.py" for i in range(MAX_ACTIVE_FILES)]
        _add_to_mru(files, "/new.py")
        assert len(files) == MAX_ACTIVE_FILES
        assert files[0] == "/new.py"

    def test_evicts_oldest_when_full(self):
        files = [f"/{i}.py" for i in range(MAX_ACTIVE_FILES)]
        last_file = files[-1]
        _add_to_mru(files, "/new.py")
        assert last_file not in files


# ── _classify_test_output ───────────────────────────────────────────


class TestClassifyTestOutput:
    def test_pass_output(self):
        assert _classify_test_output("5 passed in 0.3s") == "pass"

    def test_fail_output(self):
        assert _classify_test_output("2 failed, 3 passed") == "fail"

    def test_error_output(self):
        assert _classify_test_output("1 error during collection") == "fail"

    def test_zero_failed_counts_as_pass(self):
        assert _classify_test_output("5 passed, 0 failed in 1.2s") == "pass"

    def test_zero_errors_counts_as_pass(self):
        assert _classify_test_output("5 passed, 0 errors") == "pass"

    def test_indeterminate_output(self):
        assert _classify_test_output("collecting ...") is None

    def test_empty_string(self):
        assert _classify_test_output("") is None

    def test_passed_with_real_fail(self):
        # "passed" present but also real "failed" (not 0 failed)
        assert _classify_test_output("3 passed, 1 failed") == "fail"

    def test_passed_with_real_error(self):
        assert _classify_test_output("3 passed, 1 error") == "fail"


# ── detect_test_result ──────────────────────────────────────────────


class TestDetectTestResult:
    def test_empty_command_sig(self):
        state = HabitModeState()
        detect_test_result(state, "5 passed", "")
        assert state.last_test_status == ""

    def test_non_test_command(self):
        state = HabitModeState()
        detect_test_result(state, "5 passed", "ls -la")
        assert state.last_test_status == ""

    def test_pytest_pass(self):
        state = HabitModeState()
        detect_test_result(state, "5 passed in 0.5s", "pytest tests/")
        assert state.last_test_status == "pass"

    def test_pytest_fail(self):
        state = HabitModeState()
        detect_test_result(state, "2 failed, 3 passed", "pytest tests/")
        assert state.last_test_status == "fail"

    def test_test_keyword_in_sig(self):
        state = HabitModeState()
        detect_test_result(state, "5 passed", "python -m test_foo")
        assert state.last_test_status == "pass"

    def test_empty_output(self):
        state = HabitModeState()
        detect_test_result(state, "", "pytest tests/")
        assert state.last_test_status == ""

    def test_none_output(self):
        state = HabitModeState()
        detect_test_result(state, None, "pytest tests/")  # type: ignore[arg-type]  # intentional: test None input
        assert state.last_test_status == ""

    def test_indeterminate_output_no_update(self):
        state = HabitModeState()
        state.last_test_status = "pass"
        detect_test_result(state, "collecting ...", "pytest tests/")
        # Indeterminate -> no update
        assert state.last_test_status == "pass"

    def test_case_insensitive_sig(self):
        state = HabitModeState()
        detect_test_result(state, "5 passed", "PYTEST tests/")
        assert state.last_test_status == "pass"


# ── _score_component ────────────────────────────────────────────────


class TestScoreComponent:
    # op="lt" (lower-is-better)
    def test_lt_full(self):
        assert _score_component(1.0, full=2.0, half=3.0, op="lt") == 1.0

    def test_lt_half(self):
        assert _score_component(2.5, full=2.0, half=3.0, op="lt") == 0.5

    def test_lt_zero(self):
        assert _score_component(4.0, full=2.0, half=3.0, op="lt") == 0.0

    def test_lt_at_full_boundary(self):
        # value == full -> not < full, so check half
        assert _score_component(2.0, full=2.0, half=3.0, op="lt") == 0.5

    def test_lt_at_half_boundary(self):
        # value == half -> not < half, so 0.0
        assert _score_component(3.0, full=2.0, half=3.0, op="lt") == 0.0

    # op="gt" (higher-is-better, strict >)
    def test_gt_full(self):
        assert _score_component(3.0, full=2.0, half=1.0, op="gt") == 1.0

    def test_gt_half(self):
        assert _score_component(1.5, full=2.0, half=1.0, op="gt") == 0.5

    def test_gt_zero(self):
        assert _score_component(0.5, full=2.0, half=1.0, op="gt") == 0.0

    def test_gt_at_full_boundary(self):
        # value == full -> not > full, check half
        assert _score_component(2.0, full=2.0, half=1.0, op="gt") == 0.5

    def test_gt_at_half_boundary(self):
        # value == half -> not > half, so 0.0
        assert _score_component(1.0, full=2.0, half=1.0, op="gt") == 0.0

    # op="gte" (higher-is-better, >= for ints)
    def test_gte_full(self):
        assert _score_component(3, full=3, half=2, op="gte") == 1.0

    def test_gte_above_full(self):
        assert _score_component(5, full=3, half=2, op="gte") == 1.0

    def test_gte_half(self):
        assert _score_component(2, full=3, half=2, op="gte") == 0.5

    def test_gte_zero(self):
        assert _score_component(1, full=3, half=2, op="gte") == 0.0


# ── compute_habit_score ─────────────────────────────────────────────


class TestComputeHabitScore:
    def test_default_state_score(self):
        state = HabitModeState()
        score = compute_habit_score(state)
        # Default signals: read_edit_ratio=0.0 (<2.0 -> 1.0*0.25=0.25)
        # execute_pct=0.0 (not >0.5 or >0.3 -> 0.0)
        # edit_streak=0 (not >=3 or >=2 -> 0.0)
        # sub_agent_freq=0.0 (<0.05 -> 1.0*0.10=0.10)
        # same_file_ratio=0.0 (not >0.6 or >0.4 -> 0.0)
        # inter_tool_gap_median=0.0 (skipped because not >0 -> 0.0)
        # declared=False -> 0.0
        # Total: 0.25 + 0.10 = 0.35
        assert score == pytest.approx(0.35)

    def test_declared_adds_weight(self):
        state = HabitModeState(declared=True)
        score = compute_habit_score(state)
        # Same as default + declared weight (0.10)
        assert score == pytest.approx(0.45)

    def test_high_execution_score(self):
        state = HabitModeState()
        state.signals.read_edit_ratio = 1.0  # <2.0 -> full (0.25)
        state.signals.execute_pct = 0.6  # >0.5 -> full (0.20)
        state.signals.edit_streak = 4  # >=3 -> full (0.15)
        state.signals.sub_agent_freq = 0.01  # <0.05 -> full (0.10)
        state.signals.same_file_ratio = 0.7  # >0.6 -> full (0.10)
        state.signals.inter_tool_gap_median = 2.0  # >0 and <3.0 -> full (0.10)
        state.declared = True  # declared -> 0.10
        score = compute_habit_score(state)
        assert score == pytest.approx(1.0)

    def test_score_capped_at_1(self):
        state = HabitModeState()
        state.signals.read_edit_ratio = 0.5
        state.signals.execute_pct = 0.9
        state.signals.edit_streak = 10
        state.signals.sub_agent_freq = 0.0
        state.signals.same_file_ratio = 0.9
        state.signals.inter_tool_gap_median = 1.0
        state.declared = True
        score = compute_habit_score(state)
        assert score <= 1.0

    def test_inter_tool_gap_zero_excluded(self):
        state = HabitModeState()
        state.signals.inter_tool_gap_median = 0.0
        score1 = compute_habit_score(state)
        state.signals.inter_tool_gap_median = 1.0  # <3.0 -> full
        score2 = compute_habit_score(state)
        assert score2 > score1


# ── update_mode ─────────────────────────────────────────────────────


class TestUpdateMode:
    def _make_high_score_state(self) -> HabitModeState:
        """Create a state that produces a high habit score."""
        state = HabitModeState()
        state.signals.read_edit_ratio = 1.0
        state.signals.execute_pct = 0.6
        state.signals.edit_streak = 4
        state.signals.sub_agent_freq = 0.01
        state.signals.same_file_ratio = 0.7
        state.signals.inter_tool_gap_median = 2.0
        state.declared = True
        return state

    def test_enter_after_sustain(self):
        state = self._make_high_score_state()
        state.active = False
        # Call update_mode sustain_calls times
        result = None
        for i in range(DEFAULT_SUSTAIN_CALLS):
            result = update_mode(state, event_counter=i)
        assert result == "enter"
        assert state.active is True

    def test_no_enter_before_sustain(self):
        state = self._make_high_score_state()
        state.active = False
        # Call fewer times than sustain_calls
        for i in range(DEFAULT_SUSTAIN_CALLS - 1):
            result = update_mode(state, event_counter=i)
            assert result is None
        assert state.active is False

    def test_exit_on_low_score(self):
        state = HabitModeState(active=True)
        # Default signals produce score ~0.35, below exit_score (0.40)
        result = update_mode(state, event_counter=10)
        assert result == "exit"
        assert state.active is False

    def test_stay_active_with_good_score(self):
        state = self._make_high_score_state()
        state.active = True
        result = update_mode(state, event_counter=10)
        assert result is None
        assert state.active is True

    def test_exit_on_user_message(self):
        state = self._make_high_score_state()
        state.active = True
        state.user_message_detected = True
        result = update_mode(state, event_counter=10)
        assert result == "exit"
        assert state.active is False
        assert state.user_message_detected is False

    def test_user_message_clears_when_not_active(self):
        state = HabitModeState()
        state.user_message_detected = True
        update_mode(state, event_counter=10)
        assert state.user_message_detected is False

    def test_sustain_counter_resets_on_low_score(self):
        state = HabitModeState()
        state.sustain_counter = 3
        # Default score is 0.35, below enter_score 0.70
        update_mode(state, event_counter=10)
        assert state.sustain_counter == 0

    def test_total_events_increments_when_active(self):
        state = self._make_high_score_state()
        state.active = True
        state.total_events_in_habit = 5
        update_mode(state, event_counter=10)
        assert state.total_events_in_habit == 6

    def test_custom_thresholds(self):
        state = HabitModeState()
        # Default score is ~0.35
        state.active = True
        # With very low exit_score, should stay active
        result = update_mode(state, event_counter=10, exit_score=0.1)
        assert result is None
        assert state.active is True

    def test_entered_at_event_set(self):
        state = self._make_high_score_state()
        state.active = False
        for i in range(DEFAULT_SUSTAIN_CALLS):
            update_mode(state, event_counter=100 + i)
        assert state.entered_at_event == 100 + DEFAULT_SUSTAIN_CALLS - 1

    def test_exit_resets_sustain_counter(self):
        state = HabitModeState(active=True)
        state.sustain_counter = 5
        update_mode(state, event_counter=10)  # Low score -> exit
        assert state.sustain_counter == 0


# ── _classify_user_message ──────────────────────────────────────────


class TestClassifyUserMessage:
    def test_empty_string(self):
        assert _classify_user_message("") == "continuation"

    def test_whitespace_only(self):
        assert _classify_user_message("   ") == "continuation"

    def test_yes(self):
        assert _classify_user_message("yes") == "continuation"

    def test_ok_with_exclamation(self):
        assert _classify_user_message("ok!") == "continuation"

    def test_continue_with_period(self):
        assert _classify_user_message("continue.") == "continuation"

    def test_go_ahead(self):
        assert _classify_user_message("go ahead") == "continuation"

    def test_confirmed(self):
        assert _classify_user_message("confirmed") == "continuation"

    def test_stop_directive(self):
        assert _classify_user_message("stop") == "directive"

    def test_actually_directive(self):
        assert _classify_user_message("actually, do something else") == "directive"

    def test_wait_directive(self):
        assert _classify_user_message("wait") == "directive"

    def test_scratch_that_directive(self):
        assert _classify_user_message("scratch that") == "directive"

    def test_long_message_is_directive(self):
        msg = "Please refactor the entire module to use async patterns instead"
        assert len(msg) > 50
        assert _classify_user_message(msg) == "directive"

    def test_multi_sentence_is_directive(self):
        assert _classify_user_message("Do this. Then that.") == "directive"

    # test_short_question_is_clarification removed (byte-identical to test_habit_mode.py)

    def test_short_non_question_is_directive(self):
        # Short, no continuation keyword, no ?, no directive keyword
        assert _classify_user_message("fix the bug") == "directive"

    def test_case_insensitive(self):
        assert _classify_user_message("YES") == "continuation"

    def test_proceed(self):
        assert _classify_user_message("proceed") == "continuation"

    def test_y_single_char(self):
        assert _classify_user_message("y") == "continuation"

    def test_instead_in_long_message(self):
        assert _classify_user_message("use X instead") == "directive"

    def test_never_mind(self):
        assert _classify_user_message("never mind") == "directive"


# ── signal_user_message ─────────────────────────────────────────────


class TestSignalUserMessage:
    def test_directive_collapses_active(self):
        state = HabitModeState(active=True, habit_score=0.8, declared=True, sustain_counter=5)
        result = signal_user_message(state, "stop everything")
        assert result == "directive"
        assert state.active is False
        assert state.declared is False
        assert state.habit_score == 0.0
        assert state.sustain_counter == 0
        assert state.user_message_detected is True

    def test_directive_when_not_active(self):
        state = HabitModeState(active=False, habit_score=0.5)
        result = signal_user_message(state, "stop")
        assert result == "directive"
        assert state.user_message_detected is True
        # Not active, so no collapse — active stays False
        assert state.active is False

    def test_continuation_no_effect(self):
        state = HabitModeState(active=True, habit_score=0.8)
        result = signal_user_message(state, "yes")
        assert result == "continuation"
        assert state.active is True
        assert state.habit_score == 0.8

    def test_clarification_decays_score(self):
        state = HabitModeState(habit_score=0.8)
        result = signal_user_message(state, "what file?")
        assert result == "clarification"
        assert state.habit_score == pytest.approx(0.65)

    def test_clarification_floor_at_zero(self):
        state = HabitModeState(habit_score=0.05)
        signal_user_message(state, "which one?")
        assert state.habit_score == 0.0

    def test_empty_message_is_continuation(self):
        state = HabitModeState(active=True, habit_score=0.8)
        result = signal_user_message(state, "")
        assert result == "continuation"
        assert state.habit_score == 0.8


# ── declare_mode ────────────────────────────────────────────────────


class TestDeclareMode:
    def test_declare_habit_when_inactive(self):
        state = HabitModeState()
        result = declare_mode(state, "habit", event_counter=42)
        assert result == "enter"
        assert state.active is True
        assert state.declared is True
        assert state.entered_at_event == 42
        assert state.total_events_in_habit == 0
        assert state.sustain_counter == DEFAULT_SUSTAIN_CALLS

    def test_declare_habit_when_already_active(self):
        state = HabitModeState(active=True)
        result = declare_mode(state, "habit", event_counter=42)
        assert result is None
        assert state.declared is True

    def test_declare_standard_when_active(self):
        state = HabitModeState(active=True, declared=True)
        result = declare_mode(state, "standard", event_counter=42)
        assert result == "exit"
        assert state.active is False
        assert state.declared is False
        assert state.sustain_counter == 0

    def test_declare_standard_when_inactive(self):
        state = HabitModeState(active=False)
        result = declare_mode(state, "standard", event_counter=42)
        assert result is None
        assert state.active is False

    def test_declare_unknown_mode(self):
        state = HabitModeState()
        result = declare_mode(state, "turbo", event_counter=42)
        assert result is None

    def test_declare_habit_computes_score(self):
        state = HabitModeState()
        declare_mode(state, "habit", event_counter=0)
        # declared=True adds 0.10 weight, plus defaults
        # Score should reflect declared=True
        assert state.habit_score > 0.0


# ── quick_intent ────────────────────────────────────────────────────


class TestQuickIntent:
    def test_inspect_tools(self):
        for tool in ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]:
            assert quick_intent(tool) == "inspect"

    def test_modify_tools(self):
        for tool in ["Write", "Edit", "MultiEdit", "NotebookEdit"]:
            assert quick_intent(tool) == "modify"

    def test_meta_tools(self):
        for tool in ["Task", "TodoWrite", "AskUserQuestion"]:
            assert quick_intent(tool) == "meta"

    def test_bash(self):
        assert quick_intent("Bash") == "execute"

    def test_unknown_tool(self):
        assert quick_intent("SomeRandomTool") == "unknown"

    def test_empty_string(self):
        assert quick_intent("") == "unknown"


# ── HabitSignals serialization ───────────────────────────────────────


class TestHabitSignalsSerialization:
    def test_roundtrip(self):
        sig = HabitSignals(
            read_edit_ratio=1.5,
            gather_pct=0.3,
            execute_pct=0.7,
            same_file_ratio=0.8,
            inter_tool_gap_median=2.1,
            sub_agent_freq=0.02,
            edit_streak=4,
            test_in_last_n=True,
        )
        d = sig.to_dict()
        restored = HabitSignals.from_dict(d)
        assert restored.read_edit_ratio == 1.5
        assert restored.execute_pct == 0.7
        assert restored.edit_streak == 4
        assert restored.test_in_last_n is True

    def test_from_empty_dict(self):
        sig = HabitSignals.from_dict({})
        assert sig.read_edit_ratio == 0.0
        assert sig.edit_streak == 0


# ── HabitModeState serialization ─────────────────────────────────────


class TestHabitModeStateSerialization:
    def test_roundtrip(self):
        state = HabitModeState(
            active=True,
            habit_score=0.75,
            sustain_counter=5,
            declared=True,
            active_files=["/a.py", "/b.py"],
            last_test_status="pass",
            compaction_count=2,
        )
        d = state.to_dict()
        restored = HabitModeState.from_dict(d)
        assert restored.active is True
        assert restored.habit_score == 0.75
        assert restored.declared is True
        assert restored.active_files == ["/a.py", "/b.py"]
        assert restored.compaction_count == 2

    def test_from_empty_dict(self):
        state = HabitModeState.from_dict({})
        assert state.active is False
        assert state.habit_score == 0.0

    def test_from_none(self):
        state = HabitModeState.from_dict(None)  # type: ignore[arg-type]  # intentional: test None guard
        assert state.active is False


# ── Session-backed persistence ───────────────────────────────────────


class TestSessionPersistence:
    def test_roundtrip(self):
        compass_dict: dict = {}
        state = HabitModeState(active=True, habit_score=0.75, compaction_count=3)
        save_habit_state(compass_dict, state)
        restored = load_habit_state(compass_dict)
        assert restored.active is True
        assert restored.habit_score == 0.75
        assert restored.compaction_count == 3

    def test_empty_dict_returns_fresh(self):
        state = load_habit_state({})
        assert state.active is False
        assert state.habit_score == 0.0


# ── Standalone file-backed persistence ───────────────────────────────


class TestStandalonePersistence:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState(active=True, habit_score=0.82, compaction_count=1)
        ring = [{"tool": "Edit", "ts": 1.0, "intent": "modify"}]
        save_habit_state_standalone("/fake/project", state, ring)
        loaded_state, loaded_ring = load_habit_state_standalone("/fake/project")
        assert loaded_state.active is True
        assert loaded_state.habit_score == 0.82
        assert len(loaded_ring) == 1

    def test_missing_file_returns_fresh(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        state, ring = load_habit_state_standalone("/nonexistent/project")
        assert state.active is False
        assert ring == []

    def test_corrupted_file_returns_fresh(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        path = _standalone_path("/corrupt/project")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json{{{")
        state, ring = load_habit_state_standalone("/corrupt/project")
        assert state.active is False
        assert ring == []


# ── Compaction Snapshot ──────────────────────────────────────────────


class TestBuildCompactionSnapshot:
    def test_basic_snapshot_structure(self):
        state = HabitModeState(
            active=True,
            habit_score=0.85,
            active_files=["/a.py", "/b.py"],
            last_test_status="pass",
        )
        snapshot = build_compaction_snapshot(state, "/project")
        assert "mode" in snapshot
        assert "active_context" in snapshot
        assert "tool_guidance" in snapshot
        assert "focus_directive" in snapshot
        assert snapshot["mode"]["active"] is True

    def test_snapshot_within_char_limit(self):
        state = HabitModeState(
            active=True,
            active_files=[f"/very/long/path/to/file_{i}.py" for i in range(20)],
        )
        snapshot = build_compaction_snapshot(
            state,
            "/project",
            theory_pack={"summary": "x" * 2000},
            last_lint_run={
                "blocking_count": 5,
                "warning_count": 10,
                "issues": [
                    {"file": f"/f{i}.py", "line": i, "kind": "E", "message": "m" * 100}
                    for i in range(10)
                ],
            },
            compass={"hypotheses": [{"claim": "c" * 100, "confidence": 0.8}] * 10},
        )
        serialized = json.dumps(snapshot, separators=(",", ":"))
        assert len(serialized) <= SNAPSHOT_MAX_CHARS

    def test_tool_injections_capped_at_4(self):
        state = HabitModeState(
            active=True,
            signals=HabitSignals(edit_streak=10, test_in_last_n=False),
        )
        snapshot = build_compaction_snapshot(
            state,
            "/project",
            last_lint_run={"blocking_count": 5, "issues": []},
            session_memory={"coherence_trajectory": ["systemic"]},
            compass={"approaches": [{"outcome": "failed"} for _ in range(5)]},
        )
        assert len(snapshot["tool_guidance"]) <= 4

    def test_focus_directive_includes_files(self):
        state = HabitModeState(
            active=True,
            active_files=["/a.py", "/b.py"],
            last_test_status="fail",
        )
        snapshot = build_compaction_snapshot(state, "/project")
        assert "/a.py" in snapshot["focus_directive"]
        assert "Test: fail" in snapshot["focus_directive"]


# ── Enforce Snapshot Cap ─────────────────────────────────────────────


class TestEnforceSnapshotCap:
    def test_under_cap_unchanged(self):
        snap = {"mode": {"active": True}}
        _enforce_snapshot_cap(snap)
        assert snap["mode"]["active"] is True


# ── Habit tools MCP — standalone persistence ─────────────────────────────


class _FakeHabitMCP:
    def tool(self):  # noqa: D401
        def _decorator(fn):
            return fn

        return _decorator


def _force_standalone_mode(monkeypatch) -> None:
    def _raise_session(*args, **kwargs):
        raise RuntimeError("session unavailable")

    monkeypatch.setattr(
        "lintgate.controlplane.session_memory.get_or_create_session",
        _raise_session,
    )


def _register_habit_tools(monkeypatch, habit_dir):
    from pathlib import Path

    from mcp_tools import habit_tools

    monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", habit_dir)
    _force_standalone_mode(monkeypatch)
    return habit_tools.register(
        _FakeHabitMCP(),
        {
            "_validate_project_root": lambda path: str(Path(path).resolve()),
        },
    )


def test_habit_status_standalone_loads_persisted_tracker(monkeypatch, tmp_path) -> None:
    from lintgate._habit_persist import save_habit_state_standalone
    from lintgate._habit_types import HabitModeState as HabitModeState2
    from lintgate.token_tracker import TokenTrackerState

    project = tmp_path / "proj"
    project.mkdir()
    tools = _register_habit_tools(monkeypatch, tmp_path / "habit_state")

    state = HabitModeState2(active=True, habit_score=0.81)
    ring = [{"tool": "Edit", "ts": 1.0, "intent": "modify", "sig": "a.py"}]
    tracker = TokenTrackerState(
        estimated_tokens_used=12345,
        tool_call_count=7,
        tool_calls_since_compact=7,
        lines_written=42,
    )
    save_habit_state_standalone(
        str(project),
        state,
        ring,
        tracker_dict=tracker.to_dict(),
    )

    payload = _load_tool_result(tools["habit_status"](path=str(project)))
    token_econ = payload["token_economics"]
    assert token_econ["estimated_tokens_used"] == 12345
    assert token_econ["tool_call_count"] == 7
    assert token_econ["lines_written"] == 42


def test_habit_configure_standalone_persists_overrides(monkeypatch, tmp_path) -> None:
    from lintgate._habit_persist import load_standalone_extras

    project = tmp_path / "proj"
    project.mkdir()
    tools = _register_habit_tools(monkeypatch, tmp_path / "habit_state")

    first = json.loads(
        tools["habit_configure"](
            path=str(project),
            enter_score=0.88,
            token_api_interval=21,
        )
    )
    assert first["status"] == "ok"

    second = json.loads(
        tools["habit_configure"](
            path=str(project),
            compact_threshold=0.55,
        )
    )
    assert second["status"] == "ok"

    extras = load_standalone_extras(str(project))
    overrides = extras.get("config_overrides", {})
    assert overrides["enter_score"] == 0.88
    assert overrides["token_api_interval"] == 21
    assert overrides["compact_threshold"] == 0.55


# ── Lightweight hook integration ─────────────────────────────────────


@dataclass
class _DummyConfig:
    habit_mode_enabled: bool = True
    habit_mode_auto_detect: bool = True
    habit_mode_compact_threshold: float = 0.40
    habit_mode_token_api_interval: int = 9999
    habit_mode_enter_score: float = 0.70
    habit_mode_exit_score: float = 0.40
    habit_mode_sustain_calls: int = 5
    session_memory: bool = False

    def channel_enabled(self, _name: str) -> bool:
        return False


class TestLightweightHookPath:
    def test_respects_auto_detect_disabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path / "habit_state")
        cp = _DummyConfig(habit_mode_auto_detect=False)
        project = tmp_path / "proj"
        project.mkdir()

        for i in range(8):
            _record_habit_event_lightweight(
                cp,
                str(project),
                "Edit",
                {"file_path": str(project / "a.py"), "new_string": f"line {i}\n"},
                "ok",
            )

        state, _ring = load_habit_state_standalone(str(project))
        assert state.active is False

    def test_non_test_bash_does_not_flip_test_status(self, monkeypatch, tmp_path):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path / "habit_state")
        cp = _DummyConfig()
        project = tmp_path / "proj"
        project.mkdir()

        _record_habit_event_lightweight(
            cp,
            str(project),
            "Bash",
            {"command": "git status"},
            "error: not a git repository",
        )

        state, _ring = load_habit_state_standalone(str(project))
        assert state.last_test_status == ""

    def test_auto_compacts_when_threshold_exceeded(self, monkeypatch, tmp_path):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path / "habit_state")
        cp = _DummyConfig(habit_mode_auto_detect=True)
        project = tmp_path / "proj"
        project.mkdir()

        state = HabitModeState(active=True, declared=True, habit_score=0.8)
        ring = [{"tool": "Edit", "ts": 1.0, "intent": "modify", "sig": "a.py"}]
        tracker = TokenTrackerState(
            estimated_tokens_used=190000,
            tool_call_count=25,
            tool_calls_since_compact=25,
            last_compact_tokens=0,
            context_window_size=200000,
        )
        save_habit_state_standalone(str(project), state, ring, tracker_dict=tracker.to_dict())

        _record_habit_event_lightweight(
            cp,
            str(project),
            "Edit",
            {"file_path": str(project / "a.py"), "new_string": "patched\n"},
            "ok",
        )

        updated_state, _updated_ring = load_habit_state_standalone(str(project))
        extras = load_standalone_extras(str(project))
        updated_tracker = TokenTrackerState.from_dict(extras.get("token_tracker", {}))
        assert updated_state.compaction_count >= 1
        assert updated_tracker.tool_calls_since_compact == 0
        assert updated_tracker.last_compact_tokens > 0
        assert isinstance(extras.get("habit_last_snapshot"), dict)


# ── Mutation-targeted: _detect_test_in_window (VALUE category) ────────


class TestDetectTestInWindowMutation:
    """Exact-value assertions for _detect_test_in_window (mutation-targeted)."""

    def test_empty_window_returns_false(self):
        assert _detect_test_in_window([]) is False

    def test_no_bash_events_returns_false(self):
        window = [{"tool": "Edit", "sig": "pytest"}, {"tool": "Read", "sig": "test"}]
        assert _detect_test_in_window(window) is False

    def test_bash_with_pytest_returns_true(self):
        window = [{"tool": "Bash", "sig": "python -m pytest tests/"}]
        assert _detect_test_in_window(window) is True

    def test_bash_with_test_returns_true(self):
        window = [{"tool": "Bash", "sig": "python -m test_runner"}]
        assert _detect_test_in_window(window) is True

    def test_bash_without_test_keyword_returns_false(self):
        window = [{"tool": "Bash", "sig": "git status"}]
        assert _detect_test_in_window(window) is False

    def test_case_insensitive_match(self):
        window = [{"tool": "Bash", "sig": "PYTEST --verbose"}]
        assert _detect_test_in_window(window) is True

    def test_mixed_events_finds_test(self):
        window = [
            {"tool": "Edit", "sig": "edit file"},
            {"tool": "Bash", "sig": "ruff check ."},
            {"tool": "Bash", "sig": "pytest -x"},
        ]
        assert _detect_test_in_window(window) is True

    def test_missing_sig_key(self):
        window = [{"tool": "Bash"}]
        assert _detect_test_in_window(window) is False

    def test_empty_sig(self):
        window = [{"tool": "Bash", "sig": ""}]
        assert _detect_test_in_window(window) is False


# ── Mutation-targeted: _score_component (VALUE category) ──────────────


class TestScoreComponentMutation:
    """Exact-value assertions for _score_component across all operators (mutation-targeted)."""

    # ── op="lt" (lower is better) ─────────────────────────────────

    def test_lt_below_full_returns_1(self):
        assert _score_component(1.0, full=5.0, half=10.0, op="lt") == 1.0

    def test_lt_at_full_returns_0_5(self):
        """Exactly at full threshold — NOT below, so falls to half check."""
        assert _score_component(5.0, full=5.0, half=10.0, op="lt") == 0.5

    def test_lt_between_full_and_half_returns_0_5(self):
        assert _score_component(7.0, full=5.0, half=10.0, op="lt") == 0.5

    def test_lt_at_half_returns_0(self):
        """Exactly at half threshold — NOT below half, so returns 0.0."""
        assert _score_component(10.0, full=5.0, half=10.0, op="lt") == 0.0

    def test_lt_above_half_returns_0(self):
        assert _score_component(15.0, full=5.0, half=10.0, op="lt") == 0.0

    # ── op="gte" (higher is better, allows equality) ──────────────

    def test_gte_at_full_returns_1(self):
        assert _score_component(5.0, full=5.0, half=3.0, op="gte") == 1.0

    def test_gte_above_full_returns_1(self):
        assert _score_component(10.0, full=5.0, half=3.0, op="gte") == 1.0

    def test_gte_at_half_returns_0_5(self):
        assert _score_component(3.0, full=5.0, half=3.0, op="gte") == 0.5

    def test_gte_between_half_and_full_returns_0_5(self):
        assert _score_component(4.0, full=5.0, half=3.0, op="gte") == 0.5

    def test_gte_below_half_returns_0(self):
        assert _score_component(2.0, full=5.0, half=3.0, op="gte") == 0.0

    # ── op="gt" (higher is better, strict inequality) ─────────────

    def test_gt_above_full_returns_1(self):
        assert _score_component(6.0, full=5.0, half=3.0, op="gt") == 1.0

    def test_gt_at_full_returns_0_5(self):
        """Exactly at full — NOT above, falls to half check."""
        assert _score_component(5.0, full=5.0, half=3.0, op="gt") == 0.5

    def test_gt_at_half_returns_0(self):
        """Exactly at half — NOT above half, returns 0.0."""
        assert _score_component(3.0, full=5.0, half=3.0, op="gt") == 0.0

    def test_gt_above_half_returns_0_5(self):
        assert _score_component(4.0, full=5.0, half=3.0, op="gt") == 0.5

    def test_gt_below_half_returns_0(self):
        assert _score_component(1.0, full=5.0, half=3.0, op="gt") == 0.0

    # ── Default op ────────────────────────────────────────────────

    def test_default_op_is_lt(self):
        assert _score_component(1.0, full=5.0, half=10.0) == 1.0
