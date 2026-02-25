"""Tests for disposition enforcement configuration parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lintgate.config import load_controlplane_config
from lintgate.controlplane.types import DispositionEnforcementConfig

if TYPE_CHECKING:
    from pathlib import Path


def test_parse_disposition_enforcement_from_yaml(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    config_file = claude_dir / "lintgate.yaml"
    config_file.write_text(
        "controlplane:\n"
        "  enabled: true\n"
        "  orchestration:\n"
        "    disposition_enforcement:\n"
        "      enabled: true\n"
        "      nudge_after_edit_without_lint: false\n"
        "      max_nudges_per_disposition: 5\n"
        "      cadence_health_check_events: 10\n"
    )

    cp = load_controlplane_config(str(tmp_path))
    assert cp is not None
    de = cp.disposition_enforcement
    assert isinstance(de, DispositionEnforcementConfig)
    assert de.enabled is True
    assert de.nudge_after_edit_without_lint is False
    assert de.max_nudges_per_disposition == 5
    assert de.cadence_health_check_events == 10


def test_parse_disposition_enforcement_legacy_path(tmp_path: Path) -> None:
    """Verify backward compat for top-level disposition_enforcement key."""
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    config_file = claude_dir / "lintgate.yaml"
    config_file.write_text(
        "controlplane:\n"
        "  enabled: true\n"
        "  disposition_enforcement:\n"
        "    enabled: true\n"
        "    nudge_after_edit_without_lint: false\n"
    )

    cp = load_controlplane_config(str(tmp_path))
    assert cp is not None
    de = cp.disposition_enforcement
    assert de.enabled is True
    assert de.nudge_after_edit_without_lint is False


def test_disposition_enforcement_defaults(tmp_path: Path) -> None:
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    config_file = claude_dir / "lintgate.yaml"
    config_file.write_text("controlplane:\n  enabled: true\n")

    cp = load_controlplane_config(str(tmp_path))
    assert cp is not None
    de = cp.disposition_enforcement
    assert de.enabled is False
    assert de.nudge_after_edit_without_lint is True
    assert de.max_nudges_per_disposition == 3
