"""Shared helpers for compass renderers."""

from __future__ import annotations

import os

from ..compass import CompassDirective, CompassState


def format_directives(directives: list[CompassDirective], kind: str) -> list[str]:
    """Extract directive texts matching a given kind."""
    return [d.text for d in directives if d.kind == kind]


def axis_summary(compass: CompassState, axis_name: str) -> str:
    """Get the summary for a compass axis, or empty string."""
    axis = compass.axes.get(axis_name)
    return axis.summary if axis and axis.summary else ""


def project_name(metadata: dict[str, str]) -> str:
    """Get project name from metadata with fallback."""
    if metadata.get("project_name"):
        return metadata["project_name"]
    if metadata.get("name"):
        return metadata["name"]
    project_root = metadata.get("project_root", "")
    if project_root:
        base = os.path.basename(os.path.normpath(project_root))
        if base:
            return base
    return "project"


def truncate_lines(lines: list[str], token_budget: int) -> list[str]:
    """Truncate line list to approximate token budget (~4 chars/token)."""
    char_budget = token_budget * 4
    result: list[str] = []
    total = 0
    for line in lines:
        total += len(line) + 1  # +1 for newline
        if total > char_budget:
            break
        result.append(line)
    return result
