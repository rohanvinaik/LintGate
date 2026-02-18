"""Tests for controlplane config parsing."""

from __future__ import annotations

from pathlib import Path

from lintgate.config import load_controlplane_config


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
