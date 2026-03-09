"""Mutation-killing tests for habit_mode.py.

Targets VALUE and SWAP mutation survivors identified by mutation_run_sampling.
Each test asserts exact values and field discrimination to kill mutants that
swap field names or alter literal values.
"""

from __future__ import annotations

from lintgate.habit_mode import (
    HabitModeState,
    HabitSignals,
    _detect_test_in_window,
    detect_test_result,
    load_habit_state_standalone,
    save_habit_state_standalone,
)

# ── HabitSignals.from_dict — SWAP + VALUE survivors ─────────────────


class TestHabitSignalsFromDictFieldDiscrimination:
    """Kill SWAP mutants: verify every field maps to its own key, not a neighbor."""

    def test_all_fields_unique_values(self):
        data = {
            "read_edit_ratio": 0.1,
            "gather_pct": 0.2,
            "execute_pct": 0.3,
            "same_file_ratio": 0.4,
            "inter_tool_gap_median": 0.5,
            "sub_agent_freq": 0.6,
            "edit_streak": 7,
            "test_in_last_n": True,
        }
        sig = HabitSignals.from_dict(data)
        assert sig.read_edit_ratio == 0.1
        assert sig.gather_pct == 0.2
        assert sig.execute_pct == 0.3
        assert sig.same_file_ratio == 0.4
        assert sig.inter_tool_gap_median == 0.5
        assert sig.sub_agent_freq == 0.6
        assert sig.edit_streak == 7
        assert sig.test_in_last_n is True

    def test_swap_read_edit_ratio_and_gather_pct(self):
        data = {"read_edit_ratio": 0.11, "gather_pct": 0.99}
        sig = HabitSignals.from_dict(data)
        assert sig.read_edit_ratio == 0.11
        assert sig.gather_pct == 0.99

    def test_swap_execute_pct_and_same_file_ratio(self):
        data = {"execute_pct": 0.77, "same_file_ratio": 0.33}
        sig = HabitSignals.from_dict(data)
        assert sig.execute_pct == 0.77
        assert sig.same_file_ratio == 0.33

    def test_swap_inter_tool_gap_and_sub_agent_freq(self):
        data = {"inter_tool_gap_median": 12.5, "sub_agent_freq": 0.01}
        sig = HabitSignals.from_dict(data)
        assert sig.inter_tool_gap_median == 12.5
        assert sig.sub_agent_freq == 0.01

    def test_swap_edit_streak_and_test_in_last_n(self):
        data = {"edit_streak": 42, "test_in_last_n": False}
        sig = HabitSignals.from_dict(data)
        assert sig.edit_streak == 42
        assert sig.test_in_last_n is False

    def test_only_one_field_set_rest_defaults(self):
        data = {"gather_pct": 0.88}
        sig = HabitSignals.from_dict(data)
        assert sig.gather_pct == 0.88
        assert sig.read_edit_ratio == 0.0
        assert sig.execute_pct == 0.0
        assert sig.same_file_ratio == 0.0
        assert sig.inter_tool_gap_median == 0.0
        assert sig.sub_agent_freq == 0.0
        assert sig.edit_streak == 0
        assert sig.test_in_last_n is False


class TestHabitSignalsFromDictExactValues:
    """Kill VALUE mutants: verify exact return values for specific inputs."""

    def test_empty_dict_all_defaults(self):
        sig = HabitSignals.from_dict({})
        assert sig.read_edit_ratio == 0.0
        assert sig.gather_pct == 0.0
        assert sig.execute_pct == 0.0
        assert sig.same_file_ratio == 0.0
        assert sig.inter_tool_gap_median == 0.0
        assert sig.sub_agent_freq == 0.0
        assert sig.edit_streak == 0
        assert sig.test_in_last_n is False

    def test_float_coercion_from_int(self):
        data = {"read_edit_ratio": 3, "gather_pct": 1}
        sig = HabitSignals.from_dict(data)
        assert sig.read_edit_ratio == 3.0
        assert isinstance(sig.read_edit_ratio, float)
        assert sig.gather_pct == 1.0
        assert isinstance(sig.gather_pct, float)

    def test_int_coercion_for_edit_streak(self):
        data = {"edit_streak": 5.9}
        sig = HabitSignals.from_dict(data)
        assert sig.edit_streak == 5
        assert isinstance(sig.edit_streak, int)

    def test_bool_truthy_value_for_test_in_last_n(self):
        data = {"test_in_last_n": 1}
        sig = HabitSignals.from_dict(data)
        assert sig.test_in_last_n is True

    def test_bool_falsy_value_for_test_in_last_n(self):
        data = {"test_in_last_n": 0}
        sig = HabitSignals.from_dict(data)
        assert sig.test_in_last_n is False


