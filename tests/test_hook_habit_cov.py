"""Coverage tests for lintgate/hook_habit.py."""

from __future__ import annotations

from unittest import mock

from lintgate.hook_habit import (
    _apply_context_window_override,
    _apply_path_b_telemetry,
    _build_action_entry,
    _detect_bash_signals,
    _load_standalone_state,
    _run_mode_transition,
    _update_action_ring,
    check_habit_api_calibration,
    record_behavior_event,
    record_habit_event_lightweight,
)


class TestCheckHabitApiCalibration:
    def test_should_check_fires(self):
        tracker = mock.MagicMock()
        cfg = mock.MagicMock(habit_mode_token_api_interval=50)

        with (
            mock.patch("lintgate.token_tracker.should_api_check", return_value=True),
            mock.patch(
                "lintgate.token_tracker.do_api_calibration",
                return_value={"source": "api", "tokens": 1000},
            ),
            mock.patch("lintgate.state.log_metric"),
        ):
            check_habit_api_calibration(tracker, 50, "/tmp", {}, cfg)

    def test_should_not_check(self):
        tracker = mock.MagicMock()
        cfg = mock.MagicMock(habit_mode_token_api_interval=50)

        with mock.patch("lintgate.token_tracker.should_api_check", return_value=False):
            check_habit_api_calibration(tracker, 10, "/tmp", {}, cfg)


class TestRecordBehaviorEvent:
    def test_behavior_disabled(self):
        cfg = mock.MagicMock()
        cfg.channel_enabled.return_value = False
        cfg.session_memory = True
        record_behavior_event(cfg, "/tmp", "Read", {}, "")

    def test_session_memory_disabled(self):
        cfg = mock.MagicMock()
        cfg.channel_enabled.return_value = True
        cfg.session_memory = False
        record_behavior_event(cfg, "/tmp", "Read", {}, "")

    def test_normal_path_no_habit(self):
        cfg = mock.MagicMock()
        cfg.channel_enabled.return_value = True
        cfg.session_memory = True
        cfg.habit_mode_enabled = False
        cfg.session_max_age_hours = 24

        session = mock.MagicMock()
        session.behavior_compass = {}

        with (
            mock.patch(
                "lintgate.controlplane.session_memory.get_or_create_session",
                return_value=session,
            ),
            mock.patch(
                "lintgate.controlplane.session_memory.load_behavior_compass",
                return_value=mock.MagicMock(),
            ),
            mock.patch(
                "lintgate.controlplane.session_memory.save_behavior_compass",
            ),
            mock.patch(
                "lintgate.controlplane.session_memory.save_session",
            ),
            mock.patch(
                "lintgate.controlplane.behavior_compass.record_tool_event",
            ),
            mock.patch(
                "lintgate.hook_runtime_state.refresh_runtime_state_with_session",
            ),
        ):
            record_behavior_event(cfg, "/tmp", "Read", {}, "")


class TestUpdateHabitModePathA:
    def test_full_path(self):
        from lintgate.hook_habit import _update_habit_mode_path_a

        cfg = mock.MagicMock()
        cfg.habit_mode_auto_detect = True
        cfg.habit_mode_enter_score = 0.7
        cfg.habit_mode_exit_score = 0.3
        cfg.habit_mode_sustain_calls = 10
        cfg.habit_mode_compact_threshold = 0.6
        cfg.habit_mode_token_api_interval = 50

        session = mock.MagicMock()
        session.behavior_compass = {}
        session.to_dict.return_value = {}

        compass = mock.MagicMock()
        compass.action_history = []
        compass.event_counter = 5
        compass.to_dict.return_value = {}

        habit_state = mock.MagicMock()
        habit_state.active = False
        tracker = mock.MagicMock()

        with (
            mock.patch(
                "lintgate.habit_mode.load_habit_state", return_value=habit_state
            ),
            mock.patch(
                "lintgate.token_tracker.load_tracker_state", return_value=tracker
            ),
            mock.patch("lintgate.habit_mode.update_signals"),
            mock.patch("lintgate.habit_mode.track_active_files"),
            mock.patch("lintgate.token_tracker.estimate_tool_tokens"),
            mock.patch("lintgate.habit_mode.detect_test_result"),
            mock.patch("lintgate.habit_mode.update_mode", return_value=None),
            mock.patch("lintgate.hook_habit.check_habit_api_calibration"),
            mock.patch(
                "lintgate.hook_habit.try_habit_compaction", return_value=(False, None)
            ),
            mock.patch("lintgate.habit_mode.save_habit_state"),
            mock.patch("lintgate.token_tracker.save_tracker_state"),
            mock.patch(
                "lintgate.hook_runtime_state.refresh_runtime_state_with_session"
            ),
            mock.patch("lintgate.state.load_last_run", return_value=None),
            mock.patch("lintgate.state.log_feature_usage"),
            mock.patch("lintgate.state.log_metric"),
            mock.patch("lintgate.habit_mode.save_habit_state_standalone"),
        ):
            _update_habit_mode_path_a(cfg, session, compass, "/tmp", "Read", {}, "")


