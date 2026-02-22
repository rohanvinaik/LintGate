"""Tests for Habit Mode — signal collector, mode detector, persistence, compaction."""

from __future__ import annotations

import json

import pytest

from lintgate.habit_mode import (
    DEFAULT_SUSTAIN_CALLS,
    SNAPSHOT_MAX_CHARS,
    HabitModeState,
    HabitSignals,
    _classify_user_message,
    build_compaction_snapshot,
    compute_habit_score,
    declare_mode,
    detect_test_result,
    load_habit_state,
    load_habit_state_standalone,
    quick_intent,
    save_habit_state,
    save_habit_state_standalone,
    signal_user_message,
    track_active_files,
    update_mode,
    update_signals,
)

# ── HabitSignals serialization ───────────────────────────────────────


class TestHabitSignals:
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


class TestHabitModeState:
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
        state = HabitModeState.from_dict(None)
        assert state.active is False


# ── Signal Collector ─────────────────────────────────────────────────


class TestUpdateSignals:
    def _make_events(self, tools, ts_start=1000.0, gap=1.0):
        """Helper to generate action_history entries."""
        events = []
        for i, tool in enumerate(tools):
            intent = quick_intent(tool)
            events.append(
                {
                    "tool": tool,
                    "ts": ts_start + i * gap,
                    "intent": intent,
                    "sig": f"file_{i}.py",
                }
            )
        return events

    def test_pure_edit_session(self):
        """A pure Edit session should show high execute_pct, low read_edit_ratio."""
        state = HabitModeState()
        events = self._make_events(["Edit"] * 10)
        update_signals(state, events)
        assert state.signals.read_edit_ratio == 0.0  # 0 reads / 10 edits
        assert state.signals.execute_pct > 0.5
        assert state.signals.edit_streak == 10

    def test_mixed_session(self):
        """A mixed Read/Edit session should show balanced signals."""
        state = HabitModeState()
        events = self._make_events(["Read", "Read", "Read", "Edit", "Edit"])
        update_signals(state, events)
        assert state.signals.read_edit_ratio == 1.5  # 3 reads / 2 edits
        assert state.signals.edit_streak == 2

    def test_gather_heavy_session(self):
        """Lots of Read/Grep should show high gather_pct."""
        state = HabitModeState()
        events = self._make_events(["Read", "Grep", "Glob", "Read", "WebSearch"])
        update_signals(state, events)
        assert state.signals.gather_pct == 1.0
        assert state.signals.execute_pct == 0.0

    def test_sub_agent_detection(self):
        """Task calls should increase sub_agent_freq."""
        state = HabitModeState()
        events = self._make_events(["Edit", "Edit", "Task", "Edit", "Edit"])
        update_signals(state, events)
        assert state.signals.sub_agent_freq == 0.2  # 1/5

    def test_inter_tool_gap_median(self):
        """Gap median should reflect actual timing."""
        state = HabitModeState()
        events = [
            {"tool": "Edit", "ts": 100.0, "intent": "modify"},
            {"tool": "Edit", "ts": 102.0, "intent": "modify"},  # 2s gap
            {"tool": "Edit", "ts": 103.0, "intent": "modify"},  # 1s gap
            {"tool": "Edit", "ts": 106.0, "intent": "modify"},  # 3s gap
        ]
        update_signals(state, events)
        # Gaps: [2, 1, 3] → sorted: [1, 2, 3] → median: 2
        assert state.signals.inter_tool_gap_median == 2.0

    def test_empty_history(self):
        """Empty history should not crash."""
        state = HabitModeState()
        update_signals(state, [])
        assert state.signals.edit_streak == 0

    def test_test_detection_in_window(self):
        """Bash with pytest sig should set test_in_last_n."""
        state = HabitModeState()
        events = [
            {"tool": "Edit", "ts": 1.0, "intent": "modify"},
            {"tool": "Bash", "ts": 2.0, "intent": "verify", "sig": "pytest:tests/"},
            {"tool": "Edit", "ts": 3.0, "intent": "modify"},
        ]
        update_signals(state, events)
        assert state.signals.test_in_last_n is True


# ── Track Active Files ───────────────────────────────────────────────