# ── HabitModeState.from_dict — SWAP + VALUE survivors ────────────────


class TestHabitModeStateFromDictFieldDiscrimination:
    """Kill SWAP mutants: verify every field maps to its own key."""

    def test_all_fields_unique_values(self):
        data = {
            "active": True,
            "habit_score": 0.123,
            "sustain_counter": 4,
            "declared": False,
            "signals": {
                "read_edit_ratio": 0.9,
                "gather_pct": 0.1,
                "execute_pct": 0.8,
                "same_file_ratio": 0.7,
                "inter_tool_gap_median": 2.5,
                "sub_agent_freq": 0.05,
                "edit_streak": 3,
                "test_in_last_n": True,
            },
            "active_files": ["/x.py", "/y.py"],
            "last_test_status": "fail",
            "compaction_count": 5,
            "last_compaction_event": 100,
            "entered_at_event": 50,
            "total_events_in_habit": 200,
            "user_message_detected": True,
        }
        st = HabitModeState.from_dict(data)
        assert st.active is True
        assert st.habit_score == 0.123
        assert st.sustain_counter == 4
        assert st.declared is False
        assert st.active_files == ["/x.py", "/y.py"]
        assert st.last_test_status == "fail"
        assert st.compaction_count == 5
        assert st.last_compaction_event == 100
        assert st.entered_at_event == 50
        assert st.total_events_in_habit == 200
        assert st.user_message_detected is True
        # Nested signals discrimination
        assert st.signals.read_edit_ratio == 0.9
        assert st.signals.gather_pct == 0.1
        assert st.signals.execute_pct == 0.8

    def test_swap_active_and_declared(self):
        data = {"active": True, "declared": False}
        st = HabitModeState.from_dict(data)
        assert st.active is True
        assert st.declared is False

    def test_swap_habit_score_and_sustain_counter(self):
        data = {"habit_score": 0.55, "sustain_counter": 3}
        st = HabitModeState.from_dict(data)
        assert st.habit_score == 0.55
        assert st.sustain_counter == 3

    def test_swap_compaction_count_and_last_compaction_event(self):
        data = {"compaction_count": 7, "last_compaction_event": 999}
        st = HabitModeState.from_dict(data)
        assert st.compaction_count == 7
        assert st.last_compaction_event == 999

    def test_swap_entered_at_event_and_total_events_in_habit(self):
        data = {"entered_at_event": 10, "total_events_in_habit": 300}
        st = HabitModeState.from_dict(data)
        assert st.entered_at_event == 10
        assert st.total_events_in_habit == 300


class TestHabitModeStateFromDictExactValues:
    """Kill VALUE mutants: verify exact defaults and edge-case values."""

    def test_empty_dict_all_defaults(self):
        st = HabitModeState.from_dict({})
        assert st.active is False
        assert st.habit_score == 0.0
        assert st.sustain_counter == 0
        assert st.declared is False
        assert st.active_files == []
        assert st.last_test_status == ""
        assert st.compaction_count == 0
        assert st.last_compaction_event == 0
        assert st.entered_at_event == 0
        assert st.total_events_in_habit == 0
        assert st.user_message_detected is False

    def test_none_returns_fresh_state(self):
        st = HabitModeState.from_dict(None)
        assert st.active is False
        assert st.habit_score == 0.0
        assert st.sustain_counter == 0
        assert st.declared is False
        assert st.active_files == []
        assert st.last_test_status == ""
        assert st.compaction_count == 0
        assert st.last_compaction_event == 0
        assert st.entered_at_event == 0
        assert st.total_events_in_habit == 0
        assert st.user_message_detected is False

    def test_signals_subobject_defaults_when_missing(self):
        st = HabitModeState.from_dict({"active": True})
        assert st.signals.read_edit_ratio == 0.0
        assert st.signals.edit_streak == 0
        assert st.signals.test_in_last_n is False


# ── _detect_test_in_window — VALUE survivors ─────────────────────────