class TestLoadStandaloneState:
    def test_normal_load(self):
        habit_state = mock.MagicMock()
        action_ring = [{"tool": "Read"}]

        with (
            mock.patch(
                "lintgate.habit_mode.load_habit_state_standalone",
                return_value=(habit_state, action_ring),
            ),
            mock.patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value={
                    "token_tracker": {"tool_call_count": 5},
                    "config_overrides": {"auto_detect": True},
                    "write_scheduler": {"gen": 1},
                    "habit_last_snapshot": {"sections": 3},
                    "signal_fire_counts": {"cmd_fail": 2},
                },
            ),
        ):
            result = _load_standalone_state("/tmp")
        hs, ar, extras, tracker, overrides, scheduler, snapshot, fires = result
        assert hs is habit_state
        assert isinstance(overrides, dict)

    def test_invalid_types_handled(self):
        with (
            mock.patch(
                "lintgate.habit_mode.load_habit_state_standalone",
                return_value=(mock.MagicMock(), []),
            ),
            mock.patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value={
                    "token_tracker": "invalid",
                    "config_overrides": 42,
                    "write_scheduler": None,
                    "habit_last_snapshot": "nope",
                    "signal_fire_counts": [],
                },
            ),
        ):
            result = _load_standalone_state("/tmp")
        _, _, _, tracker, overrides, scheduler, snapshot, fires = result
        assert overrides == {}
        assert scheduler == {}
        assert snapshot is None
        assert fires == {}


class TestApplyContextWindowOverride:
    def test_with_override(self):
        tracker = mock.MagicMock()
        _apply_context_window_override(tracker, {"context_window_size": "100000"})
        assert tracker.context_window_size == 100000

    def test_no_override(self):
        tracker = mock.MagicMock(context_window_size=200000)
        _apply_context_window_override(tracker, {})
        assert tracker.context_window_size == 200000


class TestBuildActionEntry:
    def test_dict_input_read(self):
        sig, cmd = _build_action_entry("Read", {"file_path": "/foo/bar.py"})
        assert sig == "/foo/bar.py"
        assert cmd == ""

    def test_str_input(self):
        sig, cmd = _build_action_entry("Bash", "ls -la")
        assert cmd == "ls -la"
        assert sig == "ls -la"

    def test_bash_dict_input(self):
        sig, cmd = _build_action_entry("Bash", {"command": "pytest"})
        assert cmd == "pytest"
        assert sig == "pytest"


class TestUpdateActionRing:
    def test_normal_append(self):
        ring = [{"tool": "Read"}]
        with (
            mock.patch("lintgate.habit_mode.MAX_ACTION_RING", 100),
            mock.patch("lintgate.habit_mode.quick_intent", return_value="edit"),
        ):
            new_ring, cmd = _update_action_ring(ring, "Edit", {"file_path": "/foo.py"})
        assert len(new_ring) == 2

    def test_trim_when_over_max(self):
        ring = [{"tool": f"T{i}"} for i in range(100)]
        with (
            mock.patch("lintgate.habit_mode.MAX_ACTION_RING", 20),
            mock.patch("lintgate.habit_mode.quick_intent", return_value="run"),
        ):
            new_ring, _ = _update_action_ring(ring, "Bash", {"command": "ls"})
        assert len(new_ring) == 20


class TestDetectBashSignals:
    def test_non_bash(self):
        habit_state = mock.MagicMock()
        _detect_bash_signals("Read", "", "", habit_state, {})

    def test_bash_with_pytest(self):
        habit_state = mock.MagicMock()
        with mock.patch("lintgate.habit_mode.detect_test_result"):
            _detect_bash_signals("Bash", "1 passed", "pytest tests/", habit_state, {})

    def test_bash_error_tracking(self):
        fires: dict[str, int] = {}
        _detect_bash_signals(
            "Bash", "Error: something broke", "", mock.MagicMock(), fires
        )
        assert fires["command_failure"] == 1


