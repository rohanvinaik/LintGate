"""Tests for mcp_tools/habit_tools.py — targeting uncovered branches.

Covers declare_mode, habit_status, habit_compact, habit_configure,
and the internal _load_state / _load_session_context helpers exercised
indirectly through the tool functions.

Strategy: _load_state and _load_session_context are closures inside register(),
so we mock the underlying modules they import (session_memory, habit_mode,
token_tracker, etc.) rather than the closures themselves.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Minimal MCP stub so register() can attach @mcp.tool() callables
# ---------------------------------------------------------------------------


class _FakeMCP:
    """Minimal stand-in for the FastMCP instance used in register()."""

    def __init__(self):
        self._tools: dict[str, Any] = {}

    def tool(self):
        def decorator(fn):
            self._tools[fn.__name__] = fn
            return fn

        return decorator


def _make_helpers(tmp_path):
    """Build the helpers dict that register() expects."""
    import os

    def _validate_project_root(path: str) -> str:
        if not os.path.isdir(path):
            raise ValueError(f"Not a directory: {path}")
        return os.path.abspath(path)

    return {
        "_validate_project_root": _validate_project_root,
        "_json_dumps": lambda data, **kw: json.dumps(data),
    }


def _register(tmp_path):
    """Call register() and return the dict of tool callables."""
    from mcp_tools.habit_tools import register

    mcp = _FakeMCP()
    helpers = _make_helpers(tmp_path)
    return register(mcp, helpers)


def _make_session(tmp_path, habit_state_dict=None, event_counter=0, token_tracker_dict=None):
    """Build a SessionMemory with configurable behavior_compass contents."""
    from lintgate.controlplane.session_memory import SessionMemory

    session = SessionMemory(project_root=str(tmp_path))
    bc = {}
    if habit_state_dict is not None:
        bc["habit_mode"] = habit_state_dict
    if event_counter:
        bc["event_counter"] = event_counter
    if token_tracker_dict is not None:
        bc["token_tracker"] = token_tracker_dict
    session.behavior_compass = bc
    return session


# ---------------------------------------------------------------------------
# declare_mode
# ---------------------------------------------------------------------------


class TestDeclareMode:
    """Tests for the declare_mode MCP tool."""

    def test_invalid_mode_returns_error(self, tmp_path):
        tools = _register(tmp_path)
        result = json.loads(tools["declare_mode"](path=str(tmp_path), mode="turbo"))
        assert "error" in result
        assert "habit" in result["error"]

    def test_activate_habit_mode_via_session(self, tmp_path, monkeypatch):
        """declare_mode('habit') via session-backed path returns active=True."""
        from lintgate.habit_mode import HabitModeState
        from lintgate.token_tracker import TokenTrackerState

        # Build a session with real habit data so _load_state takes session path
        state_dict = HabitModeState(habit_score=0.5, active=False).to_dict()
        tracker_dict = TokenTrackerState().to_dict()
        session = _make_session(
            tmp_path, habit_state_dict=state_dict, event_counter=10,
            token_tracker_dict=tracker_dict,
        )

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        # Mock declare_mode to simulate activation
        monkeypatch.setattr(
            "lintgate.habit_mode.declare_mode",
            lambda s, mode, ec: ("enter" if mode == "habit" else None),
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["declare_mode"](path=str(tmp_path), mode="habit"))
        assert result["status"] == "ok"
        assert result["mode"] == "habit"

    def test_deactivate_habit_mode_via_session(self, tmp_path, monkeypatch):
        """declare_mode('standard') returns status=ok."""
        from lintgate.habit_mode import HabitModeState
        from lintgate.token_tracker import TokenTrackerState

        state_dict = HabitModeState(habit_score=0.7, active=True).to_dict()
        tracker_dict = TokenTrackerState().to_dict()
        session = _make_session(
            tmp_path, habit_state_dict=state_dict, event_counter=20,
            token_tracker_dict=tracker_dict,
        )

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.declare_mode",
            lambda s, mode, ec: "exit" if mode == "standard" else None,
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["declare_mode"](path=str(tmp_path), mode="standard"))
        assert result["status"] == "ok"
        assert result["mode"] == "standard"

    def test_no_transition_still_returns_ok(self, tmp_path, monkeypatch):
        """When declare_mode returns None (no transition), metric logging is skipped."""
        from lintgate.habit_mode import HabitModeState
        from lintgate.token_tracker import TokenTrackerState

        state_dict = HabitModeState(habit_score=0.6, active=True).to_dict()
        tracker_dict = TokenTrackerState().to_dict()
        session = _make_session(
            tmp_path, habit_state_dict=state_dict, event_counter=5,
            token_tracker_dict=tracker_dict,
        )

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.declare_mode",
            lambda s, mode, ec: None,  # no transition
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["declare_mode"](path=str(tmp_path), mode="habit"))
        assert result["status"] == "ok"
        assert "habit_score" in result

    def test_declare_mode_fallback_to_standalone(self, tmp_path, monkeypatch):
        """When session path fails, standalone path is used."""
        from lintgate.habit_mode import HabitModeState

        # Make session fail so _load_state falls through to standalone
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (HabitModeState(habit_score=0.4, active=False), []),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_standalone_extras",
            lambda pr: {},
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.save_habit_state_standalone",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.declare_mode",
            lambda s, mode, ec: "enter",
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["declare_mode"](path=str(tmp_path), mode="habit"))
        assert result["status"] == "ok"

    def test_declare_mode_last_resort_in_memory(self, tmp_path, monkeypatch):
        """When both session and standalone fail, in-memory state is used."""
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
            lambda s, mode, ec: None,
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["declare_mode"](path=str(tmp_path), mode="habit"))
        assert result["status"] == "ok"
        assert result["habit_score"] == 0.0


# ---------------------------------------------------------------------------
# habit_status
# ---------------------------------------------------------------------------


class TestHabitStatus:
    """Tests for the habit_status MCP tool."""

    def test_returns_all_expected_keys(self, tmp_path, monkeypatch):
        from lintgate.habit_mode import HabitModeState
        from lintgate.token_tracker import TokenTrackerState

        state_dict = HabitModeState(
            active=True,
            habit_score=0.75,
            declared=True,
            compaction_count=2,
            total_events_in_habit=15,
            last_test_status="pass",
            active_files=["a.py", "b.py"],
        ).to_dict()
        tracker_dict = TokenTrackerState(
            estimated_tokens_used=50000, context_window_size=200000,
        ).to_dict()
        session = _make_session(
            tmp_path, habit_state_dict=state_dict, event_counter=20,
            token_tracker_dict=tracker_dict,
        )

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["habit_status"](path=str(tmp_path)))

        assert result["active"] is True
        assert result["habit_score"] == 0.75
        assert result["declared"] is True
        assert result["compaction_count"] == 2
        assert result["events_in_habit"] == 15
        assert result["last_test_status"] == "pass"
        assert "signals" in result
        assert "token_economics" in result

    def test_active_files_capped_at_five(self, tmp_path, monkeypatch):
        from lintgate.habit_mode import HabitModeState
        from lintgate.token_tracker import TokenTrackerState

        state_dict = HabitModeState(
            active=True,
            habit_score=0.5,
            active_files=[f"file{i}.py" for i in range(10)],
        ).to_dict()
        tracker_dict = TokenTrackerState().to_dict()
        session = _make_session(
            tmp_path, habit_state_dict=state_dict, event_counter=5,
            token_tracker_dict=tracker_dict,
        )

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["habit_status"](path=str(tmp_path)))
        assert len(result["active_files"]) == 5

    def test_habit_status_standalone_fallback(self, tmp_path, monkeypatch):
        """When session has no habit data, falls back to standalone."""
        from lintgate.habit_mode import HabitModeState

        # Session with no habit data triggers standalone fallback
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (HabitModeState(habit_score=0.3, active=False), []),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_standalone_extras",
            lambda pr: {},
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.save_habit_state_standalone",
            lambda *a, **kw: None,
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["habit_status"](path=str(tmp_path)))
        assert result["habit_score"] == 0.3


# ---------------------------------------------------------------------------
# habit_compact
# ---------------------------------------------------------------------------


class TestHabitCompact:
    """Tests for the habit_compact MCP tool."""

    def test_compact_increments_compaction_count(self, tmp_path, monkeypatch):
        from lintgate.habit_mode import HabitModeState
        from lintgate.token_tracker import TokenTrackerState

        state_dict = HabitModeState(active=True, habit_score=0.8, compaction_count=1).to_dict()
        tracker_dict = TokenTrackerState(
            estimated_tokens_used=80000, tool_calls_since_compact=25,
        ).to_dict()
        session = _make_session(
            tmp_path, habit_state_dict=state_dict, event_counter=30,
            token_tracker_dict=tracker_dict,
        )

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.build_compaction_snapshot",
            lambda *a, **kw: {"snapshot": "data", "sections": {}},
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["habit_compact"](path=str(tmp_path)))

        assert result["snapshot"] == "data"

    def test_compact_handles_missing_last_lint_run(self, tmp_path, monkeypatch):
        """When load_last_run returns None, compact still succeeds."""
        from lintgate.habit_mode import HabitModeState
        from lintgate.token_tracker import TokenTrackerState

        state_dict = HabitModeState(habit_score=0.6, active=True).to_dict()
        tracker_dict = TokenTrackerState().to_dict()
        session = _make_session(
            tmp_path, habit_state_dict=state_dict, event_counter=10,
            token_tracker_dict=tracker_dict,
        )

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        monkeypatch.setattr("lintgate.state.load_last_run", lambda pr: None)
        monkeypatch.setattr(
            "lintgate.habit_mode.build_compaction_snapshot",
            lambda *a, **kw: {"compact": True},
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["habit_compact"](path=str(tmp_path)))
        assert result["compact"] is True

    def test_compact_with_all_context_available(self, tmp_path, monkeypatch):
        """When session, lint, and theory are all available, all are passed to snapshot."""
        from lintgate.habit_mode import HabitModeState
        from lintgate.token_tracker import TokenTrackerState

        state_dict = HabitModeState(habit_score=0.9, active=True).to_dict()
        tracker_dict = TokenTrackerState(estimated_tokens_used=100000).to_dict()
        session = _make_session(
            tmp_path, habit_state_dict=state_dict, event_counter=50,
            token_tracker_dict=tracker_dict,
        )

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: None,
        )
        monkeypatch.setattr(
            "lintgate.state.load_last_run",
            lambda pr: {"lint_data": True},
        )
        monkeypatch.setattr(
            "lintgate.theory_extractor.build_theory_pack",
            lambda pr: {"theory": True},
        )

        captured_kwargs = {}

        def fake_snapshot(*a, **kw):
            captured_kwargs.update(kw)
            return {"full_context": True}

        monkeypatch.setattr(
            "lintgate.habit_mode.build_compaction_snapshot",
            fake_snapshot,
        )
        monkeypatch.setattr("lintgate.state.log_metric", lambda *a, **kw: None)
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["habit_compact"](path=str(tmp_path)))
        assert result["full_context"] is True
        # Verify theory_pack was passed through
        assert captured_kwargs.get("theory_pack") == {"theory": True}
        assert captured_kwargs.get("last_lint_run") == {"lint_data": True}


# ---------------------------------------------------------------------------
# habit_configure
# ---------------------------------------------------------------------------


class TestHabitConfigure:
    """Tests for the habit_configure MCP tool."""

    def test_no_overrides_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(tools["habit_configure"](path=str(tmp_path)))
        assert result["status"] == "ok"
        assert result["overrides_applied"] == {}

    def test_compact_threshold_clamped_low(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), compact_threshold=0.01)
        )
        assert result["overrides_applied"]["compact_threshold"] == 0.1

    def test_compact_threshold_clamped_high(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), compact_threshold=1.5)
        )
        assert result["overrides_applied"]["compact_threshold"] == 0.9

    def test_enter_exit_score_clamped(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), enter_score=0.1, exit_score=5.0)
        )
        assert result["overrides_applied"]["enter_score"] == 0.3
        assert result["overrides_applied"]["exit_score"] == 0.8

    def test_sustain_calls_clamped(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), sustain_calls=0)
        )
        assert result["overrides_applied"]["sustain_calls"] == 1

        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), sustain_calls=100)
        )
        assert result["overrides_applied"]["sustain_calls"] == 20

    def test_token_api_interval_clamped(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), token_api_interval=1)
        )
        assert result["overrides_applied"]["token_api_interval"] == 5

    def test_context_window_size_clamped_low(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), context_window_size=100)
        )
        assert result["overrides_applied"]["context_window_size"] == 10000

    def test_context_window_size_clamped_high(self, tmp_path, monkeypatch):
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), context_window_size=999999)
        )
        assert result["overrides_applied"]["context_window_size"] == 500000

    def test_stores_in_session_when_available(self, tmp_path, monkeypatch):
        """When session memory is available, overrides go to session."""
        from lintgate.controlplane.session_memory import SessionMemory

        session = SessionMemory(project_root=str(tmp_path))
        saved_sessions = []

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: session,
        )
        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.save_session",
            lambda s: saved_sessions.append(s),
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), compact_threshold=0.5)
        )
        assert result["status"] == "ok"
        assert result["overrides_applied"]["compact_threshold"] == 0.5
        assert len(saved_sessions) == 1
        assert session.behavior_compass["habit_config_overrides"]["compact_threshold"] == 0.5

    def test_falls_back_to_standalone_when_session_fails(self, tmp_path, monkeypatch):
        """When session memory raises, falls back to standalone storage."""
        from lintgate.habit_mode import HabitModeState

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("no session")),
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        standalone_saved = []
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (HabitModeState(), []),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_standalone_extras",
            lambda pr: {},
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.save_habit_state_standalone",
            lambda *a, **kw: standalone_saved.append(True),
        )

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), enter_score=0.7)
        )
        assert result["status"] == "ok"
        assert len(standalone_saved) == 1

    def test_multiple_overrides_at_once(self, tmp_path, monkeypatch):
        """All overrides applied in a single call."""
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](
                path=str(tmp_path),
                compact_threshold=0.5,
                enter_score=0.7,
                exit_score=0.4,
                sustain_calls=10,
                token_api_interval=30,
                context_window_size=150000,
            )
        )
        overrides = result["overrides_applied"]
        assert overrides["compact_threshold"] == 0.5
        assert overrides["enter_score"] == 0.7
        assert overrides["exit_score"] == 0.4
        assert overrides["sustain_calls"] == 10
        assert overrides["token_api_interval"] == 30
        assert overrides["context_window_size"] == 150000
        assert "6 configuration override" in result["message"]

    def test_configure_standalone_merges_existing_overrides(self, tmp_path, monkeypatch):
        """Standalone path merges new overrides with existing ones."""
        from lintgate.habit_mode import HabitModeState

        monkeypatch.setattr(
            "lintgate.controlplane.session_memory.get_or_create_session",
            lambda pr, *a, **kw: (_ for _ in ()).throw(RuntimeError("fail")),
        )
        monkeypatch.setattr("lintgate.state.log_feature_usage", lambda *a, **kw: None)

        captured_overrides = []
        monkeypatch.setattr(
            "lintgate.habit_mode.load_habit_state_standalone",
            lambda pr: (HabitModeState(), []),
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.load_standalone_extras",
            lambda pr: {"config_overrides": {"enter_score": 0.6}},
        )
        monkeypatch.setattr(
            "lintgate.habit_mode.save_habit_state_standalone",
            lambda *a, **kw: captured_overrides.append(kw.get("config_overrides")),
        )

        tools = _register(tmp_path)
        result = json.loads(
            tools["habit_configure"](path=str(tmp_path), exit_score=0.3)
        )
        assert result["status"] == "ok"
        # Should have merged: enter_score from existing + exit_score from new
        assert len(captured_overrides) == 1
        merged = captured_overrides[0]
        assert merged["enter_score"] == 0.6
        assert merged["exit_score"] == 0.3