class TestDetectTestInWindowExact:
    """Kill VALUE mutants: test exact True/False for boundary cases."""

    def test_returns_true_for_pytest_keyword(self):
        window = [{"tool": "Bash", "sig": "pytest tests/unit/"}]
        assert _detect_test_in_window(window) is True

    def test_returns_true_for_test_keyword(self):
        window = [{"tool": "Bash", "sig": "python -m test_runner"}]
        assert _detect_test_in_window(window) is True

    def test_returns_false_when_no_bash_events(self):
        window = [{"tool": "Read", "sig": "pytest"}]
        assert _detect_test_in_window(window) is False

    def test_returns_false_when_bash_without_test_keyword(self):
        window = [{"tool": "Bash", "sig": "git status"}]
        assert _detect_test_in_window(window) is False

    def test_returns_false_for_empty_window(self):
        assert _detect_test_in_window([]) is False

    def test_case_insensitive_pytest(self):
        window = [{"tool": "Bash", "sig": "PYTEST --verbose"}]
        assert _detect_test_in_window(window) is True

    def test_case_insensitive_test(self):
        window = [{"tool": "Bash", "sig": "run_TEST_suite"}]
        assert _detect_test_in_window(window) is True

    def test_returns_true_if_any_bash_event_matches(self):
        window = [
            {"tool": "Bash", "sig": "git commit -m 'fix'"},
            {"tool": "Edit", "sig": "foo.py"},
            {"tool": "Bash", "sig": "pytest -x"},
        ]
        assert _detect_test_in_window(window) is True

    def test_returns_false_when_sig_is_empty(self):
        window = [{"tool": "Bash", "sig": ""}]
        assert _detect_test_in_window(window) is False

    def test_returns_false_when_sig_is_missing(self):
        window = [{"tool": "Bash"}]
        assert _detect_test_in_window(window) is False


# ── detect_test_result — VALUE survivors ─────────────────────────────


class TestDetectTestResultExact:
    """Kill VALUE mutants: test exact return values for all branches."""

    def test_pass_sets_exact_value(self):
        state = HabitModeState()
        detect_test_result(state, "5 passed in 2.3s", "pytest:tests/")
        assert state.last_test_status == "pass"

    def test_fail_sets_exact_value(self):
        state = HabitModeState()
        detect_test_result(state, "2 failed, 3 passed", "pytest:tests/")
        assert state.last_test_status == "fail"

    def test_error_sets_fail(self):
        state = HabitModeState()
        detect_test_result(state, "ERROR collecting tests", "pytest:tests/")
        assert state.last_test_status == "fail"

    def test_no_change_when_command_sig_empty(self):
        state = HabitModeState()
        detect_test_result(state, "5 passed", "")
        assert state.last_test_status == ""

    def test_no_change_when_no_test_keyword_in_sig(self):
        state = HabitModeState()
        detect_test_result(state, "5 passed", "git:status")
        assert state.last_test_status == ""

    def test_no_change_when_output_empty(self):
        state = HabitModeState()
        detect_test_result(state, "", "pytest:tests/")
        assert state.last_test_status == ""

    def test_no_change_when_output_none(self):
        state = HabitModeState()
        detect_test_result(state, None, "pytest:tests/")
        assert state.last_test_status == ""

    def test_fail_takes_priority_over_pass(self):
        state = HabitModeState()
        detect_test_result(state, "3 passed, 1 failed", "pytest:tests/")
        assert state.last_test_status == "fail"

    def test_test_keyword_in_sig_is_sufficient(self):
        state = HabitModeState()
        detect_test_result(state, "all passed", "run_test_suite")
        assert state.last_test_status == "pass"

    def test_case_insensitive_sig_matching(self):
        state = HabitModeState()
        detect_test_result(state, "5 passed in 1s", "PYTEST:TESTS/")
        assert state.last_test_status == "pass"

    def test_no_keywords_in_output_no_change(self):
        state = HabitModeState()
        detect_test_result(state, "collecting items...", "pytest:tests/")
        assert state.last_test_status == ""


# ── save_habit_state_standalone — SWAP + VALUE survivors ──────────────


