"""Tests for lintgate.hooks.habit — habit mode hook helpers.

Covers both Path A (session-backed) and Path B (lightweight/standalone) habit
tracking, plus shared helpers for API calibration, compaction, action ring
management, signal detection, and telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

from lintgate.hooks.habit import (
    _apply_context_window_override,
    _apply_path_b_telemetry,
    _build_action_entry,
    _detect_bash_signals,
    _detect_test_results,
    _log_feature_telemetry,
    _run_auto_detect,
    _run_mode_transition,
    _update_action_ring,
    check_habit_api_calibration,
    record_behavior_event,
    record_habit_event_lightweight,
    try_habit_compaction,
)

# ── Lightweight fakes ────────────────────────────────────────────────


@dataclass
class FakeTracker:
    """Minimal stand-in for TokenTrackerState."""

    estimated_tokens_used: int = 5000
    tool_calls_since_compact: int = 10
    tool_call_count: int = 20
    context_window_size: int = 200_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_tokens_used": self.estimated_tokens_used,
            "tool_calls_since_compact": self.tool_calls_since_compact,
            "tool_call_count": self.tool_call_count,
            "context_window_size": self.context_window_size,
        }


@dataclass
class FakeHabitState:
    """Minimal stand-in for HabitModeState."""

    active: bool = False
    habit_score: float = 0.5
    compaction_count: int = 0
    last_compaction_event: int = 0
    total_events_in_habit: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "habit_score": self.habit_score,
            "compaction_count": self.compaction_count,
            "last_compaction_event": self.last_compaction_event,
            "total_events_in_habit": self.total_events_in_habit,
        }


@dataclass
class FakeCpConfig:
    """Minimal stand-in for ControlPlaneConfig."""

    habit_mode_token_api_interval: int = 15
    habit_mode_compact_threshold: float = 0.40
    habit_mode_enabled: bool = True
    habit_mode_auto_detect: bool = True
    habit_mode_enter_score: float = 0.70
    habit_mode_exit_score: float = 0.40
    habit_mode_sustain_calls: int = 5
    session_memory: bool = False
    session_max_age_hours: float = 4.0
    _enabled_channels: dict = field(default_factory=lambda: {"behavior": True})

    def channel_enabled(self, name: str) -> bool:
        return bool(self._enabled_channels.get(name, True))


@dataclass
class FakeCompass:
    """Minimal stand-in for BehaviorCompass."""

    action_history: list = field(default_factory=list)
    event_counter: int = 10


@dataclass
class FakeSession:
    """Minimal stand-in for session objects."""

    behavior_compass: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"behavior_compass": self.behavior_compass}


# ── check_habit_api_calibration ──────────────────────────────────────


class TestCheckHabitApiCalibration:
    """Tests for check_habit_api_calibration."""

    def test_no_calibration_when_should_api_check_false(self):
        """When should_api_check returns False, no calibration happens."""
        tracker = FakeTracker()
        cp_config = FakeCpConfig()
        with (
            patch("lintgate.token_tracker.should_api_check", return_value=False) as mock_check,
            patch("lintgate.token_tracker.do_api_calibration") as mock_cal,
        ):
            check_habit_api_calibration(tracker, 5, "/project", {}, cp_config)
            mock_check.assert_called_once_with(tracker, 5, interval=15)
            mock_cal.assert_not_called()

    def test_calibration_runs_when_should_api_check_true(self):
        """When should_api_check returns True and calibration succeeds, log_metric is called."""
        tracker = FakeTracker()
        cp_config = FakeCpConfig()
        cal_result = {"tokens_used": 1000, "actual": 1200}
        with (
            patch("lintgate.token_tracker.should_api_check", return_value=True),
            patch(
                "lintgate.token_tracker.do_api_calibration",
                return_value=cal_result,
            ) as mock_cal,
            patch("lintgate.state.log_metric") as mock_log,
        ):
            check_habit_api_calibration(tracker, 10, "/proj", {}, cp_config)
            mock_cal.assert_called_once_with(tracker, 10, "/proj")
            mock_log.assert_called_once()
            logged = mock_log.call_args[0][0]
            assert logged["event"] == "token_estimate"
            assert logged["project"] == "/proj"
            assert logged["source"] == "api"
            assert logged["tokens_used"] == 1000

    def test_override_interval(self):
        """token_api_interval override from overrides dict is used."""
        tracker = FakeTracker()
        cp_config = FakeCpConfig(habit_mode_token_api_interval=15)
        overrides = {"token_api_interval": 42}
        with (
            patch("lintgate.token_tracker.should_api_check", return_value=False) as mock_check,
            patch("lintgate.token_tracker.do_api_calibration"),
        ):
            check_habit_api_calibration(tracker, 5, "/p", overrides, cp_config)
            mock_check.assert_called_once_with(tracker, 5, interval=42)

    def test_calibration_returns_none_no_log(self):
        """When do_api_calibration returns None, log_metric is not called."""
        tracker = FakeTracker()
        cp_config = FakeCpConfig()
        with (
            patch("lintgate.token_tracker.should_api_check", return_value=True),
            patch("lintgate.token_tracker.do_api_calibration", return_value=None),
            patch("lintgate.state.log_metric") as mock_log,
        ):
            check_habit_api_calibration(tracker, 5, "/p", {}, cp_config)
            mock_log.assert_not_called()

    def test_calibration_exception_suppressed(self):
        """Exceptions during calibration are silently suppressed."""
        tracker = FakeTracker()
        cp_config = FakeCpConfig()
        with (
            patch("lintgate.token_tracker.should_api_check", return_value=True),
            patch(
                "lintgate.token_tracker.do_api_calibration",
                side_effect=RuntimeError("boom"),
            ),
        ):
            check_habit_api_calibration(tracker, 5, "/p", {}, cp_config)
            assert tracker.tool_call_count == 20


# ── try_habit_compaction ─────────────────────────────────────────────


class TestTryHabitCompaction:
    """Tests for try_habit_compaction."""

    def test_no_compact_when_should_compact_false(self):
        """Returns (False, None) when should_compact says no."""
        tracker = FakeTracker()
        habit_state = FakeHabitState(active=True)
        cp_config = FakeCpConfig()
        with (
            patch("lintgate.token_tracker.should_compact", return_value=False),
        ):
            did, snapshot = try_habit_compaction(tracker, habit_state, {}, cp_config, "/proj", 10)
            assert did is False
            assert snapshot is None

    def test_compact_succeeds(self):
        """When should_compact is True and snapshot builds, returns (True, snapshot)."""
        tracker = FakeTracker()
        habit_state = FakeHabitState(active=True, compaction_count=2)
        cp_config = FakeCpConfig()
        fake_snapshot = {"theory": "data", "lint": None, "compass": "c"}
        with (
            patch("lintgate.token_tracker.should_compact", return_value=True),
            patch(
                "lintgate.token_tracker.get_usage_summary",
                return_value={"pct": 40},
            ),
            patch(
                "lintgate.habit_mode.build_compaction_snapshot",
                return_value=fake_snapshot,
            ),
            patch("lintgate.token_tracker.reset_post_compaction") as mock_reset,
            patch("lintgate.state.log_metric") as mock_log,
        ):
            did, snapshot = try_habit_compaction(tracker, habit_state, {}, cp_config, "/proj", 25)
            assert did is True
            assert snapshot is fake_snapshot
            # compaction_count was 2, now 3
            assert habit_state.compaction_count == 3
            assert habit_state.last_compaction_event == 25
            mock_reset.assert_called_once_with(tracker)
            mock_log.assert_called_once()
            logged = mock_log.call_args[0][0]
            assert logged["event"] == "habit_compact"
            assert logged["compaction_number"] == 3
            # sections_included = 2 (theory + compass are non-None)
            assert logged["sections_included"] == 2

    def test_compact_threshold_override(self):
        """compact_threshold from overrides is used instead of cp_config default."""
        tracker = FakeTracker()
        habit_state = FakeHabitState(active=True)
        cp_config = FakeCpConfig(habit_mode_compact_threshold=0.40)
        overrides = {"compact_threshold": "0.55"}
        with (
            patch("lintgate.token_tracker.should_compact", return_value=False) as mock_compact,
        ):
            try_habit_compaction(tracker, habit_state, overrides, cp_config, "/p", 5)
            mock_compact.assert_called_once_with(tracker, True, threshold=0.55)

    def test_compact_with_session_memory_kwargs(self):
        """session_memory, compass_dict, last_lint_run are passed through."""
        tracker = FakeTracker()
        habit_state = FakeHabitState(active=True)
        cp_config = FakeCpConfig()
        session_mem: dict[str, Any] = {"sessions": []}
        compass_d: dict[str, Any] = {"events": []}
        last_lint: dict[str, Any] = {"findings": []}
        with (
            patch("lintgate.token_tracker.should_compact", return_value=True),
            patch(
                "lintgate.token_tracker.get_usage_summary",
                return_value={},
            ),
            patch(
                "lintgate.habit_mode.build_compaction_snapshot",
                return_value={"x": 1},
            ) as mock_snap,
            patch("lintgate.token_tracker.reset_post_compaction"),
            patch("lintgate.state.log_metric"),
        ):
            try_habit_compaction(
                tracker,
                habit_state,
                {},
                cp_config,
                "/p",
                1,
                session_memory=session_mem,
                compass_dict=compass_d,
                last_lint_run=last_lint,
            )
            call_kwargs = mock_snap.call_args[1]
            assert call_kwargs["session_memory"] is session_mem
            assert call_kwargs["compass"] is compass_d
            assert call_kwargs["last_lint_run"] is last_lint

    def test_compact_snapshot_build_exception_returns_false(self):
        """If build_compaction_snapshot raises, returns (False, None)."""
        tracker = FakeTracker()
        habit_state = FakeHabitState(active=True)
        cp_config = FakeCpConfig()
        with (
            patch("lintgate.token_tracker.should_compact", return_value=True),
            patch(
                "lintgate.token_tracker.get_usage_summary",
                side_effect=RuntimeError("boom"),
            ),
        ):
            did, snapshot = try_habit_compaction(tracker, habit_state, {}, cp_config, "/p", 1)
            # Exception suppressed, snapshot stays None
            assert did is False
            assert snapshot is None


# ── _log_feature_telemetry ───────────────────────────────────────────


class TestLogFeatureTelemetry:
    """Tests for _log_feature_telemetry."""

    def test_logs_both_features_on_first_call(self):
        """First call logs both habit_mode and token_tracking."""
        bc: dict[str, Any] = {}
        log_fn = MagicMock()
        _log_feature_telemetry(bc, "/proj", log_fn)
        assert log_fn.call_count == 2
        calls = [c[0] for c in log_fn.call_args_list]
        assert ("habit_mode", "/proj", {"source": "hook_posttooluse"}) in calls
        assert ("token_tracking", "/proj", {"source": "hook_posttooluse"}) in calls
        assert bc["_feature_habit_mode_logged"] is True
        assert bc["_feature_token_tracking_logged"] is True

    def test_no_repeat_logging(self):
        """Second call does not log again."""
        bc = {"_feature_habit_mode_logged": True, "_feature_token_tracking_logged": True}
        log_fn = MagicMock()
        _log_feature_telemetry(bc, "/proj", log_fn)
        log_fn.assert_not_called()

    def test_partial_logged(self):
        """Only logs features not yet logged."""
        bc = {"_feature_habit_mode_logged": True}
        log_fn = MagicMock()
        _log_feature_telemetry(bc, "/proj", log_fn)
        assert log_fn.call_count == 1
        log_fn.assert_called_once_with("token_tracking", "/proj", {"source": "hook_posttooluse"})
        assert bc["_feature_token_tracking_logged"] is True

    def test_log_fn_exception_suppressed(self):
        """If log_fn raises, exceptions are suppressed and flag still set.

        The flag assignment (line 163) is OUTSIDE the contextlib.suppress block,
        so even when log_fn raises, the flag is set after the suppress exits.
        """
        bc: dict[str, Any] = {}
        log_fn = MagicMock(side_effect=RuntimeError("boom"))
        _log_feature_telemetry(bc, "/proj", log_fn)
        # Flag assignment is outside the suppress block, so flags ARE set
        assert bc["_feature_habit_mode_logged"] is True
        assert bc["_feature_token_tracking_logged"] is True


# ── _detect_test_results ────────────────────────────────────────────


class TestDetectTestResults:
    """Tests for _detect_test_results."""

    def test_non_bash_tool_skipped(self):
        """Non-Bash tools are ignored."""
        detect_fn = MagicMock()
        compass = FakeCompass(action_history=[{"sig": "git status"}])
        habit_state = FakeHabitState()
        _detect_test_results("Read", "output", compass, habit_state, detect_fn)
        detect_fn.assert_not_called()

    def test_bash_tool_with_action_history(self):
        """Bash tool uses last action_history sig."""
        detect_fn = MagicMock()
        compass = FakeCompass(action_history=[{"sig": "pytest tests/"}])
        habit_state = FakeHabitState()
        _detect_test_results("Bash", "PASSED", compass, habit_state, detect_fn)
        detect_fn.assert_called_once_with(habit_state, "PASSED", "pytest tests/")

    def test_bash_tool_empty_action_history(self):
        """When action_history is empty, sig defaults to empty string."""
        detect_fn = MagicMock()
        compass = FakeCompass(action_history=[])
        habit_state = FakeHabitState()
        _detect_test_results("Bash", "output", compass, habit_state, detect_fn)
        detect_fn.assert_called_once_with(habit_state, "output", "")

    def test_bash_tool_non_string_output(self):
        """Non-string tool_output becomes empty string."""
        detect_fn = MagicMock()
        compass = FakeCompass(action_history=[{"sig": "cmd"}])
        habit_state = FakeHabitState()
        _detect_test_results("Bash", {"key": "val"}, compass, habit_state, detect_fn)
        detect_fn.assert_called_once_with(habit_state, "", "cmd")

    def test_bash_tool_none_output(self):
        """None tool_output becomes empty string."""
        detect_fn = MagicMock()
        compass = FakeCompass(action_history=[{"sig": "x"}])
        habit_state = FakeHabitState()
        _detect_test_results("Bash", None, compass, habit_state, detect_fn)
        detect_fn.assert_called_once_with(habit_state, "", "x")

    def test_bash_uses_last_action_history_item(self):
        """Kill VALUE_2: must use [-1] (last item), not [-0] (first)."""
        detect_fn = MagicMock()
        compass = FakeCompass(action_history=[{"sig": "first"}, {"sig": "last"}])
        habit_state = FakeHabitState()
        _detect_test_results("Bash", "out", compass, habit_state, detect_fn)
        detect_fn.assert_called_once_with(habit_state, "out", "last")

    def test_bash_missing_sig_key_defaults_empty(self):
        """Kill VALUE_4: .get('sig', '') must default to '' not 'mutated'."""
        detect_fn = MagicMock()
        compass = FakeCompass(action_history=[{"other_key": "val"}])
        habit_state = FakeHabitState()
        _detect_test_results("Bash", "out", compass, habit_state, detect_fn)
        detect_fn.assert_called_once_with(habit_state, "out", "")


# ── _apply_context_window_override ───────────────────────────────────


class TestApplyContextWindowOverride:
    """Tests for _apply_context_window_override."""

    def test_no_override(self):
        """When context_window_size is absent, tracker is unchanged."""
        tracker = FakeTracker(context_window_size=200_000)
        _apply_context_window_override(tracker, {})
        assert tracker.context_window_size == 200_000

    def test_override_applied(self):
        """Override value is coerced to int and applied."""
        tracker = FakeTracker(context_window_size=200_000)
        _apply_context_window_override(tracker, {"context_window_size": "150000"})
        assert tracker.context_window_size == 150_000

    def test_override_none_value(self):
        """Explicit None value does not change tracker."""
        tracker = FakeTracker(context_window_size=200_000)
        _apply_context_window_override(tracker, {"context_window_size": None})
        assert tracker.context_window_size == 200_000

    def test_override_int_value(self):
        """Integer value applied directly."""
        tracker = FakeTracker(context_window_size=200_000)
        _apply_context_window_override(tracker, {"context_window_size": 100_000})
        assert tracker.context_window_size == 100_000

    def test_override_invalid_string_suppressed(self):
        """Invalid string value triggers ValueError, suppressed by contextlib."""
        tracker = FakeTracker(context_window_size=200_000)
        _apply_context_window_override(tracker, {"context_window_size": "not_a_number"})
        # Exception suppressed, value unchanged
        assert tracker.context_window_size == 200_000


# ── _run_auto_detect ─────────────────────────────────────────────────


class TestRunAutoDetect:
    """Tests for _run_auto_detect."""

    def test_auto_detect_enabled_calls_update_mode(self):
        """When auto_detect_enabled, calls update_mode with correct args."""
        habit_state = FakeHabitState()
        compass = FakeCompass(event_counter=15)
        cp_config = FakeCpConfig()
        with patch("lintgate.habit_mode.update_mode", return_value="entered") as mock_update:
            result = _run_auto_detect(habit_state, compass, {}, cp_config, auto_detect_enabled=True)
            assert result == "entered"
            mock_update.assert_called_once_with(
                habit_state,
                15,
                enter_score=0.70,
                exit_score=0.40,
                sustain_calls=5,
            )

    def test_auto_detect_enabled_with_overrides(self):
        """Overrides take precedence over cp_config for enter/exit/sustain."""
        habit_state = FakeHabitState()
        compass = FakeCompass(event_counter=20)
        cp_config = FakeCpConfig()
        overrides = {
            "enter_score": 0.80,
            "exit_score": 0.30,
            "sustain_calls": 10,
        }
        with patch("lintgate.habit_mode.update_mode", return_value=None) as mock_update:
            result = _run_auto_detect(
                habit_state, compass, overrides, cp_config, auto_detect_enabled=True
            )
            assert result is None
            mock_update.assert_called_once_with(
                habit_state,
                20,
                enter_score=0.80,
                exit_score=0.30,
                sustain_calls=10,
            )

    def test_auto_detect_disabled_active_increments_events(self):
        """When auto_detect disabled and habit active, increments total_events."""
        habit_state = FakeHabitState(active=True, total_events_in_habit=3)
        compass = FakeCompass()
        cp_config = FakeCpConfig()
        result = _run_auto_detect(habit_state, compass, {}, cp_config, auto_detect_enabled=False)
        assert result is None
        assert habit_state.total_events_in_habit == 4

    def test_auto_detect_disabled_inactive_no_increment(self):
        """When auto_detect disabled and habit inactive, no increment."""
        habit_state = FakeHabitState(active=False, total_events_in_habit=3)
        compass = FakeCompass()
        cp_config = FakeCpConfig()
        result = _run_auto_detect(habit_state, compass, {}, cp_config, auto_detect_enabled=False)
        assert result is None
        assert habit_state.total_events_in_habit == 3


# ── _build_action_entry ──────────────────────────────────────────────


class TestBuildActionEntry:
    """Tests for _build_action_entry."""

    def test_dict_input_with_file_path(self):
        """Dict input with file_path extracts sig."""
        sig, cmd = _build_action_entry("Read", {"file_path": "/foo/bar.py"})
        assert sig == "/foo/bar.py"
        assert cmd == ""

    def test_dict_input_with_path(self):
        """Dict input with 'path' key (no file_path)."""
        sig, cmd = _build_action_entry("Grep", {"path": "/src", "pattern": "x"})
        assert sig == "/src"
        assert cmd == ""

    def test_dict_input_with_command(self):
        """Dict input with 'command' key for Bash tool."""
        sig, cmd = _build_action_entry("Bash", {"command": "pytest tests/"})
        assert sig == "pytest tests/"  # Bash overrides sig with command_text
        assert cmd == "pytest tests/"

    def test_dict_input_bash_overrides_sig(self):
        """For Bash, sig is always the command text regardless of file_path."""
        sig, cmd = _build_action_entry("Bash", {"file_path": "/foo", "command": "ls -la"})
        assert sig == "ls -la"
        assert cmd == "ls -la"

    def test_string_input(self):
        """String input becomes command_text."""
        sig, cmd = _build_action_entry("Edit", "some string input")
        assert sig == ""  # Non-Bash, string input has no file_path
        assert cmd == "some string input"

    def test_string_input_bash(self):
        """String input for Bash: sig = command_text."""
        sig, cmd = _build_action_entry("Bash", "echo hello")
        assert sig == "echo hello"
        assert cmd == "echo hello"

    def test_dict_input_no_keys(self):
        """Dict with no relevant keys gives empty strings."""
        sig, cmd = _build_action_entry("Read", {"other": "val"})
        assert sig == ""
        assert cmd == ""

    def test_non_dict_non_string_input(self):
        """Non-dict, non-string input gives empty strings."""
        sig, cmd = _build_action_entry("Read", 42)
        assert sig == ""
        assert cmd == ""

    def test_none_input(self):
        """None input gives empty strings."""
        sig, cmd = _build_action_entry("Read", None)
        assert sig == ""
        assert cmd == ""

    def test_dict_file_path_none(self):
        """Dict with file_path=None falls through to path key."""
        sig, cmd = _build_action_entry("Read", {"file_path": None, "path": "/x"})
        assert sig == "/x"

    def test_dict_all_none_keys(self):
        """Dict with all None values gives empty sig."""
        sig, cmd = _build_action_entry("Read", {"file_path": None, "path": None})
        assert sig == ""
        assert cmd == ""


# ── _update_action_ring ──────────────────────────────────────────────


class TestUpdateActionRing:
    """Tests for _update_action_ring."""

    def test_append_to_empty_ring(self):
        """Appending to an empty ring creates one entry."""
        with patch("time.time", return_value=1000.0):
            ring, cmd = _update_action_ring([], "Read", {"file_path": "/foo.py"})
            assert len(ring) == 1
            assert ring[0]["tool"] == "Read"
            assert ring[0]["ts"] == 1000.0
            assert ring[0]["sig"] == "/foo.py"
            assert cmd == ""

    def test_bash_command_text_returned(self):
        """For Bash tool, command_text is returned."""
        with patch("time.time", return_value=1.0):
            ring, cmd = _update_action_ring([], "Bash", {"command": "pytest"})
            assert cmd == "pytest"

    def test_ring_trimmed_to_max(self):
        """Ring is trimmed to MAX_ACTION_RING when it exceeds the limit."""
        from lintgate._habit_types import MAX_ACTION_RING

        existing = [
            {"tool": f"T{i}", "ts": i, "intent": "x", "sig": ""} for i in range(MAX_ACTION_RING)
        ]
        with patch("time.time", return_value=9999.0):
            ring, _ = _update_action_ring(existing, "Read", {"file_path": "/new.py"})
            assert len(ring) == MAX_ACTION_RING
            # Last entry should be the newly appended one
            assert ring[-1]["tool"] == "Read"
            assert ring[-1]["ts"] == 9999.0
            # First entry should be T1 (T0 was trimmed)
            assert ring[0]["tool"] == "T1"

    def test_ring_at_max_not_trimmed(self):
        """Ring at exactly MAX_ACTION_RING is not trimmed until overflow."""
        from lintgate._habit_types import MAX_ACTION_RING

        existing = [
            {"tool": f"T{i}", "ts": i, "intent": "x", "sig": ""} for i in range(MAX_ACTION_RING - 1)
        ]
        with patch("time.time", return_value=1.0):
            ring, _ = _update_action_ring(existing, "Read", {"file_path": "/f.py"})
            assert len(ring) == MAX_ACTION_RING


# ── _detect_bash_signals ────────────────────────────────────────────


class TestDetectBashSignals:
    """Tests for _detect_bash_signals."""

    def test_non_bash_skipped(self):
        """Non-Bash tool is skipped entirely."""
        habit_state = FakeHabitState()
        signal_fires: dict = {}
        with patch("lintgate.habit_mode.detect_test_result") as mock_detect:
            _detect_bash_signals("Read", "output", "cmd", habit_state, signal_fires)
            mock_detect.assert_not_called()
        assert signal_fires == {}

    def test_bash_pytest_command_calls_detect(self):
        """Bash with pytest in command calls detect_test_result."""
        habit_state = FakeHabitState()
        signal_fires: dict = {}
        with patch("lintgate.habit_mode.detect_test_result") as mock_detect:
            _detect_bash_signals("Bash", "3 passed", "pytest tests/", habit_state, signal_fires)
            mock_detect.assert_called_once_with(habit_state, "3 passed", "pytest tests/")

    def test_bash_test_keyword_calls_detect(self):
        """Bash with 'test' keyword in command calls detect_test_result."""
        habit_state = FakeHabitState()
        signal_fires: dict = {}
        with patch("lintgate.habit_mode.detect_test_result") as mock_detect:
            _detect_bash_signals("Bash", "ok", "python -m test foo", habit_state, signal_fires)
            mock_detect.assert_called_once()

    def test_bash_no_test_keyword_no_detect(self):
        """Bash without test keyword does not call detect_test_result."""
        habit_state = FakeHabitState()
        signal_fires: dict = {}
        with patch("lintgate.habit_mode.detect_test_result") as mock_detect:
            _detect_bash_signals("Bash", "files listed", "ls -la", habit_state, signal_fires)
            mock_detect.assert_not_called()

    def test_bash_error_in_output_tracks_signal_fire(self):
        """Error in output increments command_failure signal fire."""
        habit_state = FakeHabitState()
        signal_fires: dict = {}
        with patch("lintgate.habit_mode.detect_test_result"):
            _detect_bash_signals(
                "Bash", "Error: file not found", "cat foo.py", habit_state, signal_fires
            )
        assert signal_fires["command_failure"] == 1

    def test_bash_traceback_in_output_tracks_signal_fire(self):
        """Traceback in output increments command_failure."""
        habit_state = FakeHabitState()
        signal_fires: dict = {}
        with patch("lintgate.habit_mode.detect_test_result"):
            _detect_bash_signals(
                "Bash",
                "Traceback (most recent call last):\n  ...",
                "python foo.py",
                habit_state,
                signal_fires,
            )
        assert signal_fires["command_failure"] == 1

    def test_bash_multiple_errors_accumulate(self):
        """Multiple error outputs accumulate command_failure count."""
        habit_state = FakeHabitState()
        signal_fires: dict = {"command_failure": 2}
        with patch("lintgate.habit_mode.detect_test_result"):
            _detect_bash_signals("Bash", "Error occurred", "cmd1", habit_state, signal_fires)
        assert signal_fires["command_failure"] == 3

    def test_bash_non_string_output(self):
        """Non-string output is treated as empty — no signal fire."""
        habit_state = FakeHabitState()
        signal_fires: dict = {}
        with patch("lintgate.habit_mode.detect_test_result"):
            _detect_bash_signals("Bash", {"result": "error"}, "cmd", habit_state, signal_fires)
        assert signal_fires == {}

    def test_bash_empty_command_no_detect(self):
        """Empty command_text skips detect_test_result call."""
        habit_state = FakeHabitState()
        signal_fires: dict = {}
        with patch("lintgate.habit_mode.detect_test_result") as mock_detect:
            _detect_bash_signals("Bash", "output", "", habit_state, signal_fires)
            mock_detect.assert_not_called()

    def test_bash_clean_output_no_signal_fire(self):
        """Kill VALUE_3/4: clean output must NOT trigger signal_fires ('error'/'traceback' specific)."""
        habit_state = FakeHabitState()
        signal_fires: dict = {}
        with patch("lintgate.habit_mode.detect_test_result"):
            _detect_bash_signals(
                "Bash", "all tests passed successfully", "pytest", habit_state, signal_fires
            )
        assert signal_fires == {}

    def test_bash_non_string_output_defaults_empty(self):
        """Kill VALUE_1: non-string output must become '' not 'mutated'."""
        habit_state = FakeHabitState()
        signal_fires: dict = {}
        with patch("lintgate.habit_mode.detect_test_result") as mock_detect:
            _detect_bash_signals("Bash", 12345, "pytest tests/", habit_state, signal_fires)
            mock_detect.assert_called_once_with(habit_state, "", "pytest tests/")


# ── _apply_path_b_telemetry ─────────────────────────────────────────


class TestApplyPathBTelemetry:
    """Tests for _apply_path_b_telemetry."""

    def test_not_multiple_of_50(self):
        """No telemetry when event_counter is not a multiple of 50."""
        fires = {"command_failure": 3}
        result = _apply_path_b_telemetry(25, fires)
        assert result is fires  # Same dict returned unchanged

    def test_zero_event_counter(self):
        """event_counter=0 does not trigger (0 % 50 == 0 but 0 > 0 is False)."""
        fires = {"command_failure": 3}
        result = _apply_path_b_telemetry(0, fires)
        assert result is fires

    def test_empty_signal_fires(self):
        """Empty signal_fires at event 50 does not trigger."""
        result = _apply_path_b_telemetry(50, {})
        assert result == {}

    def test_multiple_of_50_with_fires_applies(self):
        """At event 50 with fires, apply_telemetry_update is called."""
        fires = {"command_failure": 5}
        mock_profile = MagicMock()
        mock_profile.confidence = 0.8
        mock_store = MagicMock()
        mock_store.profiles = {"anthropic:claude-opus-4": mock_profile}
        with (
            patch(
                "lintgate.controlplane.model.profiles.load_profiles",
                return_value=mock_store,
            ),
            patch("lintgate.controlplane.model.profiles.save_profiles") as mock_save,
            patch("lintgate.controlplane.model.profiles.apply_telemetry_update") as mock_apply,
        ):
            result = _apply_path_b_telemetry(50, fires)
            assert result == {}  # Cleared after application
            mock_apply.assert_called_once_with(mock_profile, fires, 50)
            mock_save.assert_called_once_with(mock_store)

    def test_multiple_of_50_profile_zero_confidence_skipped(self):
        """Profile with confidence=0 is skipped but save_profiles still runs.

        The for-loop skips the update (confidence=0), but after the loop,
        save_profiles() and return {} both execute unconditionally.
        """
        fires = {"command_failure": 5}
        mock_profile = MagicMock()
        mock_profile.confidence = 0
        mock_store = MagicMock()
        mock_store.profiles = {"model": mock_profile}
        with (
            patch(
                "lintgate.controlplane.model.profiles.load_profiles",
                return_value=mock_store,
            ),
            patch("lintgate.controlplane.model.profiles.save_profiles") as mock_save,
            patch("lintgate.controlplane.model.profiles.apply_telemetry_update") as mock_apply,
        ):
            result = _apply_path_b_telemetry(100, fires)
            mock_apply.assert_not_called()
            mock_save.assert_called_once_with(mock_store)
            # return {} executes after save_profiles, clearing fires
            assert result == {}

    def test_exception_in_telemetry_suppressed(self):
        """Exceptions during telemetry application are suppressed."""
        fires = {"command_failure": 2}
        with patch(
            "lintgate.controlplane.model.profiles.load_profiles",
            side_effect=RuntimeError("boom"),
        ):
            result = _apply_path_b_telemetry(50, fires)
            # Exception suppressed, original fires returned
            assert result is fires


# ── _run_mode_transition ─────────────────────────────────────────────


class TestRunModeTransition:
    """Tests for _run_mode_transition."""

    def test_auto_detect_on_calls_update_mode(self):
        """When auto_detect is enabled, calls update_mode."""
        habit_state = FakeHabitState()
        cp_config = FakeCpConfig(habit_mode_auto_detect=True)
        with (
            patch("lintgate.habit_mode.update_mode", return_value="entered") as mock_update,
            patch("lintgate.state.log_metric") as mock_log,
        ):
            result = _run_mode_transition(habit_state, 10, {}, cp_config, "/proj")
            assert result == "entered"
            mock_update.assert_called_once_with(
                habit_state,
                10,
                enter_score=0.70,
                exit_score=0.40,
                sustain_calls=5,
            )
            # Transition logged
            mock_log.assert_called_once()
            logged = mock_log.call_args[0][0]
            assert logged["event"] == "habit_mode_transition"
            assert logged["trigger"] == "auto_detect_lightweight"

    def test_auto_detect_on_no_transition(self):
        """When auto_detect returns None, no log emitted."""
        habit_state = FakeHabitState()
        cp_config = FakeCpConfig(habit_mode_auto_detect=True)
        with (
            patch("lintgate.habit_mode.update_mode", return_value=None),
            patch("lintgate.state.log_metric") as mock_log,
        ):
            result = _run_mode_transition(habit_state, 10, {}, cp_config, "/proj")
            assert result is None
            mock_log.assert_not_called()

    def test_auto_detect_off_active_increments(self):
        """When auto_detect disabled and active, increments total_events."""
        habit_state = FakeHabitState(active=True, total_events_in_habit=5)
        cp_config = FakeCpConfig(habit_mode_auto_detect=False)
        result = _run_mode_transition(habit_state, 10, {}, cp_config, "/proj")
        assert result is None
        assert habit_state.total_events_in_habit == 6

    def test_auto_detect_off_inactive_no_increment(self):
        """When auto_detect disabled and inactive, no increment."""
        habit_state = FakeHabitState(active=False, total_events_in_habit=5)
        cp_config = FakeCpConfig(habit_mode_auto_detect=False)
        result = _run_mode_transition(habit_state, 10, {}, cp_config, "/proj")
        assert result is None
        assert habit_state.total_events_in_habit == 5

    def test_overrides_take_precedence(self):
        """Overrides for enter_score/exit_score/sustain_calls take precedence."""
        habit_state = FakeHabitState()
        cp_config = FakeCpConfig()
        overrides = {
            "auto_detect": True,
            "enter_score": 0.90,
            "exit_score": 0.20,
            "sustain_calls": 15,
        }
        with (
            patch("lintgate.habit_mode.update_mode", return_value=None) as mock_update,
            patch("lintgate.state.log_metric"),
        ):
            _run_mode_transition(habit_state, 10, overrides, cp_config, "/proj")
            mock_update.assert_called_once_with(
                habit_state,
                10,
                enter_score=0.90,
                exit_score=0.20,
                sustain_calls=15,
            )


# ── record_behavior_event ────────────────────────────────────────────


class TestRecordBehaviorEvent:
    """Tests for record_behavior_event."""

    def test_returns_early_if_behavior_channel_disabled(self):
        """Does nothing when behavior channel is disabled."""
        cp_config = FakeCpConfig(_enabled_channels={"behavior": False}, session_memory=True)
        with (
            patch("lintgate.controlplane.session_memory.get_or_create_session") as mock_session,
            patch("lintgate.controlplane.session_memory.save_session") as mock_save,
        ):
            result = record_behavior_event(cp_config, "/proj", "Read", {}, "output")
            assert result is None
            mock_session.assert_not_called()
            mock_save.assert_not_called()

    def test_returns_early_if_session_memory_off(self):
        """Does nothing when session_memory is disabled."""
        cp_config = FakeCpConfig(session_memory=False)
        with (
            patch("lintgate.controlplane.session_memory.get_or_create_session") as mock_session,
            patch("lintgate.controlplane.session_memory.save_session") as mock_save,
        ):
            result = record_behavior_event(cp_config, "/proj", "Read", {}, "output")
            assert result is None
            mock_session.assert_not_called()
            mock_save.assert_not_called()

    def test_records_event_and_calls_path_a_when_habit_enabled(self):
        """Full path: records event, saves compass, calls Path A."""
        cp_config = FakeCpConfig(
            session_memory=True,
            habit_mode_enabled=True,
            _enabled_channels={"behavior": True},
        )
        mock_session = MagicMock()
        mock_session.behavior_compass = {}
        mock_compass = FakeCompass()

        # Use module-level patches since imports are lazy inside contextlib.suppress
        session_mod = "lintgate.controlplane.session_memory"
        with (
            patch(f"{session_mod}.get_or_create_session", return_value=mock_session),
            patch(f"{session_mod}.load_behavior_compass", return_value=mock_compass),
            patch("lintgate.controlplane.behavior_compass.record_tool_event") as mock_record,
            patch(f"{session_mod}.save_behavior_compass") as mock_save_compass,
            patch(f"{session_mod}.save_session") as mock_save_session,
            patch("lintgate.hooks.habit._update_habit_mode_path_a") as mock_path_a,
            patch("lintgate.hooks.runtime_state.refresh_runtime_state_with_session"),
        ):
            result = record_behavior_event(cp_config, "/proj", "Edit", {"file_path": "/a"}, "ok")
            assert result is None
            mock_record.assert_called_once_with(mock_compass, "Edit", {"file_path": "/a"}, "ok")
            mock_save_compass.assert_called_once_with(mock_session, mock_compass)
            mock_path_a.assert_called_once_with(
                cp_config, mock_session, mock_compass, "/proj", "Edit", {"file_path": "/a"}, "ok"
            )
            mock_save_session.assert_called_once_with(mock_session)

    def test_calls_refresh_runtime_when_habit_disabled(self):
        """When habit_mode disabled, calls refresh_runtime_state_with_session instead."""
        cp_config = FakeCpConfig(
            session_memory=True,
            habit_mode_enabled=False,
            _enabled_channels={"behavior": True},
        )
        mock_session = MagicMock()
        mock_session.behavior_compass = {}
        mock_compass = FakeCompass()

        session_mod = "lintgate.controlplane.session_memory"
        with (
            patch(f"{session_mod}.get_or_create_session", return_value=mock_session),
            patch(f"{session_mod}.load_behavior_compass", return_value=mock_compass),
            patch("lintgate.controlplane.behavior_compass.record_tool_event"),
            patch(f"{session_mod}.save_behavior_compass"),
            patch(f"{session_mod}.save_session"),
            patch(
                "lintgate.hooks.runtime_state.refresh_runtime_state_with_session"
            ) as mock_refresh,
            patch("lintgate.hooks.habit._update_habit_mode_path_a") as mock_path_a,
        ):
            result = record_behavior_event(cp_config, "/proj", "Read", {}, "out")
            assert result is None
            mock_path_a.assert_not_called()
            mock_refresh.assert_called_once_with(
                "/proj",
                mock_session,
                compass=mock_compass,
                tool_name="Read",
                tool_input={},
                trigger="tool_call",
            )


# ── record_habit_event_lightweight ───────────────────────────────────


class TestRecordHabitEventLightweight:
    """Tests for record_habit_event_lightweight (Path B)."""

    def test_returns_early_if_habit_disabled(self):
        """Does nothing when habit_mode is disabled."""
        cp_config = FakeCpConfig(habit_mode_enabled=False)
        with patch("lintgate.hooks.habit._load_standalone_state") as mock_load:
            record_habit_event_lightweight(cp_config, "/p", "Read", {}, "out")
            mock_load.assert_not_called()

    def test_returns_early_if_session_memory_and_behavior_on(self):
        """Does nothing when session_memory is on and behavior channel enabled."""
        cp_config = FakeCpConfig(
            habit_mode_enabled=True,
            session_memory=True,
            _enabled_channels={"behavior": True},
        )
        with patch("lintgate.hooks.habit._load_standalone_state") as mock_load:
            record_habit_event_lightweight(cp_config, "/p", "Read", {}, "out")
            mock_load.assert_not_called()

    def test_runs_full_path_b_pipeline(self):
        """Exercises the full Path B pipeline with mocks."""
        cp_config = FakeCpConfig(
            habit_mode_enabled=True,
            session_memory=False,
        )
        habit_state = FakeHabitState(active=True)
        tracker = FakeTracker(tool_call_count=10)
        action_ring: list[Any] = []
        standalone_state: tuple[Any, ...] = (
            habit_state,
            action_ring,
            {},  # extras
            tracker,
            {},  # overrides
            {},  # scheduler
            None,  # last_snapshot
            {},  # signal_fires
        )
        with (
            patch(
                "lintgate.hooks.habit._load_standalone_state",
                return_value=standalone_state,
            ),
            patch("lintgate.hooks.habit._apply_context_window_override"),
            patch(
                "lintgate.hooks.habit._update_action_ring",
                return_value=(action_ring, "pytest tests/"),
            ),
            patch("lintgate.habit_mode.update_signals"),
            patch("lintgate.habit_mode.track_active_files"),
            patch("lintgate.token_tracker.estimate_tool_tokens"),
            patch("lintgate.hooks.habit._detect_bash_signals"),
            patch("lintgate.hooks.habit._apply_path_b_telemetry", return_value={}),
            patch("lintgate.hooks.habit._run_mode_transition", return_value=None),
            patch("lintgate.hooks.habit.check_habit_api_calibration"),
            patch(
                "lintgate.hooks.habit.try_habit_compaction",
                return_value=(False, None),
            ),
            patch(
                "lintgate.hooks.runtime_state.refresh_runtime_state_lightweight",
                return_value=None,
            ),
            patch("lintgate.habit_mode.save_habit_state_standalone") as mock_save,
        ):
            record_habit_event_lightweight(
                cp_config, "/proj", "Bash", {"command": "pytest tests/"}, "3 passed"
            )
            mock_save.assert_called_once()
            call_kwargs = mock_save.call_args
            assert call_kwargs[0][0] == "/proj"
            assert call_kwargs[0][1] is habit_state

    def test_compaction_updates_last_snapshot(self):
        """When compaction succeeds, last_snapshot is updated in save call."""
        cp_config = FakeCpConfig(habit_mode_enabled=True, session_memory=False)
        habit_state = FakeHabitState(active=True)
        tracker = FakeTracker(tool_call_count=10)
        compact_snap: dict[str, Any] = {"theory": "compressed"}
        standalone_state: tuple[Any, ...] = (
            habit_state,
            [],  # action_ring
            {},  # extras
            tracker,
            {},  # overrides
            {},  # scheduler
            None,  # last_snapshot
            {},  # signal_fires
        )
        with (
            patch(
                "lintgate.hooks.habit._load_standalone_state",
                return_value=standalone_state,
            ),
            patch("lintgate.hooks.habit._apply_context_window_override"),
            patch("lintgate.hooks.habit._update_action_ring", return_value=([], "")),
            patch("lintgate.habit_mode.update_signals"),
            patch("lintgate.habit_mode.track_active_files"),
            patch("lintgate.token_tracker.estimate_tool_tokens"),
            patch("lintgate.hooks.habit._detect_bash_signals"),
            patch("lintgate.hooks.habit._apply_path_b_telemetry", return_value={}),
            patch("lintgate.hooks.habit._run_mode_transition", return_value=None),
            patch("lintgate.hooks.habit.check_habit_api_calibration"),
            patch(
                "lintgate.hooks.habit.try_habit_compaction",
                return_value=(True, compact_snap),
            ),
            patch(
                "lintgate.hooks.runtime_state.refresh_runtime_state_lightweight",
                return_value=None,
            ),
            patch("lintgate.habit_mode.save_habit_state_standalone") as mock_save,
        ):
            record_habit_event_lightweight(
                cp_config, "/proj", "Read", {"file_path": "/x"}, "content"
            )
            call_kwargs = mock_save.call_args[1]
            assert call_kwargs["last_snapshot"] is compact_snap


# ── _load_standalone_state ───────────────────────────────────────────


class TestLoadStandaloneState:
    """Tests for _load_standalone_state."""

    def test_returns_correct_tuple_structure(self):
        """Verify the 8-element tuple structure."""
        from lintgate.hooks.habit import _load_standalone_state

        mock_habit_state = FakeHabitState()
        mock_action_ring = [{"tool": "Read"}]
        extras = {
            "token_tracker": {"estimated_tokens_used": 100},
            "config_overrides": {"enter_score": 0.80},
            "write_scheduler": {"cadence": 5},
            "habit_last_snapshot": {"theory": "data"},
            "signal_fire_counts": {"command_failure": 2},
        }
        with (
            patch(
                "lintgate.habit_mode.load_habit_state_standalone",
                return_value=(mock_habit_state, mock_action_ring),
            ),
            patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value=extras,
            ),
        ):
            result = _load_standalone_state("/proj")
            assert len(result) == 8
            habit_state, action_ring, extras_out, tracker, overrides, scheduler, snapshot, fires = (
                result
            )
            assert habit_state is mock_habit_state
            assert action_ring is mock_action_ring
            assert overrides == {"enter_score": 0.80}
            assert scheduler == {"cadence": 5}
            assert snapshot == {"theory": "data"}
            assert fires == {"command_failure": 2}
            # Tracker is constructed from dict
            assert tracker.estimated_tokens_used == 100

    def test_non_dict_extras_fields_default_safely(self):
        """Non-dict values in extras are replaced with safe defaults."""
        from lintgate.hooks.habit import _load_standalone_state

        extras = {
            "token_tracker": "not_a_dict",
            "config_overrides": 42,
            "write_scheduler": ["list"],
            "habit_last_snapshot": "string",
            "signal_fire_counts": True,
        }
        with (
            patch(
                "lintgate.habit_mode.load_habit_state_standalone",
                return_value=(FakeHabitState(), []),
            ),
            patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value=extras,
            ),
        ):
            result = _load_standalone_state("/proj")
            _, _, _, tracker, overrides, scheduler, snapshot, fires = result
            assert overrides == {}
            assert scheduler == {}
            assert snapshot is None
            assert fires == {}
            # tracker is constructed from empty dict (since raw was not a dict)
            assert tracker.estimated_tokens_used == 0

    def test_missing_extras_keys_default_safely(self):
        """Missing keys in extras produce safe defaults."""
        from lintgate.hooks.habit import _load_standalone_state

        with (
            patch(
                "lintgate.habit_mode.load_habit_state_standalone",
                return_value=(FakeHabitState(), []),
            ),
            patch(
                "lintgate.habit_mode.load_standalone_extras",
                return_value={},
            ),
        ):
            result = _load_standalone_state("/proj")
            _, _, _, tracker, overrides, scheduler, snapshot, fires = result
            assert overrides == {}
            assert scheduler == {}
            assert snapshot is None
            assert fires == {}
            assert tracker.estimated_tokens_used == 0
