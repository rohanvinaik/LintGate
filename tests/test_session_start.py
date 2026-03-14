"""Tests for lintgate/hooks/session_start.py -- SessionStart hook."""

from __future__ import annotations

from unittest.mock import patch

from lintgate.hooks.session_start import handle


# -- handle ---------------------------------------------------------------


def test_handle_no_compass_returns_continue() -> None:
    """When no compass exists, handle returns continue=True with advisory."""
    with (
        patch("lintgate.hooks.session_start._initialize_runtime_state"),
        patch("lintgate.compass_io.load_compass", return_value=None),
    ):
        result = handle({"cwd": "/tmp/fake"})
    assert result["continue"] is True
    assert "No compass found" in result.get("systemMessage", "")


def test_handle_with_compass_returns_staleness(tmp_path: object) -> None:
    """When compass is loaded, handle reports staleness."""
    from lintgate.compass import CompassAxis, CompassState, GapReport

    compass = CompassState(
        axes={"problem": CompassAxis(name="problem", depth=2)},
        gap_report=GapReport(interview_recommended=False),
    )

    with (
        patch("lintgate.hooks.session_start._initialize_runtime_state"),
        patch("lintgate.compass_io.load_compass", return_value=compass),
        patch("lintgate.compass.compute_staleness", return_value=0.3),
    ):
        result = handle({"cwd": str(tmp_path)})
    assert result["continue"] is True
    assert "staleness=30%" in result.get("systemMessage", "")


def test_handle_stale_compass_suggests_update() -> None:
    """When compass is very stale, handle suggests re-extracting."""
    from lintgate.compass import CompassAxis, CompassState, GapReport

    compass = CompassState(
        axes={"problem": CompassAxis(name="problem", depth=1)},
        gap_report=GapReport(interview_recommended=False),
    )

    with (
        patch("lintgate.hooks.session_start._initialize_runtime_state"),
        patch("lintgate.compass_io.load_compass", return_value=compass),
        patch("lintgate.compass.compute_staleness", return_value=0.9),
    ):
        result = handle({"cwd": "/tmp/fake"})
    msg = result.get("systemMessage", "")
    assert "stale" in msg