class TestSaveHabitStateStandaloneExact:
    """Kill SWAP + VALUE mutants: verify correct state is saved to correct path."""

    def test_state_persisted_correctly(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState(
            active=True,
            habit_score=0.77,
            sustain_counter=3,
            declared=True,
            active_files=["/a.py", "/b.py"],
            last_test_status="pass",
            compaction_count=2,
            last_compaction_event=50,
            entered_at_event=10,
            total_events_in_habit=40,
            user_message_detected=False,
        )
        ring = [
            {"tool": "Edit", "ts": 1.0, "intent": "modify"},
            {"tool": "Read", "ts": 2.0, "intent": "inspect"},
        ]
        save_habit_state_standalone("/my/project", state, ring)

        loaded_state, loaded_ring = load_habit_state_standalone("/my/project")
        assert loaded_state.active is True
        assert loaded_state.habit_score == 0.77
        assert loaded_state.sustain_counter == 3
        assert loaded_state.declared is True
        assert loaded_state.active_files == ["/a.py", "/b.py"]
        assert loaded_state.last_test_status == "pass"
        assert loaded_state.compaction_count == 2
        assert loaded_state.last_compaction_event == 50
        assert loaded_state.entered_at_event == 10
        assert loaded_state.total_events_in_habit == 40
        assert loaded_state.user_message_detected is False
        assert len(loaded_ring) == 2
        assert loaded_ring[0]["tool"] == "Edit"
        assert loaded_ring[1]["tool"] == "Read"

    def test_action_ring_persisted_in_order(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        ring = [
            {"tool": "Bash", "ts": 10.0, "intent": "execute"},
            {"tool": "Grep", "ts": 11.0, "intent": "inspect"},
            {"tool": "Edit", "ts": 12.0, "intent": "modify"},
        ]
        save_habit_state_standalone("/proj", state, ring)
        _, loaded_ring = load_habit_state_standalone("/proj")
        assert loaded_ring[0]["tool"] == "Bash"
        assert loaded_ring[1]["tool"] == "Grep"
        assert loaded_ring[2]["tool"] == "Edit"
        assert loaded_ring[0]["ts"] == 10.0
        assert loaded_ring[1]["ts"] == 11.0
        assert loaded_ring[2]["ts"] == 12.0

    def test_extras_tracker_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        tracker = {"estimated_tokens_used": 5000, "tool_call_count": 42}
        save_habit_state_standalone("/proj", state, [], tracker_dict=tracker)

        from lintgate.habit_mode import load_standalone_extras

        extras = load_standalone_extras("/proj")
        assert extras["token_tracker"]["estimated_tokens_used"] == 5000
        assert extras["token_tracker"]["tool_call_count"] == 42

    def test_extras_config_overrides_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        overrides = {"enter_score": 0.8, "exit_score": 0.3}
        save_habit_state_standalone("/proj", state, [], config_overrides=overrides)

        from lintgate.habit_mode import load_standalone_extras

        extras = load_standalone_extras("/proj")
        assert extras["config_overrides"]["enter_score"] == 0.8
        assert extras["config_overrides"]["exit_score"] == 0.3

    def test_extras_last_snapshot_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        snapshot = {"mode": {"active": True}, "focus_directive": "test focus"}
        save_habit_state_standalone("/proj", state, [], last_snapshot=snapshot)

        from lintgate.habit_mode import load_standalone_extras

        extras = load_standalone_extras("/proj")
        assert extras["habit_last_snapshot"]["mode"]["active"] is True
        assert extras["habit_last_snapshot"]["focus_directive"] == "test focus"

    def test_extras_scheduler_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        scheduler = {"pending_writes": 3, "batch_size": 10}
        save_habit_state_standalone("/proj", state, [], scheduler_dict=scheduler)

        from lintgate.habit_mode import load_standalone_extras

        extras = load_standalone_extras("/proj")
        assert extras["write_scheduler"]["pending_writes"] == 3
        assert extras["write_scheduler"]["batch_size"] == 10

    def test_extras_signal_fire_counts_persisted(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        counts = {"approach_cycle": 5, "verification_debt": 2}
        save_habit_state_standalone("/proj", state, [], signal_fire_counts=counts)

        from lintgate.habit_mode import load_standalone_extras

        extras = load_standalone_extras("/proj")
        assert extras["signal_fire_counts"]["approach_cycle"] == 5
        assert extras["signal_fire_counts"]["verification_debt"] == 2

    def test_action_ring_capped_at_max(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        from lintgate.habit_mode import MAX_ACTION_RING

        state = HabitModeState()
        ring = [{"tool": f"T{i}", "ts": float(i), "intent": "x"} for i in range(50)]
        save_habit_state_standalone("/proj", state, ring)
        _, loaded_ring = load_habit_state_standalone("/proj")
        assert len(loaded_ring) == MAX_ACTION_RING
        # Should keep the last MAX_ACTION_RING entries
        assert loaded_ring[0]["tool"] == f"T{50 - MAX_ACTION_RING}"
        assert loaded_ring[-1]["tool"] == "T49"

    def test_state_and_ring_go_to_same_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState(active=True, habit_score=0.65)
        ring = [{"tool": "Edit", "ts": 1.0, "intent": "modify"}]
        save_habit_state_standalone("/proj/a", state, ring)

        # Different project should not see this data
        other_state, other_ring = load_habit_state_standalone("/proj/b")
        assert other_state.active is False
        assert other_ring == []

    def test_merge_preserves_existing_extras(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        # First save with tracker
        save_habit_state_standalone("/proj", state, [], tracker_dict={"tokens": 100})
        # Second save without tracker but with config
        save_habit_state_standalone("/proj", state, [], config_overrides={"enter_score": 0.9})

        from lintgate.habit_mode import load_standalone_extras

        extras = load_standalone_extras("/proj")
        # Both should be present — merge preserves existing
        assert extras["token_tracker"]["tokens"] == 100
        assert extras["config_overrides"]["enter_score"] == 0.9
