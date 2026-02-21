"""Cursor renderer -- outputs .cursor/rules/compass.mdc."""

from __future__ import annotations

from ..compass import CompassState
from ._helpers import axis_summary, format_directives, project_name, truncate_lines


class CursorRenderer:
    name = "cursor"
    output_paths = [".cursor/rules/compass.mdc"]

    def render(
        self, compass: CompassState, metadata: dict[str, str]
    ) -> dict[str, str]:
        name = project_name(metadata)
        mission = axis_summary(compass, "problem") or "Write correct code."
        architecture = axis_summary(compass, "solution")
        toward = format_directives(compass.directives, "toward")
        away = format_directives(compass.directives, "away")
        forbidden = format_directives(compass.directives, "forbidden")

        lines = [
            "---",
            "description: Compass-derived project rules",
            f"globs: ['**/*']",
            "---",
            "",
            f"# {name} Rules",
            "",
            f"**Mission:** {mission}",
            "",
        ]
        if architecture:
            lines.append(f"**Architecture:** {architecture}")
            lines.append("")

        if toward:
            lines.append("**Do:**")
            lines += [f"- {t}" for t in toward]
            lines.append("")
        if away:
            lines.append("**Avoid:**")
            lines += [f"- {a}" for a in away]
            lines.append("")
        if forbidden:
            lines.append("**Forbidden:**")
            lines += [f"- {f}" for f in forbidden]
            lines.append("")

        return {".cursor/rules/compass.mdc": "\n".join(truncate_lines(lines, 2000))}