class TestTrackActiveFiles:
    def test_file_path_extraction(self):
        state = HabitModeState()
        track_active_files(state, "Read", {"file_path": "/foo/bar.py"})
        assert state.active_files == ["/foo/bar.py"]

    def test_path_extraction(self):
        state = HabitModeState()
        track_active_files(state, "Edit", {"path": "/baz/qux.py"})
        assert state.active_files == ["/baz/qux.py"]

    def test_mru_ordering(self):
        state = HabitModeState()
        track_active_files(state, "Read", {"file_path": "/a.py"})
        track_active_files(state, "Read", {"file_path": "/b.py"})
        track_active_files(state, "Read", {"file_path": "/a.py"})  # Re-access
        assert state.active_files[0] == "/a.py"
        assert state.active_files[1] == "/b.py"

    def test_cap_at_max(self):
        state = HabitModeState()
        for i in range(25):
            track_active_files(state, "Read", {"file_path": f"/file_{i}.py"})
        assert len(state.active_files) == 20


# ── Test Result Detection ────────────────────────────────────────────


class TestDetectTestResult:
    def test_pass_detection(self):
        state = HabitModeState()
        detect_test_result(state, "3 passed in 1.2s", "pytest:tests/")
        assert state.last_test_status == "pass"

    def test_fail_detection(self):
        state = HabitModeState()
        detect_test_result(state, "2 failed, 1 passed", "pytest:tests/")
        assert state.last_test_status == "fail"

    def test_non_test_ignored(self):
        state = HabitModeState()
        detect_test_result(state, "3 passed", "git:status")
        assert state.last_test_status == ""


# ── Mode Detector ────────────────────────────────────────────────────


class TestComputeHabitScore:
    def test_full_execution_mode(self):
        """All signals maxed should give score near 1.0."""
        state = HabitModeState(
            declared=True,
            signals=HabitSignals(
                read_edit_ratio=1.0,
                execute_pct=0.8,
                edit_streak=5,
                sub_agent_freq=0.0,
                inter_tool_gap_median=1.0,
                same_file_ratio=0.9,
            ),
        )
        score = compute_habit_score(state)
        assert score >= 0.9

    def test_pure_gather_mode(self):
        """All gather signals should give low score."""
        state = HabitModeState(
            signals=HabitSignals(
                read_edit_ratio=10.0,
                execute_pct=0.0,
                edit_streak=0,
                sub_agent_freq=0.3,
                inter_tool_gap_median=10.0,
                same_file_ratio=0.1,
            ),
        )
        score = compute_habit_score(state)
        assert score < 0.2

    def test_declaration_boost(self):
        """Declaration should add 0.10 to score."""
        state = HabitModeState(
            signals=HabitSignals(read_edit_ratio=5.0),
        )
        score_without = compute_habit_score(state)
        state.declared = True
        score_with = compute_habit_score(state)
        assert score_with - score_without == pytest.approx(0.10, abs=0.01)


class TestUpdateMode:
    def test_enter_with_sustain(self):
        """Mode should enter after sustained high score."""
        state = HabitModeState(
            signals=HabitSignals(
                read_edit_ratio=1.0,
                execute_pct=0.8,
                edit_streak=5,
                sub_agent_freq=0.0,
                inter_tool_gap_median=1.0,
                same_file_ratio=0.9,
            ),
            declared=True,
        )
        # Should need DEFAULT_SUSTAIN_CALLS consecutive calls
        for i in range(DEFAULT_SUSTAIN_CALLS - 1):
            result = update_mode(state, i)
            assert result is None
            assert state.active is False

        result = update_mode(state, DEFAULT_SUSTAIN_CALLS)
        assert result == "enter"
        assert state.active is True

    def test_exit_on_low_score(self):
        """Mode should exit when score drops below threshold."""
        state = HabitModeState(
            active=True,
            signals=HabitSignals(
                read_edit_ratio=10.0,
                execute_pct=0.0,
                edit_streak=0,
                sub_agent_freq=0.3,
                inter_tool_gap_median=10.0,
                same_file_ratio=0.0,
            ),
        )
        result = update_mode(state, 100)
        assert result == "exit"
        assert state.active is False

    def test_sustain_resets_on_low_score(self):
        """Sustain counter should reset when score drops."""
        state = HabitModeState(sustain_counter=3)
        # All gather signals → low score
        state.signals = HabitSignals(
            read_edit_ratio=10.0,
            execute_pct=0.0,
        )
        update_mode(state, 10)
        assert state.sustain_counter == 0

    def test_user_message_instant_exit(self):
        """User message should cause instant exit."""
        state = HabitModeState(
            active=True,
            habit_score=0.9,
            user_message_detected=True,
        )
        result = update_mode(state, 100)
        assert result == "exit"
        assert state.active is False
        assert state.declared is False


