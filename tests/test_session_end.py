"""Tests for lintgate/hooks/session_end.py -- SessionEnd hook."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from lintgate.hooks.session_end import _cleanup_session_state, handle


# -- handle ---------------------------------------------------------------


def test_handle_no_compass_returns_continue() -> None:
    with (
        patch("lintgate.compass_io.load_compass", return_value=None),
        patch("lintgate.hooks.session_end._cleanup_session_state"),
    ):
        result = handle({"cwd": "/tmp/fake"})
    assert result["continue"] is True


def test_handle_with_compass_saves_and_returns_continue() -> None:
    from lintgate.compass import CompassState

    compass = CompassState()
    mock_save = MagicMock()

    with (
        patch("lintgate.compass_io.load_compass", return_value=compass),
        patch("lintgate.compass_io.save_compass", mock_save),
        patch("lintgate.hooks.session_end._cleanup_session_state"),
    ):
        result = handle({"cwd": "/tmp/fake"})
    assert result["continue"] is True
    mock_save.assert_called_once()


def test_handle_uses_cwd_from_data() -> None:
    with (
        patch("lintgate.compass_io.load_compass", return_value=None) as mock_load,
        patch("lintgate.hooks.session_end._cleanup_session_state"),
    ):
        handle({"cwd": "/my/project"})
    mock_load.assert_called_once_with("/my/project")


# -- _cleanup_session_state -----------------------------------------------


def test_cleanup_session_state_is_failopen() -> None:
    """Cleanup should never raise, even if sub-calls fail."""
    # _cleanup_session_state uses a bare except, so it should swallow all errors
    _cleanup_session_state("/tmp/nonexistent_project_path_abc123")
