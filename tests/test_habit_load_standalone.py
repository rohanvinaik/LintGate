"""Prescriptive spec tests for _load_standalone_state.

Target: habit::_load_standalone_state
100% mutation survival → 8 behavioral claims on type validation paths.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.hooks.habit import _load_standalone_state
from lintgate.token_tracker import TokenTrackerState


def _mock_loaders(extras: dict | None = None):
    """Patch the two lazy-loaded functions at their source modules."""
    if extras is None:
        extras = {}
    return (
        patch(
            "lintgate.habit_mode.load_habit_state_standalone",
            return_value=(MagicMock(), []),
        ),
        patch(
            "lintgate.habit_mode.load_standalone_extras",
            return_value=extras,
        ),
    )


class TestReturnStructure:
    def test_returns_8_tuple(self):
        with _mock_loaders()[0], _mock_loaders()[1]:
            result = _load_standalone_state("/tmp")
            assert isinstance(result, tuple)
            assert len(result) == 8

    def test_tracker_is_token_tracker_state(self):
        with _mock_loaders({"token_tracker": {}})[0], _mock_loaders({"token_tracker": {}})[1]:
            result = _load_standalone_state("/tmp")
            tracker = result[3]
            assert isinstance(tracker, TokenTrackerState)


class TestTypeValidation:
    def test_token_tracker_not_dict_defaults(self):
        """Non-dict token_tracker → defaults to empty dict → TokenTrackerState from {}."""
        extras = {"token_tracker": "bad"}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            assert isinstance(result[3], TokenTrackerState)

    def test_overrides_not_dict_defaults(self):
        """Non-dict config_overrides → defaults to {}."""
        extras = {"config_overrides": 42}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            assert result[4] == {}

    def test_overrides_dict_preserved(self):
        """Dict config_overrides → passed through."""
        extras = {"config_overrides": {"key": "val"}}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            assert result[4] == {"key": "val"}

    def test_scheduler_not_dict_defaults(self):
        """Non-dict write_scheduler → defaults to {}."""
        extras = {"write_scheduler": [1, 2, 3]}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            assert result[5] == {}

    def test_scheduler_dict_preserved(self):
        extras = {"write_scheduler": {"cadence": 10}}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            assert result[5] == {"cadence": 10}

    def test_snapshot_not_dict_defaults_to_none(self):
        """Non-dict habit_last_snapshot → defaults to None."""
        extras = {"habit_last_snapshot": "bad"}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            assert result[6] is None

    def test_snapshot_dict_preserved(self):
        extras = {"habit_last_snapshot": {"ts": 123}}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            assert result[6] == {"ts": 123}

    def test_signal_fires_not_dict_defaults(self):
        """Non-dict signal_fire_counts → defaults to {}."""
        extras = {"signal_fire_counts": True}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            assert result[7] == {}

    def test_signal_fires_dict_preserved(self):
        extras = {"signal_fire_counts": {"approach_cycle": 3}}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            assert result[7] == {"approach_cycle": 3}


class TestKeySpecificity:
    def test_token_tracker_key_is_specific(self):
        """Kill VALUE_0: extras.get('token_tracker', {}) must read 'token_tracker' key specifically."""
        extras = {"token_tracker": {"estimated_tokens_used": 9999, "char_count_total": 42}}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            tracker = result[3]
            assert isinstance(tracker, TokenTrackerState)
            assert tracker.estimated_tokens_used == 9999
            assert tracker.char_count_total == 42


class TestExtrasPassthrough:
    def test_extras_returned_unchanged(self):
        """The extras dict itself is returned as result[2]."""
        extras = {"token_tracker": {}, "custom_key": "value"}
        with _mock_loaders(extras)[0], _mock_loaders(extras)[1]:
            result = _load_standalone_state("/tmp")
            assert result[2] == extras
            assert result[2]["custom_key"] == "value"


class TestAllFieldsMissing:
    def test_empty_extras_safe(self):
        """Empty extras dict → all defaults applied, no crash."""
        with _mock_loaders({})[0], _mock_loaders({})[1]:
            result = _load_standalone_state("/tmp")
            assert isinstance(result[3], TokenTrackerState)
            assert result[4] == {}
            assert result[5] == {}
            assert result[6] is None
            assert result[7] == {}
