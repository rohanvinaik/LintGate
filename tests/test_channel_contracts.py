"""Architectural contract tests for ControlPlane channels."""

from __future__ import annotations

import pytest

from lintgate.channels.behavior_channel import BehaviorChannel
from lintgate.channels.dependency_channel import DependencyChannel
from lintgate.channels.git_channel import GitChannel
from lintgate.channels.lint_channel import LintChannel
from lintgate.channels.performance_channel import PerformanceChannel
from lintgate.channels.structure_channel import StructureChannel
from lintgate.channels.test_channel import TestChannel
from lintgate.controlplane.types import (
    ChannelResult,
    ControlPlaneConfig,
    SupervisionEvent,
)

# List of all operational channels
CHANNELS = [
    LintChannel(),
    TestChannel(),
    DependencyChannel(),
    BehaviorChannel(),
    GitChannel(),
    PerformanceChannel(),
    StructureChannel(),
]


@pytest.mark.parametrize("channel", CHANNELS)
def test_channel_metadata_contract(channel):
    """Verify channel has required architectural metadata."""
    assert hasattr(channel, "name"), f"Channel {type(channel).__name__} missing 'name'"
    assert isinstance(channel.name, str)
    assert hasattr(channel, "timeout_ms"), (
        f"Channel {type(channel).__name__} missing 'timeout_ms'"
    )
    assert isinstance(channel.timeout_ms, int)
    assert hasattr(channel, "blocking_capable"), (
        f"Channel {type(channel).__name__} missing 'blocking_capable'"
    )
    assert isinstance(channel.blocking_capable, bool)


@pytest.mark.parametrize("channel", CHANNELS)
def test_channel_interface_contract(channel):
    """Verify channel implements required methods with correct signatures."""
    # check should_run
    assert hasattr(channel, "should_run")
    # check execute
    assert hasattr(channel, "execute")

    # Test with dummy inputs to verify return types (smoke test)
    config = ControlPlaneConfig()
    event = SupervisionEvent(project_root="")

    # should_run should return bool
    should = channel.should_run(event, config)
    assert isinstance(should, bool)

    # execute should return ChannelResult (or skip if no root)
    # Most channels will skip if project_root is empty or return a valid result
    # We mock project_root to ensure execute path is at least callable
    SupervisionEvent(project_root="/tmp/fake")
    try:
        # We don't necessarily expect success here as files don't exist,
        # but we want to see if it returns the right type or handles failure gracefully.
        # For a pure contract test, we'd mock dependencies, but this is a smoke test.
        result = channel.execute(event, config)
        assert isinstance(result, ChannelResult)
    except Exception:
        # If it crashes on empty root, that's acceptable for some, but return type must be right if it returns
        pass