class TestApplyPathBTelemetry:
    def test_not_at_interval(self):
        result = _apply_path_b_telemetry(49, {"x": 1})
        assert result == {"x": 1}

    def test_at_interval_with_fires(self):
        profile = mock.MagicMock()
        profile.confidence = 0.8
        store = mock.MagicMock()
        store.profiles = {"key": profile}

        with (
            mock.patch(
                "lintgate.controlplane.model_profiles.load_profiles", return_value=store
            ),
            mock.patch("lintgate.controlplane.model_profiles.apply_telemetry_update"),
            mock.patch("lintgate.controlplane.model_profiles.save_profiles"),
        ):
            result = _apply_path_b_telemetry(50, {"cmd_fail": 3})
        assert result == {}

    def test_empty_fires_skipped(self):
        result = _apply_path_b_telemetry(50, {})
        assert result == {}


class TestRunModeTransition:
    def test_auto_detect_with_transition(self):
        habit_state = mock.MagicMock()
        habit_state.habit_score = 0.8
        cfg = mock.MagicMock()
        cfg.habit_mode_auto_detect = True
        cfg.habit_mode_enter_score = 0.7
        cfg.habit_mode_exit_score = 0.3
        cfg.habit_mode_sustain_calls = 10

        with (
            mock.patch("lintgate.habit_mode.update_mode", return_value="entered"),
            mock.patch("lintgate.state.log_metric"),
        ):
            result = _run_mode_transition(habit_state, 50, {}, cfg, "/tmp")
        assert result == "entered"

    def test_auto_detect_disabled_active(self):
        habit_state = mock.MagicMock()
        habit_state.active = True
        habit_state.total_events_in_habit = 5
        cfg = mock.MagicMock()
        cfg.habit_mode_auto_detect = False

        result = _run_mode_transition(
            habit_state, 50, {"auto_detect": False}, cfg, "/tmp"
        )
        assert result is None
        assert habit_state.total_events_in_habit == 6


class TestRecordHabitEventLightweight:
    def test_disabled(self):
        cfg = mock.MagicMock()
        cfg.habit_mode_enabled = False
        record_habit_event_lightweight(cfg, "/tmp", "Read", {}, "")

    def test_session_memory_on_skips(self):
        cfg = mock.MagicMock()
        cfg.habit_mode_enabled = True
        cfg.session_memory = True
        cfg.channel_enabled.return_value = True
        record_habit_event_lightweight(cfg, "/tmp", "Read", {}, "")

    def test_normal_path(self):
        cfg = mock.MagicMock()
        cfg.habit_mode_enabled = True
        cfg.session_memory = False
        cfg.channel_enabled.return_value = False
        cfg.habit_mode_auto_detect = True
        cfg.habit_mode_enter_score = 0.7
        cfg.habit_mode_exit_score = 0.3
        cfg.habit_mode_sustain_calls = 10
        cfg.habit_mode_compact_threshold = 0.6
        cfg.habit_mode_token_api_interval = 50

        habit_state = mock.MagicMock()
        habit_state.active = False
        tracker = mock.MagicMock()
        tracker.tool_call_count = 10

        with (
            mock.patch(
                "lintgate.hook_habit._load_standalone_state",
                return_value=(habit_state, [], {}, tracker, {}, {}, None, {}),
            ),
            mock.patch(
                "lintgate.hook_habit._apply_context_window_override",
            ),
            mock.patch(
                "lintgate.hook_habit._update_action_ring",
                return_value=([], ""),
            ),
            mock.patch(
                "lintgate.habit_mode.update_signals",
            ),
            mock.patch(
                "lintgate.habit_mode.track_active_files",
            ),
            mock.patch(
                "lintgate.token_tracker.estimate_tool_tokens",
            ),
            mock.patch(
                "lintgate.hook_habit._detect_bash_signals",
            ),
            mock.patch(
                "lintgate.hook_habit._apply_path_b_telemetry",
                return_value={},
            ),
            mock.patch(
                "lintgate.hook_habit._run_mode_transition",
                return_value=None,
            ),
            mock.patch(
                "lintgate.hook_habit.check_habit_api_calibration",
            ),
            mock.patch(
                "lintgate.hook_habit.try_habit_compaction",
                return_value=(False, None),
            ),
            mock.patch(
                "lintgate.hook_runtime_state.refresh_runtime_state_lightweight",
                return_value={"gen": 1},
            ),
            mock.patch(
                "lintgate.habit_mode.save_habit_state_standalone",
            ),
        ):
            record_habit_event_lightweight(cfg, "/tmp", "Read", {}, "")
