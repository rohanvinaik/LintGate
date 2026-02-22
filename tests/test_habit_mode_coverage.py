"""Comprehensive tests for lintgate/habit_mode.py — targeting uncovered symbols."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lintgate.habit_mode import (
    DEFAULT_ENTER_SCORE,
    DEFAULT_EXIT_SCORE,
    DEFAULT_SUSTAIN_CALLS,
    SNAPSHOT_MAX_CHARS,
    WINDOW_SIZE,
    MAX_ACTIVE_FILES,
    HabitModeState,
    HabitSignals,
    _add_to_mru,
    _build_tool_injections,
    _classify_user_message,
    _compute_edit_streak,
    _compute_inter_tool_gap_median,
    _compute_same_file_ratio,
    _detect_test_in_window,
    _enforce_snapshot_cap,
    build_compaction_snapshot,
    compute_habit_score,
    declare_mode,
    detect_test_result,
    load_habit_state,
    load_habit_state_standalone,
    load_standalone_extras,
    quick_intent,
    save_habit_state,
    save_habit_state_standalone,
    signal_user_message,
    track_active_files,
    update_mode,
    update_signals,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_event(
    tool: str = "Read",
    ts: float = 1.0,
    intent: str = "inspect",
    sig: str = "",
) -> dict[str, Any]:
    """Build a minimal action event dict."""
    return {"tool": tool, "ts": ts, "intent": intent, "sig": sig}


def _make_high_habit_state() -> HabitModeState:
    """Return a HabitModeState whose signals will yield a high habit score."""
    st = HabitModeState()
    sig = st.signals
    sig.read_edit_ratio = 1.0       # < 2.0 -> full weight
    sig.execute_pct = 0.6           # > 0.5 -> full weight
    sig.edit_streak = 4             # >= 3 -> full weight
    sig.sub_agent_freq = 0.0        # < 0.05 -> full weight
    sig.inter_tool_gap_median = 2.0 # > 0 and < 3.0 -> full weight
    sig.same_file_ratio = 0.7       # > 0.6 -> full weight
    st.declared = True              # -> full weight
    return st


# ── 1. HabitSignals to_dict / from_dict round-trip ───────────────────


class TestHabitSignalsRoundTrip:
    def test_default_round_trip(self) -> None:
        sig = HabitSignals()
        d = sig.to_dict()
        restored = HabitSignals.from_dict(d)
        assert restored.read_edit_ratio == 0.0
        assert restored.edit_streak == 0
        assert restored.test_in_last_n is False

    def test_populated_round_trip(self) -> None:
        sig = HabitSignals(
            read_edit_ratio=2.5,
            gather_pct=0.33,
            execute_pct=0.67,
            same_file_ratio=0.45,
            inter_tool_gap_median=1.23,
            sub_agent_freq=0.12,
            edit_streak=3,
            test_in_last_n=True,
        )
        d = sig.to_dict()
        restored = HabitSignals.from_dict(d)
        assert restored.read_edit_ratio == pytest.approx(2.5, abs=0.01)
        assert restored.gather_pct == pytest.approx(0.33, abs=0.01)
        assert restored.edit_streak == 3
        assert restored.test_in_last_n is True

    def test_from_dict_empty(self) -> None:
        restored = HabitSignals.from_dict({})
        assert restored.read_edit_ratio == 0.0
        assert restored.edit_streak == 0

    def test_to_dict_rounds_floats(self) -> None:
        sig = HabitSignals(read_edit_ratio=1.23456789)
        d = sig.to_dict()
        assert d["read_edit_ratio"] == 1.23


# ── 2. HabitModeState to_dict / from_dict round-trip ─────────────────


class TestHabitModeStateRoundTrip:
    def test_default_round_trip(self) -> None:
        st = HabitModeState()
        d = st.to_dict()
        restored = HabitModeState.from_dict(d)
        assert restored.active is False
        assert restored.habit_score == 0.0
        assert restored.active_files == []
        assert restored.signals.edit_streak == 0

    def test_populated_round_trip(self) -> None:
        st = HabitModeState(
            active=True,
            habit_score=0.85,
            sustain_counter=5,
            declared=True,
            active_files=["a.py", "b.py"],
            last_test_status="pass",
            compaction_count=2,
            last_compaction_event=100,
            entered_at_event=50,
            total_events_in_habit=30,
            user_message_detected=True,
        )
        d = st.to_dict()
        restored = HabitModeState.from_dict(d)
        assert restored.active is True
        assert restored.habit_score == pytest.approx(0.85, abs=0.001)
        assert restored.active_files == ["a.py", "b.py"]
        assert restored.declared is True
        assert restored.user_message_detected is True

    def test_from_dict_none(self) -> None:
        restored = HabitModeState.from_dict(None)  # type: ignore[arg-type]
        assert restored.active is False

    def test_from_dict_empty(self) -> None:
        restored = HabitModeState.from_dict({})
        assert restored.active is False


# ── 3. _compute_same_file_ratio ──────────────────────────────────────


class TestComputeSameFileRatio:
    def test_empty_window(self) -> None:
        assert _compute_same_file_ratio([]) == 0.0

    def test_all_unique_files(self) -> None:
        window = [
            _make_event(tool="Read", sig="a.py"),
            _make_event(tool="Edit", sig="b.py"),
            _make_event(tool="Write", sig="c.py"),
        ]
        assert _compute_same_file_ratio(window) == 0.0

    def test_all_same_file(self) -> None:
        window = [
            _make_event(tool="Read", sig="a.py"),
            _make_event(tool="Edit", sig="a.py"),
            _make_event(tool="Write", sig="a.py"),
        ]
        # 3 file ops, first sees a.py fresh, second and third are repeats => 2/3
        assert _compute_same_file_ratio(window) == pytest.approx(2 / 3, abs=0.01)

    def test_non_file_tools_ignored(self) -> None:
        window = [
            _make_event(tool="Bash", sig="pytest"),
            _make_event(tool="Task", sig="task1"),
        ]
        assert _compute_same_file_ratio(window) == 0.0

    def test_no_sig_skipped(self) -> None:
        window = [
            _make_event(tool="Read", sig=""),
            _make_event(tool="Edit", sig=""),
        ]
        # file_ops=2 but sig empty, so repeat_ops stays 0
        assert _compute_same_file_ratio(window) == 0.0


# ── 4. _compute_inter_tool_gap_median ────────────────────────────────


class TestComputeInterToolGapMedian:
    def test_fewer_than_two_timestamps(self) -> None:
        assert _compute_inter_tool_gap_median([]) == 0.0
        assert _compute_inter_tool_gap_median([_make_event(ts=1.0)]) == 0.0

    def test_two_timestamps(self) -> None:
        window = [_make_event(ts=1.0), _make_event(ts=4.0)]
        assert _compute_inter_tool_gap_median(window) == pytest.approx(3.0)

    def test_multiple_gaps(self) -> None:
        window = [
            _make_event(ts=1.0),
            _make_event(ts=2.0),
            _make_event(ts=5.0),
        ]
        # Gaps: [1.0, 3.0] -> median 2.0
        assert _compute_inter_tool_gap_median(window) == pytest.approx(2.0)

    def test_zero_ts_filtered(self) -> None:
        window = [
            _make_event(ts=0),  # filtered because ts=0 is falsy
            _make_event(ts=1.0),
            _make_event(ts=3.0),
        ]
        assert _compute_inter_tool_gap_median(window) == pytest.approx(2.0)

    def test_more_than_ten_gaps(self) -> None:
        # Build 15 events with 1s gaps -> 14 gaps -> last 10 used -> median 1.0
        window = [_make_event(ts=float(i)) for i in range(1, 16)]
        assert _compute_inter_tool_gap_median(window) == pytest.approx(1.0)


# ── 5. _compute_edit_streak ──────────────────────────────────────────


class TestComputeEditStreak:
    def test_no_edits(self) -> None:
        window = [_make_event(tool="Read"), _make_event(tool="Bash")]
        assert _compute_edit_streak(window) == 0

    def test_consecutive_edits_at_end(self) -> None:
        window = [
            _make_event(tool="Read"),
            _make_event(tool="Edit"),
            _make_event(tool="Write"),
            _make_event(tool="MultiEdit"),
        ]
        assert _compute_edit_streak(window) == 3

    def test_mixed_ending(self) -> None:
        window = [
            _make_event(tool="Edit"),
            _make_event(tool="Read"),
            _make_event(tool="Edit"),
        ]
        assert _compute_edit_streak(window) == 1

    def test_empty_window(self) -> None:
        assert _compute_edit_streak([]) == 0

    def test_all_notebook_edits(self) -> None:
        window = [_make_event(tool="NotebookEdit") for _ in range(4)]
        assert _compute_edit_streak(window) == 4


# ── 6. _detect_test_in_window ────────────────────────────────────────


class TestDetectTestInWindow:
    def test_pytest_command(self) -> None:
        window = [_make_event(tool="Bash", sig="pytest tests/")]
        assert _detect_test_in_window(window) is True

    def test_test_keyword(self) -> None:
        window = [_make_event(tool="Bash", sig="python -m test")]
        assert _detect_test_in_window(window) is True

    def test_no_test_command(self) -> None:
        window = [_make_event(tool="Bash", sig="ls -la")]
        assert _detect_test_in_window(window) is False

    def test_non_bash_tool(self) -> None:
        window = [_make_event(tool="Read", sig="pytest")]
        assert _detect_test_in_window(window) is False

    def test_empty_window(self) -> None:
        assert _detect_test_in_window([]) is False


# ── 7. update_signals ────────────────────────────────────────────────


class TestUpdateSignals:
    def test_empty_history(self) -> None:
        st = HabitModeState()
        update_signals(st, [])
        assert st.signals.read_edit_ratio == 0.0

    def test_basic_computation(self) -> None:
        st = HabitModeState()
        history = [
            _make_event(tool="Read", ts=1.0, intent="inspect", sig="a.py"),
            _make_event(tool="Edit", ts=2.0, intent="modify", sig="a.py"),
            _make_event(tool="Edit", ts=3.0, intent="modify", sig="a.py"),
        ]
        update_signals(st, history)
        # read=1, edit=2 => ratio=0.5
        assert st.signals.read_edit_ratio == pytest.approx(0.5)
        # 1 gather, 2 execute
        assert st.signals.gather_pct == pytest.approx(1 / 3, abs=0.01)
        assert st.signals.execute_pct == pytest.approx(2 / 3, abs=0.01)

    def test_window_respects_size(self) -> None:
        st = HabitModeState()
        history = [_make_event(tool="Read", ts=float(i), intent="inspect") for i in range(1, 40)]
        update_signals(st, history)
        # Should only use last WINDOW_SIZE=20 entries
        assert st.signals.gather_pct == pytest.approx(1.0)

    def test_task_subagent_frequency(self) -> None:
        st = HabitModeState()
        history = [
            _make_event(tool="Task", ts=1.0, intent="meta"),
            _make_event(tool="Read", ts=2.0, intent="inspect"),
        ]
        update_signals(st, history)
        assert st.signals.sub_agent_freq == pytest.approx(0.5)


# ── 8. track_active_files ────────────────────────────────────────────


class TestTrackActiveFiles:
    def test_file_path_key(self) -> None:
        st = HabitModeState()
        track_active_files(st, "Read", {"file_path": "/foo/bar.py"})
        assert st.active_files == ["/foo/bar.py"]

    def test_path_key(self) -> None:
        st = HabitModeState()
        track_active_files(st, "Grep", {"path": "/src/"})
        assert st.active_files == ["/src/"]

    def test_files_list(self) -> None:
        st = HabitModeState()
        track_active_files(st, "lint_files", {"files": ["a.py", "b.py", "c.py"]})
        # MRU order: last added is first
        assert st.active_files[0] == "c.py"
        assert len(st.active_files) == 3

    def test_string_input_ignored(self) -> None:
        st = HabitModeState()
        track_active_files(st, "Bash", "echo hello")
        assert st.active_files == []

    def test_no_path_key(self) -> None:
        st = HabitModeState()
        track_active_files(st, "Bash", {"command": "echo hello"})
        assert st.active_files == []

    def test_files_list_caps_at_five(self) -> None:
        st = HabitModeState()
        track_active_files(st, "lint_files", {"files": [f"{i}.py" for i in range(10)]})
        # Only first 5 extracted from files list
        assert len(st.active_files) == 5


# ── 9. _add_to_mru ──────────────────────────────────────────────────


class TestAddToMru:
    def test_new_file(self) -> None:
        files: list[str] = ["a.py"]
        _add_to_mru(files, "b.py")
        assert files == ["b.py", "a.py"]

    def test_existing_file_moves_to_front(self) -> None:
        files = ["a.py", "b.py", "c.py"]
        _add_to_mru(files, "c.py")
        assert files[0] == "c.py"
        assert len(files) == 3

    def test_exceeding_max_active_files(self) -> None:
        files = [f"{i}.py" for i in range(MAX_ACTIVE_FILES)]
        _add_to_mru(files, "new.py")
        assert files[0] == "new.py"
        assert len(files) == MAX_ACTIVE_FILES


# ── 10. detect_test_result ───────────────────────────────────────────


class TestDetectTestResult:
    def test_pass_output(self) -> None:
        st = HabitModeState()
        detect_test_result(st, "5 passed in 1.23s", "pytest tests/")
        assert st.last_test_status == "pass"

    def test_fail_output(self) -> None:
        st = HabitModeState()
        detect_test_result(st, "2 failed, 3 passed", "pytest tests/")
        assert st.last_test_status == "fail"

    def test_error_output(self) -> None:
        st = HabitModeState()
        detect_test_result(st, "ERROR collecting tests", "pytest tests/")
        assert st.last_test_status == "fail"

    def test_non_test_command(self) -> None:
        st = HabitModeState()
        detect_test_result(st, "5 passed", "ls -la")
        assert st.last_test_status == ""

    def test_empty_command_sig(self) -> None:
        st = HabitModeState()
        detect_test_result(st, "5 passed", "")
        assert st.last_test_status == ""

    def test_empty_output(self) -> None:
        st = HabitModeState()
        detect_test_result(st, "", "pytest tests/")
        assert st.last_test_status == ""

    def test_none_output(self) -> None:
        st = HabitModeState()
        detect_test_result(st, None, "pytest tests/")  # type: ignore[arg-type]
        assert st.last_test_status == ""


# ── 11. compute_habit_score ──────────────────────────────────────────


class TestComputeHabitScore:
    def test_all_zeros(self) -> None:
        st = HabitModeState()
        score = compute_habit_score(st)
        # read_edit_ratio=0 < 2 -> 0.25
        # execute_pct=0 -> 0
        # edit_streak=0 -> 0
        # sub_agent_freq=0 < 0.05 -> 0.10
        # inter_tool_gap_median=0 -> not > 0 -> 0
        # same_file_ratio=0 -> 0
        # declared=False -> 0
        # Total = 0.25 + 0.10 = 0.35
        assert score == pytest.approx(0.35, abs=0.01)

    def test_all_maxed(self) -> None:
        st = _make_high_habit_state()
        score = compute_habit_score(st)
        assert score == pytest.approx(1.0)

    def test_half_contributions(self) -> None:
        st = HabitModeState()
        sig = st.signals
        sig.read_edit_ratio = 2.5   # half (0.25*0.5)
        sig.execute_pct = 0.4       # half (0.20*0.5)
        sig.edit_streak = 2         # half (0.15*0.5)
        sig.sub_agent_freq = 0.10   # half (0.10*0.5)
        sig.inter_tool_gap_median = 4.0  # half (0.10*0.5)
        sig.same_file_ratio = 0.5   # half (0.10*0.5)
        score = compute_habit_score(st)
        expected = 0.5 * (0.25 + 0.20 + 0.15 + 0.10 + 0.10 + 0.10)
        assert score == pytest.approx(expected, abs=0.01)

    def test_capped_at_one(self) -> None:
        st = _make_high_habit_state()
        # Even with declared=True + all max, should be capped at 1.0
        assert compute_habit_score(st) <= 1.0

    def test_read_edit_ratio_above_three(self) -> None:
        st = HabitModeState()
        st.signals.read_edit_ratio = 5.0
        score = compute_habit_score(st)
        # read_edit_ratio >= 3 -> 0 contribution, only sub_agent=0.10
        assert score == pytest.approx(0.10, abs=0.01)


# ── 12. update_mode ─────────────────────────────────────────────────


class TestUpdateMode:
    def test_enter_after_sustain(self) -> None:
        st = _make_high_habit_state()
        st.active = False
        st.sustain_counter = DEFAULT_SUSTAIN_CALLS - 1
        result = update_mode(st, event_counter=50)
        assert result == "enter"
        assert st.active is True
        assert st.entered_at_event == 50

    def test_sustain_increments_without_enter(self) -> None:
        st = _make_high_habit_state()
        st.active = False
        st.sustain_counter = 0
        result = update_mode(st, event_counter=50)
        assert result is None
        assert st.sustain_counter == 1

    def test_exit_below_threshold(self) -> None:
        st = HabitModeState(active=True)
        # Default signals yield score=0.35 which < DEFAULT_EXIT_SCORE=0.40
        result = update_mode(st, event_counter=100)
        assert result == "exit"
        assert st.active is False

    def test_sustain_in_active_mode(self) -> None:
        st = _make_high_habit_state()
        st.active = True
        initial_events = st.total_events_in_habit
        result = update_mode(st, event_counter=100)
        assert result is None
        assert st.total_events_in_habit == initial_events + 1

    def test_user_message_override_exit(self) -> None:
        st = _make_high_habit_state()
        st.active = True
        st.user_message_detected = True
        result = update_mode(st, event_counter=100)
        assert result == "exit"
        assert st.active is False
        assert st.declared is False
        assert st.user_message_detected is False

    def test_user_message_cleared_when_not_active(self) -> None:
        st = HabitModeState(user_message_detected=True)
        update_mode(st, event_counter=10)
        assert st.user_message_detected is False

    def test_sustain_counter_reset_below_enter_score(self) -> None:
        st = HabitModeState()
        st.sustain_counter = 3
        # Default signals score ~0.35 < DEFAULT_ENTER_SCORE
        update_mode(st, event_counter=10)
        assert st.sustain_counter == 0


# ── 13. _classify_user_message ───────────────────────────────────────


class TestClassifyUserMessage:
    def test_directive_keywords(self) -> None:
        assert _classify_user_message("stop") == "directive"
        assert _classify_user_message("Actually, do it differently") == "directive"
        assert _classify_user_message("never mind") == "directive"

    def test_continuation_keywords(self) -> None:
        assert _classify_user_message("yes") == "continuation"
        assert _classify_user_message("ok") == "continuation"
        assert _classify_user_message("continue") == "continuation"
        assert _classify_user_message("go ahead") == "continuation"
        assert _classify_user_message("y") == "continuation"

    def test_continuation_with_punctuation(self) -> None:
        assert _classify_user_message("yes!") == "continuation"
        assert _classify_user_message("ok.") == "continuation"

    def test_clarification_question(self) -> None:
        assert _classify_user_message("which file?") == "clarification"
        assert _classify_user_message("how?") == "clarification"

    def test_empty_string(self) -> None:
        assert _classify_user_message("") == "continuation"

    def test_long_message_directive(self) -> None:
        msg = "x" * 51
        assert _classify_user_message(msg) == "directive"

    def test_multi_sentence_directive(self) -> None:
        assert _classify_user_message("Do this. Then that.") == "directive"

    def test_short_ambiguous_defaults_to_directive(self) -> None:
        assert _classify_user_message("fix it now") == "directive"


# ── 14. signal_user_message ──────────────────────────────────────────


class TestSignalUserMessage:
    def test_directive_collapses_active(self) -> None:
        st = _make_high_habit_state()
        st.active = True
        st.habit_score = 0.9
        result = signal_user_message(st, "stop everything")
        assert result == "directive"
        assert st.active is False
        assert st.habit_score == 0.0
        assert st.declared is False
        assert st.user_message_detected is True

    def test_directive_when_inactive(self) -> None:
        st = HabitModeState(habit_score=0.5)
        result = signal_user_message(st, "actually do something else")
        assert result == "directive"
        assert st.user_message_detected is True
        # Not active, so active stays False
        assert st.active is False

    def test_continuation_noop(self) -> None:
        st = HabitModeState(active=True, habit_score=0.8)
        result = signal_user_message(st, "yes")
        assert result == "continuation"
        assert st.habit_score == 0.8
        assert st.active is True

    def test_clarification_decays_score(self) -> None:
        st = HabitModeState(habit_score=0.5)
        result = signal_user_message(st, "which file?")
        assert result == "clarification"
        assert st.habit_score == pytest.approx(0.35, abs=0.01)

    def test_clarification_does_not_go_below_zero(self) -> None:
        st = HabitModeState(habit_score=0.05)
        signal_user_message(st, "how?")
        assert st.habit_score == 0.0


# ── 15. declare_mode ─────────────────────────────────────────────────


class TestDeclareMode:
    def test_declare_habit_entry(self) -> None:
        st = HabitModeState()
        result = declare_mode(st, "habit", event_counter=10)
        assert result == "enter"
        assert st.active is True
        assert st.declared is True
        assert st.entered_at_event == 10
        assert st.sustain_counter == DEFAULT_SUSTAIN_CALLS

    def test_declare_habit_when_already_active(self) -> None:
        st = HabitModeState(active=True)
        result = declare_mode(st, "habit", event_counter=20)
        assert result is None
        assert st.declared is True

    def test_declare_standard_exit(self) -> None:
        st = HabitModeState(active=True, declared=True)
        result = declare_mode(st, "standard", event_counter=30)
        assert result == "exit"
        assert st.active is False
        assert st.declared is False
        assert st.sustain_counter == 0

    def test_declare_standard_when_inactive(self) -> None:
        st = HabitModeState()
        result = declare_mode(st, "standard", event_counter=30)
        assert result is None

    def test_declare_unknown_mode(self) -> None:
        st = HabitModeState()
        result = declare_mode(st, "unknown_mode", event_counter=30)
        assert result is None


# ── 16. quick_intent ─────────────────────────────────────────────────


class TestQuickIntent:
    def test_inspect_tools(self) -> None:
        assert quick_intent("Read") == "inspect"
        assert quick_intent("Grep") == "inspect"
        assert quick_intent("Glob") == "inspect"
        assert quick_intent("WebFetch") == "inspect"
        assert quick_intent("WebSearch") == "inspect"

    def test_modify_tools(self) -> None:
        assert quick_intent("Edit") == "modify"
        assert quick_intent("Write") == "modify"
        assert quick_intent("MultiEdit") == "modify"
        assert quick_intent("NotebookEdit") == "modify"

    def test_meta_tools(self) -> None:
        assert quick_intent("Task") == "meta"
        assert quick_intent("TodoWrite") == "meta"
        assert quick_intent("AskUserQuestion") == "meta"

    def test_bash(self) -> None:
        assert quick_intent("Bash") == "execute"

    def test_unknown(self) -> None:
        assert quick_intent("SomeRandomTool") == "unknown"
        assert quick_intent("") == "unknown"


# ── 17. build_compaction_snapshot ────────────────────────────────────


class TestBuildCompactionSnapshot:
    def test_basic_snapshot(self) -> None:
        st = HabitModeState(
            active=True,
            habit_score=0.8,
            declared=True,
            active_files=["a.py", "b.py"],
            last_test_status="pass",
            compaction_count=1,
        )
        snap = build_compaction_snapshot(st, "/project")
        assert snap["mode"]["active"] is True
        assert snap["mode"]["compaction_number"] == 2  # count + 1
        assert snap["active_context"]["files"] == ["a.py", "b.py"]
        assert snap["active_context"]["last_test_status"] == "pass"
        assert snap["theory_digest"] is None
        assert snap["lint_state"] is None
        assert "focus_directive" in snap

    def test_with_theory_pack(self) -> None:
        st = HabitModeState()
        theory = {"core_theory": "test-driven"}
        snap = build_compaction_snapshot(st, "/project", theory_pack=theory)
        assert snap["theory_digest"] == theory

    def test_with_lint_run(self) -> None:
        st = HabitModeState()
        lint_run = {
            "blocking_count": 2,
            "warning_count": 5,
            "issues": [
                {"file": "a.py", "line": 10, "kind": "E501", "message": "line too long"},
            ],
        }
        snap = build_compaction_snapshot(st, "/project", last_lint_run=lint_run)
        assert snap["lint_state"]["blocking_count"] == 2
        assert len(snap["lint_state"]["issues"]) == 1

    def test_with_compass(self) -> None:
        st = HabitModeState()
        compass = {
            "hypotheses": [
                {"claim": "test coverage matters", "confidence": 0.9},
                {"claim": "lint is good", "confidence": 0.5},
            ],
            "error_memory": {
                "SyntaxError": {"count": 3},
                "ImportError": {"count": 1},
            },
            "coverage": {"prediction_recall": 0.75},
        }
        snap = build_compaction_snapshot(st, "/project", compass=compass)
        traj = snap["behavioral_trajectory"]
        assert len(traj["top_constraints"]) == 2
        assert traj["top_constraints"][0]["confidence"] == 0.9
        assert traj["prediction_recall"] == 0.75

    def test_with_session_memory(self) -> None:
        st = HabitModeState()
        session = {
            "snapshots": [
                {"coherence_state": "isolated", "blocking_count": 1, "finding_count": 2},
                {"coherence_state": "systemic", "blocking_count": 0, "finding_count": 0},
            ],
            "coherence_trajectory": ["isolated", "converging", "systemic"],
        }
        snap = build_compaction_snapshot(st, "/project", session_memory=session)
        assert len(snap["session_history"]) == 2
        assert snap["coherence_trajectory"] == ["isolated", "converging", "systemic"]

    def test_with_token_estimate(self) -> None:
        st = HabitModeState()
        tokens = {"estimated_tokens_used": 5000, "tool_call_count": 50, "lines_written": 200}
        snap = build_compaction_snapshot(st, "/project", token_estimate=tokens)
        assert snap["token_state"]["estimated_used"] == 5000

    def test_active_files_truncated_to_basenames(self) -> None:
        st = HabitModeState()
        # Create files with very long paths that exceed 400 total chars
        st.active_files = [f"/very/long/path/{'x' * 50}/{i}.py" for i in range(10)]
        snap = build_compaction_snapshot(st, "/project")
        # Should be truncated to basenames
        for f in snap["active_context"]["files"]:
            assert "/" not in f

    def test_focus_directive_format(self) -> None:
        st = HabitModeState(active_files=["a.py", "b.py"], last_test_status="fail")
        snap = build_compaction_snapshot(st, "/project")
        assert "a.py" in snap["focus_directive"]
        assert "Test: fail." in snap["focus_directive"]


# ── 18. _enforce_snapshot_cap ────────────────────────────────────────


class TestEnforceSnapshotCap:
    def test_under_cap_unchanged(self) -> None:
        snap = {"mode": {"active": True}, "active_context": {"files": []}}
        original = json.dumps(snap, separators=(",", ":"))
        _enforce_snapshot_cap(snap)
        assert json.dumps(snap, separators=(",", ":")) == original

    def test_over_cap_truncates_low_priority(self) -> None:
        # Build a snapshot that exceeds SNAPSHOT_MAX_CHARS
        huge_data = "x" * (SNAPSHOT_MAX_CHARS + 5000)
        snap: dict[str, Any] = {
            "mode": {"active": True},
            "session_history": huge_data,
            "recurring_issues": "some issues",
            "behavioral_trajectory": "some trajectory",
            "lint_state": "some lint",
            "coherence_trajectory": "some coherence",
        }
        _enforce_snapshot_cap(snap)
        # session_history should be truncated first (lowest priority)
        assert snap["session_history"] is None

    def test_multiple_sections_truncated(self) -> None:
        # Each section huge enough that all low-prio need truncation
        chunk = "y" * 3000
        snap: dict[str, Any] = {
            "mode": {"active": True},
            "session_history": chunk,
            "recurring_issues": chunk,
            "behavioral_trajectory": chunk,
            "lint_state": chunk,
            "coherence_trajectory": chunk,
        }
        _enforce_snapshot_cap(snap)
        # At least session_history should be None
        assert snap["session_history"] is None


# ── 19. _build_tool_injections ───────────────────────────────────────


class TestBuildToolInjections:
    def test_edit_streak_no_test(self) -> None:
        st = HabitModeState()
        st.signals.edit_streak = 6
        st.signals.test_in_last_n = False
        injections = _build_tool_injections(st, None, None, None)
        tool_names = [inj["tool"] for inj in injections]
        assert "prediction_register" in tool_names

    def test_blocking_lint(self) -> None:
        st = HabitModeState()
        lint_run = {"blocking_count": 5}
        injections = _build_tool_injections(st, None, lint_run, None)
        tool_names = [inj["tool"] for inj in injections]
        assert "lint_fix" in tool_names

    def test_systemic_coherence(self) -> None:
        st = HabitModeState()
        session = {"coherence_trajectory": ["isolated", "systemic"]}
        injections = _build_tool_injections(st, None, None, session)
        tool_names = [inj["tool"] for inj in injections]
        assert "controlplane_run" in tool_names

    def test_failed_approaches(self) -> None:
        st = HabitModeState()
        compass = {
            "approaches": [
                {"outcome": "failed"},
                {"outcome": "failed"},
                {"outcome": "failed"},
            ]
        }
        injections = _build_tool_injections(st, compass, None, None)
        tool_names = [inj["tool"] for inj in injections]
        assert "constraint_check" in tool_names

    def test_always_includes_habit_status(self) -> None:
        st = HabitModeState()
        injections = _build_tool_injections(st, None, None, None)
        tool_names = [inj["tool"] for inj in injections]
        assert "habit_status" in tool_names

    def test_capped_at_four(self) -> None:
        st = HabitModeState()
        st.signals.edit_streak = 6
        st.signals.test_in_last_n = False
        lint_run = {"blocking_count": 5}
        session = {"coherence_trajectory": ["systemic"]}
        compass = {"approaches": [{"outcome": "failed"}] * 5}
        injections = _build_tool_injections(st, compass, lint_run, session)
        assert len(injections) <= 4

    def test_sorted_by_priority(self) -> None:
        st = HabitModeState()
        st.signals.edit_streak = 6
        st.signals.test_in_last_n = False
        injections = _build_tool_injections(st, None, None, None)
        priorities = [inj["priority"] for inj in injections]
        assert priorities == sorted(priorities)


# ── 20. load_habit_state / save_habit_state ──────────────────────────


class TestLoadSaveHabitState:
    def test_round_trip(self) -> None:
        st = HabitModeState(
            active=True,
            habit_score=0.75,
            active_files=["x.py"],
            last_test_status="pass",
        )
        d: dict[str, Any] = {}
        save_habit_state(d, st)
        loaded = load_habit_state(d)
        assert loaded.active is True
        assert loaded.habit_score == pytest.approx(0.75, abs=0.001)
        assert loaded.active_files == ["x.py"]

    def test_load_missing_key(self) -> None:
        loaded = load_habit_state({})
        assert loaded.active is False

    def test_overwrite(self) -> None:
        d: dict[str, Any] = {}
        save_habit_state(d, HabitModeState(habit_score=0.5))
        save_habit_state(d, HabitModeState(habit_score=0.9))
        loaded = load_habit_state(d)
        assert loaded.habit_score == pytest.approx(0.9, abs=0.001)


# ── 21. Standalone load/save ─────────────────────────────────────────


class TestStandaloneLoadSave:
    def test_load_missing_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state, ring = load_habit_state_standalone("/nonexistent/project")
        assert state.active is False
        assert ring == []

    def test_save_and_load_round_trip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        project = "/my/project"
        st = HabitModeState(active=True, habit_score=0.8, active_files=["f.py"])
        ring = [_make_event(tool="Edit", ts=1.0, intent="modify")]
        save_habit_state_standalone(project, st, ring)

        loaded_state, loaded_ring = load_habit_state_standalone(project)
        assert loaded_state.active is True
        assert loaded_state.habit_score == pytest.approx(0.8, abs=0.001)
        assert len(loaded_ring) == 1

    def test_save_with_optional_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        project = "/my/project2"
        st = HabitModeState()
        save_habit_state_standalone(
            project,
            st,
            [],
            tracker_dict={"tokens": 100},
            config_overrides={"enter_score": 0.8},
            last_snapshot={"mode": {"active": True}},
            scheduler_dict={"pending": []},
            signal_fire_counts={"s1": 3},
        )
        extras = load_standalone_extras(project)
        assert extras["token_tracker"] == {"tokens": 100}
        assert extras["config_overrides"] == {"enter_score": 0.8}
        assert extras["habit_last_snapshot"] == {"mode": {"active": True}}
        assert extras["write_scheduler"] == {"pending": []}
        assert extras["signal_fire_counts"] == {"s1": 3}

    def test_load_standalone_extras_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        extras = load_standalone_extras("/nonexistent")
        assert extras == {}

    def test_load_corrupted_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        project = "/corrupt/project"
        # Write corrupted JSON
        from lintgate.habit_mode import _standalone_path

        path = _standalone_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not valid json{{{")

        state, ring = load_habit_state_standalone(project)
        assert state.active is False
        assert ring == []

    def test_load_standalone_extras_corrupted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        project = "/corrupt2/project"
        from lintgate.habit_mode import _standalone_path

        path = _standalone_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("bad json!!!")

        extras = load_standalone_extras(project)
        assert extras == {}

    def test_load_non_dict_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        project = "/listjson/project"
        from lintgate.habit_mode import _standalone_path

        path = _standalone_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2, 3]")

        state, ring = load_habit_state_standalone(project)
        assert state.active is False
        assert ring == []

    def test_extras_non_dict_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        project = "/listjson2/project"
        from lintgate.habit_mode import _standalone_path

        path = _standalone_path(project)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[1, 2, 3]")

        extras = load_standalone_extras(project)
        assert extras == {}

    def test_save_preserves_existing_optional_fields(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When saving without optional params, existing optional fields are preserved."""
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        project = "/preserve/project"
        st = HabitModeState()

        # First save with optional fields
        save_habit_state_standalone(
            project, st, [], tracker_dict={"tokens": 999}
        )

        # Second save without tracker_dict — should preserve it
        save_habit_state_standalone(project, HabitModeState(active=True), [])

        extras = load_standalone_extras(project)
        assert extras.get("token_tracker") == {"tokens": 999}

    def test_action_ring_capped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        project = "/ringcap/project"
        big_ring = [_make_event(ts=float(i)) for i in range(100)]
        save_habit_state_standalone(project, HabitModeState(), big_ring)

        _, loaded_ring = load_habit_state_standalone(project)
        assert len(loaded_ring) <= 30  # MAX_ACTION_RING


