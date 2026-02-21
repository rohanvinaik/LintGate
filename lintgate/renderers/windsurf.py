"""Windsurf renderer -- outputs .windsurf/rules/compass.md."""

from __future__ import annotations

from ..compass import CompassState
from ._helpers import axis_summary, format_directives, project_name, truncate_lines


class WindsurfRenderer:
    name = "windsurf"
    output_paths = [".windsurf/rules/compass.md"]

    def render(
        self, compass: CompassState, metadata: dict[str, str]
    ) -> dict[str, str]:
        name = project_name(metadata)
        mission = axis_summary(compass, "problem") or "Write correct code."
        architecture = axis_summary(compass, "solution")
        impl = axis_summary(compass, "implementation")
        toward = format_directives(compass.directives, "toward")
        away = format_directives(compass.directives, "away")
        forbidden = format_directives(compass.directives, "forbidden")

        lines = [
            f"# {name} -- Windsurf Rules",
            "",
            "## Mission",
            "",
            mission,
            "",
        ]
        if architecture:
            lines += ["## Architecture", "", architecture, ""]
        if impl:
            lines += ["## Implementation", "", impl, ""]

        if toward:
            lines += ["## Best Practices"] + [f"- {t}" for t in toward] + [""]
        if away:
            lines += ["## Anti-Patterns"] + [f"- {a}" for a in away] + [""]
        if forbidden:
            lines += ["## Hard Constraints"] + [f"- {f}" for f in forbidden] + [""]

        # Axis detail section for deeper context
        lines.append("## Compass Axes")
        for axis_name in ("problem", "solution", "implementation", "world"):
            axis = compass.axes.get(axis_name)
            if axis and axis.claims:
                lines.append(f"### {axis_name.capitalize()}")
                for claim in axis.claims[:8]:
                    lines.append(f"- {claim.text}")
                lines.append("")

        return {
            ".windsurf/rules/compass.md": "\n".join(truncate_lines(lines, 6000))
        }
