"""Mutation gap tests for mcp_tools/onboarding_tools.py.

Targets:
- _linter_available — VALUE survivors (exact return value assertions)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from mcp_tools.onboarding_tools import _linter_available

# ── _linter_available — exact VALUE assertions ───────────────────────────


def test_linter_available_returns_true_when_available() -> None:
    linter = MagicMock()
    linter.available.return_value = True
    result = _linter_available(linter, "/tmp/project")
    assert result is True
    linter.available.assert_called_once_with(project_root="/tmp/project")


def test_linter_available_returns_false_when_not_available() -> None:
    linter = MagicMock()
    linter.available.return_value = False
    result = _linter_available(linter, "/tmp/project")
    assert result is False
    linter.available.assert_called_once_with(project_root="/tmp/project")


def test_linter_available_returns_true_via_fallback() -> None:
    """When project_root kwarg raises TypeError, falls back to no-arg call."""
    linter = MagicMock()
    linter.available.side_effect = [TypeError("unexpected kwarg"), True]
    result = _linter_available(linter, "/tmp/project")
    assert result is True
    assert linter.available.call_count == 2


def test_linter_available_returns_false_via_fallback() -> None:
    """Fallback no-arg call can also return False."""
    linter = MagicMock()
    linter.available.side_effect = [TypeError("unexpected kwarg"), False]
    result = _linter_available(linter, "/tmp/project")
    assert result is False
    assert linter.available.call_count == 2


def test_linter_available_coerces_truthy_to_true() -> None:
    """Non-bool truthy values are coerced to bool."""
    linter = MagicMock()
    linter.available.return_value = "yes"
    result = _linter_available(linter, "/tmp/project")
    assert result is True


def test_linter_available_coerces_falsy_to_false() -> None:
    """Non-bool falsy values are coerced to bool."""
    linter = MagicMock()
    linter.available.return_value = 0
    result = _linter_available(linter, "/tmp/project")
    assert result is False


def test_linter_available_coerces_none_to_false() -> None:
    linter = MagicMock()
    linter.available.return_value = None
    result = _linter_available(linter, "/tmp/project")
    assert result is False


def test_linter_available_coerces_empty_string_to_false() -> None:
    linter = MagicMock()
    linter.available.return_value = ""
    result = _linter_available(linter, "/tmp/project")
    assert result is False


def test_linter_available_coerces_nonempty_list_to_true() -> None:
    linter = MagicMock()
    linter.available.return_value = [1]
    result = _linter_available(linter, "/tmp/project")
    assert result is True


def test_linter_available_fallback_coerces_truthy_int() -> None:
    linter = MagicMock()
    linter.available.side_effect = [TypeError(), 42]
    result = _linter_available(linter, "/tmp/project")
    assert result is True
