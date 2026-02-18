"""Tests for MCP strictness override behavior in LintChannel."""

from __future__ import annotations

from lintgate.channels.lint_channel import _apply_mcp_strictness_override
from lintgate.controlplane.types import SupervisionEvent
from lintgate.types import LintTier


def _tier(strictness: str = "normal") -> LintTier:
    return LintTier(
        name="tier_2_logic",
        linters=["ruff_check"],
        files=["/tmp/example.py"],
        reason="test",
        strictness=strictness,
    )


def test_mcp_strictness_override_applies_strict() -> None:
    event = SupervisionEvent(
        surface="mcp",
        project_root="/tmp",
        tool_name="controlplane_run",
        raw_input={"strictness": "strict"},
    )
    out = _apply_mcp_strictness_override(event, _tier("normal"))
    assert out.strictness == "strict"


def test_mcp_strictness_override_applies_relaxed() -> None:
    event = SupervisionEvent(
        surface="mcp",
        project_root="/tmp",
        tool_name="controlplane_run",
        raw_input={"strictness": "relaxed"},
    )
    out = _apply_mcp_strictness_override(event, _tier("normal"))
    assert out.strictness == "relaxed"


def test_mcp_strictness_override_ignores_invalid_value() -> None:
    event = SupervisionEvent(
        surface="mcp",
        project_root="/tmp",
        tool_name="controlplane_run",
        raw_input={"strictness": "very_strict"},
    )
    out = _apply_mcp_strictness_override(event, _tier("normal"))
    assert out.strictness == "normal"


def test_mcp_strictness_override_ignores_non_mcp_surface() -> None:
    event = SupervisionEvent(
        surface="hook",
        project_root="/tmp",
        tool_name="Edit",
        raw_input={"strictness": "strict"},
    )
    out = _apply_mcp_strictness_override(event, _tier("normal"))
    assert out.strictness == "normal"