# ── Additional edge cases ────────────────────────────────────────────


class TestEdgeCases:
    def test_update_mode_enter_resets_total_events(self) -> None:
        st = _make_high_habit_state()
        st.total_events_in_habit = 99
        st.sustain_counter = DEFAULT_SUSTAIN_CALLS - 1
        update_mode(st, event_counter=10)
        assert st.total_events_in_habit == 0

    def test_build_compaction_snapshot_no_active_files(self) -> None:
        st = HabitModeState()
        snap = build_compaction_snapshot(st, "/project")
        assert snap["focus_directive"] == "You are in Habit Mode. Focus: [none]. "

    def test_lint_issues_capped_at_five(self) -> None:
        st = HabitModeState()
        lint_run = {
            "blocking_count": 10,
            "warning_count": 5,
            "issues": [
                {"file": f"{i}.py", "line": i, "kind": "E501", "message": "issue"}
                for i in range(20)
            ],
        }
        snap = build_compaction_snapshot(st, "/project", last_lint_run=lint_run)
        assert len(snap["lint_state"]["issues"]) == 5

    def test_lint_issue_message_truncated(self) -> None:
        st = HabitModeState()
        lint_run = {
            "blocking_count": 1,
            "warning_count": 0,
            "issues": [
                {"file": "a.py", "line": 1, "kind": "E501", "message": "x" * 200},
            ],
        }
        snap = build_compaction_snapshot(st, "/project", last_lint_run=lint_run)
        assert len(snap["lint_state"]["issues"][0]["message"]) <= 80

    def test_compass_hypotheses_sorted_by_confidence(self) -> None:
        st = HabitModeState()
        compass = {
            "hypotheses": [
                {"claim": "low", "confidence": 0.1},
                {"claim": "high", "confidence": 0.99},
                {"claim": "mid", "confidence": 0.5},
                {"claim": "extra", "confidence": 0.3},
            ],
            "error_memory": {},
            "coverage": {},
        }
        snap = build_compaction_snapshot(st, "/project", compass=compass)
        confs = [c["confidence"] for c in snap["behavioral_trajectory"]["top_constraints"]]
        assert confs == sorted(confs, reverse=True)
        assert len(confs) <= 3

    def test_issue_memory_capped(self) -> None:
        st = HabitModeState()
        issue_memory = {
            "recurrent_issues": ["issue1", "issue2", "issue3", "issue4", "issue5"]
        }
        snap = build_compaction_snapshot(st, "/project", issue_memory=issue_memory)
        assert len(snap["recurring_issues"]) == 3

    def test_track_active_files_non_string_file_path(self) -> None:
        st = HabitModeState()
        track_active_files(st, "Read", {"file_path": 123})
        assert st.active_files == []

    def test_track_active_files_non_string_in_files_list(self) -> None:
        st = HabitModeState()
        track_active_files(st, "lint_files", {"files": [123, None, "valid.py"]})
        assert st.active_files == ["valid.py"]

    def test_window_size_constant(self) -> None:
        assert WINDOW_SIZE == 20

    def test_max_active_files_constant(self) -> None:
        assert MAX_ACTIVE_FILES == 20

    def test_snapshot_max_chars_constant(self) -> None:
        assert SNAPSHOT_MAX_CHARS == 12000
