"""Tests for mcp_tools/habit_tools.py helper functions — mutation kill targets.

Directly tests _load_state, _load_session_context, _impl_declare_mode,
_impl_habit_status, _impl_habit_compact, _impl_habit_configure,
_impl_habit_bootstrap, and the register function.

Focuses on exact value assertions and branch coverage to kill mutants.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from mcp_tools.habit_tools import (
    _impl_declare_mode,
    _impl_habit_bootstrap,
    _impl_habit_compact,
    _impl_habit_configure,
    _impl_habit_status,
    _load_session_context,
    _load_state,
    register,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_session(tmp_path):
    """Build a minimal SessionMemory-like object for mocking."""
    session = MagicMock()
    session.behavior_compass = {
        "habit_mode": {
            "active": True,
            "habit_score": 0.65,
            "sustain_counter": 3,
            "declared": True,
            "signals": {
                "edit_density": 0.0,
                "test_density": 0.0,
                "file_focus": 0.0,
                "sustained_score": 0.0,
            },
            "active_files": ["a.py", "b.py"],
            "last_test_status": "pass",
            "compaction_count": 1,
            "last_compaction_event": 5,
            "entered_at_event": 2,
            "total_events_in_habit": 10,
            "user_message_detected": False,
        },
        "event_counter": 15,
        "token_tracker": {
            "estimated_tokens_used": 40000,
            "char_count_total": 0,
            "calibration_factor": 4.0,
            "calibration_count": 0,
            "last_api_check_event": 0,
            "last_api_actual": 0,
            "last_api_estimate": 0,
            "tool_call_count": 12,
            "tool_calls_since_compact": 8,
            "lines_written": 0,
            "external_tool_calls": 0,
            "lintgate_tool_calls": 0,
            "last_compact_tokens": 0,
            "context_window_size": 200000,
        },
    }
    session.to_dict = lambda: {
        "project_root": str(tmp_path),
        "behavior_compass": session.behavior_compass,
    }
    return session


# ---------------------------------------------------------------------------
# _load_state
# ---------------------------------------------------------------------------


class TestLoadState:
    """Tests for the _load_state helper."""

    def test_session_path_returns_state_tracker_counter(self, tmp_path, monkeypatch, mock_session):
        """Session path returns (state, tracker, event_counter, save_fn)."""
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: mock_session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )

        state, tracker, event_counter, save_fn = _load_state(str(tmp_path))
        assert state.active is True
        assert state.habit_score == 0.65
        assert state.declared is True
        assert event_counter == 15
        assert tracker.estimated_tokens_used == 40000
        assert callable(save_fn)

    def test_session_with_zero_score_and_no_habit_key_falls_to_standalone(
        self, tmp_path, monkeypatch
    ):
        """When session has habit_score=0 and no 'habit_mode' key, falls to standalone."""
        from lintgate.habit_mode import HabitModeState

        empty_session = MagicMock()
        empty_session.behavior_compass = {"event_counter": 5}  # no 'habit_mode'

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: empty_session,
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (HabitModeState(habit_score=0.33, active=False), ["action1"]),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_standalone_extras",
            lambda pr: {"token_tracker": {}, "config_overrides": {}},
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.save_habit_state_standalone",
            lambda *a, **kw: None,
        )

        state, tracker, event_counter, save_fn = _load_state(str(tmp_path))
        assert state.habit_score == 0.33
        assert state.active is False

    def test_standalone_with_non_dict_tracker_uses_default(self, tmp_path, monkeypatch):
        """When standalone extras has non-dict token_tracker, defaults are used."""
        from lintgate.habit_mode import HabitModeState

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("no session")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (HabitModeState(habit_score=0.5), []),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_standalone_extras",
            lambda pr: {
                "token_tracker": "invalid",
                "config_overrides": 42,
                "habit_last_snapshot": "bad",
            },
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.save_habit_state_standalone",
            lambda *a, **kw: None,
        )

        state, tracker, event_counter, save_fn = _load_state(str(tmp_path))
        assert state.habit_score == 0.5
        assert tracker.estimated_tokens_used == 0  # default
        assert event_counter == 0  # tool_call_count from default tracker

    def test_both_paths_fail_returns_in_memory_defaults(self, tmp_path, monkeypatch):
        """When both session and standalone fail, returns in-memory defaults."""
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (_ for _ in ()).throw(RuntimeError("fail")),
        )

        state, tracker, event_counter, save_fn = _load_state(str(tmp_path))
        assert state.active is False
        assert state.habit_score == 0.0
        assert state.declared is False
        assert event_counter == 0
        assert tracker.estimated_tokens_used == 0
        # noop save should not raise
        save_fn(state, tracker)

    def test_standalone_save_fn_calls_save_standalone(self, tmp_path, monkeypatch):
        """Standalone save_fn passes tracker_dict and config_overrides correctly."""
        from lintgate.habit_mode import HabitModeState

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )

        saved_calls = []

        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (HabitModeState(habit_score=0.4), ["ring_item"]),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_standalone_extras",
            lambda pr: {
                "config_overrides": {"enter_score": 0.8},
                "habit_last_snapshot": {"snap": True},
            },
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.save_habit_state_standalone",
            lambda *a, **kw: saved_calls.append(kw),
        )

        state, tracker, event_counter, save_fn = _load_state(str(tmp_path))
        save_fn(state, tracker)

        assert len(saved_calls) == 1
        assert saved_calls[0]["config_overrides"] == {"enter_score": 0.8}
        assert saved_calls[0]["last_snapshot"] == {"snap": True}
        assert isinstance(saved_calls[0]["tracker_dict"], dict)


# ---------------------------------------------------------------------------
# _load_session_context
# ---------------------------------------------------------------------------


class TestLoadSessionContext:
    """Tests for _load_session_context."""

    def test_returns_session_dict_and_compass(self, tmp_path, monkeypatch, mock_session):
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: mock_session,
        )

        session_dict, compass_dict = _load_session_context(str(tmp_path))
        assert "behavior_compass" in session_dict
        assert "habit_mode" in compass_dict
        assert compass_dict["event_counter"] == 15


# ---------------------------------------------------------------------------
# _impl_declare_mode
# ---------------------------------------------------------------------------


class TestImplDeclareMode:
    """Tests for _impl_declare_mode."""

    def test_invalid_mode_returns_json_error(self, tmp_path):
        result = json.loads(_impl_declare_mode(str(tmp_path), "turbo"))
        assert result == {"error": "mode must be 'habit' or 'standard'"}

    def test_invalid_mode_exact_error_text(self, tmp_path):
        result = json.loads(_impl_declare_mode(str(tmp_path), "invalid"))
        assert result["error"] == "mode must be 'habit' or 'standard'"

    def test_habit_mode_returns_status_ok(self, tmp_path, monkeypatch):

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.declare_mode",
            lambda s, mode, ec: "enter" if mode == "habit" else None,
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_declare_mode(str(tmp_path), "habit"))
        assert result["status"] == "ok"
        assert result["mode"] == "habit"
        assert result["habit_score"] == 0.0
        assert "message" in result

    def test_standard_mode_message_says_deactivated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.declare_mode",
            lambda s, mode, ec: "exit",
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_declare_mode(str(tmp_path), "standard"))
        # state.active is False (default), so message says deactivated
        assert "deactivated" in result["message"]

    def test_transition_enter_logs_metric(self, tmp_path, monkeypatch):
        logged_metrics = []

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.declare_mode",
            lambda s, mode, ec: "enter",
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda d: logged_metrics.append(d))
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        _impl_declare_mode(str(tmp_path), "habit")
        assert len(logged_metrics) == 1
        assert logged_metrics[0]["event"] == "habit_mode_transition"
        assert logged_metrics[0]["transition"] == "enter"
        assert logged_metrics[0]["trigger"] == "declaration"

    def test_no_transition_skips_metric_logging(self, tmp_path, monkeypatch):
        logged_metrics = []

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.declare_mode",
            lambda s, mode, ec: None,  # no transition
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda d: logged_metrics.append(d))
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_declare_mode(str(tmp_path), "habit"))
        assert result["status"] == "ok"
        assert len(logged_metrics) == 0


# ---------------------------------------------------------------------------
# _impl_habit_status
# ---------------------------------------------------------------------------


class TestImplHabitStatus:
    """Tests for _impl_habit_status."""

    def test_returns_all_required_keys(self, tmp_path, monkeypatch, mock_session):
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: mock_session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_status(str(tmp_path)))
        assert result["active"] is True
        assert result["habit_score"] == 0.65
        assert result["declared"] is True
        assert result["compaction_count"] == 1
        assert result["events_in_habit"] == 10
        assert result["last_test_status"] == "pass"
        assert isinstance(result["signals"], dict)
        assert isinstance(result["token_economics"], dict)

    def test_active_files_truncated_to_five(self, tmp_path, monkeypatch):
        from lintgate.habit_mode import HabitModeState
        from lintgate.token_tracker import TokenTrackerState

        session = MagicMock()
        state = HabitModeState(
            active=True,
            habit_score=0.5,
            active_files=[f"f{i}.py" for i in range(10)],
        )
        session.behavior_compass = {
            "habit_mode": state.to_dict(),
            "event_counter": 5,
            "token_tracker": TokenTrackerState().to_dict(),
        }

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_status(str(tmp_path)))
        assert len(result["active_files"]) == 5
        assert result["active_files"][0] == "f0.py"
        assert result["active_files"][4] == "f4.py"

    def test_habit_score_rounded_to_three_decimals(self, tmp_path, monkeypatch):
        from lintgate.habit_mode import HabitModeState
        from lintgate.token_tracker import TokenTrackerState

        session = MagicMock()
        state = HabitModeState(habit_score=0.123456789)
        session.behavior_compass = {
            "habit_mode": state.to_dict(),
            "event_counter": 0,
            "token_tracker": TokenTrackerState().to_dict(),
        }

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_status(str(tmp_path)))
        assert result["habit_score"] == 0.123


# ---------------------------------------------------------------------------
# _impl_habit_compact
# ---------------------------------------------------------------------------


class TestImplHabitCompact:
    """Tests for _impl_habit_compact."""

    def test_increments_compaction_count_and_resets_tracker(
        self, tmp_path, monkeypatch, mock_session
    ):
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: mock_session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )

        captured = {}

        def fake_snapshot(state, project_root, **kw):
            captured["session_memory"] = kw.get("session_memory")
            captured["compass"] = kw.get("compass")
            captured["token_estimate"] = kw.get("token_estimate")
            return {"snapshot": "ok"}

        monkeypatch.setattr(
            "lintgate.habit_mode.build_compaction_snapshot",
            fake_snapshot,
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_compact(str(tmp_path)))
        assert result["snapshot"] == "ok"
        # Verify session_memory and compass were loaded
        assert captured["session_memory"] is not None
        assert captured["compass"] is not None
        assert isinstance(captured["token_estimate"], dict)

    def test_compact_with_theory_pack(self, tmp_path, monkeypatch, mock_session):
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: mock_session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )

        captured_kw = {}

        def fake_snapshot(state, project_root, **kw):
            captured_kw.update(kw)
            return {"result": "with_theory"}

        monkeypatch.setattr(
            "lintgate.habit_mode.build_compaction_snapshot",
            fake_snapshot,
        )
        monkeypatch.setattr(
            "lintgate.theory_extractor.build_theory_pack",
            lambda pr: {"facets": ["core"]},
        )
        monkeypatch.setattr(
            "lintgate.state.load_last_run",
            lambda pr: {"lint": "data"},
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_compact(str(tmp_path)))
        assert result["result"] == "with_theory"
        assert captured_kw["theory_pack"] == {"facets": ["core"]}
        assert captured_kw["last_lint_run"] == {"lint": "data"}
        assert captured_kw["issue_memory"] is None

    def test_compact_graceful_when_theory_unavailable(self, tmp_path, monkeypatch, mock_session):
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: mock_session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )

        captured_kw = {}

        def fake_snapshot(state, project_root, **kw):
            captured_kw.update(kw)
            return {"minimal": True}

        monkeypatch.setattr(
            "lintgate.habit_mode.build_compaction_snapshot",
            fake_snapshot,
        )
        # theory_extractor raises
        monkeypatch.setattr(
            "lintgate.theory_extractor.build_theory_pack",
            lambda pr: (_ for _ in ()).throw(ImportError("no theory")),
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_compact(str(tmp_path)))
        assert result["minimal"] is True
        assert captured_kw["theory_pack"] is None


# ---------------------------------------------------------------------------
# _impl_habit_configure
# ---------------------------------------------------------------------------


class TestImplHabitConfigure:
    """Tests for _impl_habit_configure."""

    def test_no_overrides_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)
        result = json.loads(
            _impl_habit_configure(str(tmp_path), None, None, None, None, None, None)
        )
        assert result["status"] == "ok"
        assert result["overrides_applied"] == {}
        assert "0 configuration override" in result["message"]

    def test_compact_threshold_clamped_to_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        # Too low
        result = json.loads(
            _impl_habit_configure(str(tmp_path), 0.01, None, None, None, None, None)
        )
        assert result["overrides_applied"]["compact_threshold"] == 0.1

        # Too high
        result = json.loads(
            _impl_habit_configure(str(tmp_path), 0.99, None, None, None, None, None)
        )
        assert result["overrides_applied"]["compact_threshold"] == 0.9

        # In range
        result = json.loads(_impl_habit_configure(str(tmp_path), 0.5, None, None, None, None, None))
        assert result["overrides_applied"]["compact_threshold"] == 0.5

    def test_enter_score_clamped_to_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_configure(str(tmp_path), None, 0.1, None, None, None, None))
        assert result["overrides_applied"]["enter_score"] == 0.3

        result = json.loads(
            _impl_habit_configure(str(tmp_path), None, 0.99, None, None, None, None)
        )
        assert result["overrides_applied"]["enter_score"] == 0.95

    def test_exit_score_clamped_to_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(
            _impl_habit_configure(str(tmp_path), None, None, 0.05, None, None, None)
        )
        assert result["overrides_applied"]["exit_score"] == 0.1

        result = json.loads(_impl_habit_configure(str(tmp_path), None, None, 0.9, None, None, None))
        assert result["overrides_applied"]["exit_score"] == 0.8

    def test_sustain_calls_clamped_to_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_configure(str(tmp_path), None, None, None, 0, None, None))
        assert result["overrides_applied"]["sustain_calls"] == 1

        result = json.loads(_impl_habit_configure(str(tmp_path), None, None, None, 50, None, None))
        assert result["overrides_applied"]["sustain_calls"] == 20

    def test_token_api_interval_clamped_to_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_configure(str(tmp_path), None, None, None, None, 1, None))
        assert result["overrides_applied"]["token_api_interval"] == 5

        result = json.loads(_impl_habit_configure(str(tmp_path), None, None, None, None, 200, None))
        assert result["overrides_applied"]["token_api_interval"] == 100

    def test_context_window_size_clamped_to_range(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_configure(str(tmp_path), None, None, None, None, None, 100))
        assert result["overrides_applied"]["context_window_size"] == 10000

        result = json.loads(
            _impl_habit_configure(str(tmp_path), None, None, None, None, None, 999999)
        )
        assert result["overrides_applied"]["context_window_size"] == 500000

    def test_all_overrides_at_once(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_configure(str(tmp_path), 0.6, 0.7, 0.4, 10, 30, 150000))
        overrides = result["overrides_applied"]
        assert overrides["compact_threshold"] == 0.6
        assert overrides["enter_score"] == 0.7
        assert overrides["exit_score"] == 0.4
        assert overrides["sustain_calls"] == 10
        assert overrides["token_api_interval"] == 30
        assert overrides["context_window_size"] == 150000
        assert "6 configuration override" in result["message"]

    def test_session_storage_path(self, tmp_path, monkeypatch):
        """When session is available, overrides are stored in session."""
        from lintgate.controlplane.session_memory import SessionMemory

        session = SessionMemory(project_root=str(tmp_path))
        saved = []

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: saved.append(s),
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_configure(str(tmp_path), 0.5, None, None, None, None, None))
        assert result["status"] == "ok"
        assert len(saved) == 1
        assert session.behavior_compass["habit_config_overrides"]["compact_threshold"] == 0.5

    def test_standalone_storage_when_session_fails(self, tmp_path, monkeypatch):
        """When session fails, overrides are stored standalone."""
        from lintgate.habit_mode import HabitModeState

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )

        saved_kw: list[dict] = []
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (HabitModeState(), []),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_standalone_extras",
            lambda pr: {"config_overrides": {"existing": 1}},
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.save_habit_state_standalone",
            lambda *a, **kw: saved_kw.append(kw),
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_configure(str(tmp_path), None, 0.8, None, None, None, None))
        assert result["status"] == "ok"
        assert len(saved_kw) == 1
        assert saved_kw[0]["config_overrides"]["existing"] == 1
        assert saved_kw[0]["config_overrides"]["enter_score"] == 0.8


# ---------------------------------------------------------------------------
# _impl_habit_bootstrap
# ---------------------------------------------------------------------------


class TestImplHabitBootstrap:
    """Tests for _impl_habit_bootstrap."""

    def test_returns_error_when_mneme_not_available(self, tmp_path, monkeypatch):
        """When mneme is not importable, returns structured error."""
        import sys

        # Ensure mneme.ingest.session_parser is not importable
        monkeypatch.setitem(sys.modules, "mneme", None)
        monkeypatch.setitem(sys.modules, "mneme.ingest", None)
        monkeypatch.setitem(sys.modules, "mneme.ingest.session_parser", None)

        result = json.loads(_impl_habit_bootstrap(str(tmp_path)))
        assert "error" in result
        assert "mneme" in result["error"]

    def test_returns_error_when_no_sessions_found(self, tmp_path, monkeypatch):
        """When no sessions match the project, returns error."""
        # Mock mneme to be importable with empty sessions
        mock_module = MagicMock()
        mock_module.iter_sessions = lambda: []
        monkeypatch.setattr(
            "mcp_tools.habit_tools._impl_habit_bootstrap.__module__",
            "mcp_tools.habit_tools",
        )
        # We need to patch at the import site
        import sys

        monkeypatch.setitem(sys.modules, "mneme", MagicMock())
        monkeypatch.setitem(sys.modules, "mneme.ingest", MagicMock())
        monkeypatch.setitem(sys.modules, "mneme.ingest.session_parser", mock_module)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        result = json.loads(_impl_habit_bootstrap(str(tmp_path)))
        assert "error" in result
        assert "No sessions found" in result["error"]


# ---------------------------------------------------------------------------
# register
# ---------------------------------------------------------------------------


class TestRegister:
    """Tests for the register() function."""

    def test_register_returns_expected_tool_names(self, tmp_path):
        class FakeMCP:
            def __init__(self):
                self._tools = {}

            def tool(self):
                def dec(fn):
                    self._tools[fn.__name__] = fn
                    return fn

                return dec

        mcp = FakeMCP()
        helpers = {"_validate_project_root": lambda p: str(tmp_path)}
        result = register(mcp, helpers)

        assert "declare_mode" in result
        assert "habit_status" in result
        assert "habit_compact" in result
        assert "habit_configure" in result
        # habit_bootstrap is registered but not in returned dict
        assert "habit_bootstrap" not in result

    def test_registered_tools_are_callable(self, tmp_path):
        class FakeMCP:
            def __init__(self):
                self._tools = {}

            def tool(self):
                def dec(fn):
                    self._tools[fn.__name__] = fn
                    return fn

                return dec

        mcp = FakeMCP()
        helpers = {"_validate_project_root": lambda p: str(tmp_path)}
        result = register(mcp, helpers)

        for name, fn in result.items():
            assert callable(fn), f"{name} is not callable"

    def test_mcp_has_five_tools_registered(self, tmp_path):
        class FakeMCP:
            def __init__(self):
                self._tools = {}

            def tool(self):
                def dec(fn):
                    self._tools[fn.__name__] = fn
                    return fn

                return dec

        mcp = FakeMCP()
        helpers = {"_validate_project_root": lambda p: str(tmp_path)}
        register(mcp, helpers)

        assert len(mcp._tools) == 5
        assert "declare_mode" in mcp._tools
        assert "habit_status" in mcp._tools
        assert "habit_compact" in mcp._tools
        assert "habit_configure" in mcp._tools
        assert "habit_bootstrap" in mcp._tools
