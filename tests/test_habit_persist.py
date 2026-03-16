"""Tests for lintgate/_habit_persist.py — session-backed and file-backed persistence."""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003
from typing import Any

from lintgate._habit_persist import (
    _load_existing_standalone,
    _merge_optional_field,
    _project_hash,
    _standalone_path,
    load_error_bootstrap,
    load_habit_state,
    load_habit_state_standalone,
    load_standalone_extras,
    save_habit_state,
    save_habit_state_standalone,
)
from lintgate._habit_types import MAX_ACTION_RING, HabitModeState

# ── _project_hash ───────────────────────────────────────────────────


class TestProjectHash:
    def test_deterministic(self) -> None:
        h1 = _project_hash("/some/project")
        h2 = _project_hash("/some/project")
        assert h1 == h2

    def test_length_is_16(self) -> None:
        h = _project_hash("/any/path")
        assert len(h) == 16

    def test_hex_characters_only(self) -> None:
        h = _project_hash("/test")
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_paths_different_hashes(self) -> None:
        h1 = _project_hash("/project/a")
        h2 = _project_hash("/project/b")
        assert h1 != h2


# ── _standalone_path ────────────────────────────────────────────────


class TestStandalonePath:
    def test_returns_path_with_json_suffix(self) -> None:
        p = _standalone_path("/my/project")
        assert p.suffix == ".json"

    def test_filename_matches_project_hash(self) -> None:
        p = _standalone_path("/my/project")
        expected_stem = _project_hash("/my/project")
        assert p.stem == expected_stem

    def test_parent_is_habit_state_dir(self) -> None:
        p = _standalone_path("/my/project")
        assert p.parent.name == "habit_state"


# ── load_habit_state (session-backed) ───────────────────────────────


class TestLoadHabitState:
    def test_empty_dict_returns_default_state(self) -> None:
        state = load_habit_state({})
        assert state.active is False
        assert state.habit_score == 0.0
        assert state.sustain_counter == 0

    def test_missing_key_returns_default(self) -> None:
        state = load_habit_state({"other_key": 42})
        assert state.active is False
        assert state.declared is False

    def test_round_trips_with_save(self) -> None:
        original = HabitModeState(active=True, habit_score=0.85, sustain_counter=3)
        compass: dict[str, Any] = {}
        save_habit_state(compass, original)
        loaded = load_habit_state(compass)
        assert loaded.active is True
        assert loaded.habit_score == 0.85
        assert loaded.sustain_counter == 3

    def test_loads_declared_field(self) -> None:
        compass: dict[str, Any] = {
            "habit_mode": {"active": True, "declared": True, "habit_score": 0.7}
        }
        state = load_habit_state(compass)
        assert state.declared is True
        assert state.active is True


# ── save_habit_state (session-backed) ───────────────────────────────


class TestSaveHabitState:
    def test_writes_habit_mode_key(self) -> None:
        compass: dict[str, Any] = {}
        state = HabitModeState(active=True, habit_score=0.5)
        save_habit_state(compass, state)
        assert "habit_mode" in compass

    def test_saved_dict_contains_active(self) -> None:
        compass: dict[str, Any] = {}
        state = HabitModeState(active=True)
        save_habit_state(compass, state)
        assert compass["habit_mode"]["active"] is True

    def test_overwrites_previous_value(self) -> None:
        compass: dict[str, Any] = {"habit_mode": {"active": False}}
        state = HabitModeState(active=True, habit_score=0.9)
        save_habit_state(compass, state)
        assert compass["habit_mode"]["active"] is True
        assert compass["habit_mode"]["habit_score"] == 0.9


# ── load_error_bootstrap ───────────────────────────────────────────


