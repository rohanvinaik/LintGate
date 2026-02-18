"""Phase 4: Dependency channel tests.

Verifies:
- Channel protocol conformance
- should_run logic
- Quick check wrapping of existing dep_health module
- ChannelResult format
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lintgate.channels.dependency_channel import DependencyChannel
from lintgate.controlplane.channel import Channel
from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)
from lintgate.types import ChangeClassification


# ── Protocol conformance ─────────────────────────────────────────────────


def test_dep_channel_conforms_to_protocol() -> None:
    ch = DependencyChannel()
    assert isinstance(ch, Channel)


def test_dep_channel_has_correct_name() -> None:
    assert DependencyChannel.name == "deps"


def test_dep_channel_is_not_blocking() -> None:
    assert DependencyChannel.blocking_capable is False


# ── should_run tests ─────────────────────────────────────────────────────


def test_should_run_on_dependency_change() -> None:
    classification = ChangeClassification(
        change_kind="dependency", risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root="/tmp", tool_name="Edit",
        change_classification=classification,
    )
    assert DependencyChannel().should_run(event, ControlPlaneConfig()) is True


def test_should_run_on_build_command() -> None:
    classification = ChangeClassification(
        change_kind="build", risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root="/tmp", tool_name="Bash",
        change_classification=classification,
    )
    assert DependencyChannel().should_run(event, ControlPlaneConfig()) is True


def test_should_run_on_config_change() -> None:
    classification = ChangeClassification(
        change_kind="config", risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root="/tmp", tool_name="Edit",
        change_classification=classification,
    )
    assert DependencyChannel().should_run(event, ControlPlaneConfig()) is True


def test_should_not_run_on_logic_change() -> None:
    classification = ChangeClassification(
        change_kind="logic", risk_level="moderate",
    )
    event = SupervisionEvent(
        project_root="/tmp", tool_name="Edit",
        change_classification=classification,
    )
    assert DependencyChannel().should_run(event, ControlPlaneConfig()) is False


def test_should_not_run_without_classification() -> None:
    event = SupervisionEvent(
        project_root="/tmp", tool_name="Edit",
        change_classification=None,
    )
    assert DependencyChannel().should_run(event, ControlPlaneConfig()) is False


def test_should_run_on_mcp_without_classification() -> None:
    event = SupervisionEvent(
        surface="mcp",
        project_root="/tmp",
        tool_name="controlplane_run",
        change_classification=None,
    )
    assert DependencyChannel().should_run(event, ControlPlaneConfig()) is True


# ── Execute tests ────────────────────────────────────────────────────────


@patch("lintgate.channels.dependency_channel.DependencyChannel._quick_check")
def test_execute_hook_calls_quick_check(mock_quick: MagicMock) -> None:
    mock_quick.return_value = ([], [])

    classification = ChangeClassification(
        change_kind="dependency", risk_level="moderate",
    )
    event = SupervisionEvent(
        surface="hook",
        project_root="/tmp",
        tool_name="Bash",
        change_classification=classification,
    )

    channel = DependencyChannel()
    result = channel.execute(event, ControlPlaneConfig())

    mock_quick.assert_called_once()
    assert isinstance(result, ChannelResult)
    assert result.channel == "deps"
    assert result.status == "pass"


@patch("lintgate.channels.dependency_channel.DependencyChannel._full_check")
def test_execute_mcp_calls_full_check(mock_full: MagicMock) -> None:
    mock_full.return_value = ([], [])

    classification = ChangeClassification(
        change_kind="dependency", risk_level="moderate",
    )
    event = SupervisionEvent(
        surface="mcp",
        project_root="/tmp",
        tool_name="controlplane_run",
        change_classification=classification,
    )

    channel = DependencyChannel()
    result = channel.execute(event, ControlPlaneConfig())

    mock_full.assert_called_once()
    assert result.status == "pass"


def test_execute_with_no_issues(tmp_path) -> None:
    """No issues → pass status, none severity."""
    classification = ChangeClassification(
        change_kind="config", risk_level="cosmetic",
    )
    event = SupervisionEvent(
        surface="hook",
        project_root=str(tmp_path),
        tool_name="Edit",
        change_classification=classification,
    )

    channel = DependencyChannel()
    result = channel.execute(event, ControlPlaneConfig())

    assert result.status == "pass"
    assert result.severity == "none"


def test_execute_returns_duration() -> None:
    classification = ChangeClassification(
        change_kind="config", risk_level="cosmetic",
    )
    event = SupervisionEvent(
        surface="hook",
        project_root="/tmp",
        tool_name="Edit",
        change_classification=classification,
    )

    channel = DependencyChannel()
    result = channel.execute(event, ControlPlaneConfig())
    assert result.duration_ms >= 0
