"""Integration tests for lightweight Habit Mode hook path."""

from __future__ import annotations

from dataclasses import dataclass

from lintgate.habit_mode import (
    HabitModeState,
    load_habit_state_standalone,
    load_standalone_extras,
    save_habit_state_standalone,
)
from lintgate.hook_posttooluse import _record_habit_event_lightweight
from lintgate.token_tracker import TokenTrackerState


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


def test_lightweight_path_respects_auto_detect_disabled(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "lintgate.habit_mode._HABIT_STATE_DIR", tmp_path / "habit_state"
    )
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


def test_lightweight_path_non_test_bash_does_not_flip_test_status(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "lintgate.habit_mode._HABIT_STATE_DIR", tmp_path / "habit_state"
    )
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


def test_lightweight_path_auto_compacts_when_threshold_exceeded(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setattr(
        "lintgate.habit_mode._HABIT_STATE_DIR", tmp_path / "habit_state"
    )
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
    save_habit_state_standalone(
        str(project), state, ring, tracker_dict=tracker.to_dict()
    )

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
