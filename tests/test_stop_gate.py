"""Tests for lintgate/hooks/stop_gate.py -- Stop hook."""

from __future__ import annotations

from unittest.mock import patch

from lintgate.hooks.stop_gate import handle

# -- handle ---------------------------------------------------------------


def test_handle_no_compass_returns_continue() -> None:
    with patch("lintgate.compass_io.load_compass", return_value=None):
        result = handle({"cwd": "/tmp/fake"})
    assert result["continue"] is True
    assert "systemMessage" not in result


def test_handle_with_compass_reports_summary() -> None:
    from lintgate.compass import CompassAxis, CompassState

    compass = CompassState(
        axes={
            "problem": CompassAxis(name="problem", depth=2),
            "solution": CompassAxis(name="solution", depth=1),
        }
    )

    with (
        patch("lintgate.compass_io.load_compass", return_value=compass),
        patch("lintgate.compass.compute_staleness", return_value=0.4),
    ):
        result = handle({"cwd": "/tmp/fake"})
    assert result["continue"] is True
    msg = result.get("systemMessage", "")
    assert "2/4 axes populated" in msg
    assert "staleness=40%" in msg


def test_handle_never_blocks() -> None:
    """Stop hook should always return continue=True."""
    from lintgate.compass import CompassState

    with (
        patch("lintgate.compass_io.load_compass", return_value=CompassState()),
        patch("lintgate.compass.compute_staleness", return_value=0.0),
    ):
        result = handle({"cwd": "/tmp/fake"})
    assert result["continue"] is True


def test_handle_counts_populated_axes() -> None:
    from lintgate.compass import CompassAxis, CompassState

    compass = CompassState(
        axes={
            "problem": CompassAxis(name="problem", depth=3),
            "solution": CompassAxis(name="solution", depth=2),
            "implementation": CompassAxis(name="implementation", depth=1),
            "world": CompassAxis(name="world", depth=0),
        }
    )

    with (
        patch("lintgate.compass_io.load_compass", return_value=compass),
        patch("lintgate.compass.compute_staleness", return_value=0.1),
    ):
        result = handle({"cwd": "/tmp/fake"})
    msg = result.get("systemMessage", "")
    assert "3/4 axes populated" in msg
