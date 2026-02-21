"""Cline renderer -- outputs .clinerules/compass.md."""

from __future__ import annotations

from ..compass import CompassState
from ._helpers import axis_summary, format_directives, project_name


class ClineRenderer:
    name = "cline"
    output_paths = [".clinerules/compass.md"]

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
            f"# {name} -- Cline Rules",
            "",
            "## Mission",
            "",
            mission,
            "",
        ]
        if architecture:
            lines += ["## Architecture", "", architecture, ""]
        if impl:
            lines += ["## Implementation Notes", "", impl, ""]

        if toward:
            lines += ["## Preferred Practices"] + [f"- {t}" for t in toward] + [""]
        if away:
            lines += ["## Avoid"] + [f"- {a}" for a in away] + [""]
        if forbidden:
            lines += ["## Forbidden"] + [f"- {f}" for f in forbidden] + [""]

        # Include claim details for richer context
        for axis_name in ("problem", "solution", "implementation", "world"):
            axis = compass.axes.get(axis_name)
            if axis and axis.claims:
                lines.append(f"## {axis_name.capitalize()} Claims")
                for claim in axis.claims:
                    lines.append(f"- {claim.text}")
                lines.append("")

        return {".clinerules/compass.md": "\n".join(lines)}
