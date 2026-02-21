"""Copilot renderer -- outputs .github/copilot-instructions.md."""

from __future__ import annotations

from ..compass import CompassState
from ._helpers import axis_summary, format_directives, project_name, truncate_lines


class CopilotRenderer:
    name = "copilot"
    output_paths = [".github/copilot-instructions.md"]

    def render(
        self, compass: CompassState, metadata: dict[str, str]
    ) -> dict[str, str]:
        name = project_name(metadata)
        mission = axis_summary(compass, "problem") or "Write correct code."
        architecture = axis_summary(compass, "solution")
        toward = format_directives(compass.directives, "toward")
        away = format_directives(compass.directives, "away")

        lines = [
            f"# {name} -- Copilot Instructions",
            "",
            mission,
            "",
        ]
        if architecture:
            lines += [f"Architecture: {architecture}", ""]
        if toward:
            lines.append("Follow these practices:")
            lines += [f"- {t}" for t in toward]
            lines.append("")
        if away:
            lines.append("Avoid these patterns:")
            lines += [f"- {a}" for a in away]
            lines.append("")

        return {
            ".github/copilot-instructions.md": "\n".join(
                truncate_lines(lines, 1200)
            )
        }