# ── User Message Classification ──────────────────────────────────────


class TestClassifyUserMessage:
    def test_yes_is_continuation(self):
        assert _classify_user_message("yes") == "continuation"

    def test_ok_is_continuation(self):
        assert _classify_user_message("ok") == "continuation"

    def test_go_ahead_is_continuation(self):
        assert _classify_user_message("go ahead") == "continuation"

    def test_stop_is_directive(self):
        assert _classify_user_message("stop doing that") == "directive"

    def test_long_message_is_directive(self):
        assert _classify_user_message("a" * 60) == "directive"

    def test_short_question_is_clarification(self):
        assert _classify_user_message("what file?") == "clarification"

    def test_empty_is_continuation(self):
        assert _classify_user_message("") == "continuation"


class TestSignalUserMessage:
    def test_directive_collapses_active(self):
        state = HabitModeState(active=True, habit_score=0.8, declared=True)
        msg_type = signal_user_message(state, "stop and do something else")
        assert msg_type == "directive"
        assert state.active is False
        assert state.declared is False
        assert state.habit_score == 0.0

    def test_continuation_no_effect(self):
        state = HabitModeState(active=True, habit_score=0.8)
        msg_type = signal_user_message(state, "yes")
        assert msg_type == "continuation"
        assert state.active is True
        assert state.habit_score == 0.8

    def test_clarification_decays(self):
        state = HabitModeState(active=True, habit_score=0.8)
        msg_type = signal_user_message(state, "status?")
        assert msg_type == "clarification"
        assert state.habit_score == pytest.approx(0.65, abs=0.01)


# ── Declaration API ──────────────────────────────────────────────────


class TestDeclareMode:
    def test_declare_habit(self):
        state = HabitModeState()
        result = declare_mode(state, "habit", 10)
        assert result == "enter"
        assert state.active is True
        assert state.declared is True

    def test_declare_standard(self):
        state = HabitModeState(active=True)
        result = declare_mode(state, "standard", 20)
        assert result == "exit"
        assert state.active is False
        assert state.declared is False

    def test_declare_habit_when_already_active(self):
        state = HabitModeState(active=True)
        result = declare_mode(state, "habit", 10)
        assert result is None
        assert state.declared is True

    def test_declare_standard_when_already_inactive(self):
        state = HabitModeState(active=False)
        result = declare_mode(state, "standard", 10)
        assert result is None


# ── Quick Intent ─────────────────────────────────────────────────────


class TestQuickIntent:
    def test_read_is_inspect(self):
        assert quick_intent("Read") == "inspect"

    def test_edit_is_modify(self):
        assert quick_intent("Edit") == "modify"

    def test_bash_is_execute(self):
        assert quick_intent("Bash") == "execute"

    def test_task_is_meta(self):
        assert quick_intent("Task") == "meta"

    def test_unknown_tool(self):
        assert quick_intent("SomeTool") == "unknown"


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
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState(active=True, habit_score=0.82, compaction_count=1)
        ring = [{"tool": "Edit", "ts": 1.0, "intent": "modify"}]
        save_habit_state_standalone("/fake/project", state, ring)
        loaded_state, loaded_ring = load_habit_state_standalone("/fake/project")
        assert loaded_state.active is True
        assert loaded_state.habit_score == 0.82
        assert len(loaded_ring) == 1

    def test_missing_file_returns_fresh(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state, ring = load_habit_state_standalone("/nonexistent/project")
        assert state.active is False
        assert ring == []

    def test_corrupted_file_returns_fresh(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        # Write corrupt data
        from lintgate.habit_mode import _standalone_path

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
