"""Unified NextAction schema for self-orchestrating tool chains.

Every MCP tool's ``next_actions`` output should use this schema so the agent
can follow tool-suggested actions without understanding the underlying theory.

Four previously incompatible formats are replaced:
- CP reporter: {"tool", "args", "reason", "priority"}
- mutation_prescribe: list[str] (bare tool names)
- convergence_tools: {"tool", "when"}
- mcp_server lint: {"tool", "args", "safe", "reason", "priority"}

All now produce ``list[NextAction]`` internally, serialized to dicts at the
output boundary via ``to_dict()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NextAction:
    """A single suggested follow-up tool call.

    Attributes:
        tool: MCP tool name (e.g. ``"mutation_run_sampling"``).
        args: Tool arguments dict (e.g. ``{"path": ".", "files": ["src/core.py"]}``).
        reason: Human-readable explanation of *why* this action is suggested.
        priority: 1 = highest urgency, 10 = lowest. Default 5.
        condition: Optional trigger condition (e.g. ``"after background profiling completes"``).
        safe: Whether this action is safe to execute without user confirmation.
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    priority: int = 5
    condition: str | None = None
    safe: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a flat dict for MCP tool JSON output."""
        d: dict[str, Any] = {"tool": self.tool}
        if self.args:
            d["args"] = self.args
        if self.reason:
            d["reason"] = self.reason
        d["priority"] = self.priority
        if self.condition is not None:
            d["condition"] = self.condition
        if not self.safe:
            d["safe"] = False
        return d


def serialize_next_actions(actions: list[NextAction]) -> list[dict[str, Any]]:
    """Serialize a list of NextActions to dicts for JSON output."""
    return [a.to_dict() for a in actions]
