"""Tests for controlplane config parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from lintgate.config import load_controlplane_config
from lintgate.controlplane.types import ChannelConfig, ControlPlaneConfig

# -- channel_blocking tests ---------------------------------------------------


def test_channel_blocking_configured_blocking() -> None:
    """channel_blocking returns True when the channel is configured with blocking=True."""
    cfg = ControlPlaneConfig(channels={"tests": ChannelConfig(blocking=True)})
    assert cfg.channel_blocking("tests") is True


def test_channel_blocking_configured_not_blocking() -> None:
    """channel_blocking returns False when the channel is configured with blocking=False."""
    cfg = ControlPlaneConfig(channels={"tests": ChannelConfig(blocking=False)})
    assert cfg.channel_blocking("tests") is False


def test_channel_blocking_default_lint_is_blocking() -> None:
    """channel_blocking defaults to True for 'lint' when not in channels dict."""
    cfg = ControlPlaneConfig(channels={})
    assert cfg.channel_blocking("lint") is True


def test_channel_blocking_default_non_lint_not_blocking() -> None:
    """channel_blocking defaults to False for non-'lint' channels not in channels dict."""
    cfg = ControlPlaneConfig(channels={})
    assert cfg.channel_blocking("tests") is False
    assert cfg.channel_blocking("behavior") is False
    assert cfg.channel_blocking("git") is False


def test_behavior_channel_settings_parsed(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    config_file = claude_dir / "lintgate.yaml"
    config_file.write_text(
        "controlplane:\n"
        "  enabled: true\n"
        "  channels:\n"
        "    behavior:\n"
        "      enabled: true\n"
        "      thresholds:\n"
        "        approach_cycling_count: 5\n"
        "      premature_action_ratio: 4.0\n"
    )

    cp = load_controlplane_config(str(tmp_path))
    assert cp is not None
    behavior = cp.channels["behavior"]
    assert behavior.settings["thresholds"]["approach_cycling_count"] == 5
    assert behavior.settings["premature_action_ratio"] == 4.0
