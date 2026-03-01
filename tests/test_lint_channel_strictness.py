"""Tests for MCP strictness override behavior in LintChannel."""

from __future__ import annotations

from lintgate.channels.lint_channel import (
    _apply_mcp_strictness_override,
    _compute_dynamic_timeout_ms,
)
from lintgate.controlplane.types import (
    ChannelConfig,
    ControlPlaneConfig,
    SupervisionEvent,
)
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


def test_dynamic_timeout_respects_channel_baseline() -> None:
    cp = ControlPlaneConfig(
        enabled=True,
        channels={"lint": ChannelConfig(timeout_ms=6400)},
    )
    timeout_ms = _compute_dynamic_timeout_ms(cp, "lint", [])
    assert timeout_ms == 6400


def test_dynamic_timeout_scales_with_scope_and_caps_to_budget(monkeypatch) -> None:
    cp = ControlPlaneConfig(enabled=True, latency_budget_ms=12000)
    monkeypatch.setattr(
        "lintgate.channels.lint_channel.os.path.getsize",
        lambda _: 1_000_000,
    )
    timeout_ms = _compute_dynamic_timeout_ms(cp, "lint", ["a.py", "b.py"])
    assert timeout_ms == int(cp.latency_budget_ms * 0.9)
