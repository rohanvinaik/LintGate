"""Generic renderer -- outputs CONTEXT.md."""

from __future__ import annotations

from ..compass import CompassState
from ._helpers import axis_summary, format_directives, project_name, truncate_lines


class GenericRenderer:
    name = "generic"
    output_paths = ["CONTEXT.md"]

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
            f"# {name} Context",
            "",
            mission,
            "",
        ]
        if architecture:
            lines += [f"Architecture: {architecture}", ""]
        if toward:
            lines.append("Preferred practices:")
            lines += [f"- {t}" for t in toward]
            lines.append("")
        if away:
            lines.append("Avoid:")
            lines += [f"- {a}" for a in away]
            lines.append("")
        if forbidden:
            lines.append("Forbidden:")
            lines += [f"- {f}" for f in forbidden]
            lines.append("")

        return {"CONTEXT.md": "\n".join(truncate_lines(lines, 2000))}