class TestLoadErrorBootstrap:
    def test_missing_file_returns_empty_dict(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        result = load_error_bootstrap("/nonexistent/project")
        assert result == {}

    def test_valid_json_returns_data(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        h = _project_hash("/my/project")
        error_file = tmp_path / f"{h}_errors.json"
        error_data = {"sig1": {"count": 3, "first_seen": "2025-01-01"}}
        error_file.write_text(json.dumps(error_data))

        result = load_error_bootstrap("/my/project")
        assert result == error_data

    def test_non_dict_json_returns_empty(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        h = _project_hash("/my/project")
        error_file = tmp_path / f"{h}_errors.json"
        error_file.write_text(json.dumps([1, 2, 3]))

        result = load_error_bootstrap("/my/project")
        assert result == {}

    def test_corrupt_json_returns_empty(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        h = _project_hash("/my/project")
        error_file = tmp_path / f"{h}_errors.json"
        error_file.write_text("{invalid json")

        result = load_error_bootstrap("/my/project")
        assert result == {}


# ── load_habit_state_standalone ─────────────────────────────────────


class TestLoadHabitStateStandalone:
    def test_missing_file_returns_fresh_state(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        state, ring = load_habit_state_standalone("/no/such/project")
        assert state.active is False
        assert state.habit_score == 0.0
        assert ring == []

    def test_valid_file_restores_state(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        h = _project_hash("/test/project")
        state_file = tmp_path / f"{h}.json"
        payload = {
            "habit_state": {"active": True, "habit_score": 0.75, "sustain_counter": 2},
            "action_ring": [{"tool": "Edit", "ts": 100, "intent": "modify"}],
        }
        state_file.write_text(json.dumps(payload))

        state, ring = load_habit_state_standalone("/test/project")
        assert state.active is True
        assert state.habit_score == 0.75
        assert len(ring) == 1
        assert ring[0]["tool"] == "Edit"

    def test_non_dict_json_returns_fresh(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        h = _project_hash("/test/project")
        state_file = tmp_path / f"{h}.json"
        state_file.write_text('"just a string"')

        state, ring = load_habit_state_standalone("/test/project")
        assert state.active is False
        assert ring == []

    def test_corrupt_json_returns_fresh(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        h = _project_hash("/test/project")
        state_file = tmp_path / f"{h}.json"
        state_file.write_text("not valid json!!!")

        state, ring = load_habit_state_standalone("/test/project")
        assert state.active is False
        assert ring == []

    def test_non_list_action_ring_becomes_empty(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        h = _project_hash("/test/project")
        state_file = tmp_path / f"{h}.json"
        payload = {
            "habit_state": {"active": True},
            "action_ring": "not_a_list",
        }
        state_file.write_text(json.dumps(payload))

        state, ring = load_habit_state_standalone("/test/project")
        assert state.active is True
        assert ring == []


# ── load_standalone_extras ──────────────────────────────────────────


class TestLoadStandaloneExtras:
    def test_missing_file_returns_empty(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        result = load_standalone_extras("/no/project")
        assert result == {}

    def test_extracts_known_keys_only(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        h = _project_hash("/proj")
        state_file = tmp_path / f"{h}.json"
        payload = {
            "habit_state": {"active": True},
            "token_tracker": {"total": 5000},
            "config_overrides": {"enter_score": 0.6},
            "unknown_key": "ignored",
        }
        state_file.write_text(json.dumps(payload))

        result = load_standalone_extras("/proj")
        assert "token_tracker" in result
        assert result["token_tracker"] == {"total": 5000}
        assert "config_overrides" in result
        assert "unknown_key" not in result

    def test_non_dict_json_returns_empty(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        h = _project_hash("/proj")
        state_file = tmp_path / f"{h}.json"
        state_file.write_text(json.dumps([1, 2, 3]))

        result = load_standalone_extras("/proj")
        assert result == {}

    def test_corrupt_json_returns_empty(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        h = _project_hash("/proj")
        state_file = tmp_path / f"{h}.json"
        state_file.write_text("{bad")

        result = load_standalone_extras("/proj")
        assert result == {}


# ── _load_existing_standalone ───────────────────────────────────────


class TestLoadExistingStandalone:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result = _load_existing_standalone(tmp_path / "nope.json")
        assert result == {}

    def test_valid_dict_file(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"key": "value"}))
        result = _load_existing_standalone(p)
        assert result == {"key": "value"}

    def test_non_dict_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        p.write_text(json.dumps("a string"))
        result = _load_existing_standalone(p)
        assert result == {}

    def test_corrupt_json_returns_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "state.json"
        p.write_text("{{bad json")
        result = _load_existing_standalone(p)
        assert result == {}


# ── _merge_optional_field ───────────────────────────────────────────


class TestMergeOptionalField:
    def test_new_value_dict_wins(self) -> None:
        data: dict[str, Any] = {}
        existing: dict[str, Any] = {"k": {"old": 1}}
        _merge_optional_field(data, existing, "k", {"new": 2})
        assert data["k"] == {"new": 2}

    def test_none_falls_back_to_existing(self) -> None:
        data: dict[str, Any] = {}
        existing: dict[str, Any] = {"k": {"old": 1}}
        _merge_optional_field(data, existing, "k", None)
        assert data["k"] == {"old": 1}

    def test_none_with_no_existing_leaves_absent(self) -> None:
        data: dict[str, Any] = {}
        existing: dict[str, Any] = {}
        _merge_optional_field(data, existing, "k", None)
        assert "k" not in data

    def test_non_dict_new_value_falls_back(self) -> None:
        data: dict[str, Any] = {}
        existing: dict[str, Any] = {"k": {"old": 1}}
        # Deliberately passing wrong type to test guard
        _merge_optional_field(data, existing, "k", "not_a_dict")  # type: ignore[arg-type]
        assert data["k"] == {"old": 1}

    def test_non_dict_existing_stays_absent(self) -> None:
        data: dict[str, Any] = {}
        existing: dict[str, Any] = {"k": "not_a_dict"}
        _merge_optional_field(data, existing, "k", None)
        assert "k" not in data


# ── save_habit_state_standalone ─────────────────────────────────────


class TestSaveHabitStateStandalone:
    def test_creates_file_and_round_trips(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState(active=True, habit_score=0.8, sustain_counter=5)
        ring = [{"tool": "Read", "ts": 1, "intent": "inspect"}]
        save_habit_state_standalone("/round/trip", state, ring)

        loaded_state, loaded_ring = load_habit_state_standalone("/round/trip")
        assert loaded_state.active is True
        assert loaded_state.habit_score == 0.8
        assert loaded_state.sustain_counter == 5
        assert loaded_ring == ring

    def test_truncates_action_ring_to_max(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        big_ring = [{"tool": f"T{i}", "ts": i, "intent": "x"} for i in range(50)]
        save_habit_state_standalone("/trunc/project", state, big_ring)

        _, loaded_ring = load_habit_state_standalone("/trunc/project")
        assert len(loaded_ring) == MAX_ACTION_RING
        # Should keep the last MAX_ACTION_RING entries
        assert loaded_ring[0]["tool"] == f"T{50 - MAX_ACTION_RING}"

    def test_optional_tracker_dict_persisted(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        tracker = {"total_tokens": 1000}
        save_habit_state_standalone("/tk/project", state, [], tracker_dict=tracker)

        extras = load_standalone_extras("/tk/project")
        assert extras["token_tracker"] == {"total_tokens": 1000}

    def test_preserves_existing_extras_on_resave(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        # First save with tracker
        save_habit_state_standalone("/pres/project", state, [], tracker_dict={"t": 1})
        # Second save without tracker — should preserve from existing
        save_habit_state_standalone("/pres/project", state, [])

        extras = load_standalone_extras("/pres/project")
        assert extras.get("token_tracker") == {"t": 1}

    def test_signal_fire_counts_persisted(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr("lintgate._habit_persist._HABIT_STATE_DIR", tmp_path)
        state = HabitModeState()
        counts = {"BEH001": 3, "BEH002": 1}
        save_habit_state_standalone("/sig/project", state, [], signal_fire_counts=counts)

        extras = load_standalone_extras("/sig/project")
        assert extras["signal_fire_counts"] == counts
