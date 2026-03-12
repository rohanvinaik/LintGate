"""Habit Mode persistence — session-backed and file-backed state I/O.

Path A: session.behavior_compass dict (load_habit_state / save_habit_state).
Path B: standalone file per project hash (~/.claude/lintgate/habit_state/).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from lintgate._habit_types import MAX_ACTION_RING, HabitModeState

# ── Session-backed persistence (Path A) ──────────────────────────────


def load_habit_state(behavior_compass_dict: dict[str, Any]) -> HabitModeState:
    """Load habit state from session.behavior_compass dict."""
    data = behavior_compass_dict.get("habit_mode", {})
    return HabitModeState.from_dict(data)


def save_habit_state(behavior_compass_dict: dict[str, Any], state: HabitModeState) -> None:
    """Save habit state into session.behavior_compass dict."""
    behavior_compass_dict["habit_mode"] = state.to_dict()


# ── File-backed persistence (Path B) ─────────────────────────────────

_HABIT_STATE_DIR = Path.home() / ".claude" / "lintgate" / "habit_state"


def _project_hash(project_root: str) -> str:
    """Generate stable hash for a project path."""
    return hashlib.sha256(project_root.encode()).hexdigest()[:16]


def _standalone_path(project_root: str) -> Path:
    """Get standalone state file path for a project."""
    return _HABIT_STATE_DIR / f"{_project_hash(project_root)}.json"


def load_error_bootstrap(project_root: str) -> dict[str, Any]:
    """Load bootstrapped error memory for a project.

    Returns dict of {error_sig: {count, first_seen, last_seen}} or empty dict.
    """
    error_path = _HABIT_STATE_DIR / f"{_project_hash(project_root)}_errors.json"
    if not error_path.exists():
        return {}
    try:
        with open(error_path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def load_habit_state_standalone(
    project_root: str,
) -> tuple[HabitModeState, list[dict[str, Any]]]:
    """Load standalone file-backed habit state.

    Returns (HabitModeState, action_ring) tuple.
    Action ring is a minimal 30-entry buffer of {tool, ts, intent} dicts.
    On corruption or missing file, returns fresh state.
    """
    state_path = _standalone_path(project_root)
    if not state_path.exists():
        return HabitModeState(), []

    try:
        with open(state_path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return HabitModeState(), []

        state = HabitModeState.from_dict(data.get("habit_state", {}))
        action_ring = data.get("action_ring", [])
        if not isinstance(action_ring, list):
            action_ring = []

        return state, action_ring
    except (json.JSONDecodeError, OSError, KeyError):
        return HabitModeState(), []


def load_standalone_extras(project_root: str) -> dict[str, Any]:
    """Load non-core standalone payload fields.

    Returns a dict with optional keys:
    - token_tracker: serialized TokenTrackerState
    - config_overrides: standalone habit_configure overrides
    - habit_last_snapshot: latest auto-compaction snapshot
    - write_scheduler: serialized WriteScheduler state
    """
    state_path = _standalone_path(project_root)
    if not state_path.exists():
        return {}

    try:
        with open(state_path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        extras: dict[str, Any] = {}
        for key in (
            "token_tracker",
            "config_overrides",
            "habit_last_snapshot",
            "write_scheduler",
            "signal_fire_counts",
        ):
            if key in data:
                extras[key] = data[key]
        return extras
    except (json.JSONDecodeError, OSError):
        return {}


def _load_existing_standalone(state_path: Path) -> dict[str, Any]:
    """Load existing standalone state file, returning empty dict on failure."""
    if not state_path.exists():
        return {}
    try:
        with open(state_path) as f:
            loaded = json.load(f)
        return loaded if isinstance(loaded, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _merge_optional_field(
    data: dict[str, Any],
    existing: dict[str, Any],
    key: str,
    new_value: dict[str, Any] | None,
) -> None:
    """Merge an optional dict field: prefer new_value, fall back to existing."""
    if new_value is not None and isinstance(new_value, dict):
        data[key] = new_value
    elif isinstance(existing.get(key), dict):
        data[key] = existing[key]


def save_habit_state_standalone(
    project_root: str,
    state: HabitModeState,
    action_ring: list[dict[str, Any]],
    *,
    tracker_dict: dict[str, Any] | None = None,
    config_overrides: dict[str, Any] | None = None,
    last_snapshot: dict[str, Any] | None = None,
    scheduler_dict: dict[str, Any] | None = None,
    signal_fire_counts: dict[str, int] | None = None,
) -> None:
    """Save standalone file-backed habit state.

    Args:
        project_root: Project path.
        state: Current habit mode state.
        action_ring: Minimal action ring buffer.
        tracker_dict: Optional serialized TokenTrackerState.
        config_overrides: Optional standalone config overrides.
        last_snapshot: Optional latest compaction snapshot.
        scheduler_dict: Optional serialized WriteScheduler state.
        signal_fire_counts: Optional telemetry signal fire accumulation.
    """
    try:
        _HABIT_STATE_DIR.mkdir(parents=True, exist_ok=True)
        state_path = _standalone_path(project_root)
        existing = _load_existing_standalone(state_path)

        data: dict[str, Any] = {
            "habit_state": state.to_dict(),
            "action_ring": action_ring[-MAX_ACTION_RING:],
        }
        _merge_optional_field(data, existing, "token_tracker", tracker_dict)
        _merge_optional_field(data, existing, "config_overrides", config_overrides)
        _merge_optional_field(data, existing, "habit_last_snapshot", last_snapshot)
        _merge_optional_field(data, existing, "write_scheduler", scheduler_dict)
        _merge_optional_field(data, existing, "signal_fire_counts", signal_fire_counts)

        with open(state_path, "w") as f:
            json.dump(data, f, separators=(",", ":"))
    except OSError:
        pass  # Non-fatal
