"""MCP tests for Habit Mode tools."""

from __future__ import annotations

import json
from pathlib import Path

from lintgate.habit_mode import (
    HabitModeState,
    load_standalone_extras,
    save_habit_state_standalone,
)
from lintgate.token_tracker import TokenTrackerState


class _FakeMCP:
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


def _register_habit_tools(monkeypatch, habit_dir: Path):
    from mcp_tools import habit_tools

    monkeypatch.setattr("lintgate.habit_mode._HABIT_STATE_DIR", habit_dir)
    _force_standalone_mode(monkeypatch)
    return habit_tools.register(
        _FakeMCP(),
        {
            "_validate_project_root": lambda path: str(Path(path).resolve()),
        },
    )


def test_habit_status_standalone_loads_persisted_tracker(monkeypatch, tmp_path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    tools = _register_habit_tools(monkeypatch, tmp_path / "habit_state")

    state = HabitModeState(active=True, habit_score=0.81)
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

    payload = json.loads(tools["habit_status"](path=str(project)))
    token_econ = payload["token_economics"]
    assert token_econ["estimated_tokens_used"] == 12345
    assert token_econ["tool_call_count"] == 7
    assert token_econ["lines_written"] == 42


def test_habit_configure_standalone_persists_overrides(monkeypatch, tmp_path) -> None:
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
